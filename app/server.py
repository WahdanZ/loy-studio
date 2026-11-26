#!/usr/bin/env python3
"""
LOY Studio — Local Offline BLE Gateway & Web UI Server.

Binds strictly to 127.0.0.1 (localhost only). Zero cloud calls, zero analytics,
zero external assets. Uses the clean-room `protocol/` library directly.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import datetime
import hashlib
import io
import json
import math
import secrets
import socket
import subprocess
import sys
import time
import urllib.parse
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Set

from aiohttp import ClientSession, ClientTimeout, WSMsgType, web
from PIL import Image, ImageDraw, ImageSequence

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from protocol import (
    BLE_PROFILES,
    BLE_NOTIFY_CHAR_UUID,
    BLE_SERVICE_UUID,
    BLE_WRITE_CHAR_UUID,
    BleTransport,
    MockTransport,
    build_gif_program_frames,
    build_image_program_frames,
    build_multi_region_hw_clock_frames,
    build_text_clock_program_frames,
    build_text_program_frames,
    cmd_delete_all,
    cmd_delete_programs,
    cmd_dispatch_program,
    cmd_game_action,
    cmd_game_launch,
    cmd_get_dev_info_all,
    cmd_pgm_play,
    cmd_rt_draw_pixels,
    cmd_rt_draw_rect,
    cmd_set_ambient_light,
    cmd_set_ble_password,
    cmd_set_brightness,
    cmd_set_brightness_schedule,
    cmd_set_clock_mode,
    cmd_set_countdown,
    cmd_set_global_ani,
    cmd_set_mirror,
    cmd_set_music_spectrum,
    cmd_set_power,
    cmd_set_power_schedule,
    cmd_set_rotation,
    cmd_set_scoreboard,
    cmd_set_stopwatch,
    cmd_sync_time,
    encode_period_pro_tlv,
    encode_ystp01,
    pil_image_to_ystp01,
    recommend_profile,
)
from app.creative import (
    PROCEDURAL_EFFECTS,
    convert_video_to_gif,
    extract_palette,
    frames_to_gif_bytes,
    generate_avatar,
    generate_procedural,
    get_local_system_stats,
    get_macos_now_playing,
    process_image_studio,
)
from app.smart_features import (
    detect_mac_context,
    fetch_open_meteo_weather,
    get_macos_calendar_auth_status,
    get_upcoming_calendar_event,
    list_macos_calendars,
    render_calendar_clock_bg_gif,
    render_hydration_gif,
    render_notification_gif,
    render_weather_clock_bg_gif,
)

BUNDLE_ROOT = Path(getattr(sys, "_MEIPASS", PROJECT_ROOT))
STATIC_DIR = BUNDLE_ROOT / "app" / "static"
GIFS_DIR = BUNDLE_ROOT / "gifs"


def _resolve_data_dir() -> Path:
    """Use a portable data folder next to the executable when bundled, or `app/` in dev."""
    if getattr(sys, "frozen", False):
        exe_path = Path(sys.executable).resolve()
        # On macOS .app bundles, store portable data next to the .app folder if writable
        base = exe_path.parents[3] if ".app" in exe_path.parts else exe_path.parent
        candidate = base / "LOY-Studio-Data"
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            test_file = candidate / ".write_test"
            test_file.write_text("ok", encoding="utf-8")
            test_file.unlink(missing_ok=True)
            return candidate
        except OSError:
            fallback = Path.home() / ".loy-studio"
            fallback.mkdir(parents=True, exist_ok=True)
            return fallback
    return Path(__file__).resolve().parent


DATA_DIR = _resolve_data_dir()
LIBRARY_FILE = DATA_DIR / "local_library.json"
TOKEN_FILE = DATA_DIR / ".pairing_token"
LAST_DEVICE_FILE = DATA_DIR / ".last_device.json"
SCHEDULE_FILE = DATA_DIR / "work_schedule.json"
SMART_CONFIG_FILE = DATA_DIR / "smart_config.json"
LAN_AUTH_FILE = DATA_DIR / ".lan_auth.json"


def _hash_password(password: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{password}".encode("utf-8")).hexdigest()


def _load_lan_auth() -> Dict[str, Any]:
    if LAN_AUTH_FILE.exists():
        try:
            data = json.loads(LAN_AUTH_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "salt" in data and "hash" in data:
                return data
        except Exception:
            pass
    salt = secrets.token_hex(12)
    data = {
        "enabled": True,
        "salt": salt,
        "hash": _hash_password("1234", salt),
        "session_secret": secrets.token_urlsafe(24),
        "is_default": True,
    }
    _save_lan_auth(data)
    return data


def _save_lan_auth(data: Dict[str, Any]) -> None:
    LAN_AUTH_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        LAN_AUTH_FILE.chmod(0o600)
    except OSError:
        pass

DEFAULT_SMART_CONFIG: Dict[str, Any] = {
    "context_enabled": False,
    "auto_on_air": True,
    "auto_coding_app": True,
    "auto_idle_brb": True,
    "idle_minutes": 5,
    "calendar_alerts": False,
    "calendar_clock_enabled": False,
    "calendar_name": "",
    "calendar_ics_url": "",
    "hydration_enabled": False,
    "hydration_interval_min": 60,
    "weather_enabled": False,
    "weather_city": "Berlin",
    "auto_cycle_enabled": False,
}

DAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

DEFAULT_WORK_SCHEDULE: Dict[str, Any] = {
    "enabled": True,
    "rules": [
        {"id": "wk_coffee", "enabled": True, "name": "Morning Coffee", "days": ["mon", "tue", "wed", "thu", "fri"], "start": "08:00", "end": "09:00", "kind": "gif", "target": "13_mode_coffee_break.gif", "brightness": 10},
        {"id": "wk_focus", "enabled": True, "name": "Deep Focus", "days": ["mon", "tue", "wed", "thu", "fri"], "start": "09:00", "end": "12:00", "kind": "gif", "target": "09_mode_deep_focus.gif", "brightness": 12},
        {"id": "wk_lunch", "enabled": True, "name": "Lunch Break", "days": ["mon", "tue", "wed", "thu", "fri"], "start": "12:00", "end": "13:00", "kind": "gif", "target": "13_mode_coffee_break.gif", "brightness": 11},
        {"id": "wk_code", "enabled": True, "name": "Coding Sprint", "days": ["mon", "tue", "wed", "thu", "fri"], "start": "13:00", "end": "17:00", "kind": "gif", "target": "10_mode_coding.gif", "brightness": 12},
        {"id": "wk_vibe", "enabled": True, "name": "Afternoon Lo-Fi", "days": ["mon", "tue", "wed", "thu", "fri"], "start": "17:00", "end": "19:00", "kind": "gif", "target": "16_mode_music_vibe.gif", "brightness": 9},
        {"id": "wk_clock", "enabled": True, "name": "Evening Clock", "days": ["mon", "tue", "wed", "thu", "fri"], "start": "19:00", "end": "22:30", "kind": "hw_clock", "target": "hw_neon_ring", "brightness": 7},
        {"id": "we_coffee", "enabled": True, "name": "Weekend Coffee", "days": ["sat", "sun"], "start": "09:00", "end": "13:00", "kind": "gif", "target": "13_mode_coffee_break.gif", "brightness": 10},
        {"id": "we_play", "enabled": True, "name": "Weekend Gaming", "days": ["sat", "sun"], "start": "13:00", "end": "22:30", "kind": "gif", "target": "15_mode_gaming_chill.gif", "brightness": 11},
        {"id": "night_done", "enabled": True, "name": "Night / Done", "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"], "start": "22:30", "end": "08:00", "kind": "gif", "target": "18_mode_done_offline.gif", "brightness": 2},
    ],
}


def _load_or_create_token() -> str:
    if TOKEN_FILE.exists():
        tok = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if len(tok) >= 16:
            return tok
    tok = secrets.token_urlsafe(18)
    TOKEN_FILE.write_text(tok, encoding="utf-8")
    try:
        TOKEN_FILE.chmod(0o600)
    except OSError:
        pass
    return tok


def _detect_lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


class ControllerState:
    def __init__(self) -> None:
        self.transport: Optional[Any] = None
        self.mode: str = "disconnected"  # "ble" | "mock" | "disconnected"
        self.address: Optional[str] = None
        self.device_name: Optional[str] = None
        self.width: int = 64
        self.height: int = 64
        self.power_on: bool = True
        self.brightness: int = 12
        self.rotation: int = 0
        self.mirror: bool = False
        self.device_info: Dict[str, Any] = {}
        self.packet_log: Deque[Dict[str, Any]] = deque(maxlen=200)
        self.recent_sent: Deque[Dict[str, Any]] = deque(maxlen=12)
        self.now_playing: Dict[str, Any] = {
            "kind": "idle",
            "title": "Ready",
            "subtitle": "Pick a mode, clock, or artwork",
            "active_pin": "",
            "preview_url": "",
            "source": "",
            "ts": 0.0,
        }
        self.lock = asyncio.Lock()
        self.ws_clients: Dict[web.WebSocketResponse, Dict[str, Any]] = {}
        self.pairing_token: str = _load_or_create_token()
        self.lan_enabled: bool = False
        self.bound_port: int = 18420
        self.auto_reconnect: bool = False
        self.in_modal_mode: bool = False  # True when panel is in built-in Clock/Timer/Scoreboard/Game mode
        self.last_ble_address: Optional[str] = None
        self.last_ble_name: Optional[str] = None
        self._load_last_device()

    def _load_last_device(self) -> None:
        if LAST_DEVICE_FILE.exists():
            try:
                d = json.loads(LAST_DEVICE_FILE.read_text(encoding="utf-8"))
                self.last_ble_address = d.get("address")
                self.last_ble_name = d.get("name")
            except Exception:
                pass

    def save_last_device(self, address: str, name: str) -> None:
        self.last_ble_address = address
        self.last_ble_name = name
        try:
            LAST_DEVICE_FILE.write_text(json.dumps({"address": address, "name": name}), encoding="utf-8")
        except Exception:
            pass

    def rotate_token(self) -> str:
        self.pairing_token = secrets.token_urlsafe(18)
        TOKEN_FILE.write_text(self.pairing_token, encoding="utf-8")
        return self.pairing_token

    async def broadcast(self, event: str, data: Any) -> None:
        if not self.ws_clients:
            return
        payload = json.dumps({"event": event, "data": data}, default=lambda o: getattr(o, "__dict__", str(o)))
        dead: List[web.WebSocketResponse] = []
        for ws in list(self.ws_clients.keys()):
            if ws.closed:
                dead.append(ws)
                continue
            try:
                await ws.send_str(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.ws_clients.pop(ws, None)

    def broadcast_soon(self, event: str, data: Any) -> None:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.broadcast(event, data))
        except RuntimeError:
            pass

    def clients_list(self) -> List[Dict[str, Any]]:
        return list(self.ws_clients.values())

    def attach_logger(self, transport: Any) -> None:
        def _log(direction: str, raw: bytes, decoded: Optional[Dict[str, Any]]) -> None:
            entry = {
                "ts": round(time.time(), 3),
                "dir": direction,
                "len": len(raw),
                "hex": raw.hex(" "),
                "decoded": json.loads(json.dumps(decoded, default=lambda o: o.__dict__)) if decoded else None,
            }
            self.packet_log.appendleft(entry)
        transport.set_packet_logger(_log)
        transport.set_progress_callback(
            lambda prog: self.broadcast_soon("transfer_update", prog.to_dict())
        )

    def set_now_playing(
        self,
        kind: str,
        title: str,
        *,
        subtitle: str = "",
        active_pin: str = "",
        preview_url: str = "",
        source: str = "App",
        record: bool = True,
    ) -> None:
        self.now_playing = {
            "kind": kind,
            "title": title,
            "subtitle": subtitle,
            "active_pin": active_pin,
            "preview_url": preview_url,
            "source": source,
            "ts": round(time.time(), 1),
        }
        if record:
            tstr = datetime.datetime.now().strftime("%H:%M:%S")
            self.recent_sent.appendleft({"time": tstr, "title": title, "source": source})
        self.broadcast_soon("status_update", self.status_dict())

    def status_dict(self) -> Dict[str, Any]:
        cfg = _load_smart_config() if SMART_CONFIG_FILE.exists() else DEFAULT_SMART_CONFIG
        return {
            "connected": bool(self.transport and self.transport.is_connected),
            "mode": self.mode,
            "address": self.address,
            "device_name": self.device_name,
            "width": self.width,
            "height": self.height,
            "power_on": self.power_on,
            "brightness": self.brightness,
            "rotation": self.rotation,
            "mirror": self.mirror,
            "device_info": self.device_info,
            "now_playing": self.now_playing,
            "recent_sent": list(self.recent_sent),
            "auto_cycle_enabled": bool(cfg.get("auto_cycle_enabled", False)),
            "schedule_summary": _build_schedule_summary(),
        }


def _detect_req_source(req: Optional[web.Request]) -> str:
    if req is None:
        return "Auto"
    hdr = (req.headers.get("X-LOY-Source") or "").strip()
    if hdr:
        return hdr[:20]
    ua = (req.headers.get("User-Agent") or "").lower()
    if "iphone" in ua or "ipad" in ua:
        return "iPhone"
    if "android" in ua:
        return "Android"
    if "python-urllib" in ua:
        return "Menu Bar"
    if "node" in ua or "undici" in ua:
        return "Google Home"
    if _is_loopback(req.remote):
        return "Mac"
    return "Wi-Fi"


state = ControllerState()


def _parse_hex_rgb(hex_color: str) -> tuple[int, int, int]:
    c = hex_color.strip().lstrip("#")
    if len(c) != 6:
        raise ValueError(f"Invalid hex color: {hex_color}")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


async def handle_index(_req: web.Request) -> web.StreamResponse:
    """Serve LOY Studio shell cleanly (no URL pairing tokens)."""
    return web.FileResponse(STATIC_DIR / "index.html")


def _is_loopback_req(req: web.Request) -> bool:
    peer = req.remote or "127.0.0.1"
    return peer in ("127.0.0.1", "::1", "localhost") or peer.startswith("127.")


def _is_lan_authenticated(req: web.Request) -> bool:
    if _is_loopback_req(req):
        return True
    auth = _load_lan_auth()
    if not auth.get("enabled", True):
        return True
    expected = str(auth.get("session_secret", ""))
    if not expected:
        return False
    token = (
        req.headers.get("X-LOY-Token")
        or req.query.get("token")
        or req.cookies.get("loy_session")
        or ""
    )
    return bool(token) and secrets.compare_digest(token, expected)


@web.middleware
async def local_auth_middleware(req: web.Request, handler):
    """
    Protect API & WebSocket endpoints when LAN access is enabled:
      - Loopback clients (127.0.0.1 / ::1) on your Mac are always trusted.
      - Non-loopback LAN clients authenticate with your chosen password via /api/auth/login
        and present the returned session token via X-LOY-Token header, ?token= (WebSocket), or cookie.
    """
    public_paths = ("/", "/api/auth/status", "/api/auth/login", "/api/auth/logout")
    if (
        _is_loopback_req(req)
        or req.path in public_paths
        or req.path.startswith("/static/")
        or req.path.startswith("/gifs/")
        or req.path.startswith("/api/hw_clock_preview/")
    ):
        return await handler(req)

    if not _is_lan_authenticated(req):
        return web.json_response(
            {"error": "Password required to access LOY Studio over Wi-Fi.", "auth_required": True},
            status=401,
        )
    return await handler(req)


async def handle_auth_status(req: web.Request) -> web.Response:
    auth = _load_lan_auth()
    return web.json_response({
        "authenticated": _is_lan_authenticated(req),
        "is_local": _is_loopback_req(req),
        "password_enabled": bool(auth.get("enabled", True)),
        "is_default_password": bool(auth.get("is_default", False)),
    })


async def handle_auth_login(req: web.Request) -> web.Response:
    body = await req.json()
    password = str(body.get("password", ""))
    auth = _load_lan_auth()
    if not auth.get("enabled", True):
        return web.json_response({"ok": True, "token": auth.get("session_secret", "")})
    if not secrets.compare_digest(_hash_password(password, auth["salt"]), auth["hash"]):
        return web.json_response({"error": "Incorrect password"}, status=401)
    token = auth["session_secret"]
    resp = web.json_response({"ok": True, "token": token})
    resp.set_cookie("loy_session", token, httponly=True, samesite="Lax", max_age=86400 * 30)
    return resp


async def handle_auth_set_password(req: web.Request) -> web.Response:
    body = await req.json()
    new_pwd = str(body.get("password", "")).strip()
    auth = _load_lan_auth()
    if not new_pwd:
        auth["enabled"] = False
        auth["is_default"] = False
    else:
        salt = secrets.token_hex(12)
        auth["enabled"] = True
        auth["salt"] = salt
        auth["hash"] = _hash_password(new_pwd, salt)
        auth["session_secret"] = secrets.token_urlsafe(24)
        auth["is_default"] = False
    _save_lan_auth(auth)
    return web.json_response({
        "ok": True,
        "password_enabled": auth["enabled"],
        "token": auth["session_secret"],
    })


async def handle_auth_logout(_req: web.Request) -> web.Response:
    resp = web.json_response({"ok": True})
    resp.del_cookie("loy_session")
    return resp


async def handle_ws(req: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=25.0)
    await ws.prepare(req)
    ua = (req.headers.get("User-Agent") or "Unknown")[:80]
    kind = "Mobile" if any(k in ua for k in ("iPhone", "Android", "iPad", "Mobile")) else "Desktop"
    client_info = {
        "ip": req.remote or "127.0.0.1",
        "kind": kind,
        "ua": ua,
        "connected_at": datetime.datetime.now().strftime("%H:%M:%S"),
    }
    state.ws_clients[ws] = client_info
    await ws.send_str(json.dumps({"event": "status_update", "data": state.status_dict()}))
    await state.broadcast("clients_update", state.clients_list())
    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT and msg.data == "ping":
                await ws.send_str(json.dumps({"event": "pong"}))
    finally:
        state.ws_clients.pop(ws, None)
        await state.broadcast("clients_update", state.clients_list())
    return ws


async def handle_phone_connect(_req: web.Request) -> web.Response:
    """Return clean Mac LAN URL (no token), QR PNG (base64), password status, and connected clients."""
    import qrcode

    lan_ip = _detect_lan_ip()
    port = state.bound_port
    url = f"http://{lan_ip}:{port}"
    qr = qrcode.QRCode(border=2, box_size=6)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    qr_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    auth = _load_lan_auth()
    return web.json_response({
        "lan_enabled": state.lan_enabled,
        "lan_ip": lan_ip,
        "port": port,
        "url": url,
        "base_url": url,
        "password_enabled": bool(auth.get("enabled", True)),
        "is_default_password": bool(auth.get("is_default", False)),
        "qr_png_b64": qr_b64,
        "clients": state.clients_list(),
    })


async def handle_rotate_token(_req: web.Request) -> web.Response:
    state.rotate_token()
    return await handle_phone_connect(_req)


async def handle_status(_req: web.Request) -> web.Response:
    return web.json_response(state.status_dict())


async def handle_packets(_req: web.Request) -> web.Response:
    return web.json_response({"packets": list(state.packet_log)})


async def handle_scan(req: web.Request) -> web.Response:
    include_all = req.query.get("all", "0") == "1"
    timeout = float(req.query.get("timeout", "4.0"))
    try:
        devices = await BleTransport.scan(timeout=timeout, include_all=include_all)
        return web.json_response(
            {
                "devices": [
                    {"name": d.name, "address": d.address, "rssi": d.rssi, "uuids": d.service_uuids}
                    for d in devices
                ]
            }
        )
    except Exception as exc:
        return web.json_response({"error": str(exc), "devices": []}, status=500)


async def handle_connect(req: web.Request) -> web.Response:
    body = await req.json()
    mock = bool(body.get("mock", False))
    address = body.get("address", "")
    name = body.get("name", "Mock Display" if mock else address)
    width = int(body.get("width", 64))
    height = int(body.get("height", 64))

    async with state.lock:
        if state.transport and state.transport.is_connected:
            await state.transport.disconnect()

        transport = MockTransport(width=width, height=height) if mock else BleTransport()
        state.attach_logger(transport)
        try:
            await transport.connect(address or "MOCK-DISPLAY")
            state.transport = transport
            state.mode = "mock" if mock else "ble"
            state.address = address or "MOCK-DISPLAY"
            state.device_name = name

            # Query initial device info & capabilities (retry once if panel is still waking up)
            resp = None
            for _attempt in range(2):
                try:
                    resp = await transport.send_frame(cmd_get_dev_info_all(sno=transport.next_sno()), timeout=4.0)
                    break
                except TimeoutError:
                    await asyncio.sleep(0.6)
            if resp and resp.get("data"):
                data = resp["data"]
                if "dev_info" in data:
                    state.device_info = data["dev_info"].__dict__
                if "param_dev" in data and data["param_dev"]:
                    state.width = data["param_dev"].width
                    state.height = data["param_dev"].height
                if "power" in data:
                    state.power_on = bool(data["power"].get("on", True))
                if "light" in data:
                    state.brightness = int(data["light"].get("level", 12))
            if not mock:
                rec = recommend_profile(name, state.device_info.get("version", ""))
                transport.set_profile(rec)
                state.auto_reconnect = True
                state.save_last_device(address, name)
            else:
                state.auto_reconnect = False
            state.broadcast_soon("status_update", state.status_dict())
            return web.json_response(state.status_dict())
        except Exception as exc:
            state.transport = None
            state.mode = "disconnected"
            return web.json_response({"error": str(exc)}, status=500)


async def handle_disconnect(_req: web.Request) -> web.Response:
    async with state.lock:
        state.auto_reconnect = False
        if state.transport:
            await state.transport.disconnect()
        state.transport = None
        state.mode = "disconnected"
        state.address = None
        state.device_name = None
        state.device_info = {}
        state.broadcast_soon("status_update", state.status_dict())
        return web.json_response(state.status_dict())


async def _ensure_exit_modal_mode(clear_temp_alert: bool = True) -> None:
    """Exit built-in Clock / Countdown / Stopwatch / Scoreboard / Game mode and any active temporary alert (Slot 2) before uploading."""
    if clear_temp_alert:
        prev_task = getattr(state, "_notify_restore_task", None)
        if prev_task and not prev_task.done():
            prev_task.cancel()
            state._notify_restore_task = None
        if getattr(state, "_temp_alert_active", False) and state.transport and state.transport.is_connected:
            try:
                t = state.transport
                await t.send_frame(cmd_delete_programs([2], sno=t.next_sno()))
                await t.send_frame(cmd_dispatch_program(1, sno=t.next_sno()))
                await t.send_frame(cmd_pgm_play(model=1, index=0, sno=t.next_sno()))
            except Exception:
                pass
            state._temp_alert_active = False
    if state.in_modal_mode and state.transport and state.transport.is_connected:
        try:
            await state.transport.send_frame(cmd_game_action("exit", sno=state.transport.next_sno()))
        except Exception:
            pass
        state.in_modal_mode = False


async def handle_control(req: web.Request) -> web.Response:
    if not state.transport or not state.transport.is_connected:
        return web.json_response({"error": "Not connected"}, status=400)
    body = await req.json()
    action = body.get("action")
    async with state.lock:
        t = state.transport
        try:
            if action == "power":
                on = bool(body["on"])
                res = await t.send_frame(cmd_set_power(on, sno=t.next_sno()))
                state.power_on = on
            elif action == "power_schedule":
                periods = body.get("periods") or ["08:00-23:00"]
                await t.send_frame(cmd_sync_time(sno=t.next_sno()))
                res = await t.send_frame(cmd_set_power_schedule(periods, sno=t.next_sno()))
            elif action == "brightness":
                lvl = max(1, min(15, int(body["level"])))
                res = await t.send_frame(cmd_set_brightness(lvl, sno=t.next_sno()))
                state.brightness = lvl
            elif action == "brightness_schedule":
                entries = [(str(e["time"]), int(e["level"])) for e in body["entries"]]
                await t.send_frame(cmd_sync_time(sno=t.next_sno()))
                res = await t.send_frame(cmd_set_brightness_schedule(entries, sno=t.next_sno()))
            elif action == "rotate":
                deg = int(body["degrees"])
                res = await t.send_frame(cmd_set_rotation(deg, sno=t.next_sno()))
                state.rotation = deg
            elif action == "mirror":
                en = bool(body["enabled"])
                res = await t.send_frame(cmd_set_mirror(en, sno=t.next_sno()))
                state.mirror = en
            elif action == "sync_time":
                res = await t.send_frame(cmd_sync_time(sno=t.next_sno()))
            elif action == "clock":
                if body.get("sync_first", True):
                    await t.send_frame(cmd_sync_time(sno=t.next_sno()))
                fmt = int(body.get("style", 0))
                clock_style = body.get("clock_style")
                date_style = body.get("date_style")
                color_hex = body.get("color")
                if clock_style is not None and date_style is not None and color_hex:
                    res = await t.send_frame(
                        cmd_set_clock_mode(
                            fmt=fmt,
                            clock_style=int(clock_style),
                            date_style=int(date_style),
                            color=_parse_hex_rgb(color_hex),
                            sno=t.next_sno(),
                        )
                    )
                else:
                    res = await t.send_frame(cmd_set_clock_mode(fmt=fmt, sno=t.next_sno()))
                state.in_modal_mode = True
            elif action == "countdown":
                res = await t.send_frame(
                    cmd_set_countdown(
                        int(body.get("hours", 0)),
                        int(body.get("minutes", 5)),
                        int(body.get("seconds", 0)),
                        sno=t.next_sno(),
                    )
                )
                state.in_modal_mode = True
            elif action == "scoreboard":
                res = await t.send_frame(
                    cmd_set_scoreboard(int(body.get("red", 0)), int(body.get("blue", 0)), sno=t.next_sno())
                )
                state.in_modal_mode = True
            elif action == "delete_all":
                res = await t.send_frame(cmd_delete_all(sno=t.next_sno()))
            elif action == "delete_slots":
                ids = [int(x) for x in body["slots"]]
                res = await t.send_frame(cmd_delete_programs(ids, sno=t.next_sno()))
            elif action == "dispatch_slot":
                res = await t.send_frame(cmd_dispatch_program(int(body["slot"]), sno=t.next_sno()))
            elif action == "pgm_play":
                ids = [int(x) for x in body.get("slots", [])] if body.get("slots") else None
                res = await t.send_frame(
                    cmd_pgm_play(
                        model=int(body.get("model", 0)),
                        index=int(body.get("index", 0)),
                        ids_pro=ids,
                        sno=t.next_sno(),
                    )
                )
            elif action == "stopwatch":
                res = await t.send_frame(cmd_set_stopwatch(fmt=int(body.get("fmt", 0)), sno=t.next_sno()))
            elif action == "game_launch":
                res = await t.send_frame(cmd_game_launch(int(body["game_id"]), sno=t.next_sno()))
                state.in_modal_mode = True
            elif action == "game_opt":
                res = await t.send_frame(cmd_game_action(body["opt"], sno=t.next_sno(), unack=False))
                if body["opt"] == "exit":
                    state.in_modal_mode = False
            elif action == "music_spectrum":
                use_mic = bool(body.get("use_device_mic", True))
                bars = body.get("bars")
                res = await t.send_frame(
                    cmd_set_music_spectrum(
                        style=int(body.get("style", 0)),
                        bars=bars,
                        use_device_mic=use_mic,
                        sno=t.next_sno(),
                        unack=not use_mic,
                    ),
                    wait_ack=use_mic,
                )
            elif action == "ambient_light":
                sl = int(body.get("style_light", 0))
                sv = int(body.get("style_val", 0))
                col = _parse_hex_rgb(body.get("color", "#05d9e8"))
                cols = [_parse_hex_rgb(c) for c in body["colors"]] if body.get("colors") else None
                res = await t.send_frame(
                    cmd_set_ambient_light(
                        style_light=sl,
                        style_val=sv,
                        color=col,
                        colors=cols,
                        speed=int(body.get("speed", 128)),
                        sno=t.next_sno(),
                    )
                )
            elif action == "global_ani":
                res = await t.send_frame(
                    cmd_set_global_ani(
                        val=int(body.get("val", 0)),
                        speed=int(body.get("speed", 9)),
                        time_stay=int(body.get("time_stay", 3)),
                        disable=bool(body.get("disable", False)),
                        sno=t.next_sno(),
                    )
                )
            elif action == "ble_password":
                res = await t.send_frame(
                    cmd_set_ble_password(
                        pwd_type=int(body.get("pwd_type", 2)),
                        password=str(body.get("password", "")),
                        sno=t.next_sno(),
                    )
                )
            elif action == "rt_pixel":
                pts = [(int(p[0]), int(p[1])) for p in body["pixels"]]
                rgb = _parse_hex_rgb(body["color"])
                res = await t.send_frame(
                    cmd_rt_draw_pixels(pts, rgb, sno=t.next_sno(), unack=True), wait_ack=False
                )
            elif action == "rt_rect":
                rgb = _parse_hex_rgb(body["color"])
                res = await t.send_frame(
                    cmd_rt_draw_rect(
                        int(body["x0"]),
                        int(body["y0"]),
                        int(body["x1"]),
                        int(body["y1"]),
                        rgb,
                        sno=t.next_sno(),
                    )
                )
            else:
                return web.json_response({"error": f"Unknown action: {action}"}, status=400)
            state.broadcast_soon("status_update", state.status_dict())
            return web.json_response({"ok": True, "result": res, "status": state.status_dict()})
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=500)


async def handle_upload_frames(req: web.Request) -> web.Response:
    """Upload one or more RGB frames from the Pixel Art / Animation Editor."""
    if not state.transport or not state.transport.is_connected:
        return web.json_response({"error": "Not connected"}, status=400)
    body = await req.json()
    width = int(body.get("width", state.width))
    height = int(body.get("height", state.height))
    slot = int(body.get("slot", 1))
    speed = int(body.get("speed", 9))
    frames_b64: List[str] = body["frames"]

    frames_rgb = [base64.b64decode(b) for b in frames_b64]
    ystp = encode_ystp01(frames_rgb, width, height)
    wire_frames = build_image_program_frames(
        ystp,
        width,
        height,
        program_id=slot,
        start_sno=state.transport.next_sno(),
        speed=speed,
        time_stay=0 if len(frames_rgb) > 1 else 3,
    )
    async with state.lock:
        try:
            await _ensure_exit_modal_mode()
            res = await state.transport.send_frames(wire_frames)
            return web.json_response(
                {"ok": True, "ystp_bytes": len(ystp), "wire_frames": len(wire_frames), "last_ack": res[-1]}
            )
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=500)


async def handle_upload_media(req: web.Request) -> web.Response:
    """Import PNG/JPG/BMP/GIF file, auto-resize or passthrough GIF, and send to display."""
    if not state.transport or not state.transport.is_connected:
        return web.json_response({"error": "Not connected"}, status=400)
    body = await req.json()
    raw = base64.b64decode(body["data_b64"])
    slot = int(body.get("slot", 1))
    passthrough_gif = bool(body.get("passthrough_gif", True))

    async with state.lock:
        try:
            if passthrough_gif and raw.startswith(b"GIF"):
                wire_frames = build_gif_program_frames(
                    raw,
                    state.width,
                    state.height,
                    program_id=slot,
                    start_sno=state.transport.next_sno(),
                )
            else:
                with Image.open(io.BytesIO(raw)) as img:
                    ystp = pil_image_to_ystp01(img, state.width, state.height)
                wire_frames = build_image_program_frames(
                    ystp,
                    state.width,
                    state.height,
                    program_id=slot,
                    start_sno=state.transport.next_sno(),
                )
            res = await state.transport.send_frames(wire_frames)
            return web.json_response({"ok": True, "wire_frames": len(wire_frames), "last_ack": res[-1]})
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=500)


async def handle_send_text(req: web.Request) -> web.Response:
    if not state.transport or not state.transport.is_connected:
        return web.json_response({"error": "Not connected"}, status=400)
    body = await req.json()
    msg = body.get("text", "").strip()
    if not msg:
        return web.json_response({"error": "Text cannot be empty"}, status=400)
    fg = _parse_hex_rgb(body.get("color", "#ffffff"))
    bg = _parse_hex_rgb(body.get("bg", "#000000"))
    scroll = body.get("scroll", "left")
    speed = int(body.get("speed", 9))
    size = int(body.get("size", 16))
    slot = int(body.get("slot", 1))

    async with state.lock:
        try:
            wire_frames = build_text_program_frames(
                msg,
                state.width,
                state.height,
                color=fg,
                bg_color=bg,
                font_size=size,
                scroll=scroll,
                speed=speed,
                program_id=slot,
                start_sno=state.transport.next_sno(),
            )
            res = await state.transport.send_frames(wire_frames)
            return web.json_response({"ok": True, "wire_frames": len(wire_frames), "last_ack": res[-1]})
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=500)


async def handle_library_get(_req: web.Request) -> web.Response:
    if LIBRARY_FILE.exists():
        data = json.loads(LIBRARY_FILE.read_text(encoding="utf-8"))
    else:
        data = []
    return web.json_response({"items": data})


async def handle_library_save(req: web.Request) -> web.Response:
    body = await req.json()
    items = json.loads(LIBRARY_FILE.read_text(encoding="utf-8")) if LIBRARY_FILE.exists() else []
    item = {
        "id": body.get("id") or f"art_{int(time.time() * 1000)}",
        "name": body.get("name", "Untitled"),
        "width": int(body["width"]),
        "height": int(body["height"]),
        "favorite": bool(body.get("favorite", False)),
        "frames": body["frames"],
    }
    items = [x for x in items if x["id"] != item["id"]]
    items.insert(0, item)
    LIBRARY_FILE.write_text(json.dumps(items, indent=2), encoding="utf-8")
    state.broadcast_soon("library_update", {"count": len(items)})
    return web.json_response({"ok": True, "item": item})


async def handle_library_delete(req: web.Request) -> web.Response:
    item_id = req.match_info["id"]
    items = json.loads(LIBRARY_FILE.read_text(encoding="utf-8")) if LIBRARY_FILE.exists() else []
    items = [x for x in items if x["id"] != item_id]
    LIBRARY_FILE.write_text(json.dumps(items, indent=2), encoding="utf-8")
    return web.json_response({"ok": True})


# Crisp 3x5 pixel bitmap font (digits, punctuation, and A-Z for weekdays/AM/PM)
_FONT_3X5: Dict[str, List[str]] = {
    "0": ["111", "101", "101", "101", "111"],
    "1": ["010", "110", "010", "010", "111"],
    "2": ["111", "001", "111", "100", "111"],
    "3": ["111", "001", "111", "001", "111"],
    "4": ["101", "101", "111", "001", "001"],
    "5": ["111", "100", "111", "001", "111"],
    "6": ["111", "100", "111", "101", "111"],
    "7": ["111", "001", "001", "010", "010"],
    "8": ["111", "101", "111", "101", "111"],
    "9": ["111", "101", "111", "001", "111"],
    ":": ["000", "010", "000", "010", "000"],
    "-": ["000", "000", "111", "000", "000"],
    "/": ["001", "001", "010", "100", "100"],
    " ": ["000", "000", "000", "000", "000"],
    "A": ["010", "101", "111", "101", "101"],
    "B": ["110", "101", "110", "101", "110"],
    "C": ["011", "100", "100", "100", "011"],
    "D": ["110", "101", "101", "101", "110"],
    "E": ["111", "100", "110", "100", "111"],
    "F": ["111", "100", "110", "100", "100"],
    "G": ["011", "100", "101", "101", "011"],
    "H": ["101", "101", "111", "101", "101"],
    "I": ["111", "010", "010", "010", "111"],
    "M": ["101", "111", "101", "101", "101"],
    "N": ["111", "101", "101", "101", "101"],
    "O": ["111", "101", "101", "101", "111"],
    "P": ["110", "101", "110", "100", "100"],
    "R": ["110", "101", "110", "101", "101"],
    "S": ["011", "100", "010", "001", "110"],
    "T": ["111", "010", "010", "010", "010"],
    "U": ["101", "101", "101", "101", "111"],
    "W": ["101", "101", "101", "111", "101"],
}


def _draw_pixel_text(
    img: Image.Image, text: str, x: int, y: int, color: tuple[int, int, int], scale: int = 1
) -> None:
    px = img.load()
    w, h = img.size
    cx = x
    for ch in text.upper():
        glyph = _FONT_3X5.get(ch, _FONT_3X5[" "])
        for ry, row in enumerate(glyph):
            for rx, bit in enumerate(row):
                if bit == "1":
                    for sy in range(scale):
                        for sx in range(scale):
                            tx, ty = cx + rx * scale + sx, y + ry * scale + sy
                            if 0 <= tx < w and 0 <= ty < h:
                                px[tx, ty] = color
        cx += 4 * scale


def _draw_centered_text(
    img: Image.Image, text: str, y: int, color: tuple[int, int, int], scale: int = 1
) -> None:
    w = img.size[0]
    tw = (len(text) * 4 - 1) * scale
    _draw_pixel_text(img, text, max(0, (w - tw) // 2), y, color, scale=scale)


def _render_custom_clock_frames(
    width: int,
    height: int,
    *,
    design: str = "digital",
    fg: tuple[int, int, int] = (5, 217, 232),
    accent: tuple[int, int, int] = (255, 42, 109),
    bg: tuple[int, int, int] = (0, 0, 0),
    use_12h: bool = False,
    blink_colon: bool = True,
) -> List[bytes]:
    now = datetime.datetime.now()
    hour = now.hour % 12 or 12 if use_12h else now.hour
    ampm = "PM" if now.hour >= 12 else "AM"
    minute = now.minute
    second = now.second
    weekday = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"][now.weekday()]
    dim_fg = (max(15, fg[0] // 4), max(15, fg[1] // 4), max(15, fg[2] // 4))

    no_blink = design in ("analog", "analog_round", "analog_square", "binary_bcd")
    frame_states = [True, False] if (blink_colon and not no_blink) else [True]
    frames_out: List[bytes] = []

    for colon_on in frame_states:
        img = Image.new("RGB", (width, height), bg)
        draw = ImageDraw.Draw(img)
        sep = ":" if colon_on else " "

        if design in ("analog", "analog_round"):
            cx, cy = width // 2, height // 2
            radius = max(4, min(width, height) // 2 - 2)
            draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], outline=fg)
            for i in range(12):
                ang = math.radians(i * 30 - 90)
                r_in = radius - (3 if i % 3 == 0 else 2)
                x0, y0 = int(round(cx + r_in * math.cos(ang))), int(round(cy + r_in * math.sin(ang)))
                x1, y1 = int(round(cx + (radius - 1) * math.cos(ang))), int(round(cy + (radius - 1) * math.sin(ang)))
                draw.line([(x0, y0), (x1, y1)], fill=accent if i % 3 == 0 else fg)
            h_ang = math.radians(((hour % 12) + minute / 60.0) * 30 - 90)
            m_ang = math.radians(minute * 6 - 90)
            s_ang = math.radians(second * 6 - 90)
            draw.line([(cx, cy), (int(cx + radius * 0.48 * math.cos(h_ang)), int(cy + radius * 0.48 * math.sin(h_ang)))], fill=fg, width=2 if width >= 48 else 1)
            draw.line([(cx, cy), (int(cx + radius * 0.72 * math.cos(m_ang)), int(cy + radius * 0.72 * math.sin(m_ang)))], fill=(255, 255, 255))
            draw.line([(cx, cy), (int(cx + radius * 0.80 * math.cos(s_ang)), int(cy + radius * 0.80 * math.sin(s_ang)))], fill=accent)
            draw.rectangle([cx - 1, cy - 1, cx + 1, cy + 1], fill=(255, 255, 255))

        elif design == "analog_square":
            pad = 2
            draw.rounded_rectangle([pad, pad, width - 1 - pad, height - 1 - pad], radius=max(3, width // 8), outline=fg, width=1)
            cx, cy = width // 2, height // 2
            r = min(width, height) // 2 - 4
            for i in range(12):
                ang = math.radians(i * 30 - 90)
                tx = int(round(cx + r * math.cos(ang)))
                ty = int(round(cy + r * math.sin(ang)))
                if 0 <= tx < width and 0 <= ty < height:
                    img.putpixel((tx, ty), accent if i % 3 == 0 else fg)
            if width >= 48:
                _draw_pixel_text(img, f"{now.day:02d}", cx + r // 2 - 4, cy - 2, accent, scale=1)
            h_ang = math.radians(((hour % 12) + minute / 60.0) * 30 - 90)
            m_ang = math.radians(minute * 6 - 90)
            s_ang = math.radians(second * 6 - 90)
            draw.line([(cx, cy), (int(cx + r * 0.50 * math.cos(h_ang)), int(cy + r * 0.50 * math.sin(h_ang)))], fill=fg, width=2 if width >= 48 else 1)
            draw.line([(cx, cy), (int(cx + r * 0.75 * math.cos(m_ang)), int(cy + r * 0.75 * math.sin(m_ang)))], fill=(255, 255, 255))
            draw.line([(cx, cy), (int(cx + r * 0.82 * math.cos(s_ang)), int(cy + r * 0.82 * math.sin(s_ang)))], fill=accent)
            draw.rectangle([cx - 1, cy - 1, cx + 1, cy + 1], fill=(255, 255, 255))

        elif design == "stacked_big":
            # Giant 2-row HH over MM filling 64x64
            sc = 4 if (width >= 64 and height >= 64) else (2 if width >= 32 else 1)
            row_h = 5 * sc
            gap = max(2, (height - 2 * row_h) // 3)
            _draw_centered_text(img, f"{hour:02d}", gap, fg, scale=sc)
            _draw_centered_text(img, f"{minute:02d}", gap * 2 + row_h, accent, scale=sc)
            if colon_on and width >= 32:
                draw.rectangle([width - 4, height // 2 - 1, width - 2, height // 2 + 1], fill=(255, 255, 255))

        elif design == "neon_ring":
            # Circular glowing seconds ring with digital time inside
            cx, cy = width // 2, height // 2
            r = min(width, height) // 2 - 2
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=dim_fg, width=2 if width >= 48 else 1)
            end_deg = -90 + int((second / 60.0) * 360)
            draw.arc([cx - r, cy - r, cx + r, cy + r], start=-90, end=max(-85, end_deg), fill=accent, width=2 if width >= 48 else 1)
            sc = 2 if width >= 56 else 1
            _draw_centered_text(img, f"{hour:02d}{sep}{minute:02d}", cy - (5 * sc) // 2 - (3 if width >= 48 else 0), fg, scale=sc)
            if width >= 48:
                _draw_centered_text(img, f"{now.month:02d}/{now.day:02d}", cy + (5 * sc) // 2 + 2, accent, scale=1)

        elif design == "flip_card":
            # Retro split-flap flip clock cards for HH and MM
            pad = 2
            mid = width // 2
            card_top = max(4, height // 5)
            card_bot = height - card_top - (6 if height >= 48 else 2)
            for x0, x1 in [(pad, mid - 2), (mid + 2, width - 1 - pad)]:
                draw.rounded_rectangle([x0, card_top, x1, card_bot], radius=4, fill=(24, 30, 48), outline=dim_fg)
            sc = 3 if width >= 64 else (2 if width >= 32 else 1)
            th = 5 * sc
            ty = card_top + (card_bot - card_top - th) // 2
            tw = (2 * 4 - 1) * sc
            _draw_pixel_text(img, f"{hour:02d}", pad + ((mid - 2 - pad) - tw) // 2, ty, fg, scale=sc)
            _draw_pixel_text(img, f"{minute:02d}", mid + 2 + ((width - 1 - pad - (mid + 2)) - tw) // 2, ty, accent, scale=sc)
            # Split hinge line across both cards
            hy = (card_top + card_bot) // 2
            draw.line([(pad, hy), (width - 1 - pad, hy)], fill=bg, width=1)
            if height >= 48:
                _draw_centered_text(img, f"{weekday} {now.month:02d}/{now.day:02d}", card_bot + 3, dim_fg if not colon_on else fg, scale=1)

        elif design == "binary_bcd":
            # 6-column Binary Coded Decimal (H H M M S S)
            digits = [hour // 10, hour % 10, minute // 10, minute % 10, second // 10, second % 10]
            col_colors = [fg, fg, (255, 255, 255), (255, 255, 255), accent, accent]
            margin_x = max(3, width // 10)
            step_x = (width - 2 * margin_x) // 6
            dot_r = max(1, min(step_x, height // 7) // 2 - 1)
            top_y = max(4, height // 6)
            step_y = max(4, (height - top_y - 12) // 4)
            for c_idx, val in enumerate(digits):
                cx = margin_x + c_idx * step_x + step_x // 2
                for bit in range(4):
                    cy = top_y + (3 - bit) * step_y + dot_r
                    is_on = bool((val >> bit) & 1)
                    fill = col_colors[c_idx] if is_on else (22, 28, 44)
                    draw.ellipse([cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r], fill=fill)
            if height >= 32:
                _draw_centered_text(img, f"{hour:02d}:{minute:02d}:{second:02d}", height - 8, dim_fg, scale=1)

        elif design == "digital_sec":
            sc = 2 if width >= 64 else 1
            _draw_centered_text(img, f"{hour:02d}{sep}{minute:02d}", height // 2 - 5 * sc, fg, scale=sc)
            _draw_centered_text(img, f"{second:02d}S" if not use_12h else f"{second:02d} {ampm}", height // 2 + 4, accent, scale=max(1, sc - 1))

        elif design == "digital_date":
            sc = 2 if (width >= 48 and height >= 24) else 1
            th = 5 * sc
            _draw_centered_text(img, weekday, max(2, height // 2 - th - 9), accent, scale=1)
            _draw_centered_text(img, f"{hour:02d}{sep}{minute:02d}", max(1, height // 2 - th // 2), fg, scale=sc)
            _draw_centered_text(img, f"{now.month:02d}/{now.day:02d}", min(height - 7, height // 2 + th // 2 + 5), accent, scale=1)

        elif design == "neon_bar":
            # Corner cyber brackets + time + seconds progress bar
            b = max(4, width // 8)
            for pts in [
                [(1, b), (1, 1), (b, 1)],
                [(width - 1 - b, 1), (width - 2, 1), (width - 2, b)],
                [(1, height - 1 - b), (1, height - 2), (b, height - 2)],
                [(width - 1 - b, height - 2), (width - 2, height - 2), (width - 2, height - 1 - b)],
            ]:
                draw.line(pts, fill=accent)
            sc = 2 if width >= 48 else 1
            _draw_centered_text(img, f"{hour:02d}{sep}{minute:02d}", (height - 5 * sc) // 2 - 3, fg, scale=sc)
            bar_w = max(1, int((second / 59.0) * (width - 10)))
            draw.rectangle([5, height - 9, width - 6, height - 5], outline=dim_fg)
            draw.rectangle([6, height - 8, 5 + bar_w, height - 6], fill=accent)

        else:
            # Classic digital HH:MM
            sc = 3 if width >= 64 else (2 if width >= 48 else 1)
            _draw_centered_text(img, f"{hour:02d}{sep}{minute:02d}", (height - 5 * sc) // 2, fg, scale=sc)
            if use_12h and height >= 32:
                _draw_centered_text(img, ampm, height - 8, accent, scale=1)

        frames_out.append(img.tobytes())
    return frames_out


def _frames_rgb_to_gif_bytes(
    frames_rgb: List[bytes], width: int, height: int, delay_ms: int = 200
) -> bytes:
    pil_frames = [
        Image.frombytes("RGB", (width, height), raw).convert("P", palette=Image.Palette.ADAPTIVE)
        for raw in frames_rgb
    ]
    buf = io.BytesIO()
    pil_frames[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=pil_frames[1:],
        duration=max(20, int(delay_ms)),
        loop=0,
        disposal=2,
    )
    return buf.getvalue()


async def handle_export_gif(req: web.Request) -> web.Response:
    """Create a real animated GIF from editor frames, optionally uploading it to the display."""
    body = await req.json()
    width = int(body.get("width", state.width))
    height = int(body.get("height", state.height))
    delay_ms = int(body.get("delay_ms", 200))
    send_to_display = bool(body.get("send", False))
    slot = int(body.get("slot", 1))
    frames_rgb = [base64.b64decode(b) for b in body["frames"]]
    if not frames_rgb:
        return web.json_response({"error": "No frames provided"}, status=400)

    gif_bytes = _frames_rgb_to_gif_bytes(frames_rgb, width, height, delay_ms=delay_ms)
    resp_payload: Dict[str, Any] = {
        "ok": True,
        "gif_bytes": len(gif_bytes),
        "gif_b64": base64.b64encode(gif_bytes).decode("ascii"),
    }

    if send_to_display:
        if not state.transport or not state.transport.is_connected:
            return web.json_response({"error": "Not connected to display"}, status=400)
        async with state.lock:
            wire_frames = build_gif_program_frames(
                gif_bytes,
                width,
                height,
                program_id=slot,
                start_sno=state.transport.next_sno(),
            )
            res = await state.transport.send_frames(wire_frames)
            resp_payload["wire_frames"] = len(wire_frames)
            resp_payload["last_ack"] = res[-1]
    return web.json_response(resp_payload)


async def handle_custom_clock(req: web.Request) -> web.Response:
    """Render a custom pixel-art clock (digital, digital_date, analog, neon_bar) as frames/GIF."""
    body = await req.json()
    width = int(body.get("width", state.width))
    height = int(body.get("height", state.height))
    design = body.get("design", "digital")
    fg = _parse_hex_rgb(body.get("fg", "#05d9e8"))
    accent = _parse_hex_rgb(body.get("accent", "#ff2a6d"))
    bg = _parse_hex_rgb(body.get("bg", "#000000"))
    use_12h = bool(body.get("use_12h", False))
    blink = bool(body.get("blink", True))
    send = bool(body.get("send", False))
    slot = int(body.get("slot", 1))

    frames_rgb = _render_custom_clock_frames(
        width, height, design=design, fg=fg, accent=accent, bg=bg, use_12h=use_12h, blink_colon=blink
    )
    gif_bytes = _frames_rgb_to_gif_bytes(frames_rgb, width, height, delay_ms=500)
    out: Dict[str, Any] = {
        "ok": True,
        "width": width,
        "height": height,
        "frames": [base64.b64encode(f).decode("ascii") for f in frames_rgb],
        "gif_b64": base64.b64encode(gif_bytes).decode("ascii"),
        "gif_bytes": len(gif_bytes),
    }

    if send:
        if not state.transport or not state.transport.is_connected:
            return web.json_response({"error": "Not connected to display"}, status=400)
        async with state.lock:
            if len(frames_rgb) > 1:
                wire_frames = build_gif_program_frames(
                    gif_bytes, width, height, program_id=slot, start_sno=state.transport.next_sno()
                )
            else:
                ystp = encode_ystp01(frames_rgb, width, height)
                wire_frames = build_image_program_frames(
                    ystp, width, height, program_id=slot, start_sno=state.transport.next_sno()
                )
            res = await state.transport.send_frames(wire_frames)
            out["wire_frames"] = len(wire_frames)
            out["last_ack"] = res[-1]
    return web.json_response(out)


async def handle_gif_pack_list(_req: web.Request) -> web.Response:
    items = []
    if GIFS_DIR.exists():
        for p in sorted(GIFS_DIR.glob("*.gif")):
            items.append({"file": p.name, "url": f"/gifs/{p.name}", "size": p.stat().st_size})
    return web.json_response({"gifs": items})


async def handle_gif_pack_send(req: web.Request) -> web.Response:
    if not state.transport or not state.transport.is_connected:
        return web.json_response({"error": "Not connected to display"}, status=400)
    body = await req.json()
    raw_name = body.get("file") or body.get("filename") or ""
    if not raw_name:
        return web.json_response({"error": "Missing file/filename"}, status=400)
    filename = Path(str(raw_name)).name
    gif_path = GIFS_DIR / filename
    if not gif_path.is_file():
        return web.json_response({"error": f"GIF not found: {filename}"}, status=404)
    raw = gif_path.read_bytes()
    slot = int(body.get("slot", 1))
    src = _detect_req_source(req)
    async with state.lock:
        try:
            await _ensure_exit_modal_mode()
            wire_frames = build_gif_program_frames(
                raw, state.width, state.height, program_id=slot, start_sno=state.transport.next_sno()
            )
            res = await state.transport.send_frames(wire_frames, label=f"GIF: {filename}")
            await state.transport.send_frame(cmd_pgm_play(model=1, index=(slot - 1) & 0xFF, sno=state.transport.next_sno()))
            pretty = filename.rsplit(".", 1)[0].replace("_", " ").title()
            pin = "pinOnAir" if "on_air" in filename else ("pinCoding" if "coding" in filename else "")
            state.set_now_playing(
                "gif", pretty, subtitle=f"64×64 Animated Mode · via {src}",
                active_pin=pin, preview_url=f"/gifs/{filename}", source=src,
            )
            return web.json_response({"ok": True, "file": filename, "wire_frames": len(wire_frames), "last_ack": res[-1]})
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=500)


def _render_hw_clock_shape_pil(
    width: int, height: int, shape: str, fg: tuple[int, int, int], accent: tuple[int, int, int], bg: tuple[int, int, int]
) -> Optional[Image.Image]:
    """Generate a PIL RGB image for the shape/bezel background of multi-region hardware RTC clocks."""
    if shape in ("custom", "hw_minimal", "hw_seconds", "hw_date_time", "hw_stacked"):
        return None
    img = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)
    dim_fg = (max(20, fg[0] // 3), max(20, fg[1] // 3), max(20, fg[2] // 3))
    dim_acc = (max(20, accent[0] // 3), max(20, accent[1] // 3), max(20, accent[2] // 3))

    if shape == "hw_neon_ring":
        cx, cy = width // 2, height // 2
        r = min(width, height) // 2 - 2
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=accent, width=2)
        draw.ellipse([cx - r + 3, cy - r + 3, cx + r - 3, cy + r - 3], outline=dim_fg, width=1)
    elif shape == "hw_flip_cards":
        pad = 2
        mid = width // 2
        top, bot = 8, height - 10
        draw.rounded_rectangle([pad, top, mid - 2, bot], radius=4, fill=(18, 24, 40), outline=dim_fg)
        draw.rounded_rectangle([mid + 2, top, width - 1 - pad, bot], radius=4, fill=(18, 24, 40), outline=dim_fg)
        hy = (top + bot) // 2
        draw.line([(pad, hy), (width - 1 - pad, hy)], fill=bg, width=1)
    elif shape == "hw_cyber_frame":
        b = max(6, width // 6)
        for pts in [
            [(1, b), (1, 1), (b, 1)],
            [(width - 1 - b, 1), (width - 2, 1), (width - 2, b)],
            [(1, height - 1 - b), (1, height - 2), (b, height - 2)],
            [(width - 1 - b, height - 2), (width - 2, height - 2), (width - 2, height - 1 - b)],
        ]:
            draw.line(pts, fill=accent, width=2)
        draw.rectangle([4, 4, width - 5, height - 5], outline=dim_fg, width=1)
    elif shape == "hw_synthwave_sun":
        cx, cy = width // 2, height // 2 - 4
        r = min(width, height) // 2 - 6
        px = img.load()
        for y in range(max(0, cy - r), min(height, cy + r + 1)):
            frac = (y - (cy - r)) / max(1, 2 * r)
            if frac > 0.52 and (y % 4 == 0):
                continue
            r_col = 255
            g_col = int(205 * (1.0 - frac) + 30 * frac)
            b_col = int(40 * (1.0 - frac) + 170 * frac)
            for x in range(max(0, cx - r), min(width, cx + r + 1)):
                if (x - cx) ** 2 + (y - cy) ** 2 <= r * r:
                    px[x, y] = (r_col // 2, g_col // 2, b_col // 2)
        horizon = int(height * 0.68)
        draw.line([(0, horizon), (width - 1, horizon)], fill=(255, 42, 180))
        for gy in (horizon + 5, horizon + 11, height - 2):
            if gy < height:
                draw.line([(0, gy), (width - 1, gy)], fill=(120, 18, 95))
        for gx in range(-2, 5):
            bx = int(width // 2 + gx * 16)
            tx = int(width // 2 + gx * 6)
            draw.line([(tx, horizon), (bx, height - 1)], fill=(120, 18, 95))
    elif shape == "hw_pipboy_hud":
        hud_col = (20, 220, 110)
        hud_dim = (8, 70, 35)
        draw.rectangle([2, 6, width - 3, height - 3], outline=hud_dim)
        draw.line([(4, 4), (width - 5, 4)], fill=hud_col)
        for tx in range(6, width - 5, 6):
            draw.line([(tx, 2), (tx, 5)], fill=hud_col if tx % 12 == 0 else hud_dim)
        for x0, y0, x1, y1 in [(1, 7, 9, 7), (width - 10, 7, width - 2, 7), (1, height - 2, 9, height - 2), (width - 10, height - 2, width - 2, height - 2)]:
            draw.line([(x0, y0), (x1, y1)], fill=hud_col, width=2)
        draw.line([(6, height // 2 + 5), (width - 7, height // 2 + 5)], fill=hud_dim)
    elif shape == "hw_gameboy_retro":
        draw.rounded_rectangle([1, 1, width - 2, height - 2], radius=5, fill=(32, 36, 44), outline=(85, 92, 108), width=2)
        draw.rectangle([9, 8, width - 6, height - 9], fill=(12, 30, 14), outline=(45, 90, 45))
        draw.ellipse([3, 22, 6, 25], fill=(255, 40, 40))
        draw.line([(12, 4), (width - 10, 4)], fill=(140, 45, 95))
        draw.line([(12, 5), (width - 10, 5)], fill=(50, 75, 165))
    elif shape == "hw_matrix_terminal":
        draw.rounded_rectangle([2, 2, width - 3, height - 3], radius=4, fill=(4, 10, 8), outline=(25, 120, 65))
        draw.line([(3, 11), (width - 4, 11)], fill=(25, 120, 65))
        draw.rectangle([6, 5, 8, 7], fill=(255, 75, 85))
        draw.rectangle([11, 5, 13, 7], fill=(255, 200, 50))
        draw.rectangle([16, 5, 18, 7], fill=(40, 220, 110))
        draw.line([(23, 6), (width - 8, 6)], fill=(18, 65, 38))
    elif shape == "hw_galaxy_stars":
        px = img.load()
        for y in range(height):
            for x in range(width):
                px[x, y] = (2, 4, 14)
        stars = [(6, 7), (16, 5), (29, 9), (42, 6), (8, 22), (56, 20), (5, 44), (14, 55), (34, 58), (52, 54), (58, 38), (22, 49)]
        for i, (sx, sy) in enumerate(stars):
            if 0 <= sx < width and 0 <= sy < height:
                c = (255, 250, 200) if i % 3 == 0 else (130, 175, 255)
                px[sx, sy] = c
        draw.ellipse([width - 16, 4, width - 5, 15], fill=(255, 225, 110))
        draw.ellipse([width - 13, 3, width - 3, 13], fill=(2, 4, 14))
    elif shape == "hw_arcade_cabinet":
        draw.rectangle([1, 1, width - 2, height - 2], outline=(255, 200, 30), width=2)
        draw.rectangle([4, 4, width - 5, height - 5], outline=(5, 217, 232), width=1)
        for cx0, cy0 in [(2, 2), (width - 8, 2), (2, height - 8), (width - 8, height - 8)]:
            for dy in range(0, 6, 2):
                for dx in range(0, 6, 2):
                    if ((dx + dy) // 2) % 2 == 0:
                        draw.rectangle([cx0 + dx, cy0 + dy, cx0 + dx + 1, cy0 + dy + 1], fill=(255, 42, 109))
        draw.line([(6, height - 20), (width - 7, height - 20)], fill=(255, 42, 109))
    elif shape == "hw_heart_bezel":
        heart_col = accent
        for hx, hy in [(4, 4), (width - 10, 4), (4, height - 9), (width - 10, height - 9)]:
            for dx, dy in [(1, 0), (3, 0), (0, 1), (1, 1), (2, 1), (3, 1), (4, 1), (1, 2), (2, 2), (3, 2), (2, 3)]:
                if 0 <= hx + dx < width and 0 <= hy + dy < height:
                    img.putpixel((hx + dx, hy + dy), heart_col)
        mid_y = height // 2 + 4
        ekg = [(4, mid_y), (18, mid_y), (22, mid_y - 5), (26, mid_y + 6), (30, mid_y - 8), (34, mid_y + 4), (38, mid_y), (width - 5, mid_y)]
        draw.line(ekg, fill=dim_acc, width=1)
    elif shape == "hw_gradient_pill":
        px = img.load()
        for y in range(2, height - 2):
            for x in range(2, width - 2):
                on_outer = (x in (2, 3, width - 3, width - 4) or y in (2, 3, height - 3, height - 4))
                dx = min(x - 2, width - 3 - x)
                dy = min(y - 2, height - 3 - y)
                if on_outer and (dx + dy >= 5):
                    frac = (x + y) / float(width + height)
                    r_c = int(5 + 245 * frac)
                    g_c = int(225 * (1.0 - frac) + 45 * frac)
                    b_c = int(245)
                    px[x, y] = (r_c, g_c, b_c)
        draw.rounded_rectangle([6, 6, width - 7, height - 7], radius=6, outline=dim_fg)
    else:
        return None
    return img


def _render_hw_clock_shape_bg(
    width: int, height: int, shape: str, fg: tuple[int, int, int], accent: tuple[int, int, int], bg: tuple[int, int, int]
) -> Optional[bytes]:
    """Generate a YSTP01 shape/bezel background for multi-region hardware RTC clocks."""
    img = _render_hw_clock_shape_pil(width, height, shape, fg, accent, bg)
    return encode_ystp01([img.tobytes()], width, height) if img is not None else None


_PREVIEW_FONT_3X5: Dict[str, List[str]] = {
    "0": ["###", "#.#", "#.#", "#.#", "###"],
    "1": [".#.", "##.", ".#.", ".#.", "###"],
    "2": ["###", "..#", "###", "#..", "###"],
    "3": ["###", "..#", "###", "..#", "###"],
    "4": ["#.#", "#.#", "###", "..#", "..#"],
    "5": ["###", "#..", "###", "..#", "###"],
    "6": ["###", "#..", "###", "#.#", "###"],
    "7": ["###", "..#", "..#", "..#", "..#"],
    "8": ["###", "#.#", "###", "#.#", "###"],
    "9": ["###", "#.#", "###", "..#", "###"],
    ":": ["...", ".#.", "...", ".#.", "..."],
    "/": ["..#", "..#", ".#.", "#..", "#.."],
    "-": ["...", "...", "###", "...", "..."],
    "s": ["...", ".##", "#..", ".#.", "##."],
    " ": ["...", "...", "...", "...", "..."],
}


def _draw_preview_text(img: Image.Image, text: str, rx: int, ry: int, rw: int, rh: int, color: tuple[int, int, int], font_size: int) -> None:
    scale = 2 if (font_size >= 16 and len(text) * 8 <= rw + 2) else 1
    ch_w = 3 * scale
    ch_h = 5 * scale
    gap = scale
    total_w = len(text) * (ch_w + gap) - gap
    sx = rx + max(0, (rw - total_w) // 2)
    sy = ry + max(0, (rh - ch_h) // 2)
    px = img.load()
    for i, ch in enumerate(text):
        glyph = _PREVIEW_FONT_3X5.get(ch, _PREVIEW_FONT_3X5[" "])
        gx = sx + i * (ch_w + gap)
        for r_i, row in enumerate(glyph):
            for c_i, bit in enumerate(row):
                if bit == "#":
                    for dy in range(scale):
                        for dx in range(scale):
                            tx, ty = gx + c_i * scale + dx, sy + r_i * scale + dy
                            if 0 <= tx < img.width and 0 <= ty < img.height:
                                px[tx, ty] = color


async def handle_hw_clock_preview(req: web.Request) -> web.Response:
    """Render a crisp 64x64 live PNG preview of any creative Hardware RTC Clock."""
    shape = req.match_info.get("shape", "hw_neon_ring")
    fg = _parse_hex_rgb(req.query.get("color", "#38bdf8"))
    accent = _parse_hex_rgb(req.query.get("accent", "#f43f5e"))
    bg = _parse_hex_rgb(req.query.get("bg", "#000000"))
    w, h = 64, 64

    img = _render_hw_clock_shape_pil(w, h, shape, fg, accent, bg) or Image.new("RGB", (w, h), bg)
    regions = _build_hw_clock_regions(shape, w, h, fg, accent, bg) or [
        {"template": "{hh}:{mm}", "x": 0, "y": 0, "w": w, "h": h, "color": fg, "font_size": 16}
    ]
    now = datetime.datetime.now()
    for reg in regions:
        txt = (
            reg["template"]
            .replace("{YYYY}", now.strftime("%Y"))
            .replace("{YY}", now.strftime("%y"))
            .replace("{MM}", now.strftime("%m"))
            .replace("{DD}", now.strftime("%d"))
            .replace("{hh}", now.strftime("%H"))
            .replace("{mm}", now.strftime("%M"))
            .replace("{ss}", now.strftime("%S"))
        )
        _draw_preview_text(
            img, txt, int(reg.get("x", 0)), int(reg.get("y", 0)),
            int(reg.get("w", w)), int(reg.get("h", h)),
            tuple(reg.get("color", fg)), int(reg.get("font_size", 16)),
        )
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return web.Response(body=buf.getvalue(), content_type="image/png", headers={"Cache-Control": "no-cache"})


def _build_hw_clock_regions(
    shape: str, w: int, h: int, fg: tuple[int, int, int], accent: tuple[int, int, int], bg: tuple[int, int, int]
) -> Optional[List[Dict[str, Any]]]:
    """Return multi-region hardware RTC text_clock layout for all 15 built-in creative clock shapes."""
    if shape == "hw_minimal":
        return [{"template": "{hh}:{mm}", "x": 0, "y": 0, "w": w, "h": h, "color": fg, "bg_color": bg, "font_size": 24 if w >= 64 else 16}]
    if shape == "hw_seconds":
        return [
            {"template": "{hh}:{mm}", "x": 0, "y": 4, "w": w, "h": h // 2, "color": fg, "bg_color": bg, "font_size": 20 if w >= 64 else 12},
            {"template": "{ss}", "x": 0, "y": h // 2, "w": w, "h": h // 2 - 4, "color": accent, "bg_color": bg, "font_size": 16 if w >= 64 else 12},
        ]
    if shape == "hw_date_time":
        return [
            {"template": "{MM}/{DD}", "x": 0, "y": 4, "w": w, "h": h // 3, "color": accent, "bg_color": bg, "font_size": 12},
            {"template": "{hh}:{mm}:{ss}", "x": 0, "y": h // 3, "w": w, "h": (h * 2) // 3 - 4, "color": fg, "bg_color": bg, "font_size": 16},
        ]
    if shape == "hw_stacked":
        return [
            {"template": "{hh}", "x": 0, "y": 0, "w": w, "h": h // 2, "color": fg, "bg_color": bg, "font_size": 24 if w >= 64 else 16},
            {"template": "{mm}", "x": 0, "y": h // 2, "w": w, "h": h // 2, "color": accent, "bg_color": bg, "font_size": 24 if w >= 64 else 16},
        ]
    if shape == "hw_neon_ring":
        return [
            {"template": "{hh}:{mm}", "x": 6, "y": h // 4, "w": w - 12, "h": h // 3, "color": fg, "bg_color": bg, "font_size": 16},
            {"template": "{MM}/{DD}", "x": 10, "y": (h * 7) // 12, "w": w - 20, "h": h // 4, "color": accent, "bg_color": bg, "font_size": 12},
        ]
    if shape == "hw_flip_cards":
        mid = w // 2
        return [
            {"template": "{hh}", "x": 3, "y": 10, "w": mid - 6, "h": h - 22, "color": fg, "bg_color": (18, 24, 40), "font_size": 20 if w >= 64 else 12},
            {"template": "{mm}", "x": mid + 3, "y": 10, "w": mid - 6, "h": h - 22, "color": accent, "bg_color": (18, 24, 40), "font_size": 20 if w >= 64 else 12},
        ]
    if shape == "hw_cyber_frame":
        return [
            {"template": "{hh}:{mm}:{ss}", "x": 6, "y": 12, "w": w - 12, "h": h // 2 - 4, "color": fg, "bg_color": bg, "font_size": 16},
            {"template": "{YYYY}-{MM}-{DD}", "x": 6, "y": h // 2 + 6, "w": w - 12, "h": h // 3, "color": accent, "bg_color": bg, "font_size": 12},
        ]
    if shape == "hw_synthwave_sun":
        return [
            {"template": "{hh}:{mm}", "x": 4, "y": 14, "w": w - 8, "h": 22, "color": (255, 245, 160), "bg_color": bg, "font_size": 16},
            {"template": "{MM}/{DD}", "x": 8, "y": 44, "w": w - 16, "h": 16, "color": (5, 240, 255), "bg_color": bg, "font_size": 12},
        ]
    if shape == "hw_pipboy_hud":
        return [
            {"template": "{hh}:{mm}:{ss}", "x": 4, "y": 13, "w": w - 8, "h": 22, "color": (50, 255, 130), "bg_color": bg, "font_size": 16},
            {"template": "{YYYY}-{MM}-{DD}", "x": 4, "y": 40, "w": w - 8, "h": 18, "color": (20, 200, 100), "bg_color": bg, "font_size": 12},
        ]
    if shape == "hw_gameboy_retro":
        return [
            {"template": "{hh}:{mm}", "x": 11, "y": 14, "w": w - 18, "h": 22, "color": (155, 240, 95), "bg_color": (12, 30, 14), "font_size": 16},
            {"template": "{MM}/{DD}", "x": 11, "y": 37, "w": w - 18, "h": 14, "color": (105, 195, 75), "bg_color": (12, 30, 14), "font_size": 12},
        ]
    if shape == "hw_matrix_terminal":
        return [
            {"template": "{hh}:{mm}:{ss}", "x": 5, "y": 17, "w": w - 10, "h": 22, "color": (60, 255, 140), "bg_color": (4, 10, 8), "font_size": 16},
            {"template": "{MM}/{DD}", "x": 5, "y": 41, "w": w - 10, "h": 16, "color": (5, 217, 232), "bg_color": (4, 10, 8), "font_size": 12},
        ]
    if shape == "hw_galaxy_stars":
        return [
            {"template": "{hh}:{mm}", "x": 4, "y": 17, "w": w - 8, "h": 22, "color": (255, 235, 150), "bg_color": (2, 4, 14), "font_size": 16},
            {"template": "{MM}/{DD}", "x": 8, "y": 41, "w": w - 16, "h": 14, "color": (155, 190, 255), "bg_color": (2, 4, 14), "font_size": 12},
        ]
    if shape == "hw_arcade_cabinet":
        return [
            {"template": "{hh}:{mm}", "x": 6, "y": 14, "w": w - 12, "h": 24, "color": (255, 215, 45), "bg_color": bg, "font_size": 16},
            {"template": "{ss}s", "x": 8, "y": 45, "w": w - 16, "h": 13, "color": (5, 230, 255), "bg_color": bg, "font_size": 12},
        ]
    if shape == "hw_heart_bezel":
        return [
            {"template": "{hh}:{mm}", "x": 6, "y": 11, "w": w - 12, "h": 20, "color": fg, "bg_color": bg, "font_size": 16},
            {"template": "{MM}/{DD}", "x": 10, "y": 42, "w": w - 20, "h": 15, "color": accent, "bg_color": bg, "font_size": 12},
        ]
    if shape == "hw_gradient_pill":
        return [
            {"template": "{hh}:{mm}", "x": 8, "y": 15, "w": w - 16, "h": 22, "color": (255, 255, 255), "bg_color": bg, "font_size": 16},
            {"template": ":{ss}", "x": 12, "y": 39, "w": w - 24, "h": 15, "color": (5, 230, 255), "bg_color": bg, "font_size": 12},
        ]
    return None


async def handle_hw_text_clock(req: web.Request) -> web.Response:
    """Sync RTC and upload a standalone hardware-ticking `text_clock` shape/program."""
    if not state.transport or not state.transport.is_connected:
        return web.json_response({"error": "Not connected to display"}, status=400)
    body = await req.json()
    shape = body.get("shape", "custom")
    template = (body.get("template") or "{hh}:{mm}:{ss}").strip()
    fg = _parse_hex_rgb(body.get("color", "#05d9e8"))
    accent = _parse_hex_rgb(body.get("accent", "#ff2a6d"))
    bg = _parse_hex_rgb(body.get("bg", "#000000"))
    font_size = int(body.get("size", 16))
    scroll = body.get("scroll", "static")
    slot = int(body.get("slot", 1))
    bg_gif_name = body.get("bg_gif", "")
    bg_mode = str(body.get("bg_mode", "behind"))
    w, h = state.width, state.height

    bg_resource = None
    if bg_gif_name:
        gif_path = GIFS_DIR / Path(bg_gif_name).name
        if gif_path.is_file():
            bg_resource = gif_path.read_bytes()
    if bg_resource is None:
        bg_resource = _render_hw_clock_shape_bg(w, h, shape, fg, accent, bg)

    async with state.lock:
        try:
            await _ensure_exit_modal_mode()
            if body.get("sync_first", True):
                await state.transport.send_frame(cmd_sync_time(sno=state.transport.next_sno()))

            regions = _build_hw_clock_regions(shape, w, h, fg, accent, bg)
            if regions is None:
                wire_frames = build_text_clock_program_frames(
                    template, w, h, color=fg, bg_color=bg, font_size=font_size, scroll=scroll,
                    program_id=slot, start_sno=state.transport.next_sno(),
                    background_resource=bg_resource, bg_mode=bg_mode,
                )
            else:
                wire_frames = build_multi_region_hw_clock_frames(
                    regions, w, h, program_id=slot, start_sno=state.transport.next_sno(),
                    background_resource=bg_resource, bg_mode=bg_mode,
                )
            res = await state.transport.send_frames(wire_frames, label=f"Clock: {shape}")
            await state.transport.send_frame(cmd_pgm_play(model=1, index=(slot - 1) & 0xFF, sno=state.transport.next_sno()))
            src = _detect_req_source(req)
            col_q = urllib.parse.quote(body.get("color", "#05d9e8"))
            acc_q = urllib.parse.quote(body.get("accent", "#ff2a6d"))
            state.set_now_playing(
                "clock", shape.replace("hw_", "").replace("_", " ").title() + " Clock",
                subtitle=f"Hardware RTC · via {src}",
                preview_url=f"/api/hw_clock_preview/{shape}?color={col_q}&accent={acc_q}",
                source=src,
            )
            return web.json_response({"ok": True, "shape": shape, "wire_frames": len(wire_frames), "last_ack": res[-1]})
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=500)


async def handle_qrcode(req: web.Request) -> web.Response:
    """Generate a crisp QR code 100% locally (no external URL shorteners) and optionally send to display."""
    import qrcode

    body = await req.json()
    text = (body.get("text") or "").strip()
    if not text:
        return web.json_response({"error": "QR content cannot be empty"}, status=400)
    fg = _parse_hex_rgb(body.get("fg", "#000000"))
    bg = _parse_hex_rgb(body.get("bg", "#ffffff"))
    width = int(body.get("width", state.width))
    height = int(body.get("height", state.height))
    slot = int(body.get("slot", 1))
    send = bool(body.get("send", False))

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=1,
        border=2,
    )
    qr.add_data(text)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color=fg, back_color=bg).convert("RGB")
    side = min(width, height)
    qr_resized = qr_img.resize((side, side), Image.Resampling.NEAREST)
    canvas = Image.new("RGB", (width, height), bg)
    canvas.paste(qr_resized, ((width - side) // 2, (height - side) // 2))
    raw_rgb = canvas.tobytes()

    out: Dict[str, Any] = {
        "ok": True,
        "width": width,
        "height": height,
        "frame_b64": base64.b64encode(raw_rgb).decode("ascii"),
    }
    if send:
        if not state.transport or not state.transport.is_connected:
            return web.json_response({"error": "Not connected to display"}, status=400)
        ystp = encode_ystp01([raw_rgb], width, height)
        async with state.lock:
            wire_frames = build_image_program_frames(
                ystp, width, height, program_id=slot, start_sno=state.transport.next_sno()
            )
            res = await state.transport.send_frames(wire_frames)
            out["wire_frames"] = len(wire_frames)
            out["last_ack"] = res[-1]
    return web.json_response(out)


def _resize_gif_bytes(raw_gif: bytes, width: int, height: int, max_frames: int = 36) -> bytes:
    """Resize an arbitrary web GIF down to (width, height) for fast BLE transfer."""
    with Image.open(io.BytesIO(raw_gif)) as img:
        frames_out: List[Image.Image] = []
        durations: List[int] = []
        total = getattr(img, "n_frames", 1)
        step = max(1, math.ceil(total / max_frames))
        for idx, frame in enumerate(ImageSequence.Iterator(img)):
            if idx % step != 0:
                continue
            dur = int(frame.info.get("duration", 100)) * step
            durations.append(max(40, min(2000, dur)))
            rgb = frame.convert("RGB")
            # Center-crop to square/target aspect ratio before resizing
            src_w, src_h = rgb.size
            target_ratio = width / height
            src_ratio = src_w / max(1, src_h)
            if src_ratio > target_ratio:
                new_w = int(src_h * target_ratio)
                left = (src_w - new_w) // 2
                rgb = rgb.crop((left, 0, left + new_w, src_h))
            else:
                new_h = int(src_w / target_ratio)
                top = (src_h - new_h) // 2
                rgb = rgb.crop((0, top, src_w, top + new_h))
            resized = rgb.resize((width, height), Image.Resampling.BILINEAR)
            frames_out.append(resized.convert("P", palette=Image.Palette.ADAPTIVE))
        if not frames_out:
            raise ValueError("GIF contained no readable frames")
        buf = io.BytesIO()
        frames_out[0].save(
            buf,
            format="GIF",
            save_all=True,
            append_images=frames_out[1:],
            duration=durations if len(durations) > 1 else durations[0],
            loop=0,
            disposal=2,
        )
        return buf.getvalue()


async def handle_gif_search(req: web.Request) -> web.Response:
    """
    Opt-in online GIF search (Tenor or GIPHY). Zero outbound requests unless explicitly called.
    Query params: q, provider (tenor|giphy), api_key (optional for tenor v1), limit (default 12).
    """
    q = (req.query.get("q") or "").strip()
    provider = (req.query.get("provider") or "wikimedia").lower()
    api_key = (req.query.get("api_key") or "").strip()
    limit = max(1, min(24, int(req.query.get("limit", "12"))))

    timeout = ClientTimeout(total=12)
    results: List[Dict[str, Any]] = []
    headers = {"User-Agent": "LOYStudio/1.0 (local offline pixel controller)"}
    try:
        async with ClientSession(timeout=timeout, headers=headers) as session:
            if provider == "wikimedia":
                search_term = f"{q} animation" if q else "pixel art animation"
                params = {
                    "action": "query",
                    "generator": "search",
                    "gsrsearch": f"filetype:bitmap {search_term} gif",
                    "gsrnamespace": "6",
                    "gsrlimit": str(min(30, limit * 2)),
                    "prop": "imageinfo",
                    "iiprop": "url|mime|size",
                    "format": "json",
                }
                async with session.get("https://commons.wikimedia.org/w/api.php", params=params) as resp:
                    data = await resp.json()
                    pages = (data.get("query") or {}).get("pages") or {}
                    for page in pages.values():
                        ii = (page.get("imageinfo") or [{}])[0]
                        url = ii.get("url", "")
                        if ii.get("mime") == "image/gif" and url:
                            title = (page.get("title") or "GIF").replace("File:", "")
                            results.append({"id": str(page.get("pageid", "")), "title": title, "preview": url, "url": url})
                            if len(results) >= limit:
                                break
            elif provider == "giphy":
                if not api_key:
                    return web.json_response(
                        {"error": "Enter a free GIPHY API key (from developers.giphy.com) or switch provider to Tenor."},
                        status=400,
                    )
                endpoint = "https://api.giphy.com/v1/gifs/search" if q else "https://api.giphy.com/v1/gifs/trending"
                params = {"api_key": api_key, "limit": str(limit), "rating": "g"}
                if q:
                    params["q"] = q
                async with session.get(endpoint, params=params) as resp:
                    data = await resp.json()
                    if resp.status != 200:
                        return web.json_response({"error": data.get("meta", {}).get("msg", f"GIPHY HTTP {resp.status}")}, status=resp.status)
                    for item in data.get("data", []):
                        imgs = item.get("images", {})
                        preview = (imgs.get("fixed_width_small") or imgs.get("fixed_width") or imgs.get("original") or {}).get("url")
                        full = (imgs.get("fixed_width") or imgs.get("original") or {}).get("url")
                        if preview and full:
                            results.append({"id": item.get("id", ""), "title": item.get("title") or "GIF", "preview": preview, "url": full})
            else:
                # Tenor v2 if user supplied a Google Tenor key; otherwise Tenor v1 public demo key
                if api_key and len(api_key) > 20:
                    endpoint = "https://tenor.googleapis.com/v2/search" if q else "https://tenor.googleapis.com/v2/featured"
                    params = {"key": api_key, "limit": str(limit), "media_filter": "tinygif,gif"}
                    if q:
                        params["q"] = q
                    async with session.get(endpoint, params=params) as resp:
                        data = await resp.json()
                        if resp.status != 200:
                            return web.json_response({"error": str(data.get("error", f"Tenor HTTP {resp.status}"))}, status=resp.status)
                        for item in data.get("results", []):
                            mf = item.get("media_formats", {})
                            preview = (mf.get("tinygif") or mf.get("gif") or {}).get("url")
                            full = (mf.get("tinygif") or mf.get("gif") or {}).get("url")
                            if preview and full:
                                results.append({"id": item.get("id", ""), "title": item.get("content_description") or "GIF", "preview": preview, "url": full})
                else:
                    key = api_key or "LIVDSRZULELA"
                    endpoint = "https://g.tenor.com/v1/search" if q else "https://g.tenor.com/v1/trending"
                    params = {"key": key, "limit": str(limit), "media_filter": "minimal"}
                    if q:
                        params["q"] = q
                    async with session.get(endpoint, params=params) as resp:
                        data = await resp.json()
                        if resp.status != 200:
                            return web.json_response({"error": f"Tenor HTTP {resp.status}"}, status=resp.status)
                        for item in data.get("results", []):
                            media = (item.get("media") or [{}])[0]
                            preview = (media.get("tinygif") or media.get("gif") or {}).get("url")
                            full = (media.get("tinygif") or media.get("gif") or {}).get("url")
                            if preview and full:
                                results.append({"id": item.get("id", ""), "title": item.get("content_description") or "GIF", "preview": preview, "url": full})
        return web.json_response({"ok": True, "provider": provider, "results": results})
    except Exception as exc:
        return web.json_response({"error": f"GIF API request failed: {exc}"}, status=502)


async def handle_gif_fetch_send(req: web.Request) -> web.Response:
    """Download a selected GIF URL, resize to display resolution, optionally save locally, and send."""
    body = await req.json()
    url = (body.get("url") or "").strip()
    if not (url.startswith("https://") or url.startswith("http://")):
        return web.json_response({"error": "Invalid GIF URL"}, status=400)
    save_name = (body.get("save_name") or "").strip()
    send = bool(body.get("send", True))
    slot = int(body.get("slot", 1))
    width = int(body.get("width", state.width))
    height = int(body.get("height", state.height))

    try:
        async with ClientSession(timeout=ClientTimeout(total=15)) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return web.json_response({"error": f"Failed to fetch GIF (HTTP {resp.status})"}, status=502)
                raw = await resp.read()
        resized_gif = _resize_gif_bytes(raw, width, height)
    except Exception as exc:
        return web.json_response({"error": f"Failed to download/resize GIF: {exc}"}, status=500)

    saved_file = None
    if save_name:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in save_name)[:40] or f"web_{int(time.time())}"
        if not safe.endswith(".gif"):
            safe += ".gif"
        GIFS_DIR.mkdir(parents=True, exist_ok=True)
        (GIFS_DIR / safe).write_bytes(resized_gif)
        saved_file = safe

    out: Dict[str, Any] = {
        "ok": True,
        "gif_bytes": len(resized_gif),
        "saved_file": saved_file,
        "gif_b64": base64.b64encode(resized_gif).decode("ascii"),
    }
    if send:
        if not state.transport or not state.transport.is_connected:
            return web.json_response({"error": "Not connected to display"}, status=400)
        async with state.lock:
            wire_frames = build_gif_program_frames(
                resized_gif, width, height, program_id=slot, start_sno=state.transport.next_sno()
            )
            res = await state.transport.send_frames(wire_frames)
            out["wire_frames"] = len(wire_frames)
            out["last_ack"] = res[-1]
    return web.json_response(out)


async def handle_upload_sequence(req: web.Request) -> web.Response:
    """
    Convert an ordered sequence of multiple images (PNG/JPG/BMP/WEBP) into:
      - mode='gif': a single looping animated GIF89a (returned as frames + gif_b64, optionally sent)
      - mode='playlist': separate hardware program slots (slot, slot+1, ...) looped via pgm_play
    """
    body = await req.json()
    images_b64: List[str] = body.get("images") or []
    if not images_b64:
        return web.json_response({"error": "Select at least one image"}, status=400)
    width = int(body.get("width", state.width))
    height = int(body.get("height", state.height))
    delay_ms = int(body.get("delay_ms", 400))
    mode = body.get("mode", "gif")  # "gif" | "playlist"
    start_slot = int(body.get("slot", 1))
    send = bool(body.get("send", True))

    frames_rgb: List[bytes] = []
    for b64 in images_b64:
        raw = base64.b64decode(b64)
        with Image.open(io.BytesIO(raw)) as img:
            rgb = img.convert("RGB").resize((width, height), Image.Resampling.BILINEAR)
            frames_rgb.append(rgb.tobytes())

    gif_bytes = _frames_rgb_to_gif_bytes(frames_rgb, width, height, delay_ms=delay_ms)
    out: Dict[str, Any] = {
        "ok": True,
        "count": len(frames_rgb),
        "mode": mode,
        "gif_bytes": len(gif_bytes),
        "gif_b64": base64.b64encode(gif_bytes).decode("ascii"),
        "frames": [base64.b64encode(f).decode("ascii") for f in frames_rgb],
    }

    if send:
        if not state.transport or not state.transport.is_connected:
            return web.json_response({"error": "Not connected to display"}, status=400)
        async with state.lock:
            total_wire = 0
            if mode == "playlist":
                slots: List[int] = []
                for idx, fr in enumerate(frames_rgb):
                    slot_id = start_slot + idx
                    slots.append(slot_id)
                    ystp = encode_ystp01([fr], width, height)
                    wfs = build_image_program_frames(
                        ystp, width, height, program_id=slot_id, start_sno=state.transport.next_sno(), show_now=False
                    )
                    await state.transport.send_frames(wfs)
                    total_wire += len(wfs)
                res = await state.transport.send_frame(
                    cmd_pgm_play(model=2, index=0, ids_pro=slots, sno=state.transport.next_sno())
                )
                total_wire += 1
                out["slots"] = slots
                out["wire_frames"] = total_wire
                out["last_ack"] = res
            else:
                wfs = build_gif_program_frames(
                    gif_bytes, width, height, program_id=start_slot, start_sno=state.transport.next_sno()
                )
                res = await state.transport.send_frames(wfs)
                out["wire_frames"] = len(wfs)
                out["last_ack"] = res[-1]
    return web.json_response(out)


async def handle_transfer_status(_req: web.Request) -> web.Response:
    if state.transport:
        return web.json_response({"transfer": state.transport.transfer.to_dict()})
    return web.json_response({"transfer": {"active": False, "percent": 0.0}})


async def handle_transfer_cancel(_req: web.Request) -> web.Response:
    if state.transport:
        state.transport.cancel_transfer()
    return web.json_response({"ok": True})


async def handle_ble_profile(req: web.Request) -> web.Response:
    if req.method == "GET":
        cur = state.transport.profile.name if state.transport else "conservative"
        return web.json_response({
            "current": cur,
            "profiles": {
                k: {
                    "name": p.name,
                    "ble_chunk_size": p.ble_chunk_size,
                    "swnd_max": p.swnd_max,
                    "write_delay_s": p.write_delay_s,
                    "write_with_response": p.write_with_response,
                    "estimated_bps": p.estimated_bps,
                    "hardware_verified": p.hardware_verified,
                    "description": p.description,
                }
                for k, p in BLE_PROFILES.items()
            },
        })
    body = await req.json()
    name = body.get("profile", "conservative")
    if name not in BLE_PROFILES:
        return web.json_response({"error": f"Unknown profile: {name}"}, status=400)
    prof = BLE_PROFILES[name]
    ver = str(state.device_info.get("version", ""))
    if (
        state.mode == "ble"
        and prof.swnd_max > 1
        and "EXYC-A" in ver
        and not body.get("force", False)
    ):
        return web.json_response(
            {
                "error": (
                    f"Profile '{name}' (SWND {prof.swnd_max}) is blocked on EXYC-A firmware "
                    "because sliding-window pipelining stalls FFF1 RX ACKs. "
                    "Use 'ys_l_fast', 'conservative', or 'safe_ack'."
                )
            },
            status=400,
        )
    if state.transport:
        state.transport.set_profile(name)
    return web.json_response({"ok": True, "current": name})


async def handle_benchmark(req: web.Request) -> web.Response:
    if not state.transport or not state.transport.is_connected:
        return web.json_response({"error": "Not connected to display"}, status=400)
    body = await req.json() if req.can_read_body else {}
    size_bytes = max(512, min(16384, int(body.get("bytes", 4096))))
    fake_gif = b"GIF89a" + bytes([i % 256 for i in range(size_bytes - 6)])
    async with state.lock:
        try:
            frames = build_gif_program_frames(
                fake_gif,
                state.width,
                state.height,
                program_id=9,
                start_sno=state.transport.next_sno(),
                show_now=False,
                data_save=1,
            )
            t0 = time.monotonic()
            await state.transport.send_frames(frames, label="Throughput Benchmark")
            elapsed = max(0.001, time.monotonic() - t0)
            total_wire = sum(len(f) for f in frames)
            return web.json_response({
                "ok": True,
                "mode": state.mode,
                "profile": state.transport.profile.name,
                "payload_bytes": size_bytes,
                "wire_bytes": total_wire,
                "frames": len(frames),
                "elapsed_s": round(elapsed, 4),
                "measured_bps": round(total_wire / elapsed, 1),
                "hardware_measured": state.mode == "ble",
            })
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=500)


async def handle_diagnostics(_req: web.Request) -> web.Response:
    t = state.transport
    return web.json_response({
        "status": state.status_dict(),
        "uuids": {
            "service": BLE_SERVICE_UUID,
            "notify": BLE_NOTIFY_CHAR_UUID,
            "write": BLE_WRITE_CHAR_UUID,
        },
        "profile": t.profile.name if t else "conservative",
        "negotiated_mtu": t.negotiated_mtu if t else None,
        "last_rssi": t.last_rssi if t else None,
        "last_measured_bps": round(t.last_measured_bps, 1) if t else None,
        "transfer": t.transfer.to_dict() if t else None,
        "transfer_errors": t.transfer_errors if t else [],
        "recent_packets": list(state.packet_log)[:40],
    })


async def handle_procedural(req: web.Request) -> web.Response:
    if req.method == "GET":
        return web.json_response({"effects": PROCEDURAL_EFFECTS})
    body = await req.json()
    effect = body.get("effect", "plasma")
    width = int(body.get("width", state.width))
    height = int(body.get("height", state.height))
    c1 = _parse_hex_rgb(body.get("color1", "#05d9e8"))
    c2 = _parse_hex_rgb(body.get("color2", "#ff2a6d"))
    density = float(body.get("density", 0.6))
    n_frames = int(body.get("frames", 16))
    delay_ms = int(body.get("delay_ms", 90))
    send = bool(body.get("send", False))
    slot = int(body.get("slot", 1))

    pil_frames = generate_procedural(effect, width, height, color1=c1, color2=c2, density=density, n_frames=n_frames)
    gif_bytes = frames_to_gif_bytes(pil_frames, delay_ms=delay_ms)
    est_s = state.transport.estimate_transfer_seconds(len(gif_bytes)) if state.transport else round(len(gif_bytes) / 3500.0, 2)
    out: Dict[str, Any] = {
        "ok": True,
        "effect": effect,
        "gif_bytes": len(gif_bytes),
        "estimated_s": est_s,
        "gif_b64": base64.b64encode(gif_bytes).decode("ascii"),
        "frames": [base64.b64encode(f.tobytes()).decode("ascii") for f in pil_frames],
    }
    if send:
        if not state.transport or not state.transport.is_connected:
            return web.json_response({"error": "Not connected to display"}, status=400)
        async with state.lock:
            wire_frames = build_gif_program_frames(
                gif_bytes, width, height, program_id=slot, start_sno=state.transport.next_sno()
            )
            res = await state.transport.send_frames(wire_frames, label=f"Procedural: {effect}")
            out["wire_frames"] = len(wire_frames)
            out["last_ack"] = res[-1]
    return web.json_response(out)


async def handle_avatar(req: web.Request) -> web.Response:
    body = await req.json()
    seed = int(body.get("seed", int(time.time()) % 9999))
    width = int(body.get("width", state.width))
    height = int(body.get("height", state.height))
    img = generate_avatar(
        seed,
        width=width,
        height=height,
        primary=_parse_hex_rgb(body.get("primary", "#05d9e8")),
        accent=_parse_hex_rgb(body.get("accent", "#ff2a6d")),
        bg=_parse_hex_rgb(body.get("bg", "#0a0e18")),
    )
    raw = img.tobytes()
    out: Dict[str, Any] = {"ok": True, "seed": seed, "frame_b64": base64.b64encode(raw).decode("ascii")}
    if body.get("send"):
        if not state.transport or not state.transport.is_connected:
            return web.json_response({"error": "Not connected to display"}, status=400)
        ystp = encode_ystp01([raw], width, height)
        async with state.lock:
            wfs = build_image_program_frames(ystp, width, height, program_id=int(body.get("slot", 1)), start_sno=state.transport.next_sno())
            res = await state.transport.send_frames(wfs, label="Pixel Avatar")
            out["wire_frames"] = len(wfs)
            out["last_ack"] = res[-1]
    return web.json_response(out)


async def handle_process_image(req: web.Request) -> web.Response:
    body = await req.json()
    raw = base64.b64decode(body["data_b64"])
    width = int(body.get("width", state.width))
    height = int(body.get("height", state.height))
    img = process_image_studio(
        raw,
        width,
        height,
        fit=body.get("fit", "cover"),
        brightness=float(body.get("brightness", 1.0)),
        contrast=float(body.get("contrast", 1.0)),
        saturation=float(body.get("saturation", 1.0)),
        colors=int(body.get("colors", 64)),
        dither=bool(body.get("dither", False)),
        bg=_parse_hex_rgb(body.get("bg", "#000000")),
    )
    rgb = img.tobytes()
    ystp = encode_ystp01([rgb], width, height)
    est_s = state.transport.estimate_transfer_seconds(len(ystp)) if state.transport else round(len(ystp) / 3500.0, 2)
    out: Dict[str, Any] = {
        "ok": True,
        "ystp_bytes": len(ystp),
        "estimated_s": est_s,
        "frame_b64": base64.b64encode(rgb).decode("ascii"),
    }
    if body.get("send"):
        if not state.transport or not state.transport.is_connected:
            return web.json_response({"error": "Not connected to display"}, status=400)
        async with state.lock:
            wfs = build_image_program_frames(ystp, width, height, program_id=int(body.get("slot", 1)), start_sno=state.transport.next_sno())
            res = await state.transport.send_frames(wfs, label="Image Studio")
            out["wire_frames"] = len(wfs)
            out["last_ack"] = res[-1]
    return web.json_response(out)


async def handle_upload_video(req: web.Request) -> web.Response:
    body = await req.json()
    raw = base64.b64decode(body["data_b64"])
    filename = body.get("filename", "clip.mp4")
    width = int(body.get("width", state.width))
    height = int(body.get("height", state.height))
    try:
        gif_bytes = convert_video_to_gif(
            raw,
            filename,
            width,
            height,
            start_s=float(body.get("start_s", 0.0)),
            duration_s=float(body.get("duration_s", 3.0)),
            fps=int(body.get("fps", 10)),
        )
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)

    est_s = state.transport.estimate_transfer_seconds(len(gif_bytes)) if state.transport else round(len(gif_bytes) / 3500.0, 2)
    out: Dict[str, Any] = {
        "ok": True,
        "gif_bytes": len(gif_bytes),
        "estimated_s": est_s,
        "gif_b64": base64.b64encode(gif_bytes).decode("ascii"),
    }
    if body.get("send"):
        if not state.transport or not state.transport.is_connected:
            return web.json_response({"error": "Not connected to display"}, status=400)
        async with state.lock:
            wfs = build_gif_program_frames(gif_bytes, width, height, program_id=int(body.get("slot", 1)), start_sno=state.transport.next_sno())
            res = await state.transport.send_frames(wfs, label="Video GIF")
            out["wire_frames"] = len(wfs)
            out["last_ack"] = res[-1]
    return web.json_response(out)


async def handle_extract_palette(req: web.Request) -> web.Response:
    body = await req.json()
    raw = base64.b64decode(body["data_b64"])
    colors = extract_palette(raw, n_colors=int(body.get("colors", 12)))
    return web.json_response({"ok": True, "palette": colors})


async def handle_system_stats(_req: web.Request) -> web.Response:
    return web.json_response({
        "system": get_local_system_stats(),
        "now_playing": get_macos_now_playing(),
    })


async def handle_widget_frame(req: web.Request) -> web.Response:
    """Render a connection-dependent local live widget card (Pomodoro, System, Now Playing, Focus, Info)."""
    body = await req.json()
    kind = body.get("kind", "focus")
    width = int(body.get("width", state.width))
    height = int(body.get("height", state.height))
    fg = _parse_hex_rgb(body.get("fg", "#38bdf8"))
    accent = _parse_hex_rgb(body.get("accent", "#f43f5e"))
    bg = _parse_hex_rgb(body.get("bg", "#090b10"))

    img = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)

    if kind == "system":
        st = get_local_system_stats()
        _draw_centered_text(img, "SYSTEM", 5, (148, 163, 184), scale=1)
        _draw_pixel_text(img, f"CPU {st['cpu_pct']:2d}", 4, 18, fg, scale=1)
        draw.rectangle([4, 26, width - 5, 31], outline=(50, 60, 80))
        draw.rectangle([5, 27, 5 + int((width - 11) * st["cpu_pct"] / 100), 30], fill=fg)
        _draw_pixel_text(img, f"RAM {st['ram_pct']:2d}", 4, 38, accent, scale=1)
        draw.rectangle([4, 46, width - 5, 51], outline=(50, 60, 80))
        draw.rectangle([5, 47, 5 + int((width - 11) * st["ram_pct"] / 100), 50], fill=accent)
    elif kind == "now_playing":
        np = get_macos_now_playing()
        _draw_centered_text(img, np["app"][:10], 8, accent, scale=1)
        track = "".join(c for c in np["track"].upper() if c in _FONT_3X5)
        _draw_centered_text(img, track[:14] or "PAUSED", 26, fg, scale=1)
        _draw_centered_text(img, track[14:28], 38, (200, 210, 230), scale=1)
    elif kind == "pomodoro":
        rem = max(0, int(body.get("remaining_s", 1500)))
        tot = max(1, int(body.get("total_s", 1500)))
        label = (body.get("label") or "FOCUS").upper()[:10]
        mm, ss = divmod(rem, 60)
        _draw_centered_text(img, label, 8, accent, scale=1)
        _draw_centered_text(img, f"{mm:02d}:{ss:02d}", 24, fg, scale=2 if width >= 48 else 1)
        pct = rem / tot
        draw.rectangle([6, height - 12, width - 7, height - 7], outline=(55, 65, 85))
        draw.rectangle([7, height - 11, 7 + int((width - 15) * pct), height - 8], fill=accent)
    else:
        title = "".join(c for c in (body.get("title") or "FOCUS").upper() if c in _FONT_3X5)[:12]
        subtitle = "".join(c for c in (body.get("subtitle") or "DO NOT DISTURB").upper() if c in _FONT_3X5)[:14]
        draw.rounded_rectangle([2, 2, width - 3, height - 3], radius=6, outline=accent, width=2)
        _draw_centered_text(img, title, height // 2 - 10, fg, scale=1)
        _draw_centered_text(img, subtitle, height // 2 + 4, (220, 230, 245), scale=1)

    raw = img.tobytes()
    out: Dict[str, Any] = {"ok": True, "kind": kind, "frame_b64": base64.b64encode(raw).decode("ascii")}
    if body.get("send"):
        if not state.transport or not state.transport.is_connected:
            return web.json_response({"error": "Not connected to display"}, status=400)
        ystp = encode_ystp01([raw], width, height)
        async with state.lock:
            wfs = build_image_program_frames(ystp, width, height, program_id=int(body.get("slot", 1)), start_sno=state.transport.next_sno())
            res = await state.transport.send_frames(wfs, label=f"Widget: {kind}")
            out["wire_frames"] = len(wfs)
            out["last_ack"] = res[-1]
    return web.json_response(out)


async def _auto_reconnect_loop() -> None:
    """Background watchdog: automatically recovers BLE connection after Mac sleep/wake or display power-cycle."""
    while True:
        await asyncio.sleep(6.0)
        if not state.auto_reconnect or not state.last_ble_address:
            continue
        if state.mode == "ble" and state.transport and state.transport.is_connected:
            continue
        async with state.lock:
            if not state.auto_reconnect or (state.transport and state.transport.is_connected):
                continue
            try:
                t = BleTransport()
                state.attach_logger(t)
                await t.connect(state.last_ble_address)
                state.transport = t
                state.mode = "ble"
                state.address = state.last_ble_address
                state.device_name = state.last_ble_name or state.last_ble_address
                resp = await t.send_frame(cmd_get_dev_info_all(sno=t.next_sno()))
                if resp and resp.get("data"):
                    data = resp["data"]
                    if "dev_info" in data:
                        state.device_info = data["dev_info"].__dict__
                    if "param_dev" in data and data["param_dev"]:
                        state.width = data["param_dev"].width
                        state.height = data["param_dev"].height
                t.set_profile(recommend_profile(state.device_name or "", state.device_info.get("version", "")))
                state.broadcast_soon("status_update", state.status_dict())
            except Exception:
                pass


async def _work_scheduler_loop() -> None:
    """Periodically evaluate time & day work-mode schedule and auto-switch the display."""
    while True:
        await asyncio.sleep(20.0)
        try:
            sched = _load_work_schedule()
            if not sched.get("enabled", True):
                continue
            if not state.transport or not state.transport.is_connected:
                continue
            if getattr(state, "_context_override", None):
                continue
            now = datetime.datetime.now()
            matched = _match_schedule_rule(sched.get("rules", []), now)
            if not matched:
                continue
            rule_id = matched.get("id")
            if rule_id and rule_id != getattr(state, "last_applied_rule_id", None):
                await _apply_schedule_rule(matched)
        except Exception:
            pass


def _load_work_schedule() -> Dict[str, Any]:
    if SCHEDULE_FILE.exists():
        try:
            data = json.loads(SCHEDULE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("rules"), list):
                return data
        except Exception:
            pass
    data = json.loads(json.dumps(DEFAULT_WORK_SCHEDULE))
    _save_work_schedule(data)
    return data


def _save_work_schedule(data: Dict[str, Any]) -> None:
    SCHEDULE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _hhmm_to_min(hhmm: str) -> int:
    try:
        h, m = str(hhmm).strip().split(":")
        return max(0, min(1439, int(h) * 60 + int(m)))
    except Exception:
        return 0


def _match_schedule_rule(rules: List[Dict[str, Any]], now: Optional[datetime.datetime] = None) -> Optional[Dict[str, Any]]:
    now = now or datetime.datetime.now()
    day_key = DAY_KEYS[now.weekday()]
    prev_day_key = DAY_KEYS[(now.weekday() - 1) % 7]
    cur_min = now.hour * 60 + now.minute
    for rule in rules:
        if not rule.get("enabled", True):
            continue
        days = [str(d).lower() for d in rule.get("days", [])]
        s_min = _hhmm_to_min(rule.get("start", "00:00"))
        e_min = _hhmm_to_min(rule.get("end", "00:00"))
        if s_min <= e_min:
            if day_key in days and s_min <= cur_min < e_min:
                return rule
        else:
            # Overnight window (e.g., 22:30 -> 08:00)
            if (day_key in days and cur_min >= s_min) or (prev_day_key in days and cur_min < e_min):
                return rule
    return None


def _build_schedule_summary(now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """Compute what is scheduled right now and what comes up next for synced display across all devices."""
    now = now or datetime.datetime.now()
    sched = _load_work_schedule()
    rules = sched.get("rules", [])
    active = _match_schedule_rule(rules, now)
    day_key = DAY_KEYS[now.weekday()]
    cur_min = now.hour * 60 + now.minute
    next_rule = None
    best_delta = 10**9
    for r in rules:
        if not r.get("enabled", True):
            continue
        days = [str(d).lower() for d in r.get("days", [])]
        if day_key not in days:
            continue
        s_min = _hhmm_to_min(r.get("start", "00:00"))
        if s_min > cur_min and (s_min - cur_min) < best_delta:
            best_delta = s_min - cur_min
            next_rule = r
    return {
        "enabled": bool(sched.get("enabled", True)),
        "active": {"name": active["name"], "start": active["start"], "end": active["end"]} if active else None,
        "next": {"name": next_rule["name"], "start": next_rule["start"], "end": next_rule["end"]} if next_rule else None,
        "flashed_at": sched.get("flashed_at"),
        "flashed_count": sched.get("flashed_count", 0),
    }


async def handle_work_schedule_flash_device(req: web.Request) -> web.Response:
    """
    POST /api/work_schedule/flash_device — Store enabled schedule rules directly in the LED
    display's on-board flash memory (Slots 1..N) with TLV 0x16 (PERIOD_PRO) date/weekday/time
    windows so the display switches autonomously on its internal RTC even with Mac offline!
    """
    if not state.transport or not state.transport.is_connected:
        return web.json_response({"error": "Not connected to display"}, status=400)
    body = await req.json() if req.can_read_body else {}
    start_date = str(body.get("start_date") or datetime.date.today().isoformat()).strip()
    end_date = str(body.get("end_date") or "2099-12-31").strip()
    sched = _load_work_schedule()
    day_to_num = {k: i + 1 for i, k in enumerate(DAY_KEYS)}

    flashable = [
        r for r in sched.get("rules", [])
        if r.get("enabled", True) and r.get("kind") in ("gif", "hw_clock")
    ][:8]
    if not flashable:
        return web.json_response({"error": "No enabled GIF or Clock rules to flash"}, status=400)

    flashed: List[Dict[str, Any]] = []
    async with state.lock:
        t = state.transport
        await _ensure_exit_modal_mode()
        await t.send_frame(cmd_sync_time(sno=t.next_sno()))
        for idx, rule in enumerate(flashable):
            slot = idx + 1
            weekdays = [day_to_num[d.lower()] for d in rule.get("days", DAY_KEYS) if d.lower() in day_to_num] or [1, 2, 3, 4, 5, 6, 7]
            s_t = str(rule.get("start", "08:00"))
            e_t = str(rule.get("end", "18:00"))
            windows = [f"{s_t}-23:59", f"00:00-{e_t}"] if _hhmm_to_min(s_t) > _hhmm_to_min(e_t) else [f"{s_t}-{e_t}"]
            period_tlv = encode_period_pro_tlv(windows, weekdays=weekdays, start_date=start_date, end_date=end_date)

            kind = rule.get("kind")
            target = str(rule.get("target", ""))
            if kind == "gif":
                gif_path = GIFS_DIR / Path(target).name
                if not gif_path.is_file():
                    continue
                wire_frames = build_gif_program_frames(
                    gif_path.read_bytes(), state.width, state.height,
                    program_id=slot, start_sno=t.next_sno(), show_now=False, data_save=0,
                    period_pro_tlv=period_tlv,
                )
            else:
                fg = _parse_hex_rgb(rule.get("color", "#38bdf8"))
                accent = _parse_hex_rgb(rule.get("accent", "#ec4899"))
                bg_res = _render_hw_clock_shape_bg(state.width, state.height, target, fg, accent, (0, 0, 0))
                regions = _build_hw_clock_regions(target, state.width, state.height, fg, accent, (0, 0, 0))
                if regions is None:
                    continue
                wire_frames = build_multi_region_hw_clock_frames(
                    regions, state.width, state.height,
                    program_id=slot, start_sno=t.next_sno(), show_now=False, data_save=0,
                    background_resource=bg_res, period_pro_tlv=period_tlv,
                )
            await t.send_frames(wire_frames, label=f"Flash Slot {slot}: {rule.get('name', target)}")
            flashed.append({"slot": slot, "name": rule.get("name", target), "windows": windows})

        await t.send_frame(cmd_pgm_play(model=0, index=0, sno=t.next_sno()))

    sched["flashed_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    sched["flashed_count"] = len(flashed)
    _save_work_schedule(sched)
    state.set_now_playing(
        "schedule", f"On-Device Schedule ({len(flashed)} slots)",
        subtitle=f"{start_date} → {end_date} · Runs offline on display RTC",
        source="Device Flash",
    )
    return web.json_response({"ok": True, "flashed": flashed, "start_date": start_date, "end_date": end_date})


async def _apply_schedule_rule(rule: Dict[str, Any]) -> Dict[str, Any]:
    if not state.transport or not state.transport.is_connected:
        raise RuntimeError("Not connected to display")
    kind = str(rule.get("kind", "gif"))
    target = str(rule.get("target", ""))
    bri = rule.get("brightness")

    async with state.lock:
        t = state.transport
        if state.in_modal_mode:
            await t.send_frame(cmd_game_action("exit", sno=t.next_sno()))
            state.in_modal_mode = False
        if kind == "power_off":
            await t.send_frame(cmd_set_power(False, sno=t.next_sno()))
            state.power_on = False
        else:
            if not state.power_on:
                await t.send_frame(cmd_set_power(True, sno=t.next_sno()))
                state.power_on = True
            if bri is not None:
                lvl = max(1, min(15, int(bri)))
                await t.send_frame(cmd_set_brightness(lvl, sno=t.next_sno()))
                state.brightness = lvl
            if kind == "gif":
                path = GIFS_DIR / Path(target).name
                if not path.exists():
                    raise FileNotFoundError(f"Scheduled GIF not found: {target}")
                gif_bytes = path.read_bytes()
                wire_frames = build_gif_program_frames(
                    gif_bytes, state.width, state.height, program_id=1, start_sno=t.next_sno(), show_now=True
                )
                await t.send_frames(wire_frames, label=f"Schedule: {rule.get('name', target)}")
                await t.send_frame(cmd_pgm_play(model=1, index=0, sno=t.next_sno()))
            elif kind == "hw_clock":
                await t.send_frame(cmd_sync_time(sno=t.next_sno()))
                w, h = state.width, state.height
                shape = target or "hw_neon_ring"
                fg, accent, bg = (0, 240, 255), (255, 43, 214), (0, 0, 0)
                bg_resource = _render_hw_clock_shape_bg(w, h, shape, fg, accent, bg)
                regions = _build_hw_clock_regions(shape, w, h, fg, accent, bg) or [
                    {"template": "{hh}:{mm}", "x": 6, "y": 16, "w": w - 12, "h": 21, "color": fg, "bg_color": bg, "font_size": 16},
                    {"template": "{MM}/{DD}", "x": 10, "y": 37, "w": w - 20, "h": 16, "color": accent, "bg_color": bg, "font_size": 12},
                ]
                wire_frames = build_multi_region_hw_clock_frames(
                    regions, w, h, program_id=1, start_sno=t.next_sno(), show_now=True, background_resource=bg_resource
                )
                await t.send_frames(wire_frames, label=f"Schedule: {rule.get('name', shape)}")
                await t.send_frame(cmd_pgm_play(model=1, index=0, sno=t.next_sno()))
        state.last_applied_rule_id = rule.get("id")
        state.broadcast_soon("status_update", state.status_dict())
        state.broadcast_soon("schedule_update", {"active_rule_id": state.last_applied_rule_id, "rule": rule})
    return {"ok": True, "applied": rule}


SCHEDULE_PRESETS: Dict[str, List[Dict[str, Any]]] = {
    "developer": DEFAULT_WORK_SCHEDULE["rules"],
    "minimal_clock": [
        {"id": "mc_day", "enabled": True, "name": "Daytime Live Clock", "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"], "start": "08:00", "end": "20:00", "kind": "hw_clock", "target": "hw_neon_ring", "brightness": 12},
        {"id": "mc_eve", "enabled": True, "name": "Evening Dim Clock", "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"], "start": "20:00", "end": "23:00", "kind": "hw_clock", "target": "hw_minimal_big", "brightness": 5},
        {"id": "mc_off", "enabled": True, "name": "Night Power Off", "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"], "start": "23:00", "end": "08:00", "kind": "power_off", "target": "", "brightness": 2},
    ],
    "creator": [
        {"id": "cr_prep", "enabled": True, "name": "Morning Coffee", "days": ["mon", "tue", "wed", "thu", "fri"], "start": "08:30", "end": "10:00", "kind": "gif", "target": "13_mode_coffee_break.gif", "brightness": 10},
        {"id": "cr_air", "enabled": True, "name": "On Air / Recording", "days": ["mon", "tue", "wed", "thu", "fri"], "start": "10:00", "end": "13:00", "kind": "gif", "target": "11_mode_on_air_meeting.gif", "brightness": 14},
        {"id": "cr_edit", "enabled": True, "name": "Deep Focus Edit", "days": ["mon", "tue", "wed", "thu", "fri"], "start": "13:00", "end": "18:00", "kind": "gif", "target": "09_mode_deep_focus.gif", "brightness": 12},
        {"id": "cr_game", "enabled": True, "name": "Evening Gaming", "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"], "start": "18:00", "end": "23:00", "kind": "gif", "target": "15_mode_gaming_chill.gif", "brightness": 11},
        {"id": "cr_night", "enabled": True, "name": "Night / Done", "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"], "start": "23:00", "end": "08:30", "kind": "gif", "target": "18_mode_done_offline.gif", "brightness": 2},
    ],
}


async def handle_work_schedule(req: web.Request) -> web.Response:
    if req.method == "GET":
        sched = _load_work_schedule()
        now = datetime.datetime.now()
        matched = _match_schedule_rule(sched.get("rules", []), now)
        gifs = sorted(p.name for p in GIFS_DIR.glob("*.gif")) if GIFS_DIR.exists() else []
        return web.json_response({
            "enabled": bool(sched.get("enabled", True)),
            "rules": sched.get("rules", []),
            "matched_rule": matched,
            "active_rule_id": getattr(state, "last_applied_rule_id", None),
            "now": now.strftime("%a %H:%M"),
            "now_day": DAY_KEYS[now.weekday()],
            "now_minutes": now.hour * 60 + now.minute,
            "available_gifs": gifs,
        })
    body = await req.json()
    sched = _load_work_schedule()
    if "enabled" in body:
        sched["enabled"] = bool(body["enabled"])
    if isinstance(body.get("rules"), list):
        sched["rules"] = body["rules"]
    _save_work_schedule(sched)
    now = datetime.datetime.now()
    matched = _match_schedule_rule(sched.get("rules", []), now)
    return web.json_response({
        "ok": True,
        "enabled": sched["enabled"],
        "rules": sched["rules"],
        "matched_rule": matched,
        "now": now.strftime("%a %H:%M"),
        "now_day": DAY_KEYS[now.weekday()],
        "now_minutes": now.hour * 60 + now.minute,
    })


async def handle_work_schedule_apply(_req: web.Request) -> web.Response:
    sched = _load_work_schedule()
    matched = _match_schedule_rule(sched.get("rules", []))
    if not matched:
        return web.json_response({"error": "No schedule rule matches the current day & time"}, status=404)
    try:
        res = await _apply_schedule_rule(matched)
        return web.json_response(res)
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)


async def handle_work_schedule_test_rule(req: web.Request) -> web.Response:
    body = await req.json()
    rule = body.get("rule") if isinstance(body, dict) else None
    if not isinstance(rule, dict):
        return web.json_response({"error": "Missing rule object"}, status=400)
    try:
        res = await _apply_schedule_rule(rule)
        return web.json_response(res)
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)


async def handle_work_schedule_preset(req: web.Request) -> web.Response:
    body = await req.json()
    preset = str(body.get("preset", "developer"))
    if preset not in SCHEDULE_PRESETS:
        return web.json_response({"error": f"Unknown preset: {preset}"}, status=400)
    sched = _load_work_schedule()
    sched["rules"] = json.loads(json.dumps(SCHEDULE_PRESETS[preset]))
    _save_work_schedule(sched)
    now = datetime.datetime.now()
    return web.json_response({
        "ok": True,
        "preset": preset,
        "enabled": sched["enabled"],
        "rules": sched["rules"],
        "matched_rule": _match_schedule_rule(sched["rules"], now),
        "now": now.strftime("%a %H:%M"),
        "now_day": DAY_KEYS[now.weekday()],
        "now_minutes": now.hour * 60 + now.minute,
    })


def _load_smart_config() -> Dict[str, Any]:
    cfg = dict(DEFAULT_SMART_CONFIG)
    if SMART_CONFIG_FILE.exists():
        try:
            saved = json.loads(SMART_CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                cfg.update(saved)
        except Exception:
            pass
    return cfg


def _save_smart_config(cfg: Dict[str, Any]) -> None:
    SMART_CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


async def _flash_temporary_gif(gif_bytes: bytes, label: str, duration_s: float = 10.0, brightness: Optional[int] = None) -> Dict[str, Any]:
    """Upload a temporary alert GIF to Slot 2 (RAM-only) and automatically restore Slot 1 after duration_s."""
    if not state.transport or not state.transport.is_connected:
        raise RuntimeError("Not connected to display")

    prev_task = getattr(state, "_notify_restore_task", None)
    if prev_task and not prev_task.done():
        prev_task.cancel()

    prev_bri = state.brightness
    async with state.lock:
        t = state.transport
        await _ensure_exit_modal_mode(clear_temp_alert=False)
        if not state.power_on:
            await t.send_frame(cmd_set_power(True, sno=t.next_sno()))
            state.power_on = True
        if brightness is not None:
            await t.send_frame(cmd_set_brightness(max(1, min(15, int(brightness))), sno=t.next_sno()))
        wire_frames = build_gif_program_frames(
            gif_bytes, state.width, state.height, program_id=2, start_sno=t.next_sno(), show_now=True, data_save=1
        )
        res = await t.send_frames(wire_frames, label=label)
        await t.send_frame(cmd_pgm_play(model=1, index=1, sno=t.next_sno()))
        state._temp_alert_active = True

    async def _restore_slot1() -> None:
        try:
            await asyncio.sleep(max(1.0, float(duration_s)))
            if not state.transport or not state.transport.is_connected:
                state._temp_alert_active = False
                return
            async with state.lock:
                if not getattr(state, "_temp_alert_active", False):
                    return
                t2 = state.transport
                if brightness is not None:
                    await t2.send_frame(cmd_set_brightness(max(1, min(15, int(prev_bri))), sno=t2.next_sno()))
                await t2.send_frame(cmd_delete_programs([2], sno=t2.next_sno()))
                await t2.send_frame(cmd_dispatch_program(1, sno=t2.next_sno()))
                await t2.send_frame(cmd_pgm_play(model=1, index=0, sno=t2.next_sno()))
                state._temp_alert_active = False
        except asyncio.CancelledError:
            pass
        except Exception:
            state._temp_alert_active = False

    if duration_s > 0:
        state._notify_restore_task = asyncio.create_task(_restore_slot1())
    return {"ok": True, "wire_frames": len(wire_frames), "duration_s": duration_s, "last_ack": res[-1]}


async def handle_notify(req: web.Request) -> web.Response:
    """POST /api/notify — Flash a temporary 64x64 alert on the panel, then restore Slot 1."""
    body = await req.json() if req.can_read_body else {}
    title = str(body.get("title", "DONE")).strip()[:10]
    subtitle = str(body.get("subtitle", "")).strip()[:10]
    style = str(body.get("style", "success")).strip().lower()
    duration_s = float(body.get("duration_s", 10))
    bri = body.get("brightness")
    try:
        gif_bytes = render_notification_gif(title=title, subtitle=subtitle, style=style, size=state.width)
        res = await _flash_temporary_gif(gif_bytes, label=f"Alert: {title}", duration_s=duration_s, brightness=bri)
        return web.json_response(res)
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)


async def handle_hydration_now(req: web.Request) -> web.Response:
    """POST /api/hydration_now — Flash animated water-drop hydration reminder."""
    body = await req.json() if req.can_read_body else {}
    duration_s = float(body.get("duration_s", 12))
    try:
        gif_bytes = render_hydration_gif(size=state.width)
        res = await _flash_temporary_gif(gif_bytes, label="Hydration Reminder", duration_s=duration_s)
        return web.json_response(res)
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)


async def _upload_weather_clock(city: str, source: str = "App") -> Dict[str, Any]:
    if not state.transport or not state.transport.is_connected:
        raise RuntimeError("Not connected to display")
    wx = await asyncio.to_thread(fetch_open_meteo_weather, city)
    state._weather_cache = {**wx, "ts": round(time.time(), 1)}
    bg_gif = render_weather_clock_bg_gif(wx["temp_c"], wx["kind"], wx["city"], size=state.width)
    w, h = state.width, state.height
    regions = [
        {"template": "{hh}:{mm}", "x": 4, "y": 4, "w": w - 8, "h": 24, "color": (255, 255, 255), "bg_color": (0, 0, 0), "font_size": 16},
    ]
    async with state.lock:
        t = state.transport
        await _ensure_exit_modal_mode()
        await t.send_frame(cmd_sync_time(sno=t.next_sno()))
        wire_frames = build_multi_region_hw_clock_frames(
            regions, w, h, program_id=1, start_sno=t.next_sno(), show_now=True,
            background_resource=bg_gif, bg_mode="behind",
        )
        res = await t.send_frames(wire_frames, label=f"Weather: {wx['city']} {wx['temp_c']}C")
        await t.send_frame(cmd_pgm_play(model=1, index=0, sno=t.next_sno()))
    b64_prev = "data:image/gif;base64," + base64.b64encode(bg_gif).decode("ascii")
    state.set_now_playing(
        "clock", f"Weather Clock · {wx['city']} {wx['temp_c']}°C",
        subtitle=f"{wx['kind'].replace('_', ' ').title()} · via {source}",
        active_pin="pinWxClock", preview_url=b64_prev, source=source,
    )
    return {"ok": True, "weather": wx, "wire_frames": len(wire_frames), "last_ack": res[-1]}


async def _upload_calendar_clock(ics_url: str = "", calendar_name: str = "", source: str = "App") -> Dict[str, Any]:
    if not state.transport or not state.transport.is_connected:
        raise RuntimeError("Not connected to display")
    ev = await asyncio.to_thread(get_upcoming_calendar_event, 480, ics_url, calendar_name)
    bg_gif = render_calendar_clock_bg_gif(ev, size=state.width)
    w, h = state.width, state.height
    regions = [
        {"template": "{hh}:{mm}", "x": 4, "y": 4, "w": w - 8, "h": 24, "color": (255, 255, 255), "bg_color": (0, 0, 0), "font_size": 16},
    ]
    async with state.lock:
        t = state.transport
        await _ensure_exit_modal_mode()
        await t.send_frame(cmd_sync_time(sno=t.next_sno()))
        wire_frames = build_multi_region_hw_clock_frames(
            regions, w, h, program_id=1, start_sno=t.next_sno(), show_now=True,
            background_resource=bg_gif, bg_mode="behind",
        )
        lbl = f"Cal: {ev['title']} ({ev['minutes_left']}m)" if ev else "Cal Clock: Free"
        res = await t.send_frames(wire_frames, label=lbl)
        await t.send_frame(cmd_pgm_play(model=1, index=0, sno=t.next_sno()))
    b64_prev = "data:image/gif;base64," + base64.b64encode(bg_gif).decode("ascii")
    sub = f"Next: {ev['title']} in {ev['minutes_left']}m · via {source}" if ev else f"No meetings today · via {source}"
    state.set_now_playing(
        "clock", "Workday Calendar Clock",
        subtitle=sub, active_pin="pinCalClock", preview_url=b64_prev, source=source,
    )
    return {"ok": True, "event": ev, "wire_frames": len(wire_frames), "last_ack": res[-1]}


async def handle_calendar_clock(req: web.Request) -> web.Response:
    body = await req.json() if req.can_read_body else {}
    cfg = _load_smart_config()
    ics_url = str(body.get("ics_url") if "ics_url" in body else cfg.get("calendar_ics_url") or "").strip()
    cal_name = str(body.get("calendar_name") if "calendar_name" in body else cfg.get("calendar_name") or "").strip()
    changed = False
    if ics_url != cfg.get("calendar_ics_url"):
        cfg["calendar_ics_url"] = ics_url
        changed = True
    if cal_name != cfg.get("calendar_name"):
        cfg["calendar_name"] = cal_name
        changed = True
    if changed:
        _save_smart_config(cfg)
    try:
        res = await _upload_calendar_clock(ics_url, cal_name, source=_detect_req_source(req))
        return web.json_response(res)
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)


async def handle_auto_cycle(req: web.Request) -> web.Response:
    body = await req.json() if req.can_read_body else {}
    cfg = _load_smart_config()
    enabled = bool(body.get("enabled")) if "enabled" in body else not bool(cfg.get("auto_cycle_enabled"))
    cfg["auto_cycle_enabled"] = enabled
    _save_smart_config(cfg)
    state.broadcast_soon("status_update", state.status_dict())
    return web.json_response({"ok": True, "auto_cycle_enabled": enabled})


async def handle_calendars_list(req: web.Request) -> web.Response:
    req_access = req.query.get("request", "0") in ("1", "true", "yes")
    cals = await asyncio.to_thread(list_macos_calendars, request_access=req_access)
    auth = await asyncio.to_thread(get_macos_calendar_auth_status)
    return web.json_response({"calendars": cals, "auth_status": auth})


async def handle_weather_clock(req: web.Request) -> web.Response:
    body = await req.json() if req.can_read_body else {}
    cfg = _load_smart_config()
    city = str(body.get("city") or cfg.get("weather_city") or "Berlin").strip()
    if city != cfg.get("weather_city"):
        cfg["weather_city"] = city
        _save_smart_config(cfg)
    try:
        res = await _upload_weather_clock(city, source=_detect_req_source(req))
        return web.json_response(res)
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=500)


async def handle_smart_config(req: web.Request) -> web.Response:
    if req.method == "GET":
        cfg = _load_smart_config()
        ctx = await asyncio.to_thread(
            detect_mac_context,
            check_calendar=bool(cfg.get("calendar_alerts") or cfg.get("calendar_clock_enabled")),
            ics_url=str(cfg.get("calendar_ics_url") or ""),
            calendar_name=str(cfg.get("calendar_name") or ""),
        )
        cals = await asyncio.to_thread(list_macos_calendars, request_access=False)
        cal_auth = await asyncio.to_thread(get_macos_calendar_auth_status)
        return web.json_response({
            "config": cfg,
            "context": ctx,
            "calendars": cals,
            "calendar_auth": cal_auth,
            "active_override": getattr(state, "_context_override", None),
            "weather_cache": getattr(state, "_weather_cache", None),
        })
    body = await req.json()
    cfg = _load_smart_config()
    for k in DEFAULT_SMART_CONFIG:
        if k in body:
            cfg[k] = body[k]
    _save_smart_config(cfg)
    state.broadcast_soon("smart_config_update", cfg)
    state.broadcast_soon("status_update", state.status_dict())
    return web.json_response({"ok": True, "config": cfg})


async def _smart_watcher_loop() -> None:
    """Background watcher for Zero-Touch Mac Context, Hydration Reminders & Weather Refresh."""
    last_hydration_ts = time.monotonic()
    last_weather_ts = 0.0
    last_cal_clock_ts = 0.0
    last_cycle_ts = 0.0
    cycle_idx = 0
    alerted_events: Set[str] = set()

    while True:
        await asyncio.sleep(15.0)
        try:
            if not state.transport or not state.transport.is_connected:
                continue
            cfg = _load_smart_config()
            now_mono = time.monotonic()

            # 1. Zero-Touch Mac/Windows Context Awareness
            if cfg.get("context_enabled"):
                ctx = await asyncio.to_thread(
                    detect_mac_context,
                    check_calendar=False,
                )
                desired_override: Optional[str] = None
                target_gif: Optional[str] = None

                if cfg.get("auto_on_air") and ctx.get("in_call"):
                    desired_override = "on_air"
                    target_gif = "11_mode_on_air_meeting.gif"
                elif cfg.get("auto_idle_brb") and ctx.get("idle_seconds", 0) >= int(cfg.get("idle_minutes", 5)) * 60:
                    desired_override = "idle_brb"
                    target_gif = "17_mode_away_brb.gif"
                elif cfg.get("auto_coding_app") and ctx.get("is_coding"):
                    desired_override = "coding"
                    target_gif = "10_mode_coding.gif"

                cur_override = getattr(state, "_context_override", None)
                if desired_override != cur_override:
                    state._context_override = desired_override
                    if desired_override and target_gif:
                        await _apply_schedule_rule({
                            "id": f"ctx_{desired_override}",
                            "name": f"Auto: {desired_override}",
                            "kind": "gif",
                            "target": target_gif,
                            "brightness": 14 if desired_override == "on_air" else None,
                        })
                    else:
                        # Context override ended -> restore current scheduled rule if enabled
                        sched = _load_work_schedule()
                        matched = _match_schedule_rule(sched.get("rules", []))
                        if matched:
                            await _apply_schedule_rule(matched)

            # 1b. Independent Calendar 5-Min Meeting Alert (works even without Auto-Switch!)
            if cfg.get("calendar_alerts"):
                cal_ctx = await asyncio.to_thread(
                    detect_mac_context,
                    check_calendar=True,
                    ics_url=str(cfg.get("calendar_ics_url") or ""),
                    calendar_name=str(cfg.get("calendar_name") or ""),
                )
                ev = cal_ctx.get("next_event")
                if ev and 0 <= ev["minutes_left"] <= 5:
                    ev_key = f"{datetime.date.today()}:{ev['title']}"
                    if ev_key not in alerted_events:
                        alerted_events.add(ev_key)
                        gif_b = render_notification_gif("MEETING", f"IN {ev['minutes_left']}M", style="meeting", size=state.width)
                        await _flash_temporary_gif(gif_b, label="Calendar Alert", duration_s=12.0, brightness=14)

            # 2. Periodic Hydration Reminder
            if cfg.get("hydration_enabled"):
                interval_s = max(5, int(cfg.get("hydration_interval_min", 60))) * 60
                if now_mono - last_hydration_ts >= interval_s:
                    last_hydration_ts = now_mono
                    gif_b = render_hydration_gif(size=state.width)
                    await _flash_temporary_gif(gif_b, label="Hydration Reminder", duration_s=12.0)

            # 3. Periodic Weather Clock Auto-Refresh (every 30 min)
            if cfg.get("weather_enabled") and not getattr(state, "_context_override", None):
                if now_mono - last_weather_ts >= 1800.0:
                    last_weather_ts = now_mono
                    await _upload_weather_clock(str(cfg.get("weather_city", "Berlin")))

            # 4. Workday Calendar + RTC Clock Auto-Refresh (Mon-Fri 08:00-19:00, every 5 min)
            if cfg.get("calendar_clock_enabled") and not cfg.get("auto_cycle_enabled") and not getattr(state, "_context_override", None):
                now_dt = datetime.datetime.now()
                if now_dt.weekday() < 5 and 8 <= now_dt.hour < 19:
                    if now_mono - last_cal_clock_ts >= 300.0:
                        last_cal_clock_ts = now_mono
                        await _upload_calendar_clock(
                            str(cfg.get("calendar_ics_url") or ""),
                            str(cfg.get("calendar_name") or ""),
                        )

            # 5. One-Tap Auto-Cycle (rotates Calendar Clock -> Weather Clock -> Synthwave every 5 min)
            if cfg.get("auto_cycle_enabled") and not getattr(state, "_context_override", None):
                if now_mono - last_cycle_ts >= 300.0:
                    last_cycle_ts = now_mono
                    step = cycle_idx % 3
                    cycle_idx += 1
                    if step == 0:
                        await _upload_calendar_clock(str(cfg.get("calendar_ics_url") or ""), str(cfg.get("calendar_name") or ""))
                    elif step == 1:
                        await _upload_weather_clock(str(cfg.get("weather_city", "Berlin")))
                    else:
                        await _apply_schedule_rule({"id": "cycle_synth", "name": "Synthwave", "kind": "hw_clock", "target": "hw_synthwave_sun", "color": "#38bdf8", "accent": "#ec4899"})
        except Exception:
            pass


async def _start_bg_tasks(app: web.Application) -> None:
    app["reconnect_task"] = asyncio.create_task(_auto_reconnect_loop())
    app["scheduler_task"] = asyncio.create_task(_work_scheduler_loop())
    app["smart_task"] = asyncio.create_task(_smart_watcher_loop())


async def _stop_bg_tasks(app: web.Application) -> None:
    for key in ("reconnect_task", "scheduler_task", "smart_task"):
        task = app.get(key)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


def create_app() -> web.Application:
    app = web.Application(client_max_size=32 * 1024 * 1024, middlewares=[local_auth_middleware])
    app.on_startup.append(_start_bg_tasks)
    app.on_cleanup.append(_stop_bg_tasks)
    app.router.add_get("/", handle_index)
    app.router.add_get("/ws", handle_ws)
    app.router.add_static("/static", STATIC_DIR)
    if GIFS_DIR.exists():
        app.router.add_static("/gifs", GIFS_DIR)
    app.router.add_get("/api/auth/status", handle_auth_status)
    app.router.add_post("/api/auth/login", handle_auth_login)
    app.router.add_post("/api/auth/set_password", handle_auth_set_password)
    app.router.add_post("/api/auth/logout", handle_auth_logout)
    app.router.add_get("/api/status", handle_status)
    app.router.add_get("/api/phone_connect", handle_phone_connect)
    app.router.add_post("/api/phone_connect/rotate", handle_rotate_token)
    app.router.add_get("/api/packets", handle_packets)
    app.router.add_get("/api/transfer", handle_transfer_status)
    app.router.add_post("/api/transfer/cancel", handle_transfer_cancel)
    app.router.add_get("/api/ble_profile", handle_ble_profile)
    app.router.add_post("/api/ble_profile", handle_ble_profile)
    app.router.add_post("/api/benchmark", handle_benchmark)
    app.router.add_get("/api/diagnostics", handle_diagnostics)
    app.router.add_get("/api/scan", handle_scan)
    app.router.add_post("/api/connect", handle_connect)
    app.router.add_post("/api/disconnect", handle_disconnect)
    app.router.add_post("/api/control", handle_control)
    app.router.add_post("/api/upload_frames", handle_upload_frames)
    app.router.add_post("/api/upload_media", handle_upload_media)
    app.router.add_post("/api/upload_sequence", handle_upload_sequence)
    app.router.add_post("/api/process_image", handle_process_image)
    app.router.add_post("/api/upload_video", handle_upload_video)
    app.router.add_post("/api/extract_palette", handle_extract_palette)
    app.router.add_get("/api/procedural", handle_procedural)
    app.router.add_post("/api/procedural", handle_procedural)
    app.router.add_post("/api/avatar", handle_avatar)
    app.router.add_get("/api/system_stats", handle_system_stats)
    app.router.add_post("/api/widget_frame", handle_widget_frame)
    app.router.add_post("/api/export_gif", handle_export_gif)
    app.router.add_post("/api/custom_clock", handle_custom_clock)
    app.router.add_post("/api/hw_text_clock", handle_hw_text_clock)
    app.router.add_get("/api/hw_clock_preview/{shape}", handle_hw_clock_preview)
    app.router.add_post("/api/qrcode", handle_qrcode)
    app.router.add_get("/api/gif_search", handle_gif_search)
    app.router.add_post("/api/gif_fetch_send", handle_gif_fetch_send)
    app.router.add_get("/api/gif_pack", handle_gif_pack_list)
    app.router.add_post("/api/gif_pack/send", handle_gif_pack_send)
    app.router.add_post("/api/send_text", handle_send_text)
    app.router.add_get("/api/library", handle_library_get)
    app.router.add_post("/api/library", handle_library_save)
    app.router.add_delete("/api/library/{id}", handle_library_delete)
    app.router.add_get("/api/work_schedule", handle_work_schedule)
    app.router.add_post("/api/work_schedule", handle_work_schedule)
    app.router.add_post("/api/work_schedule/apply", handle_work_schedule_apply)
    app.router.add_post("/api/work_schedule/test_rule", handle_work_schedule_test_rule)
    app.router.add_post("/api/work_schedule/preset", handle_work_schedule_preset)
    app.router.add_post("/api/work_schedule/flash_device", handle_work_schedule_flash_device)
    app.router.add_post("/api/notify", handle_notify)
    app.router.add_post("/api/hydration_now", handle_hydration_now)
    app.router.add_post("/api/weather_clock", handle_weather_clock)
    app.router.add_post("/api/calendar_clock", handle_calendar_clock)
    app.router.add_post("/api/auto_cycle", handle_auto_cycle)
    app.router.add_get("/api/calendars", handle_calendars_list)
    app.router.add_get("/api/smart_config", handle_smart_config)
    app.router.add_post("/api/smart_config", handle_smart_config)
    return app


def _is_port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def _describe_port_owner(port: int) -> str:
    try:
        out = subprocess.check_output(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        lines = out.splitlines()
        if len(lines) > 1:
            return lines[1]
    except Exception:
        pass
    return "unknown process"


def resolve_port(host: str, requested_port: int, *, strict: bool = False, max_tries: int = 20) -> int:
    if _is_port_free(host, requested_port):
        return requested_port

    owner = _describe_port_owner(requested_port)
    print(f"[!] Port {requested_port} on {host} is already in use ({owner}).", file=sys.stderr)
    if strict:
        raise RuntimeError(f"Port {requested_port} is already in use and --strict-port was specified.")

    for candidate in range(requested_port + 1, requested_port + 1 + max_tries):
        if _is_port_free(host, candidate):
            print(f"[i] Falling back to next free port: {candidate}", file=sys.stderr)
            return candidate
    raise RuntimeError(f"No free port found in range {requested_port}..{requested_port + max_tries}")


def main() -> None:
    parser = argparse.ArgumentParser(description="LOY Studio 2.0 — Local Mac BLE Gateway & Web Studio")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default 127.0.0.1)")
    parser.add_argument("--lan", action="store_true", help="Enable local Wi-Fi access (binds 0.0.0.0 with pairing token)")
    parser.add_argument("--port", type=int, default=18420, help="Bind port (default 18420)")
    parser.add_argument("--strict-port", action="store_true", help="Exit with error instead of picking the next free port")
    args = parser.parse_args()
    host = "0.0.0.0" if args.lan else args.host
    state.lan_enabled = args.lan or (host == "0.0.0.0")
    port = resolve_port(host, args.port, strict=args.strict_port)
    state.bound_port = port
    print(f"LOY Studio 2.0 running locally at http://127.0.0.1:{port}")
    if state.lan_enabled:
        lan_ip = _detect_lan_ip()
        print(f"Phone / LAN access enabled (password protected): http://{lan_ip}:{port}")
    web.run_app(create_app(), host=host, port=port)


if __name__ == "__main__":
    main()
