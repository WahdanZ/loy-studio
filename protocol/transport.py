"""
Transport abstraction for YS / YSTP01 BLE Pixel LED Displays (LOY Studio 2.0).

Provides:
  - BleProfile & BLE_PROFILES: Conservative, Balanced, Fast, and Safe-ACK candidate profiles.
  - TransferProgress: Live transfer state, ETA, bytes/sec, and safe cancellation.
  - BleTransport: Real BLE transport using Bleak with sliding-window ACK, retries,
    profile switching, auto-reconnect, and conservative fallback.
  - MockTransport: In-memory simulated display with realistic latency simulation and
    protocol-accurate ACK/GET responses for automated testing and benchmarking.
"""

from __future__ import annotations

import asyncio
import logging
import struct
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from .constants import (
    BLE_NOTIFY_CHAR_UUID,
    BLE_SERVICE_UUID,
    BLE_WRITE_CHAR_UUID,
    CMD_TYPE_GET,
    CMD_TYPE_SET,
    DEFAULT_BLE_CHUNK_SIZE,
    DEFAULT_DEVICE_PREFIXES,
    FLAG_ACK_REQUIRED,
    FLAG_CHECKSUM,
    RESP_TYPE_GET_ACK,
    RESP_TYPE_SET_ACK,
    TLV,
)
from .decoder import decode_response
from .framing import PacketParser, build_frame, encode_tlv, split_ble_writes

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BleProfile:
    """Configurable BLE transport performance profile."""
    name: str
    ble_chunk_size: int
    swnd_max: int
    write_delay_s: float
    write_with_response: bool
    retries: int
    estimated_bps: float  # Conservative planning estimate until measured on live hardware
    hardware_verified: bool = False
    description: str = ""


BLE_PROFILES: Dict[str, BleProfile] = {
    "conservative": BleProfile(
        name="conservative",
        ble_chunk_size=DEFAULT_BLE_CHUNK_SIZE,
        swnd_max=1,
        write_delay_s=0.015,
        write_with_response=False,
        retries=3,
        estimated_bps=1950.0,
        hardware_verified=True,
        description="180B chunks, 15ms pacing, stop-and-wait ACK (verified 1953 B/s on EXYC-A)",
    ),
    "ys_l_fast": BleProfile(
        name="ys_l_fast",
        ble_chunk_size=DEFAULT_BLE_CHUNK_SIZE,
        swnd_max=1,
        write_delay_s=0.002,
        write_with_response=False,
        retries=3,
        estimated_bps=1850.0,
        hardware_verified=True,
        description="180B chunks, 2ms pacing, stop-and-wait ACK (verified 1800 B/s on EXYC-A)",
    ),
    "safe_ack": BleProfile(
        name="safe_ack",
        ble_chunk_size=180,
        swnd_max=1,
        write_delay_s=0.025,
        write_with_response=True,
        retries=4,
        estimated_bps=2200.0,
        hardware_verified=False,
        description="180B Write-With-Response fallback for strict BLE stacks",
    ),
}

# Sliding-window (swnd > 1) profiles. HARDWARE RULE: swnd > 1 wedges the panel's BLE RX
# buffer (CLAUDE.md constraint 2). These exist ONLY for MockTransport simulation/benchmarks
# and are never selectable on a real BleTransport.
EXPERIMENTAL_PROFILES: Dict[str, BleProfile] = {
    "ys_l_swnd3": BleProfile(
        name="ys_l_swnd3",
        ble_chunk_size=DEFAULT_BLE_CHUNK_SIZE,
        swnd_max=3,
        write_delay_s=0.002,
        write_with_response=False,
        retries=3,
        estimated_bps=4000.0,
        hardware_verified=False,
        description="180B chunks, 2ms pacing, sliding window 3 (candidate speedup for YS*L)",
    ),
    "balanced": BleProfile(
        name="balanced",
        ble_chunk_size=248,
        swnd_max=5,
        write_delay_s=0.010,
        write_with_response=False,
        retries=3,
        estimated_bps=7500.0,
        hardware_verified=False,
        description="248B chunks, sliding window 5 (candidate for EX* firmware < V2.8.9#V25)",
    ),
    "fast": BleProfile(
        name="fast",
        ble_chunk_size=509,
        swnd_max=10,
        write_delay_s=0.005,
        write_with_response=False,
        retries=3,
        estimated_bps=12000.0,
        hardware_verified=False,
        description="509B chunks, sliding window 10 (candidate for EX* firmware >= V2.8.9#V25)",
    ),
}

