"""Binary framing, ASN.1-like TLV encoding, and 16-bit checksum for YS / YSTP01."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .constants import (
    DEFAULT_BLE_CHUNK_SIZE,
    FLAG_CHECKSUM,
    FLAG_DEFAULT,
    PACKET_MAGIC,
    PACKET_TARGET_BROADCAST,
)

#: cmd_type 4 = firmware UPGRADE/OTA. Never sent by LOY Studio: it can permanently brick the panel.
FORBIDDEN_CMD_TYPES = frozenset({4})


def encode_tlv_length(length: int) -> bytes:
    """Encode TLV length using the protocol's 1/2/3-byte scheme (module a829 D())."""
    if length < 0:
        raise ValueError(f"TLV length must be non-negative, got {length}")
    if length < 128:
        return bytes([length])
    if length < 256:
        return bytes([0x81, length])
    if length <= 0xFFFF:
        return bytes([0x82, length & 0xFF, (length >> 8) & 0xFF])
    raise ValueError(f"TLV length exceeds 65535: {length}")


def decode_tlv_length(data: bytes, offset: int = 0) -> Tuple[int, int]:
    """Decode TLV length at offset. Returns (length, bytes_consumed)."""
    if offset >= len(data):
        raise ValueError("Truncated TLV length")
    first = data[offset]
    if first == 0x81:
        if offset + 1 >= len(data):
            raise ValueError("Truncated 2-byte TLV length")
        return data[offset + 1], 2
    if first == 0x82:
        if offset + 2 >= len(data):
            raise ValueError("Truncated 3-byte TLV length")
        val = struct.unpack_from("<H", data, offset + 1)[0]
        return val, 3
    return first, 1


def encode_tlv(tag: int, value: bytes) -> bytes:
    """Encode a single TLV element: [tag:1][len:1..3][value]."""
    return bytes([tag & 0xFF]) + encode_tlv_length(len(value)) + value


def compute_checksum(frame_without_checksum: bytes) -> int:
    """Compute 16-bit additive checksum modulo 65536 (module a829 $e() / 3341 u())."""
    return sum(frame_without_checksum) & 0xFFFF


def verify_checksum(frame: bytes) -> bool:
    """Verify trailing 16-bit little-endian additive checksum of a complete frame."""
    if len(frame) < 12:
        return False
    expected = struct.unpack_from("<H", frame, len(frame) - 2)[0]
    actual = compute_checksum(frame[:-2])
    return expected == actual


def build_frame(
    tlv_payload: bytes,
    sno: int,
    cmd_type: int,
    flags: int = FLAG_DEFAULT,
    target: int = PACKET_TARGET_BROADCAST,
) -> bytes:
    """
    Wrap a TLV payload in a complete YS wire frame:
      [0..1]  0xAA 0x55 (0x55AA LE)
      [2..3]  target (0xFFFF LE)
      [4..5]  body_len = len(tlv_payload) + (2 if checksum else 0) + 4 (LE)
      [6..7]  sno (uint16 LE)
      [8]     flags (0xC1 = checksum + ACK; 0x81 = checksum, no ACK)
      [9]     cmd_type (2=SET, 3=GET, 4=UPGRADE, 7=DOWNLOAD)
      [10..]  tlv_payload
      [-2..]  checksum (uint16 LE, if flags & 0x80)
    """
    if (cmd_type & 0xFF) in FORBIDDEN_CMD_TYPES:
        raise ValueError("Refusing to build an OTA/firmware-upgrade frame (cmd_type 4): risk of bricking the panel")
    has_checksum = bool(flags & FLAG_CHECKSUM)
    chk_len = 2 if has_checksum else 0
    body_len = len(tlv_payload) + chk_len + 4
    header = struct.pack(
        "<HHHHBB",
        PACKET_MAGIC,
        target & 0xFFFF,
        body_len & 0xFFFF,
        sno & 0xFFFF,
        flags & 0xFF,
        cmd_type & 0xFF,
    )
    frame = header + tlv_payload
    if has_checksum:
        chk = compute_checksum(frame)
        frame += struct.pack("<H", chk)
    return frame


def split_ble_writes(frame: bytes, mtu_payload: int = DEFAULT_BLE_CHUNK_SIZE) -> List[bytes]:
    """Split a wire frame into BLE GATT write chunks (default 180 bytes)."""
    if mtu_payload <= 0:
        raise ValueError("mtu_payload must be > 0")
    return [frame[i : i + mtu_payload] for i in range(0, len(frame), mtu_payload)]


@dataclass
class ParsedPacket:
    """A validated raw frame received from the display over FFF1 notifications."""
    sno: int
    flags: int
    cmd_type: int
    payload: bytes
    raw: bytes


class PacketParser:
    """
    Streaming reassembly buffer for FFF1 BLE notifications.
    Mirrors the logic in module 2658 (onBLECharacteristicValueChange) & 3341 (parse_ack_ex).
    """

    def __init__(self) -> None:
        self._buf = bytearray()

    def reset(self) -> None:
        self._buf.clear()

    def feed(self, chunk: bytes) -> List[ParsedPacket]:
        """Append incoming BLE notification bytes and extract all complete valid frames."""
        packets: List[ParsedPacket] = []
        if not chunk:
            return packets
        self._buf.extend(chunk)

        while len(self._buf) >= 10:
            # Find magic 0xAA 0x55
            idx = self._buf.find(b"\xaa\x55")
            if idx < 0:
                # Keep trailing 0xAA in case magic is split across notifications
                if self._buf and self._buf[-1] == 0xAA:
                    self._buf = bytearray(b"\xaa")
                else:
                    self._buf.clear()
                break
            if idx > 0:
                del self._buf[:idx]
            if len(self._buf) < 10:
                break

            body_len = struct.unpack_from("<H", self._buf, 4)[0]
            total_len = body_len + 6
            if total_len < 10 or total_len > 4096:
                # Invalid length, skip past this magic
                del self._buf[:2]
                continue
            if len(self._buf) < total_len:
                # Wait for more BLE notification fragments
                break

            raw = bytes(self._buf[:total_len])
            flags = raw[8]
            has_checksum = bool(flags & FLAG_CHECKSUM)
            if has_checksum and not verify_checksum(raw):
                # Checksum mismatch — resync past magic
                del self._buf[:2]
                continue

            del self._buf[:total_len]
            sno = struct.unpack_from("<H", raw, 6)[0]
            cmd_type = raw[9]
            payload = raw[10:-2] if has_checksum else raw[10:]
            packets.append(
                ParsedPacket(
                    sno=sno,
                    flags=flags,
                    cmd_type=cmd_type,
                    payload=payload,
                    raw=raw,
                )
            )

        # Prevent unbounded buffer growth if stream is corrupt
        if len(self._buf) > 4096:
            self._buf.clear()
        return packets