ALL_PROFILES: Dict[str, BleProfile] = {**BLE_PROFILES, **EXPERIMENTAL_PROFILES}


HIGH_SPEED_PREFIXES = (
    "EXYS-AS", "EXYB-AS", "EXHM_AS", "EXYX-B", "EXYX-BS",
    "EXEYE-AS", "EXYD-CS", "EXSZ-A", "EXSZ-AS", "EXP6-A", "EXP6-AS",
)


def recommend_profile(device_name: str = "", version: str = "") -> str:
    """
    Recommend the safest fast BLE profile based on recovered module 4156.compatibleManage()
    and real hardware verification on EXYC-A / YS*L.
    """
    # Never auto-select a sliding-window profile: swnd > 1 wedges the panel (CLAUDE.md #2).
    # High-speed EX* firmware still gets a verified stop-and-wait profile.
    if (device_name or "").endswith("L"):
        return "ys_l_fast"
    return "conservative"


@dataclass
class TransferProgress:
    """Observable state of the active or most recent multi-frame transfer."""
    active: bool = False
    label: str = ""
    profile: str = "conservative"
    sent_frames: int = 0
    total_frames: int = 0
    sent_bytes: int = 0
    total_bytes: int = 0
    percent: float = 0.0
    elapsed_s: float = 0.0
    eta_s: float = 0.0
    measured_bps: float = 0.0
    cancelled: bool = False
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class DisplayNotAdvertising(RuntimeError):
    """The panel is not advertising: it is off, or another app/process still holds its BLE link."""

    def __init__(self, address: str) -> None:
        super().__init__(
            "Display not found over Bluetooth. It is either powered off, or still connected to another "
            "app (LOY on your phone, or another LOY Studio window/process on this Mac). Close the other "
            "app; if it still isn't found, unplug the display's USB power for 3 seconds and try again."
        )
        self.address = address


@dataclass
class DiscoveredDisplay:
    name: str
    address: str
    rssi: int
    service_uuids: List[str] = field(default_factory=list)


PacketLogCallback = Callable[[str, bytes, Optional[Dict[str, Any]]], None]
ProgressCallback = Callable[[TransferProgress], None]


class BaseTransport:
    """Abstract transport interface separating protocol logic from BLE I/O."""

    #: Real hardware transports must never run swnd > 1 profiles.
    allow_experimental_profiles: bool = False

    def __init__(self, profile: str = "conservative") -> None:
        self._sno = 1
        self._packet_log_cb: Optional[PacketLogCallback] = None
        self._progress_cb: Optional[ProgressCallback] = None
        self._profile: BleProfile = self._resolve_profile(profile, strict=False)
        self._cancel_event = asyncio.Event()
        self.transfer = TransferProgress(profile=self._profile.name)
        self.transfer_errors: List[Dict[str, Any]] = []
        self.last_measured_bps: float = self._profile.estimated_bps
        self.negotiated_mtu: Optional[int] = None
        self.last_rssi: Optional[int] = None

    @property
    def profile(self) -> BleProfile:
        return self._profile

    def _resolve_profile(self, name: str, *, strict: bool = True) -> BleProfile:
        if name in BLE_PROFILES:
            return BLE_PROFILES[name]
        if name in EXPERIMENTAL_PROFILES:
            if self.allow_experimental_profiles:
                return EXPERIMENTAL_PROFILES[name]
            if strict:
                raise ValueError(
                    f"BLE profile '{name}' uses sliding window {EXPERIMENTAL_PROFILES[name].swnd_max} > 1, "
                    "which wedges the display's BLE RX buffer. Use 'ys_l_fast', 'conservative', or 'safe_ack'."
                )
        elif strict:
            raise ValueError(f"Unknown BLE profile '{name}'. Choose from: {list(BLE_PROFILES)}")
        return BLE_PROFILES["conservative"]

    def set_profile(self, name: str) -> BleProfile:
        self._profile = self._resolve_profile(name, strict=True)
        self.transfer.profile = self._profile.name
        return self._profile

    def estimate_transfer_seconds(self, payload_bytes: int) -> float:
        bps = self.last_measured_bps if self.last_measured_bps > 100 else self._profile.estimated_bps
        return round(max(0.05, payload_bytes / max(1.0, bps)), 2)

    def next_sno(self) -> int:
        val = self._sno
        self._sno = (self._sno % 65535) + 1
        return val

    def set_packet_logger(self, cb: Optional[PacketLogCallback]) -> None:
        self._packet_log_cb = cb

    def set_progress_callback(self, cb: Optional[ProgressCallback]) -> None:
        self._progress_cb = cb

    def cancel_transfer(self) -> None:
        """Request safe cancellation between protocol frames."""
        self._cancel_event.set()

    def _emit_progress(self) -> None:
        if self._progress_cb:
            self._progress_cb(self.transfer)

    def _record_error(self, message: str) -> None:
        self.transfer_errors.append({"ts": round(time.time(), 3), "error": message})
        if len(self.transfer_errors) > 50:
            self.transfer_errors = self.transfer_errors[-50:]

    def _log_packet(self, direction: str, raw: bytes, decoded: Optional[Dict[str, Any]] = None) -> None:
        if self._packet_log_cb:
            self._packet_log_cb(direction, raw, decoded)

    async def connect(self, address: str) -> None:
        raise NotImplementedError

    async def disconnect(self) -> None:
        raise NotImplementedError

    @property
    def is_connected(self) -> bool:
        raise NotImplementedError

    async def send_frame(
        self, frame: bytes, *, wait_ack: bool = True, timeout: float = 5.0
    ) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    async def _send_frame_with_retry(
        self, frame: bytes, *, wait_ack: bool = True, timeout: float = 5.0
    ) -> Optional[Dict[str, Any]]:
        last_exc: Optional[Exception] = None
        for attempt in range(1, self._profile.retries + 1):
            try:
                return await self.send_frame(frame, wait_ack=wait_ack, timeout=timeout)
            except Exception as exc:
                last_exc = exc
                self._record_error(f"Attempt {attempt}/{self._profile.retries} failed: {exc}")
                if attempt < self._profile.retries:
                    await asyncio.sleep(0.15 * attempt)
        assert last_exc is not None
        raise last_exc

    async def send_frames(
        self,
        frames: Sequence[bytes],
        *,
        wait_ack: bool = True,
        timeout: float = 5.0,
        label: str = "Transfer",
        progress_cb: Optional[Callable[[int, int], None]] = None,
    ) -> List[Optional[Dict[str, Any]]]:
        """
        Send a multi-frame program using either stop-and-wait (swnd_max == 1)
        or pipelined sliding-window ACKs (swnd_max > 1, mirrors module 3132),
        with live progress tracking, ETA, and safe cancellation.
        """
        self._cancel_event.clear()
        total_frames = len(frames)
        total_bytes = sum(len(f) for f in frames)
        t0 = time.monotonic()

        self.transfer = TransferProgress(
            active=True,
            label=label,
            profile=self._profile.name,
            sent_frames=0,
            total_frames=total_frames,
            sent_bytes=0,
            total_bytes=total_bytes,
            percent=0.0,
            elapsed_s=0.0,
            eta_s=self.estimate_transfer_seconds(total_bytes),
        )
        self._emit_progress()

        results: List[Optional[Dict[str, Any]]] = [None] * total_frames
        sent_bytes = 0
        sent_count = 0

        try:
            use_swnd = (
                wait_ack
                and self._profile.swnd_max > 1
                and self.allow_experimental_profiles
                and total_frames > 4
            )
            if not use_swnd:
                for idx, frame in enumerate(frames):
                    if self._cancel_event.is_set():
                        self.transfer.cancelled = True
                        raise asyncio.CancelledError("Transfer cancelled by user")
                    res = await self._send_frame_with_retry(frame, wait_ack=wait_ack, timeout=timeout)
                    results[idx] = res
                    sent_count += 1
                    sent_bytes += len(frame)
                    self._update_progress_metrics(t0, sent_count, total_frames, sent_bytes, total_bytes)
                    if progress_cb:
                        progress_cb(sent_count, total_frames)
            else:
                # Sliding window: first frame (index 0) must ACK before pipelining (module aa14/0075)
                results[0] = await self._send_frame_with_retry(frames[0], wait_ack=True, timeout=timeout)
                sent_count = 1
                sent_bytes = len(frames[0])
                self._update_progress_metrics(t0, sent_count, total_frames, sent_bytes, total_bytes)
                if progress_cb:
                    progress_cb(sent_count, total_frames)

                window = self._profile.swnd_max
                pos = 1
                while pos < total_frames:
                    if self._cancel_event.is_set():
                        self.transfer.cancelled = True
                        raise asyncio.CancelledError("Transfer cancelled by user")
                    batch_end = min(total_frames, pos + window)
                    tasks = [
                        asyncio.create_task(
                            self._send_frame_with_retry(frames[i], wait_ack=True, timeout=timeout)
                        )
                        for i in range(pos, batch_end)
                    ]
                    batch_results = await asyncio.gather(*tasks)
                    for offset, res in enumerate(batch_results):
                        idx = pos + offset
                        results[idx] = res
                        sent_count += 1
                        sent_bytes += len(frames[idx])
                    self._update_progress_metrics(t0, sent_count, total_frames, sent_bytes, total_bytes)
                    if progress_cb:
                        progress_cb(sent_count, total_frames)
                    pos = batch_end

            elapsed = max(0.001, time.monotonic() - t0)
            self.last_measured_bps = total_bytes / elapsed
            self.transfer.measured_bps = round(self.last_measured_bps, 1)
            self.transfer.percent = 100.0
            self.transfer.eta_s = 0.0
            return results
        except Exception as exc:
            self.transfer.error = str(exc)
            raise
        finally:
            self.transfer.active = False
            self._emit_progress()

    def _update_progress_metrics(
        self, t0: float, sent_frames: int, total_frames: int, sent_bytes: int, total_bytes: int
    ) -> None:
        elapsed = max(0.001, time.monotonic() - t0)
        bps = sent_bytes / elapsed
        remaining = max(0, total_bytes - sent_bytes)
        self.transfer.sent_frames = sent_frames
        self.transfer.sent_bytes = sent_bytes
        self.transfer.percent = round((sent_frames / max(1, total_frames)) * 100.0, 1)
        self.transfer.elapsed_s = round(elapsed, 2)
        self.transfer.measured_bps = round(bps, 1)
        self.transfer.eta_s = round(remaining / max(1.0, bps), 2)
        self._emit_progress()


class BleTransport(BaseTransport):
    """Real BLE transport built on Bleak (CoreBluetooth / BlueZ / WinRT)."""

    def __init__(
        self,
        *,
        profile: str = "conservative",
        ble_chunk_size: Optional[int] = None,
        write_delay_s: Optional[float] = None,
    ) -> None:
        super().__init__(profile=profile)
        self._chunk_override = ble_chunk_size
        self._delay_override = write_delay_s
        self._client: Any = None
        self._last_address: Optional[str] = None
        self._parser = PacketParser()
        self._pending_acks: Dict[int, asyncio.Future[Dict[str, Any]]] = {}
        self._write_lock = asyncio.Lock()
        self._unsolicited_cb: Optional[Callable[[Dict[str, Any]], None]] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    @property
    def effective_chunk_size(self) -> int:
        base = self._chunk_override or self._profile.ble_chunk_size
        if self.negotiated_mtu and self.negotiated_mtu > 23:
            return min(base, max(20, self.negotiated_mtu - 3))
        return base

    @property
    def effective_write_delay(self) -> float:
        return self._delay_override if self._delay_override is not None else self._profile.write_delay_s

    @staticmethod
    async def scan(
        timeout: float = 5.0,
        prefixes: Sequence[str] = DEFAULT_DEVICE_PREFIXES,
        include_all: bool = False,
    ) -> List[DiscoveredDisplay]:
        from bleak import BleakScanner

        found = await BleakScanner.discover(timeout=timeout, return_adv=True)
        displays: List[DiscoveredDisplay] = []
        for device, adv in found.values():
            name = adv.local_name or device.name or ""
            uuids = [u.lower() for u in (adv.service_uuids or [])]
            matches_prefix = any(name.startswith(p) for p in prefixes)
            matches_uuid = BLE_SERVICE_UUID in uuids
            if include_all or matches_prefix or matches_uuid:
                displays.append(
                    DiscoveredDisplay(
                        name=name or "(unnamed)",
                        address=device.address,
                        rssi=adv.rssi if adv.rssi is not None else -100,
                        service_uuids=uuids,
                    )
                )
        displays.sort(key=lambda d: d.rssi, reverse=True)
        return displays

    def _on_notify(self, _sender: Any, data: bytearray) -> None:
        for pkt in self._parser.feed(bytes(data)):
            decoded = decode_response(pkt)
            self._log_packet("RX", pkt.raw, decoded)
            fut = self._pending_acks.pop(pkt.sno, None)
            if fut and not fut.done():
                fut.set_result(decoded)
            elif self._unsolicited_cb:
                self._unsolicited_cb(decoded)

    def _on_disconnected(self, _client: Any) -> None:
        """Bleak callback: fail pending ACK waits immediately instead of waiting for timeouts."""
        logger.warning("BLE link to %s dropped", self._last_address)
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(self._fail_pending_acks)

    def _fail_pending_acks(self) -> None:
        for fut in list(self._pending_acks.values()):
            if not fut.done():
                fut.set_exception(ConnectionError("BLE link dropped"))
        self._pending_acks.clear()

    async def connect(self, address: str) -> None:
        from bleak import BleakClient

        self._parser.reset()
        self._pending_acks.clear()
        self._last_address = address
        self._loop = asyncio.get_running_loop()
        self._client = BleakClient(address, disconnected_callback=self._on_disconnected)
        try:
            await self._client.connect()
            self.negotiated_mtu = getattr(self._client, "mtu_size", None)
            await asyncio.sleep(1.0)
            await self._client.start_notify(BLE_NOTIFY_CHAR_UUID, self._on_notify)
            await asyncio.sleep(0.25)
        except BaseException as exc:
            # Never leave a half-open link behind (CLAUDE.md constraint 5)
            try:
                await self.disconnect()
            except Exception:
                logger.exception("Cleanup disconnect after failed connect also failed")
            if type(exc).__name__ == "BleakDeviceNotFoundError":
                raise DisplayNotAdvertising(address) from exc
            raise
        logger.info("Connected to %s (MTU=%s) and enabled FFF1 notifications", address, self.negotiated_mtu)

    async def reconnect(self) -> None:
        if not self._last_address:
            raise RuntimeError("No previous BLE address to reconnect to")
        await self.disconnect()
        await asyncio.sleep(0.4)
        await self.connect(self._last_address)

    async def disconnect(self) -> None:
        """Graceful disconnect. Each step is isolated so a failed stop_notify never skips disconnect()."""
        client = self._client
        if client is not None:
            try:
                if client.is_connected:
                    try:
                        await asyncio.wait_for(client.stop_notify(BLE_NOTIFY_CHAR_UUID), timeout=2.0)
                    except Exception as exc:
                        logger.warning("stop_notify failed during disconnect: %s", exc)
                    try:
                        await asyncio.wait_for(client.disconnect(), timeout=5.0)
                    except Exception as exc:
                        logger.warning("BLE disconnect failed: %s", exc)
            finally:
                self._client = None
        for fut in self._pending_acks.values():
            if not fut.done():
                fut.cancel()
        self._pending_acks.clear()

    @property
    def is_connected(self) -> bool:
        return bool(self._client and self._client.is_connected)

    async def send_frame(
        self, frame: bytes, *, wait_ack: bool = True, timeout: float = 5.0
    ) -> Optional[Dict[str, Any]]:
        if not self.is_connected:
            raise RuntimeError("Not connected to BLE display")

        sno = struct.unpack_from("<H", frame, 6)[0]
        flags = frame[8]
        expects_ack = wait_ack and bool(flags & FLAG_ACK_REQUIRED)

        fut: Optional[asyncio.Future[Dict[str, Any]]] = None
        if expects_ack:
            loop = asyncio.get_running_loop()
            fut = loop.create_future()
            self._pending_acks[sno] = fut

        self._log_packet("TX", frame, {"sno": sno, "cmd_type": frame[9], "len": len(frame)})

        chunks = split_ble_writes(frame, self.effective_chunk_size)
        async with self._write_lock:
            for idx, chunk in enumerate(chunks):
                await self._client.write_gatt_char(
                    BLE_WRITE_CHAR_UUID, chunk, response=self._profile.write_with_response
                )
                if idx + 1 < len(chunks) and self.effective_write_delay > 0:
                    await asyncio.sleep(self.effective_write_delay)

        if fut is None:
            return None

        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending_acks.pop(sno, None)
            raise TimeoutError(f"Timed out waiting for ACK on sno={sno}")


class MockTransport(BaseTransport):
    """
    Simulated YS / YSTP01 LED display for offline testing, demos, and unit tests.
    Validates incoming frames, maintains virtual state, and returns protocol-accurate
    ACK/GET response frames.
    """

    # The simulator may exercise sliding-window code paths; real hardware may not.
    allow_experimental_profiles = True

    def __init__(
        self,
        width: int = 64,
        height: int = 64,
        device_id: str = "YS00000001",
        profile: str = "conservative",
        simulated_latency_s: float = 0.0,
    ) -> None:
        super().__init__(profile=profile)
        self.width = width
        self.height = height
        self.device_id = device_id
        self.version = "EXYS_V2.8.9#V25"
        self.model = f"YS-{width}x{height}"
        self.connected = False
        self.power_on = True
        self.brightness = 12
        self.rotation = 0
        self.mirror = False
        self.negotiated_mtu = 512
        self.last_rssi = -48
        self.simulated_latency_s = simulated_latency_s
        self.sent_frames: List[bytes] = []

    async def connect(self, address: str = "MOCK-00:11:22:33:44:55") -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    @property
    def is_connected(self) -> bool:
        return self.connected

    async def send_frame(
        self, frame: bytes, *, wait_ack: bool = True, timeout: float = 5.0
    ) -> Optional[Dict[str, Any]]:
        if not self.connected:
            raise RuntimeError("MockTransport is not connected")

        if self.simulated_latency_s > 0:
            await asyncio.sleep(self.simulated_latency_s)

        parser = PacketParser()
        pkts = parser.feed(frame)
        if not pkts:
            raise ValueError("Invalid YS frame sent to MockTransport")
        pkt = pkts[0]
        self.sent_frames.append(frame)
        self._log_packet("TX", frame, {"sno": pkt.sno, "cmd_type": pkt.cmd_type, "len": len(frame)})

        if not (wait_ack and (pkt.flags & FLAG_ACK_REQUIRED)):
            return None

        if pkt.cmd_type == CMD_TYPE_GET:
            resp_tlv = bytearray()
            pos = 0
            while pos < len(pkt.payload):
                tag = pkt.payload[pos]
                pos += 1
                length = pkt.payload[pos]
                pos += 1 + length
                if tag == TLV.DEV_INFO:
                    info_str = f"{self.device_id},EXYS,V2.8.9#V25,{self.model},0,MOCKCID".encode("ascii")
                    resp_tlv.extend(encode_tlv(TLV.DEV_INFO, info_str))
                elif tag == TLV.PARAM_DEV:
                    resp_tlv.extend(encode_tlv(TLV.PARAM_DEV, struct.pack("<BHH", 1, self.width, self.height)))
                elif tag == TLV.POWER:
                    resp_tlv.extend(encode_tlv(TLV.POWER, bytes([0, 1 if self.power_on else 0])))
                elif tag == TLV.LIGHT:
                    resp_tlv.extend(encode_tlv(TLV.LIGHT, bytes([0, self.brightness])))
                elif tag == TLV.ROTATE:
                    resp_tlv.extend(encode_tlv(TLV.ROTATE, bytes([self.rotation // 90])))
                elif tag == TLV.MIRROR:
                    resp_tlv.extend(encode_tlv(TLV.MIRROR, bytes([1 if self.mirror else 0])))
            resp_frame = build_frame(
                bytes(resp_tlv), sno=pkt.sno, cmd_type=RESP_TYPE_GET_ACK, flags=FLAG_CHECKSUM | 1
            )
        else:
            if len(pkt.payload) >= 4 and pkt.payload[0] == TLV.POWER:
                self.power_on = bool(pkt.payload[3])
            elif len(pkt.payload) >= 4 and pkt.payload[0] == TLV.LIGHT:
                self.brightness = pkt.payload[3]
            elif len(pkt.payload) >= 3 and pkt.payload[0] == TLV.ROTATE:
                self.rotation = (pkt.payload[2] % 4) * 90
            elif len(pkt.payload) >= 3 and pkt.payload[0] == TLV.MIRROR:
                self.mirror = bool(pkt.payload[2])
            resp_frame = build_frame(
                bytes([0]), sno=pkt.sno, cmd_type=RESP_TYPE_SET_ACK, flags=FLAG_CHECKSUM | 1
            )

        resp_pkt = PacketParser().feed(resp_frame)[0]
        decoded = decode_response(resp_pkt)
        self._log_packet("RX", resp_frame, decoded)
        return decoded
