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
import functools
import hashlib
import io
import ipaddress
import json
import logging
import math
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Set

from aiohttp import ClientSession, ClientTimeout, WSMsgType, web
from PIL import Image, ImageDraw, ImageSequence

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from protocol import (
    ALL_PROFILES,
    EXPERIMENTAL_PROFILES,
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
    cmd_format_flash,
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
from app.catalog import CATEGORIES, gif_meta
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
from app.security import LanAuthStore, LoginRateLimiter, host_allowed, same_origin
from app.storage import atomic_write_json, read_json
from app.update_signing import verify_release
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

log = logging.getLogger("loy.server")

BUNDLE_ROOT = Path(getattr(sys, "_MEIPASS", PROJECT_ROOT))
STATIC_DIR = BUNDLE_ROOT / "app" / "static"
GIFS_DIR = BUNDLE_ROOT / "gifs"


def _resolve_data_dir() -> Path:
    """Use a persistent data folder outside the .app bundle when frozen, or `app/` in dev."""
    override = os.environ.get("LOY_DATA_DIR", "").strip()
    if override:  # tests / custom installs
        target = Path(override).expanduser()
        target.mkdir(parents=True, exist_ok=True)
        return target
    if getattr(sys, "frozen", False):
        exe_path = Path(sys.executable).resolve()
        app_bundle = next((p for p in exe_path.parents if p.suffix == ".app"), None)
        if sys.platform == "darwin" and app_bundle is not None:
            # Always keep macOS user data outside the .app bundle so in-app updates never wipe it
            # and macOS App Translocation / codesign never breaks.
            target = Path.home() / "Library" / "Application Support" / "LOY Studio"
            try:
                target.mkdir(parents=True, exist_ok=True)
            except OSError:
                target = Path.home() / ".loy-studio"
                target.mkdir(parents=True, exist_ok=True)
            # One-time migration from legacy inside-bundle LOY-Studio-Data if present
            legacy = exe_path.parent / "LOY-Studio-Data"
            if legacy.is_dir() and not any(target.iterdir()):
                try:
                    for item in legacy.iterdir():
                        dst = target / item.name
                        if not dst.exists():
                            if item.is_dir():
                                shutil.copytree(item, dst)
                            else:
                                shutil.copy2(item, dst)
                except Exception:
                    pass
            return target
        candidate = exe_path.parent / "LOY-Studio-Data"
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
LAST_DEVICE_FILE = DATA_DIR / ".last_device.json"
SCHEDULE_FILE = DATA_DIR / "work_schedule.json"
SMART_CONFIG_FILE = DATA_DIR / "smart_config.json"
LAN_AUTH_FILE = DATA_DIR / ".lan_auth.json"
GH_TOKEN_FILE = DATA_DIR / ".github_token"
NOW_PLAYING_FILE = DATA_DIR / ".now_playing.json"
DISPLAY_STATE_FILE = DATA_DIR / ".display_state.json"
USER_GIFS_DIR = DATA_DIR / "user_gifs"  # GIFs saved by the user (never inside the read-only app bundle)

# ---------------------------------------------------------------------------
# Program slot map (CLAUDE.md constraint 3). The ONLY place slot numbers are defined.
#   1 <-> 2 : live double-buffer (upload hidden slot, then switch atomically)
#   3       : RAM-only temporary alerts (Drink Water, webhooks, meetings)
#   4..8    : on-device Flash schedule programs (TLV 0x16 PERIOD_PRO)
#   9       : RAM-only throughput benchmark scratch slot
# ---------------------------------------------------------------------------
LIVE_SLOTS = (1, 2)
ALERT_SLOT = 3
SCHEDULE_SLOTS = (4, 5, 6, 7, 8)
BENCHMARK_SLOT = 9
# Live Slots 1<->2 MUST stay Flash (data_save=0), automatic refreshes included.
# HARDWARE FINDING (EXYC-A V2.8.7, 2026-11-26): writing a RAM program (data_save=1) over a
# slot that holds a Flash program leaves a dead entry in the panel's program list. Because
# pgm_play(model=1, index) selects by LIST POSITION, every later switch then plays the
# wrong program (or nothing) until the panel's programs are wiped. RAM is only safe for the
# alert Slot 3, which is deleted before and after each alert.
AUTO_REFRESH_PERSIST = True
MAX_DIM = 128
MAX_FRAMES = 120
# On-device Flash schedules (TLV 0x16) are OFF by default. HARDWARE FINDING (EXYC-A V2.8.7,
# 2026-11-26): after flashing two GIF programs with PERIOD_PRO and switching to sequence
# playback, the panel showed its factory logo during an active window, and afterwards
# Slot 2 switching stayed broken even after delete_all and a power cycle. Opt in with
# LOY_EXPERIMENTAL_DEVICE_SCHEDULE=1 only for protocol research.
DEVICE_SCHEDULE_ENABLED = os.environ.get("LOY_EXPERIMENTAL_DEVICE_SCHEDULE", "").strip() == "1"
DEVICE_SCHEDULE_DISABLED_MSG = (
    "On-device schedules are turned off: on this display firmware they show the startup logo and "
    "disturb normal switching. Use the Mac schedule instead (it runs while LOY Studio is open)."
)
APP_VERSION = "4.4.7"
GITHUB_REPO = "WahdanZ/loy-studio"


LAN_AUTH = LanAuthStore(LAN_AUTH_FILE)
LOGIN_LIMITER = LoginRateLimiter()

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
    # Off until the user turns it on: a fresh install must not override what they just picked.
    "enabled": False,
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


_VPN_IFACE_PREFIXES = (
    "lo", "utun", "tun", "tap", "ppp", "ipsec", "wg", "tailscale",
    "zt", "awdl", "llw", "anpi", "bridge", "gif", "stf",
    "vmnet", "veth", "docker", "br-", "virbr", "vboxnet",
)


def _is_usable_lan_ipv4(ip: str) -> bool:
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    try:
        a, b, c, d = (int(p) for p in parts)
    except ValueError:
        return False
    if not all(0 <= x <= 255 for x in (a, b, c, d)):
        return False
    if a in (0, 127):  # loopback / unspecified
        return False
    if a == 169 and b == 254:  # link-local
        return False
    if a == 100 and 64 <= b <= 127:  # CGNAT / Tailscale
        return False
    return True


def _pick_best_lan_ip(candidates: List[tuple[str, str]]) -> Optional[str]:
    """Pick the best physical LAN IPv4 from (iface_name, ip) pairs, skipping VPN/virtual tunnels."""
    scored: List[tuple[int, str]] = []
    for iface, ip in candidates:
        name = iface.lower().strip()
        if any(name.startswith(pfx) for pfx in _VPN_IFACE_PREFIXES):
            continue
        if not _is_usable_lan_ipv4(ip):
            continue
        # Lower score = higher priority
        if name == "en0":
            prio = 0
        elif name.startswith(("en", "eth", "wlan", "wlp", "wl")):
            prio = 10
        else:
            prio = 30
        # Within same interface tier, prefer home/office LAN ranges (192.168.*, 172.16-31.*) over 10.*
        if ip.startswith("192.168."):
            prio += 0
        elif ip.startswith("172.") and 16 <= int(ip.split(".")[1]) <= 31:
            prio += 1
        elif ip.startswith("10."):
            prio += 2
        else:
            prio += 5
        scored.append((prio, ip))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0])
    return scored[0][1]


def _detect_lan_ip() -> str:
    """Detect the real local Wi-Fi / Ethernet LAN IP, ignoring VPN tunnels (utun/tun/10.10.*)."""
    candidates: List[tuple[str, str]] = []

    if sys.platform == "darwin":
        # 1. Fast path: macOS ipconfig for physical Wi-Fi / Ethernet interfaces (en0..en6)
        for iface in ("en0", "en1", "en2", "en3", "en4", "en5", "en6"):
            try:
                out = subprocess.check_output(
                    ["/usr/sbin/ipconfig", "getifaddr", iface],
                    text=True,
                    stderr=subprocess.DEVNULL,
                    timeout=1.0,
                ).strip()
                if out:
                    candidates.append((iface, out))
            except Exception:
                continue
        best = _pick_best_lan_ip(candidates)
        if best:
            return best
        # 2. Fallback: parse ifconfig output excluding VPN/virtual interfaces
        try:
            out = subprocess.check_output(["/sbin/ifconfig"], text=True, stderr=subprocess.DEVNULL, timeout=1.5)
            cur_iface = ""
            for line in out.splitlines():
                if line and not line[0].isspace() and ":" in line:
                    cur_iface = line.split(":", 1)[0].strip()
                elif cur_iface and "\tinet " in line:
                    m = re.search(r"\binet (\d+\.\d+\.\d+\.\d+)\b", line)
                    if m:
                        candidates.append((cur_iface, m.group(1)))
            best = _pick_best_lan_ip(candidates)
            if best:
                return best
        except Exception:
            pass
    elif sys.platform.startswith("linux"):
        try:
            out = subprocess.check_output(
                ["ip", "-4", "-o", "addr", "show", "scope", "global"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=1.5,
            )
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 4:
                    candidates.append((parts[1], parts[3].split("/", 1)[0]))
            best = _pick_best_lan_ip(candidates)
            if best:
                return best
        except Exception:
            pass
    else:
        # Windows / generic: inspect local host addresses first
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                candidates.append(("lan", info[4][0]))
            best = _pick_best_lan_ip(candidates)
            if best:
                return best
        except OSError:
            pass

    # Final fallback: UDP route probe preferring 192.168.* over 10.* VPN routes
    for target in ("192.168.255.255", "172.16.255.255", "10.255.255.255"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect((target, 1))
                ip = s.getsockname()[0]
                if _is_usable_lan_ipv4(ip):
                    return ip
        except OSError:
            continue
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
            "overlay_clock": False,
            "source": "",
            "ts": 0.0,
        }
        self._load_now_playing()
        self.lock = asyncio.Lock()
        self.ws_clients: Dict[web.WebSocketResponse, Dict[str, Any]] = {}
        self.lan_enabled: bool = False
        self.bound_port: int = 18420
        self.auto_reconnect: bool = False
        self.in_modal_mode: bool = False  # True when panel is in built-in Clock/Timer/Scoreboard/Game mode
        self.active_slot: int = LIVE_SLOTS[0]  # visible live slot (1 <-> 2 ping-pong)
        self.temp_alert_active: bool = False  # RAM Slot 3 alert currently on screen
        self.alert_brightness_changed: bool = False
        self.device_schedule_active: bool = False  # on-device Flash schedule currently driving the panel
        self.notify_restore_task: Optional[asyncio.Task] = None
        self.context_override: Optional[str] = None
        self.weather_cache: Optional[Dict[str, Any]] = None
        self.last_applied_rule_id: Optional[str] = None
        self.last_ble_address: Optional[str] = None
        self.last_ble_name: Optional[str] = None
        self._load_last_device()
        self._load_display_state()

    def _load_display_state(self) -> None:
        """Restore what the panel was showing, so the first upload after a restart never hits the visible slot."""
        d = read_json(DISPLAY_STATE_FILE, dict, validate=lambda x: isinstance(x, dict))
        slot = d.get("active_slot")
        if slot in LIVE_SLOTS:
            self.active_slot = slot
        self.in_modal_mode = bool(d.get("in_modal_mode", False))
        self.temp_alert_active = bool(d.get("temp_alert_active", False))
        self.alert_brightness_changed = bool(d.get("alert_brightness_changed", False))
        self.device_schedule_active = bool(d.get("device_schedule_active", False))

    def save_display_state(self) -> None:
        try:
            atomic_write_json(DISPLAY_STATE_FILE, {
                "active_slot": self.active_slot,
                "in_modal_mode": self.in_modal_mode,
                "temp_alert_active": self.temp_alert_active,
                "alert_brightness_changed": self.alert_brightness_changed,
                "device_schedule_active": self.device_schedule_active,
            })
        except OSError:
            log.exception("Could not save display state")

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
            atomic_write_json(LAST_DEVICE_FILE, {"address": address, "name": name}, indent=None)
        except OSError:
            log.exception("Could not save last BLE device")

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

    def next_back_slot(self) -> int:
        """Hidden back-buffer slot (1 <-> 2). Only call while holding `self.lock`."""
        return LIVE_SLOTS[1] if self.active_slot == LIVE_SLOTS[0] else LIVE_SLOTS[0]

    def _load_now_playing(self) -> None:
        if NOW_PLAYING_FILE.exists():
            try:
                d = json.loads(NOW_PLAYING_FILE.read_text(encoding="utf-8"))
                if isinstance(d, dict) and d.get("title"):
                    self.now_playing.update(d)
            except Exception:
                pass

    def set_now_playing(
        self,
        kind: str,
        title: str,
        *,
        subtitle: str = "",
        active_pin: str = "",
        preview_url: str = "",
        overlay_clock: bool = False,
        source: str = "App",
        record: bool = True,
    ) -> None:
        self.now_playing = {
            "kind": kind,
            "title": title,
            "subtitle": subtitle,
            "active_pin": active_pin,
            "preview_url": preview_url,
            "overlay_clock": overlay_clock,
            "source": source,
            "ts": round(time.time(), 1),
        }
        try:
            atomic_write_json(NOW_PLAYING_FILE, self.now_playing, indent=None)
        except OSError:
            log.exception("Could not save now playing")
        if record:
            tstr = datetime.datetime.now().strftime("%H:%M:%S")
            self.recent_sent.appendleft({"time": tstr, "title": title, "source": source})
        self.broadcast_soon("status_update", self.status_dict())

    def status_dict(self) -> Dict[str, Any]:
        cfg = _load_smart_config() if SMART_CONFIG_FILE.exists() else DEFAULT_SMART_CONFIG
        return {
            "connected": bool(self.transport and self.transport.is_connected),
            "app_version": APP_VERSION,
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
    hdr = re.sub(r"[^A-Za-z0-9 ._-]", "", req.headers.get("X-LOY-Source") or "").strip()
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
    if _is_loopback_req(req):
        return "Mac"
    return "Wi-Fi"


state = ControllerState()


def _parse_hex_rgb(hex_color: str) -> tuple[int, int, int]:
    c = hex_color.strip().lstrip("#")
    if len(c) != 6:
        raise ValueError(f"Invalid hex color: {hex_color}")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _resolve_gif(name: str) -> Optional[Path]:
    """Find a GIF by bare file name in the bundled pack or the user's saved GIFs (no path traversal)."""
    fname = Path(str(name or "")).name
    if not fname:
        return None
    for base in (GIFS_DIR, USER_GIFS_DIR):
        cand = base / fname
        if cand.is_file():
            return cand
    return None


def _gif_url(path: Path) -> str:
    return f"/user_gifs/{path.name}" if path.parent == USER_GIFS_DIR else f"/gifs/{path.name}"


def _all_gif_paths() -> List[Path]:
    seen: Dict[str, Path] = {}
    for base in (GIFS_DIR, USER_GIFS_DIR):
        if base.exists():
            for g in sorted(base.glob("*.gif")):
                seen.setdefault(g.name, g)
    return list(seen.values())


async def handle_index(_req: web.Request) -> web.StreamResponse:
    """Serve LOY Studio shell cleanly (no URL pairing tokens)."""
    return web.FileResponse(STATIC_DIR / "index.html")


def _is_loopback_req(req: web.Request) -> bool:
    peer = req.remote or ""
    return peer in ("127.0.0.1", "::1", "::ffff:127.0.0.1") or peer.startswith("127.")


def _req_token(req: web.Request) -> str:
    tok = req.headers.get("X-LOY-Token") or req.cookies.get("loy_session") or ""
    # Query-string tokens leak into logs/history; only accept them for the WebSocket upgrade,
    # where browsers cannot set custom headers.
    if not tok and req.path == "/ws":
        tok = req.query.get("token", "")
    return tok


def _is_lan_authenticated(req: web.Request) -> bool:
    if _is_loopback_req(req):
        return True
    return LAN_AUTH.validate_session(_req_token(req))


# Paths a phone on Wi-Fi may never call, even when signed in: they change who can access
# the Mac or replace the app itself.
LOOPBACK_ONLY_PREFIXES = (
    "/api/display/repair",
    "/api/auth/set_password",
    "/api/auth/sessions",
    "/api/update/",
)
PUBLIC_PATHS = ("/", "/api/auth/status", "/api/auth/login", "/api/auth/logout")
_MUTATING_METHODS = ("POST", "PUT", "PATCH", "DELETE")


CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob: https:; connect-src 'self' ws: wss:; worker-src 'self'; "
    "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)


@web.middleware
async def errors_and_headers_middleware(req: web.Request, handler):
    """Outermost middleware: map bad input to 400 JSON (no tracebacks) and add security headers."""
    try:
        resp = await handler(req)
    except web.HTTPException as exc:
        resp = exc
    except DisplayNotConnected as exc:
        resp = web.json_response({"error": str(exc)}, status=400)
    except (ValueError, KeyError, TypeError) as exc:
        # json.JSONDecodeError is a ValueError
        log.info("Bad request to %s: %r", req.path, exc)
        msg = f"Missing field: {exc}" if isinstance(exc, KeyError) else str(exc)
        resp = web.json_response({"error": msg or "Bad request"}, status=400)
    except Exception as exc:
        log.exception("Unhandled error in %s %s", req.method, req.path)
        resp = web.json_response({"error": f"Internal error: {exc}"}, status=500)
    if not resp.prepared:
        resp.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("X-Frame-Options", "DENY")
    if isinstance(resp, web.HTTPException):
        raise resp
    return resp


@web.middleware
async def local_auth_middleware(req: web.Request, handler):
    """
    Request guard for every endpoint:
      1. Host allowlist (blocks DNS-rebinding from malicious websites).
      2. Origin must match Host when a browser sends one (blocks CSRF / cross-site WebSocket).
      3. Mutating requests with a body must be JSON (forces a CORS preflight for browsers).
      4. Loopback-only paths (password, sessions, updater) are refused from the LAN.
      5. Non-loopback clients need a valid per-device session token.
    """
    if not host_allowed(req.host):
        return web.json_response({"error": "Forbidden host"}, status=403)
    origin = req.headers.get("Origin")
    if origin is not None and not same_origin(origin, req.host):
        return web.json_response({"error": "Cross-origin request blocked"}, status=403)
    if req.method in _MUTATING_METHODS and req.body_exists and req.content_type != "application/json":
        return web.json_response({"error": "Content-Type must be application/json"}, status=415)

    loopback = _is_loopback_req(req)
    if not loopback and req.path.startswith(LOOPBACK_ONLY_PREFIXES):
        return web.json_response(
            {"error": "This action is only available on the Mac running LOY Studio."}, status=403
        )
    if (
        loopback
        or req.path in PUBLIC_PATHS
        or req.path.startswith("/static/")
        or req.path.startswith("/gifs/")
        or req.path.startswith("/user_gifs/")
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
    pwd_set = LAN_AUTH.password_set
    return web.json_response({
        "authenticated": _is_lan_authenticated(req),
        "is_local": _is_loopback_req(req),
        "password_enabled": pwd_set,
        "password_set": pwd_set,
        "setup_required": not pwd_set,
        "is_default_password": False,
    })


async def handle_auth_login(req: web.Request) -> web.Response:
    ip = req.remote or "?"
    wait = LOGIN_LIMITER.retry_after(ip)
    if wait > 0:
        return web.json_response(
            {"error": f"Too many attempts. Try again in {int(wait) + 1}s."},
            status=429,
            headers={"Retry-After": str(int(wait) + 1)},
        )
    if not LAN_AUTH.password_set:
        return web.json_response(
            {
                "error": "Phone access is locked. Set a Wi-Fi password on your Mac first (Device → Phone Access).",
                "setup_required": True,
            },
            status=403,
        )
    try:
        body = await req.json()
    except Exception:
        body = {}
    password = str(body.get("password", ""))
    # PBKDF2 is deliberately slow: keep it off the event loop
    ok = await asyncio.to_thread(LAN_AUTH.verify_password, password)
    if not ok:
        LOGIN_LIMITER.record_failure(ip)
        return web.json_response({"error": "Incorrect password"}, status=401)
    LOGIN_LIMITER.record_success(ip)
    token = LAN_AUTH.create_session(ip, req.headers.get("User-Agent", ""))
    resp = web.json_response({"ok": True, "token": token})
    resp.set_cookie("loy_session", token, httponly=True, samesite="Strict", max_age=86400 * 30)
    return resp


async def handle_auth_set_password(req: web.Request) -> web.Response:
    """Loopback-only (enforced by middleware). Empty password = lock phone access."""
    body = await req.json()
    new_pwd = str(body.get("password", "")).strip()
    try:
        await asyncio.to_thread(LAN_AUTH.set_password, new_pwd)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    # Every phone session was revoked: disconnect their live WebSockets too
    for ws, info in list(state.ws_clients.items()):
        if not info.get("loopback"):
            await ws.close(code=4401, message=b"password changed")
    return web.json_response({"ok": True, "password_enabled": LAN_AUTH.password_set})


async def handle_auth_sessions(req: web.Request) -> web.Response:
    """Loopback-only: list signed-in phones (GET) or revoke one (POST {"id": ...})."""
    if req.method == "POST":
        body = await req.json()
        ok = LAN_AUTH.revoke_session_id(str(body.get("id", "")))
        return web.json_response({"ok": ok, "sessions": LAN_AUTH.list_sessions()})
    return web.json_response({"sessions": LAN_AUTH.list_sessions()})


async def handle_auth_logout(req: web.Request) -> web.Response:
    tok = _req_token(req)
    if tok:
        LAN_AUTH.revoke_session(tok)
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
        "loopback": _is_loopback_req(req),
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
    return web.json_response({
        "lan_enabled": state.lan_enabled,
        "lan_ip": lan_ip,
        "port": port,
        "url": url,
        "base_url": url,
        "password_enabled": LAN_AUTH.password_set,
        "password_set": LAN_AUTH.password_set,
        "is_default_password": False,
        "qr_png_b64": qr_b64,
        "clients": state.clients_list(),
    })


async def handle_status(_req: web.Request) -> web.Response:
    return web.json_response(state.status_dict())


async def handle_packets(_req: web.Request) -> web.Response:
    return web.json_response({"packets": list(state.packet_log)})


_scan_lock = asyncio.Lock()


async def handle_scan(req: web.Request) -> web.Response:
    include_all = req.query.get("all", "0") == "1"
    timeout = max(1.0, min(15.0, float(req.query.get("timeout", "4.0"))))
    if _scan_lock.locked():
        return web.json_response({"error": "A scan is already running", "devices": []}, status=409)
    if state.transport is not None and state.transport.transfer.active:
        return web.json_response({"error": "Wait for the current upload to finish before scanning", "devices": []}, status=409)
    try:
        async with _scan_lock:
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
            await _resync_after_connect()
            state.broadcast_soon("status_update", state.status_dict())
            return web.json_response(state.status_dict())
        except Exception as exc:
            # Never leave a half-open BLE link if the post-connect handshake failed (CLAUDE.md #5)
            try:
                await transport.disconnect()
            except Exception:
                log.exception("Cleanup disconnect after failed connect failed")
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


class DisplayNotConnected(RuntimeError):
    """Raised when a display operation needs a connected panel."""

    def __init__(self, msg: str = "Not connected to display") -> None:
        super().__init__(msg)


def _require_transport() -> Any:
    t = state.transport
    if t is None or not t.is_connected:
        raise DisplayNotConnected()
    return t


def _clamp_dims(body: Dict[str, Any]) -> tuple[int, int]:
    w = max(8, min(MAX_DIM, int(body.get("width", state.width))))
    h = max(8, min(MAX_DIM, int(body.get("height", state.height))))
    return w, h


def _decode_frames(frames_b64: Any, width: int, height: int) -> List[bytes]:
    if not isinstance(frames_b64, list) or not frames_b64:
        raise ValueError("No frames provided")
    if len(frames_b64) > MAX_FRAMES:
        raise ValueError(f"Too many frames ({len(frames_b64)} > {MAX_FRAMES})")
    expected = width * height * 3
    out: List[bytes] = []
    for b64 in frames_b64:
        raw = base64.b64decode(b64)
        if len(raw) != expected:
            raise ValueError(f"Frame is {len(raw)} bytes; expected {expected} for {width}x{height} RGB")
        out.append(raw)
    return out


def _cancel_alert_restore() -> None:
    """Cancel the pending alert auto-restore. Call only while holding `state.lock`, so the
    restore task is either sleeping or waiting for the lock (never half-way through BLE frames)."""
    task = state.notify_restore_task
    state.notify_restore_task = None
    if task is not None and not task.done() and task is not asyncio.current_task():
        task.cancel()


async def _exit_modal_mode() -> None:
    """Leave built-in Clock / Countdown / Scoreboard / Game mode. Caller holds `state.lock`."""
    t = state.transport
    if state.in_modal_mode and t is not None and t.is_connected:
        try:
            await t.send_frame(cmd_game_action("exit", sno=t.next_sno()))
        except Exception as exc:
            log.warning("Exiting built-in mode failed: %s", exc)
        state.in_modal_mode = False
        state.save_display_state()


async def _end_temp_alert(*, restore_previous: bool) -> None:
    """
    End the RAM Slot 3 alert. Caller holds `state.lock`.
    restore_previous=True switches back to the visible live slot first, THEN deletes Slot 3,
    so the panel never shows an empty program (zero flicker).
    """
    _cancel_alert_restore()
    if not state.temp_alert_active:
        return
    t = state.transport
    if t is None or not t.is_connected:
        return  # keep the flag; _resync_after_connect() finishes the restore later
    try:
        if state.alert_brightness_changed:
            await t.send_frame(cmd_set_brightness(max(1, min(15, int(state.brightness))), sno=t.next_sno()))
        if restore_previous:
            await t.send_frame(cmd_dispatch_program(state.active_slot, sno=t.next_sno()))
            await t.send_frame(cmd_pgm_play(model=1, index=(state.active_slot - 1) & 0xFF, sno=t.next_sno()))
        await t.send_frame(cmd_delete_programs([ALERT_SLOT], sno=t.next_sno()))
    except Exception as exc:
        log.warning("Ending temporary alert failed: %s", exc)
    state.temp_alert_active = False
    state.alert_brightness_changed = False
    state.save_display_state()


async def _ensure_exit_modal_mode(clear_temp_alert: bool = True) -> None:
    """Exit any built-in mode and (optionally) a temporary alert. Caller holds `state.lock`."""
    if clear_temp_alert:
        await _end_temp_alert(restore_previous=True)
    await _exit_modal_mode()


async def _resync_after_connect() -> None:
    """After (re)connecting: finish an alert restore that a disconnect interrupted. Caller holds `state.lock`."""
    if state.temp_alert_active:
        await _end_temp_alert(restore_previous=True)


def _reserve_snos(t: Any, frames: List[bytes]) -> None:
    """Builders number frames start_sno..start_sno+N-1; advance the transport past that block."""
    for _ in range(max(0, len(frames) - 1)):
        t.next_sno()


async def present_program(
    builder: Callable[..., List[bytes]],
    *,
    label: str,
    persist: bool = True,
    sync_time: bool = False,
    now_playing: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    The single path for putting new content on the panel (CLAUDE.md constraint 3):
      1. under `state.lock`: cancel any alert restore and leave built-in modes,
      2. pick the hidden back slot (1 <-> 2) INSIDE the lock (no races between clients),
      3. upload silently (show_now=False) to RAM or Flash,
      4. switch atomically: dispatch + pgm_play(model=1),
      5. then delete an alert that was on screen (no intermediate flash),
      6. persist slot state and update the live mirror for every client.

    `builder(program_id=, start_sno=, show_now=, data_save=)` returns the wire frames.
    """
    _require_transport()
    async with state.lock:
        t = _require_transport()
        alert_was_active = state.temp_alert_active
        _cancel_alert_restore()
        await _exit_modal_mode()
        slot = state.next_back_slot()
        try:
            if sync_time:
                await t.send_frame(cmd_sync_time(sno=t.next_sno()))
            frames = builder(
                program_id=slot,
                start_sno=t.next_sno(),
                show_now=False,
                data_save=0 if persist else 1,
            )
            _reserve_snos(t, frames)
            res = await t.send_frames(frames, label=label)
            await t.send_frame(cmd_dispatch_program(slot, sno=t.next_sno()))
            await t.send_frame(cmd_pgm_play(model=1, index=(slot - 1) & 0xFF, sno=t.next_sno()))
        except BaseException:
            # The old content (or alert) is still on screen; put the alert away cleanly.
            if alert_was_active:
                try:
                    await _end_temp_alert(restore_previous=True)
                except Exception:
                    log.exception("Alert cleanup after failed upload failed")
            raise
        state.active_slot = slot
        state.device_schedule_active = False  # a pinned single program overrides the on-device schedule
        if alert_was_active:
            await _end_temp_alert(restore_previous=False)
        state.save_display_state()
    if now_playing:
        state.set_now_playing(**now_playing)
    return {"ok": True, "slot": slot, "wire_frames": len(frames), "last_ack": res[-1] if res else None}


async def _present_or_error(builder: Callable[..., List[bytes]], out: Optional[Dict[str, Any]] = None, **kwargs: Any) -> web.Response:
    """Run present_program() and map errors to JSON responses (400 if not connected)."""
    try:
        res = await present_program(builder, **kwargs)
    except DisplayNotConnected as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except Exception as exc:
        log.exception("Upload '%s' failed", kwargs.get("label"))
        return web.json_response({"error": str(exc)}, status=500)
    payload = dict(out or {})
    payload.update(res)
    return web.json_response(payload)


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
                # Accept legacy `value` key too (older Menu Bar / Matter / editor clients)
                raw_lvl = body.get("level", body.get("value"))
                if raw_lvl is None:
                    return web.json_response({"error": "Missing brightness 'level' (1-15)"}, status=400)
                lvl = max(1, min(15, int(raw_lvl)))
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
                state.save_display_state()
            elif action == "countdown":
                h = max(0, min(99, int(body.get("hours", 0))))
                m = max(0, min(59, int(body.get("minutes", 5))))
                s = max(0, min(59, int(body.get("seconds", 0))))
                res = await t.send_frame(cmd_set_countdown(h, m, s, sno=t.next_sno()))
                state.in_modal_mode = True
                state.save_display_state()
                lbl = f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
                state.set_now_playing("timer", f"Timer {lbl}", subtitle="Live hardware countdown", source=_detect_req_source(req))
            elif action == "scoreboard":
                res = await t.send_frame(
                    cmd_set_scoreboard(int(body.get("red", 0)), int(body.get("blue", 0)), sno=t.next_sno())
                )
                state.in_modal_mode = True
                state.save_display_state()
            elif action == "delete_all":
                _cancel_alert_restore()
                res = await t.send_frame(cmd_delete_all(sno=t.next_sno()))
                # Next upload must write Slot 1 first so list positions match slot ids
                # (pgm_play selects by position): pretend Slot 2 is "visible".
                state.active_slot = LIVE_SLOTS[1]
                state.temp_alert_active = False
                state.device_schedule_active = False
                state.save_display_state()
                state.set_now_playing("idle", "Display cleared", subtitle="All programs deleted", record=False)
            elif action == "delete_slots":
                ids = [int(x) for x in body["slots"] if 1 <= int(x) <= 255]
                res = await t.send_frame(cmd_delete_programs(ids, sno=t.next_sno()))
                if state.active_slot in ids:
                    state.set_now_playing("idle", "Slot deleted", subtitle=f"Slot {state.active_slot} was removed", record=False)
            elif action == "dispatch_slot":
                slot_n = int(body["slot"])
                if not 1 <= slot_n <= 255:
                    return web.json_response({"error": "slot must be 1..255"}, status=400)
                res = await t.send_frame(cmd_dispatch_program(slot_n, sno=t.next_sno()))
                if slot_n in LIVE_SLOTS:
                    state.active_slot = slot_n
                    state.save_display_state()
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
                # Blank the background using RAM Slot 3 so the built-in game never renders
                # on top of a running clock/GIF; Exit Game (or any new upload) restores the live slot.
                _cancel_alert_restore()
                await _exit_modal_mode()
                await t.send_frame(cmd_delete_programs([ALERT_SLOT], sno=t.next_sno()))
                black_ystp = pil_image_to_ystp01(
                    Image.new("RGB", (state.width, state.height), (0, 0, 0)),
                    state.width,
                    state.height,
                )
                blank_frames = build_image_program_frames(
                    black_ystp,
                    state.width,
                    state.height,
                    program_id=ALERT_SLOT,
                    start_sno=t.next_sno(),
                    show_now=False,
                    data_save=1,
                )
                _reserve_snos(t, blank_frames)
                await t.send_frames(blank_frames, label="Clear screen for game")
                await t.send_frame(cmd_dispatch_program(ALERT_SLOT, sno=t.next_sno()))
                await t.send_frame(cmd_pgm_play(model=1, index=(ALERT_SLOT - 1) & 0xFF, sno=t.next_sno()))
                state.temp_alert_active = True
                gid = int(body["game_id"])
                res = await t.send_frame(cmd_game_launch(gid, sno=t.next_sno()))
                state.in_modal_mode = True
                state.save_display_state()
                gnames = {0: "Snake", 1: "Blocks", 2: "Breakout", 3: "Arcade"}
                state.set_now_playing("game", f"Game · {gnames.get(gid, f'#{gid}')}", subtitle="Hardware Arcade", source=_detect_req_source(req))
            elif action == "game_opt":
                res = await t.send_frame(cmd_game_action(body["opt"], sno=t.next_sno(), unack=False))
                if body["opt"] == "exit":
                    state.in_modal_mode = False
                    await _end_temp_alert(restore_previous=True)
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
                if not _is_loopback_req(req):
                    return web.json_response({"error": "BLE password can only be changed on the Mac."}, status=403)
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


async def handle_display_repair(req: web.Request) -> web.Response:
    """
    POST /api/display/repair (Mac only) — "Repair display": run the panel's own Format Flash to
    reset a scrambled program list (symptom: switches show briefly, then jump back). Erases every
    stored program, then resets slot bookkeeping so the next upload re-creates Slot 1 before Slot 2.
    """
    _require_transport()
    async with state.lock:
        t = _require_transport()
        _cancel_alert_restore()
        await _exit_modal_mode()
        res = await t.send_frame(cmd_format_flash(sno=t.next_sno()), timeout=30.0)
        if (res or {}).get("status") != "ok":
            log.warning("Format Flash was not acknowledged: %s", res)
            return web.json_response({"error": "The display did not confirm the repair. Try again."}, status=502)
        state.active_slot = LIVE_SLOTS[1]  # next upload writes Slot 1 first (position order)
        state.temp_alert_active = False
        state.alert_brightness_changed = False
        state.device_schedule_active = False
        state.save_display_state()
    sched = _load_work_schedule()
    if sched.get("device_mode") or sched.get("flashed_count"):
        sched.update(device_mode=False, flashed_count=0, flashed_at=None)
        _save_work_schedule(sched)
    state.last_applied_rule_id = None
    src = _detect_req_source(req)
    log.info("Display repaired (Format Flash) via %s", src)
    state.set_now_playing("idle", "Display repaired", subtitle="Stored programs erased · pick something to show", source=src)
    return web.json_response({"ok": True})


async def handle_upload_frames(req: web.Request) -> web.Response:
    """Upload one or more RGB frames from the Pixel Art / Animation Editor."""
    body = await req.json()
    width, height = _clamp_dims(body)
    speed = int(body.get("speed", 9))
    frames_rgb = _decode_frames(body.get("frames"), width, height)
    ystp = encode_ystp01(frames_rgb, width, height)
    try:
        pbuf = io.BytesIO()
        Image.frombytes("RGB", (width, height), frames_rgb[0]).save(pbuf, format="PNG")
        prev_uri = "data:image/png;base64," + base64.b64encode(pbuf.getvalue()).decode("ascii")
    except Exception:
        prev_uri = ""
    src = _detect_req_source(req)
    builder = functools.partial(
        build_image_program_frames, ystp, width, height,
        speed=speed, time_stay=0 if len(frames_rgb) > 1 else 3,
    )
    return await _present_or_error(
        builder,
        {"ystp_bytes": len(ystp)},
        label=f"Canvas ({len(frames_rgb)}f)",
        now_playing=dict(
            kind="canvas", title=f"Custom Pixel Art ({len(frames_rgb)}f)",
            subtitle=f"64×64 Canvas · via {src}", preview_url=prev_uri, source=src,
        ),
    )


async def handle_upload_media(req: web.Request) -> web.Response:
    """Import PNG/JPG/BMP/GIF file, auto-resize or passthrough GIF, and send to display."""
    body = await req.json()
    raw = base64.b64decode(body["data_b64"])
    passthrough_gif = bool(body.get("passthrough_gif", True))
    w, h = state.width, state.height
    if passthrough_gif and raw.startswith(b"GIF"):
        builder = functools.partial(build_gif_program_frames, raw, w, h)
        preview = "data:image/gif;base64," + base64.b64encode(raw).decode("ascii")
    else:
        with Image.open(io.BytesIO(raw)) as img:
            ystp = pil_image_to_ystp01(img, w, h)
        builder = functools.partial(build_image_program_frames, ystp, w, h)
        preview = ""
    src = _detect_req_source(req)
    return await _present_or_error(
        builder,
        label="Imported media",
        now_playing=dict(kind="canvas", title="Imported Media", subtitle=f"via {src}", preview_url=preview, source=src),
    )


async def handle_send_text(req: web.Request) -> web.Response:
    body = await req.json()
    msg = str(body.get("text", "")).strip()
    if not msg:
        return web.json_response({"error": "Text cannot be empty"}, status=400)
    if len(msg) > 500:
        return web.json_response({"error": "Text is too long (max 500 characters)"}, status=400)
    fg = _parse_hex_rgb(body.get("color", "#ffffff"))
    bg = _parse_hex_rgb(body.get("bg", "#000000"))
    builder = functools.partial(
        build_text_program_frames, msg, state.width, state.height,
        color=fg, bg_color=bg, font_size=int(body.get("size", 16)),
        scroll=body.get("scroll", "left"), speed=int(body.get("speed", 9)),
    )
    src = _detect_req_source(req)
    return await _present_or_error(
        builder,
        label="Text",
        now_playing=dict(kind="text", title=msg[:40], subtitle=f"Scrolling text · via {src}", source=src),
    )


_library_lock = asyncio.Lock()


def _load_library() -> List[Dict[str, Any]]:
    data = read_json(LIBRARY_FILE, list, validate=lambda d: isinstance(d, list))
    return [x for x in data if isinstance(x, dict) and "id" in x]


async def handle_library_get(_req: web.Request) -> web.Response:
    return web.json_response({"items": _load_library()})


async def handle_library_save(req: web.Request) -> web.Response:
    body = await req.json()
    width, height = _clamp_dims(body)
    _decode_frames(body.get("frames"), width, height)  # validate before persisting
    item = {
        "id": str(body.get("id") or f"art_{int(time.time() * 1000)}")[:64],
        "name": str(body.get("name", "Untitled"))[:80],
        "width": width,
        "height": height,
        "favorite": bool(body.get("favorite", False)),
        "frames": body["frames"],
    }
    async with _library_lock:
        items = [x for x in _load_library() if x["id"] != item["id"]]
        items.insert(0, item)
        atomic_write_json(LIBRARY_FILE, items)
    state.broadcast_soon("library_update", {"count": len(items)})
    return web.json_response({"ok": True, "item": item})


async def handle_library_delete(req: web.Request) -> web.Response:
    item_id = req.match_info["id"]
    async with _library_lock:
        items = [x for x in _load_library() if x["id"] != item_id]
        atomic_write_json(LIBRARY_FILE, items)
    state.broadcast_soon("library_update", {"count": len(items)})
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
    width, height = _clamp_dims(body)
    delay_ms = int(body.get("delay_ms", 200))
    send_to_display = bool(body.get("send", False))
    frames_rgb = _decode_frames(body.get("frames"), width, height)

    gif_bytes = _frames_rgb_to_gif_bytes(frames_rgb, width, height, delay_ms=delay_ms)
    resp_payload: Dict[str, Any] = {
        "ok": True,
        "gif_bytes": len(gif_bytes),
        "gif_b64": base64.b64encode(gif_bytes).decode("ascii"),
    }

    if send_to_display:
        src = _detect_req_source(req)
        return await _present_or_error(
            functools.partial(build_gif_program_frames, gif_bytes, width, height),
            resp_payload,
            label=f"Animation ({len(frames_rgb)}f)",
            now_playing=dict(
                kind="canvas", title=f"Custom Animation ({len(frames_rgb)}f)",
                subtitle=f"64×64 GIF · via {src}",
                preview_url="data:image/gif;base64," + resp_payload["gif_b64"], source=src,
            ),
        )
    return web.json_response(resp_payload)


async def handle_custom_clock(req: web.Request) -> web.Response:
    """Render a custom pixel-art clock (digital, digital_date, analog, neon_bar) as frames/GIF."""
    body = await req.json()
    width, height = _clamp_dims(body)
    design = body.get("design", "digital")
    fg = _parse_hex_rgb(body.get("fg", "#05d9e8"))
    accent = _parse_hex_rgb(body.get("accent", "#ff2a6d"))
    bg = _parse_hex_rgb(body.get("bg", "#000000"))
    use_12h = bool(body.get("use_12h", False))
    blink = bool(body.get("blink", True))
    send = bool(body.get("send", False))

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
        if len(frames_rgb) > 1:
            builder = functools.partial(build_gif_program_frames, gif_bytes, width, height)
        else:
            builder = functools.partial(build_image_program_frames, encode_ystp01(frames_rgb, width, height), width, height)
        src = _detect_req_source(req)
        return await _present_or_error(
            builder, out, label=f"Pixel clock: {design}",
            now_playing=dict(kind="clock", title=f"Pixel Clock ({design})", subtitle=f"Snapshot · via {src}",
                             preview_url="data:image/gif;base64," + out["gif_b64"], source=src),
        )
    return web.json_response(out)


async def handle_gif_pack_list(_req: web.Request) -> web.Response:
    items = []
    for p in _all_gif_paths():
        meta = gif_meta(p.name)
        items.append({
            "file": p.name,
            "url": _gif_url(p),
            "size": p.stat().st_size,
            "user": p.parent == USER_GIFS_DIR,
            "label": meta["label"],
            "emoji": meta["emoji"],
            "category": "mine" if p.parent == USER_GIFS_DIR else meta["category"],
            "blurb": meta["blurb"],
        })
    categories = [{"id": cid, "label": clabel} for cid, clabel in CATEGORIES]
    return web.json_response({"gifs": items, "categories": categories})


async def handle_gif_pack_send(req: web.Request) -> web.Response:
    body = await req.json()
    raw_name = body.get("file") or body.get("filename") or ""
    if not raw_name:
        return web.json_response({"error": "Missing file/filename"}, status=400)
    gif_path = _resolve_gif(raw_name)
    if gif_path is None:
        return web.json_response({"error": f"GIF not found: {Path(str(raw_name)).name}"}, status=404)
    filename = gif_path.name
    raw = gif_path.read_bytes()
    src = _detect_req_source(req)
    meta = gif_meta(filename)
    pretty = meta["label"]
    pin = "pinOnAir" if "on_air" in filename else ("pinCoding" if "coding" in filename else "")
    return await _present_or_error(
        functools.partial(build_gif_program_frames, raw, state.width, state.height),
        {"file": filename, "label": pretty},
        label=f"GIF: {pretty}",
        now_playing=dict(kind="gif", title=pretty, subtitle=f"64×64 Animated Mode · via {src}",
                         active_pin=pin, preview_url=_gif_url(gif_path), source=src),
    )


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
    body = await req.json()
    shape = str(body.get("shape", "custom"))
    template = (body.get("template") or "{hh}:{mm}:{ss}").strip()[:64]
    fg = _parse_hex_rgb(body.get("color", "#05d9e8"))
    accent = _parse_hex_rgb(body.get("accent", "#ff2a6d"))
    bg = _parse_hex_rgb(body.get("bg", "#000000"))
    font_size = int(body.get("size", 16))
    scroll = body.get("scroll", "static")
    bg_mode = str(body.get("bg_mode", "behind"))
    w, h = state.width, state.height

    bg_resource = None
    bg_gif = _resolve_gif(body.get("bg_gif", "")) if body.get("bg_gif") else None
    if bg_gif is not None:
        bg_resource = bg_gif.read_bytes()
    if bg_resource is None:
        bg_resource = _render_hw_clock_shape_bg(w, h, shape, fg, accent, bg)

    regions = _build_hw_clock_regions(shape, w, h, fg, accent, bg)
    if regions is None:
        builder = functools.partial(
            build_text_clock_program_frames, template, w, h, color=fg, bg_color=bg, font_size=font_size,
            scroll=scroll, background_resource=bg_resource, bg_mode=bg_mode,
        )
    else:
        builder = functools.partial(
            build_multi_region_hw_clock_frames, regions, w, h, background_resource=bg_resource, bg_mode=bg_mode,
        )
    src = _detect_req_source(req)
    col_q = urllib.parse.quote(str(body.get("color", "#05d9e8")))
    acc_q = urllib.parse.quote(str(body.get("accent", "#ff2a6d")))
    return await _present_or_error(
        builder,
        {"shape": shape},
        label=f"Clock: {shape}",
        sync_time=bool(body.get("sync_first", True)),
        now_playing=dict(
            kind="clock", title=shape.replace("hw_", "").replace("_", " ").title() + " Clock",
            subtitle=f"Hardware RTC · via {src}",
            preview_url=f"/api/hw_clock_preview/{urllib.parse.quote(shape)}?color={col_q}&accent={acc_q}",
            source=src,
        ),
    )


async def handle_qrcode(req: web.Request) -> web.Response:
    """Generate a crisp QR code 100% locally (no external URL shorteners) and optionally send to display."""
    import qrcode

    body = await req.json()
    text = (body.get("text") or "").strip()
    if not text:
        return web.json_response({"error": "QR content cannot be empty"}, status=400)
    fg = _parse_hex_rgb(body.get("fg", "#000000"))
    bg = _parse_hex_rgb(body.get("bg", "#ffffff"))
    if len(text) > 1000:
        return web.json_response({"error": "QR content is too long (max 1000 characters)"}, status=400)
    width, height = _clamp_dims(body)
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
        ystp = encode_ystp01([raw_rgb], width, height)
        src = _detect_req_source(req)
        return await _present_or_error(
            functools.partial(build_image_program_frames, ystp, width, height),
            out, label="QR code",
            now_playing=dict(kind="canvas", title="QR Code", subtitle=f"{text[:30]} · via {src}", source=src),
        )
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


MAX_REMOTE_GIF_BYTES = 12 * 1024 * 1024


def _assert_public_host(host: str) -> None:
    """Block SSRF: refuse hosts that resolve to loopback/private/link-local/reserved addresses."""
    if not host:
        raise ValueError("URL has no host")
    infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved
                or addr.is_multicast or addr.is_unspecified):
            raise ValueError(f"Refusing to fetch from a local/private address ({host})")


async def _fetch_public_https(url: str, max_bytes: int = MAX_REMOTE_GIF_BYTES) -> bytes:
    """Download a public HTTPS resource with SSRF checks on every redirect hop and a size cap."""
    async with ClientSession(timeout=ClientTimeout(total=20)) as session:
        for _hop in range(4):
            parts = urllib.parse.urlsplit(url)
            if parts.scheme != "https":
                raise ValueError("Only https:// GIF URLs are allowed")
            await asyncio.to_thread(_assert_public_host, parts.hostname or "")
            async with session.get(url, allow_redirects=False) as resp:
                if resp.status in (301, 302, 303, 307, 308) and resp.headers.get("Location"):
                    url = urllib.parse.urljoin(url, resp.headers["Location"])
                    continue
                if resp.status != 200:
                    raise ValueError(f"Failed to fetch GIF (HTTP {resp.status})")
                buf = bytearray()
                async for chunk in resp.content.iter_chunked(65536):
                    buf.extend(chunk)
                    if len(buf) > max_bytes:
                        raise ValueError(f"GIF is larger than {max_bytes // (1024 * 1024)} MB")
                return bytes(buf)
        raise ValueError("Too many redirects")


async def handle_gif_fetch_send(req: web.Request) -> web.Response:
    """Download a selected GIF URL, resize to display resolution, optionally save locally, and send."""
    body = await req.json()
    url = str(body.get("url") or "").strip()
    if not url.startswith("https://"):
        return web.json_response({"error": "Only https:// GIF URLs are allowed"}, status=400)
    save_name = str(body.get("save_name") or "").strip()
    send = bool(body.get("send", True))
    width, height = _clamp_dims(body)

    try:
        raw = await _fetch_public_https(url)
        resized_gif = await asyncio.to_thread(_resize_gif_bytes, raw, width, height)
    except Exception as exc:
        return web.json_response({"error": f"Failed to download/resize GIF: {exc}"}, status=400)

    saved_file = None
    if save_name:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in save_name)[:40] or f"web_{int(time.time())}"
        if not safe.endswith(".gif"):
            safe += ".gif"
        # Saved into the user's data folder: the app bundle is read-only / code-signed
        USER_GIFS_DIR.mkdir(parents=True, exist_ok=True)
        atomic_write_text_bytes(USER_GIFS_DIR / safe, resized_gif)
        saved_file = safe

    out: Dict[str, Any] = {
        "ok": True,
        "gif_bytes": len(resized_gif),
        "saved_file": saved_file,
        "gif_b64": base64.b64encode(resized_gif).decode("ascii"),
    }
    if send:
        src = _detect_req_source(req)
        return await _present_or_error(
            functools.partial(build_gif_program_frames, resized_gif, width, height),
            out, label="Web GIF",
            now_playing=dict(kind="gif", title=saved_file or "Web GIF", subtitle=f"Online GIF · via {src}",
                             preview_url="data:image/gif;base64," + out["gif_b64"], source=src),
        )
    return web.json_response(out)


def atomic_write_text_bytes(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


async def handle_upload_sequence(req: web.Request) -> web.Response:
    """
    Convert an ordered sequence of images (PNG/JPG/BMP/WEBP) into one looping animated GIF.
    `mode="playlist"` is accepted for compatibility and produces the same single program with
    the per-image delay: writing one hardware program per image would collide with the
    reserved slot map (live 1-2, alert 3, schedule 4-8).
    """
    body = await req.json()
    images_b64: List[str] = body.get("images") or []
    if not images_b64:
        return web.json_response({"error": "Select at least one image"}, status=400)
    if len(images_b64) > MAX_FRAMES:
        return web.json_response({"error": f"Too many images (max {MAX_FRAMES})"}, status=400)
    width, height = _clamp_dims(body)
    delay_ms = max(40, min(60000, int(body.get("delay_ms", 400))))
    mode = body.get("mode", "gif")
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
        src = _detect_req_source(req)
        return await _present_or_error(
            functools.partial(build_gif_program_frames, gif_bytes, width, height),
            out, label=f"Sequence ({len(frames_rgb)} images)",
            now_playing=dict(kind="canvas", title=f"Image Sequence ({len(frames_rgb)})", subtitle=f"via {src}",
                             preview_url="data:image/gif;base64," + out["gif_b64"], source=src),
        )
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
                    # swnd > 1 wedges the panel's BLE RX buffer: never selectable on real hardware
                    "blocked_on_hardware": k in EXPERIMENTAL_PROFILES,
                }
                for k, p in ALL_PROFILES.items()
            },
        })
    body = await req.json()
    name = str(body.get("profile", "conservative"))
    if name not in ALL_PROFILES:
        return web.json_response({"error": f"Unknown profile: {name}"}, status=400)
    if state.transport:
        try:
            state.transport.set_profile(name)
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
    elif name in EXPERIMENTAL_PROFILES:
        return web.json_response(
            {"error": f"Profile '{name}' uses a sliding window > 1 and is blocked on real hardware."},
            status=400,
        )
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
                program_id=BENCHMARK_SLOT,
                start_sno=state.transport.next_sno(),
                show_now=False,
                data_save=1,
            )
            t0 = time.monotonic()
            await state.transport.send_frames(frames, label="Throughput Benchmark")
            elapsed = max(0.001, time.monotonic() - t0)
            try:  # free the RAM scratch program
                await state.transport.send_frame(cmd_delete_programs([BENCHMARK_SLOT], sno=state.transport.next_sno()))
            except Exception as exc:
                log.warning("Deleting benchmark slot failed: %s", exc)
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
    width, height = _clamp_dims(body)
    c1 = _parse_hex_rgb(body.get("color1", "#05d9e8"))
    c2 = _parse_hex_rgb(body.get("color2", "#ff2a6d"))
    density = float(body.get("density", 0.6))
    n_frames = max(1, min(MAX_FRAMES, int(body.get("frames", 16))))
    delay_ms = int(body.get("delay_ms", 90))
    send = bool(body.get("send", False))

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
        src = _detect_req_source(req)
        return await _present_or_error(
            functools.partial(build_gif_program_frames, gif_bytes, width, height),
            out, label=f"Procedural: {effect}",
            now_playing=dict(kind="gif", title=f"{str(effect).replace('_', ' ').title()} Effect",
                             subtitle=f"Procedural · via {src}",
                             preview_url="data:image/gif;base64," + out["gif_b64"], source=src),
        )
    return web.json_response(out)


async def handle_avatar(req: web.Request) -> web.Response:
    body = await req.json()
    seed = int(body.get("seed", int(time.time()) % 9999))
    width, height = _clamp_dims(body)
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
        src = _detect_req_source(req)
        return await _present_or_error(
            functools.partial(build_image_program_frames, encode_ystp01([raw], width, height), width, height),
            out, label="Pixel Avatar",
            now_playing=dict(kind="canvas", title=f"Pixel Avatar #{seed}", subtitle=f"via {src}", source=src),
        )
    return web.json_response(out)


async def handle_process_image(req: web.Request) -> web.Response:
    body = await req.json()
    raw = base64.b64decode(body["data_b64"])
    width, height = _clamp_dims(body)
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
        src = _detect_req_source(req)
        return await _present_or_error(
            functools.partial(build_image_program_frames, ystp, width, height),
            out, label="Image Studio",
            now_playing=dict(kind="canvas", title="Photo", subtitle=f"Image Studio · via {src}", source=src),
        )
    return web.json_response(out)


async def handle_upload_video(req: web.Request) -> web.Response:
    body = await req.json()
    raw = base64.b64decode(body["data_b64"])
    filename = Path(str(body.get("filename", "clip.mp4"))).name
    width, height = _clamp_dims(body)
    try:
        gif_bytes = convert_video_to_gif(
            raw,
            filename,
            width,
            height,
            start_s=float(body.get("start_s", 0.0)),
            duration_s=max(0.2, min(15.0, float(body.get("duration_s", 3.0)))),
            fps=max(1, min(25, int(body.get("fps", 10)))),
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
        src = _detect_req_source(req)
        return await _present_or_error(
            functools.partial(build_gif_program_frames, gif_bytes, width, height),
            out, label="Video GIF",
            now_playing=dict(kind="gif", title="Video Clip", subtitle=f"{filename} · via {src}",
                             preview_url="data:image/gif;base64," + out["gif_b64"], source=src),
        )
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
    width, height = _clamp_dims(body)
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
        src = _detect_req_source(req)
        pbuf = io.BytesIO()
        img.save(pbuf, format="PNG")
        return await _present_or_error(
            functools.partial(build_image_program_frames, encode_ystp01([raw], width, height), width, height),
            out, label=f"Widget: {kind}",
            now_playing=dict(kind="canvas", title=f"{str(kind).replace('_', ' ').title()} Widget", subtitle=f"via {src}",
                             preview_url="data:image/png;base64," + base64.b64encode(pbuf.getvalue()).decode("ascii"),
                             source=src),
        )
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
                await _resync_after_connect()
                log.info("Auto-reconnected to %s", state.device_name)
                state.broadcast_soon("status_update", state.status_dict())
            except Exception as exc:
                log.warning("Auto-reconnect to %s failed: %s", state.last_ble_address, exc)
                try:
                    await t.disconnect()
                except Exception:
                    log.exception("Cleanup disconnect after failed auto-reconnect failed")
                if state.transport is t:
                    state.transport = None


async def _work_scheduler_loop() -> None:
    """Periodically evaluate the time & day work-mode schedule and auto-switch the display."""
    failed_rule_id: Optional[str] = None
    while True:
        await asyncio.sleep(20.0)
        try:
            sched = _load_work_schedule()
            if not sched.get("enabled", True) or sched.get("device_mode"):
                continue  # disabled, or the on-device Flash schedule is in charge
            if not state.transport or not state.transport.is_connected:
                continue
            if state.context_override or _load_smart_config().get("auto_cycle_enabled"):
                continue  # an explicit mode the user turned on takes precedence
            matched = _match_schedule_rule(sched.get("rules", []), datetime.datetime.now())
            if not matched:
                continue
            rule_id = matched.get("id")
            if rule_id and rule_id != state.last_applied_rule_id:
                try:
                    await _apply_schedule_rule(matched, source="Schedule", persist=AUTO_REFRESH_PERSIST)
                    failed_rule_id = None
                except DisplayNotConnected:
                    pass
                except Exception:
                    if failed_rule_id != rule_id:  # log once per rule, retry silently every tick
                        log.exception("Applying schedule rule '%s' failed; will retry", matched.get("name"))
                        failed_rule_id = rule_id
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Work scheduler tick failed")


_HHMM_RE = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")
RULE_KINDS = ("gif", "hw_clock", "power_off")
MAX_RULES = 50


def _normalize_rule(rule: Any, idx: int, *, strict: bool) -> Dict[str, Any]:
    """Validate one schedule rule. strict=True also checks the target exists (on save)."""
    if not isinstance(rule, dict):
        raise ValueError(f"Rule {idx + 1} is not an object")
    name = str(rule.get("name") or f"Rule {idx + 1}").strip()[:60]
    kind = str(rule.get("kind", "gif"))
    if kind not in RULE_KINDS:
        raise ValueError(f"Rule '{name}': kind must be one of {', '.join(RULE_KINDS)}")
    start, end = str(rule.get("start", "")).strip(), str(rule.get("end", "")).strip()
    if not _HHMM_RE.match(start) or not _HHMM_RE.match(end):
        raise ValueError(f"Rule '{name}': start/end must be HH:MM")
    days = [d for d in (str(x).lower() for x in (rule.get("days") or [])) if d in DAY_KEYS]
    if not days:
        raise ValueError(f"Rule '{name}': pick at least one day")
    target = str(rule.get("target", "")).strip()[:120]
    if strict and kind == "gif" and _resolve_gif(target) is None:
        raise ValueError(f"Rule '{name}': GIF not found: {target}")
    bri = rule.get("brightness")
    out: Dict[str, Any] = {
        "id": str(rule.get("id") or f"r_{secrets.token_hex(4)}")[:40],
        "enabled": bool(rule.get("enabled", True)),
        "name": name,
        "days": [d for d in DAY_KEYS if d in days],
        "start": start,
        "end": end,
        "kind": kind,
        "target": target,
        "brightness": None if bri in (None, "") else max(1, min(15, int(bri))),
    }
    for key in ("color", "accent"):
        val = rule.get(key)
        if isinstance(val, str) and re.fullmatch(r"#?[0-9a-fA-F]{6}", val.strip()):
            out[key] = "#" + val.strip().lstrip("#")
    return out


_schedule_cache: Dict[str, Any] = {"mtime": None, "data": None}


def _load_work_schedule() -> Dict[str, Any]:
    """Load + validate the schedule (cached by file mtime; status_dict calls this on every broadcast)."""
    try:
        mtime = SCHEDULE_FILE.stat().st_mtime_ns
    except OSError:
        mtime = None
    if mtime is not None and _schedule_cache["mtime"] == mtime and _schedule_cache["data"] is not None:
        return json.loads(json.dumps(_schedule_cache["data"]))
    data = _load_work_schedule_uncached()
    try:
        _schedule_cache.update(mtime=SCHEDULE_FILE.stat().st_mtime_ns, data=json.loads(json.dumps(data)))
    except OSError:
        pass
    return data


def _load_work_schedule_uncached() -> Dict[str, Any]:
    def _default() -> Dict[str, Any]:
        data = json.loads(json.dumps(DEFAULT_WORK_SCHEDULE))
        _save_work_schedule(data)
        return data

    data = read_json(SCHEDULE_FILE, _default, validate=lambda d: isinstance(d, dict) and isinstance(d.get("rules"), list))
    rules: List[Dict[str, Any]] = []
    for idx, raw in enumerate(data.get("rules", [])[:MAX_RULES]):
        try:
            rules.append(_normalize_rule(raw, idx, strict=False))
        except (ValueError, TypeError) as exc:
            log.warning("Ignoring invalid schedule rule: %s", exc)
    data["rules"] = rules
    return data


def _save_work_schedule(data: Dict[str, Any]) -> None:
    atomic_write_json(SCHEDULE_FILE, data)


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
        if not isinstance(rule, dict) or not rule.get("enabled", True):
            continue
        days = [str(d).lower() for d in rule.get("days", [])]
        s_min = _hhmm_to_min(rule.get("start", "00:00"))
        e_min = _hhmm_to_min(rule.get("end", "00:00"))
        if s_min <= e_min:
            if day_key in days and s_min <= cur_min < e_min:
                return rule
        else:
            # Overnight window (e.g., 22:30 -> 08:00): the part after midnight belongs to the previous day's rule
            if (day_key in days and cur_min >= s_min) or (prev_day_key in days and cur_min < e_min):
                return rule
    return None


def _rule_brief(rule: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not rule:
        return None
    return {"name": rule.get("name", "Rule"), "start": rule.get("start", ""), "end": rule.get("end", "")}


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
        if not r.get("enabled", True) or day_key not in r.get("days", []):
            continue
        s_min = _hhmm_to_min(r.get("start", "00:00"))
        if s_min > cur_min and (s_min - cur_min) < best_delta:
            best_delta = s_min - cur_min
            next_rule = r
    return {
        "enabled": bool(sched.get("enabled", True)),
        "active": _rule_brief(active),
        "next": _rule_brief(next_rule),
        "flashed_at": sched.get("flashed_at"),
        "flashed_count": sched.get("flashed_count", 0),
        "device_mode": bool(sched.get("device_mode", False)),
        "device_schedule_active": state.device_schedule_active,
    }


def _clock_regions_or_default(shape: str, w: int, h: int, fg: tuple, accent: tuple, bg: tuple) -> List[Dict[str, Any]]:
    return _build_hw_clock_regions(shape or "hw_neon_ring", w, h, fg, accent, bg) or [
        {"template": "{hh}:{mm}", "x": 6, "y": 16, "w": w - 12, "h": 21, "color": fg, "bg_color": bg, "font_size": 16},
        {"template": "{MM}/{DD}", "x": 10, "y": 37, "w": w - 20, "h": 16, "color": accent, "bg_color": bg, "font_size": 12},
    ]


def _shift_days(days: List[str], offset: int) -> List[str]:
    idx = {d: i for i, d in enumerate(DAY_KEYS)}
    return [DAY_KEYS[(idx[d] + offset) % 7] for d in days if d in idx]


def _rule_device_programs(rule: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Turn one schedule rule into 1-2 on-device programs (PERIOD_PRO has a single weekday mask
    per program). An overnight rule on specific days needs a second program for the part
    after midnight, which belongs to the FOLLOWING days.
    """
    days = rule.get("days") or list(DAY_KEYS)
    s_t, e_t = str(rule.get("start", "08:00")), str(rule.get("end", "18:00"))
    if _hhmm_to_min(s_t) <= _hhmm_to_min(e_t):
        return [{"days": days, "windows": [f"{s_t}-{e_t}"]}]
    if set(days) == set(DAY_KEYS):
        return [{"days": days, "windows": [f"{s_t}-23:59", f"00:00-{e_t}"]}]
    return [
        {"days": days, "windows": [f"{s_t}-23:59"]},
        {"days": _shift_days(days, 1), "windows": [f"00:00-{e_t}"]},
    ]


async def handle_work_schedule_flash_device(req: web.Request) -> web.Response:
    """
    POST /api/work_schedule/flash_device — store enabled schedule rules in the display's
    on-board Flash (Slots 4..8) with TLV 0x16 (PERIOD_PRO) windows, so the panel switches
    on its own RTC even with the Mac offline. Live content (Slots 1-2) and alerts (Slot 3)
    never overlap these slots. Disabled unless DEVICE_SCHEDULE_ENABLED (see its comment).
    """
    if not DEVICE_SCHEDULE_ENABLED:
        return web.json_response({"error": DEVICE_SCHEDULE_DISABLED_MSG, "disabled": True}, status=409)
    body = await req.json() if req.body_exists else {}
    start_date = str(body.get("start_date") or datetime.date.today().isoformat()).strip()
    end_date = str(body.get("end_date") or "2099-12-31").strip()
    try:
        datetime.date.fromisoformat(start_date)
        datetime.date.fromisoformat(end_date)
    except ValueError:
        return web.json_response({"error": "start_date/end_date must be YYYY-MM-DD"}, status=400)
    sched = _load_work_schedule()
    day_to_num = {k: i + 1 for i, k in enumerate(DAY_KEYS)}

    skipped: List[Dict[str, str]] = []
    planned: List[Dict[str, Any]] = []
    for rule in sched.get("rules", []):
        if not rule.get("enabled", True):
            continue
        name = rule.get("name", rule.get("target", "Rule"))
        if rule.get("kind") not in ("gif", "hw_clock"):
            skipped.append({"name": name, "reason": "Power-off rules can't run on-device; use the display's power schedule"})
            continue
        if rule.get("kind") == "gif" and _resolve_gif(rule.get("target", "")) is None:
            skipped.append({"name": name, "reason": "GIF not found"})
            continue
        progs = _rule_device_programs(rule)
        if len(planned) + len(progs) > len(SCHEDULE_SLOTS):
            skipped.append({"name": name, "reason": f"Only {len(SCHEDULE_SLOTS)} on-device slots"})
            continue
        planned.extend({"rule": rule, **prog} for prog in progs)
    if not planned:
        return web.json_response({"error": "No enabled GIF or Clock rules to flash", "skipped": skipped}, status=400)

    flashed: List[Dict[str, Any]] = []
    try:
        _require_transport()
        async with state.lock:
            t = _require_transport()
            await _ensure_exit_modal_mode()
            await t.send_frame(cmd_sync_time(sno=t.next_sno()))
            # Clear stale schedule programs from an earlier flash before writing new ones
            await t.send_frame(cmd_delete_programs(list(SCHEDULE_SLOTS), sno=t.next_sno()))
            for slot, item in zip(SCHEDULE_SLOTS, planned):
                rule = item["rule"]
                weekdays = [day_to_num[d] for d in item["days"]] or list(range(1, 8))
                period_tlv = encode_period_pro_tlv(item["windows"], weekdays=weekdays, start_date=start_date, end_date=end_date)
                if rule["kind"] == "gif":
                    gif_path = _resolve_gif(rule["target"])
                    assert gif_path is not None
                    wire_frames = build_gif_program_frames(
                        gif_path.read_bytes(), state.width, state.height,
                        program_id=slot, start_sno=t.next_sno(), show_now=False, data_save=0,
                        period_pro_tlv=period_tlv,
                    )
                else:
                    fg = _parse_hex_rgb(rule.get("color", "#38bdf8"))
                    accent = _parse_hex_rgb(rule.get("accent", "#ec4899"))
                    bg_res = _render_hw_clock_shape_bg(state.width, state.height, rule["target"], fg, accent, (0, 0, 0))
                    regions = _clock_regions_or_default(rule["target"], state.width, state.height, fg, accent, (0, 0, 0))
                    wire_frames = build_multi_region_hw_clock_frames(
                        regions, state.width, state.height,
                        program_id=slot, start_sno=t.next_sno(), show_now=False, data_save=0,
                        background_resource=bg_res, period_pro_tlv=period_tlv,
                    )
                _reserve_snos(t, wire_frames)
                await t.send_frames(wire_frames, label=f"Flash Slot {slot}: {rule.get('name', rule['target'])}")
                flashed.append({"slot": slot, "name": rule.get("name", rule["target"]), "days": item["days"], "windows": item["windows"]})
            await _start_device_schedule_locked(t)
    except DisplayNotConnected as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except Exception as exc:
        log.exception("Flashing on-device schedule failed")
        return web.json_response({"error": f"Flashing stopped part-way: {exc}", "flashed": flashed, "skipped": skipped}, status=500)

    sched["flashed_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    sched["flashed_count"] = len(flashed)
    sched["device_mode"] = True
    _save_work_schedule(sched)
    state.set_now_playing(
        "schedule", f"On-Device Schedule ({len(flashed)} slots)",
        subtitle=f"{start_date} → {end_date} · Runs offline on display RTC",
        source="Device Flash",
    )
    return web.json_response({"ok": True, "flashed": flashed, "skipped": skipped, "start_date": start_date, "end_date": end_date})


async def _start_device_schedule_locked(t: Any) -> None:
    """Hand the panel to its Flash schedule. Caller holds `state.lock`.
    Live slots are removed so sequence playback (model 0) only cycles the scheduled programs."""
    await _end_temp_alert(restore_previous=False)
    # Switch to sequence playback FIRST, then remove the live slots: deleting the program
    # that is on screen could flash the factory intro (zero-flicker rule).
    await t.send_frame(cmd_pgm_play(model=0, index=0, sno=t.next_sno()))
    await t.send_frame(cmd_delete_programs(list(LIVE_SLOTS), sno=t.next_sno()))
    state.active_slot = LIVE_SLOTS[1]  # next manual upload re-creates Slot 1 first (position order)
    state.device_schedule_active = True
    state.save_display_state()


async def handle_work_schedule_device_mode(req: web.Request) -> web.Response:
    """POST {"action": "resume"} hands control back to the on-device schedule;
    {"action": "stop"} deletes it from the panel and returns control to the Mac scheduler."""
    body = await req.json()
    action = str(body.get("action", ""))
    if action == "resume" and not DEVICE_SCHEDULE_ENABLED:
        return web.json_response({"error": DEVICE_SCHEDULE_DISABLED_MSG, "disabled": True}, status=409)
    sched = _load_work_schedule()
    try:
        _require_transport()
        async with state.lock:
            t = _require_transport()
            await _exit_modal_mode()
            if action == "resume":
                if not sched.get("flashed_count"):
                    return web.json_response({"error": "No on-device schedule has been flashed yet"}, status=400)
                await _start_device_schedule_locked(t)
                sched["device_mode"] = True
            elif action == "stop":
                await t.send_frame(cmd_delete_programs(list(SCHEDULE_SLOTS), sno=t.next_sno()))
                state.device_schedule_active = False
                state.save_display_state()
                sched.update(device_mode=False, flashed_count=0, flashed_at=None)
            else:
                return web.json_response({"error": "action must be 'resume' or 'stop'"}, status=400)
    except DisplayNotConnected as exc:
        return web.json_response({"error": str(exc)}, status=400)
    _save_work_schedule(sched)
    if action == "resume":
        state.set_now_playing("schedule", "On-Device Schedule", subtitle="Runs offline on display RTC", source="Device Flash")
    else:
        state.last_applied_rule_id = None  # let the Mac scheduler apply the current rule again
        state.broadcast_soon("status_update", state.status_dict())
    return web.json_response({"ok": True, "device_mode": bool(sched.get("device_mode"))})


async def _apply_schedule_rule(
    rule: Dict[str, Any],
    *,
    source: str = "Schedule",
    persist: bool = True,
    track: bool = True,
) -> Dict[str, Any]:
    """Apply one schedule/context rule through the double-buffered present_program() path."""
    _require_transport()
    kind = str(rule.get("kind", "gif"))
    target = str(rule.get("target", ""))
    bri = rule.get("brightness")
    name = str(rule.get("name", target))

    async with state.lock:
        t = _require_transport()
        if kind == "power_off":
            await _ensure_exit_modal_mode()
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

    if kind == "power_off":
        state.set_now_playing("power", name, subtitle=f"Screen off · via {source}", source=source)
    elif kind == "gif":
        path = _resolve_gif(target)
        if path is None:
            raise FileNotFoundError(f"Scheduled GIF not found: {target}")
        pretty = gif_meta(path.name)["label"]
        await present_program(
            functools.partial(build_gif_program_frames, path.read_bytes(), state.width, state.height),
            label=f"Schedule: {name}", persist=persist,
            now_playing=dict(kind="gif", title=pretty, subtitle=f"{name} · via {source}",
                             preview_url=_gif_url(path), source=source),
        )
    elif kind == "hw_clock":
        w, h = state.width, state.height
        shape = target or "hw_neon_ring"
        fg = _parse_hex_rgb(rule["color"]) if rule.get("color") else (0, 240, 255)
        accent = _parse_hex_rgb(rule["accent"]) if rule.get("accent") else (255, 43, 214)
        bg = (0, 0, 0)
        bg_resource = _render_hw_clock_shape_bg(w, h, shape, fg, accent, bg)
        regions = _clock_regions_or_default(shape, w, h, fg, accent, bg)
        fg_hex = "#%02x%02x%02x" % fg
        acc_hex = "#%02x%02x%02x" % accent
        await present_program(
            functools.partial(build_multi_region_hw_clock_frames, regions, w, h, background_resource=bg_resource),
            label=f"Schedule: {name}", persist=persist, sync_time=True,
            now_playing=dict(
                kind="clock", title=shape.replace("hw_", "").replace("_", " ").title() + " Clock",
                subtitle=f"{name} · via {source}",
                preview_url=f"/api/hw_clock_preview/{shape}?color={urllib.parse.quote(fg_hex)}&accent={urllib.parse.quote(acc_hex)}",
                source=source,
            ),
        )
    else:
        raise ValueError(f"Unknown rule kind: {kind}")

    if track:
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


def _schedule_response(sched: Dict[str, Any], **extra: Any) -> web.Response:
    now = datetime.datetime.now()
    payload = {
        "enabled": bool(sched.get("enabled", True)),
        "rules": sched.get("rules", []),
        "matched_rule": _match_schedule_rule(sched.get("rules", []), now),
        "active_rule_id": state.last_applied_rule_id,
        "device_mode": bool(sched.get("device_mode", False)),
        "flashed_count": sched.get("flashed_count", 0),
        "device_slots": len(SCHEDULE_SLOTS),
        "now": now.strftime("%a %H:%M"),
        "now_day": DAY_KEYS[now.weekday()],
        "now_minutes": now.hour * 60 + now.minute,
    }
    payload.update(extra)
    return web.json_response(payload)


async def handle_work_schedule(req: web.Request) -> web.Response:
    if req.method == "GET":
        paths = _all_gif_paths()
        return _schedule_response(
            _load_work_schedule(),
            available_gifs=[p.name for p in paths],
            gif_labels={p.name: gif_meta(p.name)["label"] for p in paths},
        )
    body = await req.json()
    sched = _load_work_schedule()
    if "enabled" in body:
        sched["enabled"] = bool(body["enabled"])
    if "rules" in body:
        raw_rules = body["rules"]
        if not isinstance(raw_rules, list):
            return web.json_response({"error": "rules must be a list"}, status=400)
        if len(raw_rules) > MAX_RULES:
            return web.json_response({"error": f"Too many rules (max {MAX_RULES})"}, status=400)
        try:
            sched["rules"] = [_normalize_rule(r, i, strict=True) for i, r in enumerate(raw_rules)]
        except (ValueError, TypeError) as exc:
            return web.json_response({"error": str(exc)}, status=400)
    _save_work_schedule(sched)
    state.broadcast_soon("schedule_update", {"active_rule_id": state.last_applied_rule_id})
    state.broadcast_soon("status_update", state.status_dict())
    return _schedule_response(sched, ok=True)


async def handle_work_schedule_apply(_req: web.Request) -> web.Response:
    sched = _load_work_schedule()
    matched = _match_schedule_rule(sched.get("rules", []))
    if not matched:
        return web.json_response({"error": "No schedule rule matches the current day & time"}, status=404)
    try:
        return web.json_response(await _apply_schedule_rule(matched, source="Schedule"))
    except DisplayNotConnected as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except Exception as exc:
        log.exception("Applying schedule rule failed")
        return web.json_response({"error": str(exc)}, status=500)


async def handle_work_schedule_test_rule(req: web.Request) -> web.Response:
    body = await req.json()
    raw = body.get("rule") if isinstance(body, dict) else None
    if not isinstance(raw, dict):
        return web.json_response({"error": "Missing rule object"}, status=400)
    try:
        rule = _normalize_rule(raw, 0, strict=True)
        return web.json_response(await _apply_schedule_rule(rule, source=_detect_req_source(req), track=False))
    except DisplayNotConnected as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except (ValueError, TypeError, FileNotFoundError) as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except Exception as exc:
        log.exception("Testing schedule rule failed")
        return web.json_response({"error": str(exc)}, status=500)


async def handle_work_schedule_preset(req: web.Request) -> web.Response:
    body = await req.json()
    preset = str(body.get("preset", "developer"))
    if preset not in SCHEDULE_PRESETS:
        return web.json_response({"error": f"Unknown preset: {preset}"}, status=400)
    sched = _load_work_schedule()
    sched["rules"] = [_normalize_rule(r, i, strict=False) for i, r in enumerate(SCHEDULE_PRESETS[preset])]
    _save_work_schedule(sched)
    state.broadcast_soon("schedule_update", {"active_rule_id": state.last_applied_rule_id})
    return _schedule_response(sched, ok=True, preset=preset)


_SMART_CONFIG_TYPES: Dict[str, type] = {k: type(v) for k, v in DEFAULT_SMART_CONFIG.items()}


def _coerce_smart_value(key: str, value: Any) -> Any:
    want = _SMART_CONFIG_TYPES[key]
    if want is bool:
        return bool(value)
    if want is int:
        return max(1, min(1440, int(value)))
    return str(value or "").strip()[:300]


def _load_smart_config() -> Dict[str, Any]:
    cfg = dict(DEFAULT_SMART_CONFIG)
    saved = read_json(SMART_CONFIG_FILE, dict, validate=lambda d: isinstance(d, dict))
    for k, v in saved.items():
        if k in cfg:
            try:
                cfg[k] = _coerce_smart_value(k, v)
            except (TypeError, ValueError):
                pass
    return cfg


def _save_smart_config(cfg: Dict[str, Any]) -> None:
    atomic_write_json(SMART_CONFIG_FILE, cfg)


async def _flash_temporary_gif(
    gif_bytes: bytes, label: str, duration_s: float = 10.0, brightness: Optional[int] = None
) -> Dict[str, Any]:
    """
    Show a temporary alert from RAM Slot 3 and restore the visible live slot after `duration_s`
    (duration_s <= 0 keeps the alert until the next upload). The live slots are never touched,
    so restoring is just a switch back + deleting Slot 3.
    """
    _require_transport()
    duration_s = float(duration_s)
    if duration_s > 0:
        duration_s = max(1.0, min(3600.0, duration_s))
    async with state.lock:
        t = _require_transport()
        _cancel_alert_restore()  # safe: we hold the lock, so the old restore is not mid-frame
        await _exit_modal_mode()
        if not state.power_on:
            await t.send_frame(cmd_set_power(True, sno=t.next_sno()))
            state.power_on = True
        if brightness is not None:
            await t.send_frame(cmd_set_brightness(max(1, min(15, int(brightness))), sno=t.next_sno()))
            state.alert_brightness_changed = True  # state.brightness keeps the level to restore
        if not state.temp_alert_active:
            # RAM over a Flash program corrupts the panel's program list (see AUTO_REFRESH_PERSIST),
            # so clear Slot 3 (e.g. a leftover from an old on-device schedule) before writing to RAM.
            await t.send_frame(cmd_delete_programs([ALERT_SLOT], sno=t.next_sno()))
        wire_frames = build_gif_program_frames(
            gif_bytes, state.width, state.height, program_id=ALERT_SLOT,
            start_sno=t.next_sno(), show_now=False, data_save=1,
        )
        _reserve_snos(t, wire_frames)
        res = await t.send_frames(wire_frames, label=label)
        await t.send_frame(cmd_dispatch_program(ALERT_SLOT, sno=t.next_sno()))
        await t.send_frame(cmd_pgm_play(model=1, index=(ALERT_SLOT - 1) & 0xFF, sno=t.next_sno()))
        state.temp_alert_active = True
        state.save_display_state()
        if duration_s > 0:
            state.notify_restore_task = asyncio.create_task(_restore_after_alert(duration_s))
    return {"ok": True, "wire_frames": len(wire_frames), "duration_s": duration_s, "last_ack": res[-1] if res else None}


async def _restore_after_alert(delay_s: float) -> None:
    me = asyncio.current_task()
    await asyncio.sleep(delay_s)
    async with state.lock:
        if state.notify_restore_task is not me:
            return  # superseded by a newer alert or an upload
        state.notify_restore_task = None
        try:
            # If BLE is down, the flag stays set and _resync_after_connect() restores on reconnect.
            await _end_temp_alert(restore_previous=True)
        except Exception:
            log.exception("Restoring display after alert failed")


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


async def _upload_weather_clock(city: str, source: str = "App", *, persist: bool = True) -> Dict[str, Any]:
    _require_transport()
    wx = await asyncio.to_thread(fetch_open_meteo_weather, city)
    state.weather_cache = {**wx, "ts": round(time.time(), 1)}
    bg_gif = render_weather_clock_bg_gif(wx["temp_c"], wx["kind"], wx["city"], size=state.width)
    w, h = state.width, state.height
    regions = [
        {"template": "{hh}:{mm}", "x": 4, "y": 4, "w": w - 8, "h": 24, "color": (255, 255, 255), "bg_color": (0, 0, 0), "font_size": 16},
    ]
    b64_prev = "data:image/gif;base64," + base64.b64encode(bg_gif).decode("ascii")
    res = await present_program(
        functools.partial(build_multi_region_hw_clock_frames, regions, w, h, background_resource=bg_gif, bg_mode="behind"),
        label=f"Weather: {wx['city']} {wx['temp_c']}C",
        persist=persist,
        sync_time=True,
        now_playing=dict(
            kind="clock", title=f"Weather Clock · {wx['city']} {wx['temp_c']}°C",
            subtitle=f"{wx['kind'].replace('_', ' ').title()} · via {source}",
            active_pin="pinWxClock", preview_url=b64_prev, overlay_clock=True, source=source,
        ),
    )
    return {**res, "weather": wx}


async def _upload_calendar_clock(
    ics_url: str = "", calendar_name: str = "", source: str = "App", *, persist: bool = True
) -> Dict[str, Any]:
    _require_transport()
    ev = await asyncio.to_thread(get_upcoming_calendar_event, 480, ics_url, calendar_name)
    bg_gif = render_calendar_clock_bg_gif(ev, size=state.width)
    w, h = state.width, state.height
    regions = [
        {"template": "{hh}:{mm}", "x": 4, "y": 4, "w": w - 8, "h": 24, "color": (255, 255, 255), "bg_color": (0, 0, 0), "font_size": 16},
    ]
    cur = (ev or {}).get("current")
    nxt = f"Next: {ev['title']} in {ev['minutes_left']}m" if ev and ev["minutes_left"] >= 0 else ""
    n_today = len((ev or {}).get("day") or [])
    lbl = f"Cal: {ev['title']} ({ev['minutes_left']}m)" if nxt else (f"Cal Day: {n_today} mtgs" if n_today else "Cal Clock: Free")
    b64_prev = "data:image/gif;base64," + base64.b64encode(bg_gif).decode("ascii")
    parts = ([f"Now: {cur['title']} (ends in {cur['ends_in']}m)"] if cur else []) + ([nxt] if nxt else [])
    if not parts and n_today:
        parts = [f"{n_today} meeting{'s' if n_today != 1 else ''} today, all done"]
    sub = f"{' · '.join(parts)} · via {source}" if parts else f"No meetings today · via {source}"
    if cur:
        lbl = f"Cal: {cur['title']} (now)"
    res = await present_program(
        functools.partial(build_multi_region_hw_clock_frames, regions, w, h, background_resource=bg_gif, bg_mode="behind"),
        label=lbl,
        persist=persist,
        sync_time=True,
        now_playing=dict(
            kind="clock", title="Workday Calendar · 24h", subtitle=sub,
            active_pin="pinCalClock", preview_url=b64_prev, overlay_clock=True, source=source,
        ),
    )
    return {**res, "event": ev}


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


# ---------------------------------------------------------------------------
# In-App Self-Updater (GitHub Releases — supports public & private repos)
# ---------------------------------------------------------------------------

def _parse_semver(v: str) -> tuple[int, ...]:
    nums = [int(p) for p in re.findall(r"\d+", str(v or "0"))]
    return tuple(nums[:3] + [0] * max(0, 3 - len(nums)))


def _get_github_token() -> str:
    if GH_TOKEN_FILE.exists():
        try:
            tok = GH_TOKEN_FILE.read_text(encoding="utf-8").strip()
            if tok:
                return tok
        except Exception:
            pass
    env_tok = (os.environ.get("LOY_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN") or "").strip()
    if env_tok:
        return env_tok
    gh_candidates = [
        shutil.which("gh"),
        "/opt/homebrew/bin/gh",
        "/usr/local/bin/gh",
        "/opt/local/bin/gh",
    ]
    for gh_bin in gh_candidates:
        if not gh_bin or not Path(gh_bin).exists():
            continue
        try:
            out = subprocess.check_output([gh_bin, "auth", "token"], text=True, stderr=subprocess.DEVNULL, timeout=2.5).strip()
            if out:
                return out
        except Exception:
            continue
    return ""


def _machine_arch() -> str:
    import platform

    m = platform.machine().lower()
    if m in ("arm64", "aarch64"):
        return "arm64"
    if m in ("x86_64", "amd64"):
        return "x64"
    return m


def _pick_release_asset(assets: List[Dict[str, Any]]) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Choose the portable zip for this OS *and* CPU. Returns (asset, error)."""
    want_os = "macos" if sys.platform == "darwin" else ("windows" if sys.platform == "win32" else "linux")
    arch = _machine_arch()
    arch_aliases = {"arm64": ("arm64", "aarch64", "universal"), "x64": ("x64", "x86_64", "amd64", "intel", "universal")}
    zips = [a for a in assets if str(a.get("name", "")).lower().endswith(".zip") and want_os in str(a.get("name", "")).lower()]
    if not zips:
        return None, f"No {want_os} build in this release"
    for alias in arch_aliases.get(arch, (arch,)):
        for a in zips:
            if alias in str(a.get("name", "")).lower():
                return a, None
    named_archs = [al for als in arch_aliases.values() for al in als]
    untagged = [a for a in zips if not any(al in str(a.get("name", "")).lower() for al in named_archs)]
    if untagged:
        return untagged[0], None
    return None, f"This release has no {want_os} build for your CPU ({arch})"


def _check_github_update() -> Dict[str, Any]:
    from app.smart_features import _build_ssl_context

    tok = _get_github_token()
    headers = {"Accept": "application/vnd.github+json", "User-Agent": f"LOY-Studio/{APP_VERSION}"}
    req = urllib.request.Request(f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest", headers=headers)
    if tok:
        req.add_unredirected_header("Authorization", f"Bearer {tok}")
    try:
        with urllib.request.urlopen(req, timeout=8, context=_build_ssl_context()) as r:
            rel = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 404):
            return {
                "ok": False,
                "current_version": APP_VERSION,
                "auth_needed": True,
                "error": "Private repo: add a read-only GitHub token in Device → App Updates (or sign in via gh CLI).",
            }
        raise

    tag = str(rel.get("tag_name") or "").lstrip("vV")
    assets = rel.get("assets") or []
    chosen, asset_error = _pick_release_asset(assets)
    by_name = {str(a.get("name", "")): a for a in assets}
    sums = by_name.get("SHA256SUMS")
    sig = by_name.get("SHA256SUMS.sig")
    return {
        "ok": True,
        "current_version": APP_VERSION,
        "latest_version": tag,
        "update_available": _parse_semver(tag) > _parse_semver(APP_VERSION),
        "release_url": rel.get("html_url") or f"https://github.com/{GITHUB_REPO}/releases",
        "release_notes": (rel.get("body") or "")[:600],
        "asset_name": chosen.get("name") if chosen else None,
        "asset_error": asset_error,
        "asset_api_url": chosen.get("url") if chosen else None,
        "asset_download_url": chosen.get("browser_download_url") if chosen else None,
        "sums_url": (sums or {}).get("url"),
        "sig_url": (sig or {}).get("url"),
        "signed_release": bool(sig),
        "is_portable": bool(getattr(sys, "frozen", False)),
        "has_token": bool(tok),
    }


async def handle_update_check(_req: web.Request) -> web.Response:
    try:
        info = await asyncio.to_thread(_check_github_update)
        return web.json_response(info)
    except Exception as exc:
        return web.json_response({"ok": False, "current_version": APP_VERSION, "error": str(exc)}, status=500)


async def handle_update_token(req: web.Request) -> web.Response:
    """Loopback-only (middleware)."""
    body = await req.json()
    tok = str(body.get("token") or "").strip()
    if tok:
        if len(tok) > 400 or any(c.isspace() for c in tok):
            return web.json_response({"error": "That does not look like a GitHub token"}, status=400)
        atomic_write_text_private(GH_TOKEN_FILE, tok)
    else:
        GH_TOKEN_FILE.unlink(missing_ok=True)
    return web.json_response({"ok": True, "has_token": bool(tok)})


def atomic_write_text_private(path: Path, text: str) -> None:
    from app.storage import atomic_write_text

    atomic_write_text(path, text, private=True)


def _github_download(url: str, dest: Optional[Path], tok: str, max_bytes: int) -> bytes:
    """Download a release asset via the API URL. The token is never forwarded on the CDN redirect."""
    from app.smart_features import _build_ssl_context

    req = urllib.request.Request(url, headers={"Accept": "application/octet-stream", "User-Agent": f"LOY-Studio/{APP_VERSION}"})
    if tok and urllib.parse.urlsplit(url).hostname == "api.github.com":
        req.add_unredirected_header("Authorization", f"Bearer {tok}")
    h = hashlib.sha256()
    buf = bytearray()
    total = 0
    with urllib.request.urlopen(req, timeout=120, context=_build_ssl_context()) as r:
        out = dest.open("wb") if dest else None
        try:
            for chunk in iter(lambda: r.read(1 << 20), b""):
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError("Download is larger than expected")
                h.update(chunk)
                if out:
                    out.write(chunk)
                else:
                    buf.extend(chunk)
        finally:
            if out:
                out.close()
    return h.hexdigest().encode("ascii") if dest else bytes(buf)


# The swap scripts take every path as a positional argument (never string-interpolated into
# shell code), wait for this process to exit, then atomically swap and relaunch.
_MAC_SWAP_SH = r'''#!/bin/sh
LOG="$1"; OLD_PID="$2"; NEW_APP="$3"; TARGET="$4"; TMP_DIR="$5"
STAGE="$TARGET.new"; BACKUP="$TARGET.old"
echo "=== LOY Studio update started $(date) ===" > "$LOG"
echo "Installing $NEW_APP -> $TARGET (waiting for PID $OLD_PID)" >> "$LOG"
i=0
while kill -0 "$OLD_PID" 2>/dev/null && [ $i -lt 120 ]; do sleep 0.25; i=$((i + 1)); done
sleep 0.4
rm -rf "$STAGE" "$BACKUP" >> "$LOG" 2>&1
if ! ditto "$NEW_APP" "$STAGE" >> "$LOG" 2>&1; then echo "ERROR: ditto failed" >> "$LOG"; exit 1; fi
xattr -cr "$STAGE" >> "$LOG" 2>&1 || true
if [ -e "$TARGET" ]; then
  if ! mv "$TARGET" "$BACKUP" >> "$LOG" 2>&1; then echo "ERROR: could not move existing app to backup" >> "$LOG"; rm -rf "$STAGE"; exit 1; fi
fi
if ! mv "$STAGE" "$TARGET" >> "$LOG" 2>&1; then
  echo "ERROR: could not move staged app into place; restoring backup" >> "$LOG"
  [ -e "$BACKUP" ] && mv "$BACKUP" "$TARGET" >> "$LOG" 2>&1
  exit 1
fi
rm -rf "$BACKUP" >> "$LOG" 2>&1 || true
echo "Launching $TARGET" >> "$LOG"
open -n "$TARGET" >> "$LOG" 2>&1 || open "$TARGET" >> "$LOG" 2>&1
rm -rf "$TMP_DIR" >> "$LOG" 2>&1 || true
echo "=== Update complete $(date) ===" >> "$LOG"
'''

_WIN_SWAP_BAT = (
    "@echo off\r\n"
    "setlocal\r\n"
    'set "NEW_EXE=%~1"\r\n'
    'set "TARGET=%~2"\r\n'
    'set "TMP_DIR=%~3"\r\n'
    'set "OLD_PID=%~4"\r\n'
    ":waitloop\r\n"
    'tasklist /FI "PID eq %OLD_PID%" 2>nul | find "%OLD_PID%" >nul\r\n'
    "if not errorlevel 1 (\r\n"
    "  timeout /t 1 /nobreak >nul\r\n"
    "  goto waitloop\r\n"
    ")\r\n"
    'copy /y "%NEW_EXE%" "%TARGET%.new" >nul\r\n'
    "if errorlevel 1 exit /b 1\r\n"
    'move /y "%TARGET%.new" "%TARGET%" >nul\r\n'
    'start "" "%TARGET%"\r\n'
    'rmdir /s /q "%TMP_DIR%" >nul 2>&1\r\n'
)

_update_lock = asyncio.Lock()


async def handle_update_apply(_req: web.Request) -> web.Response:
    """Loopback-only (middleware). Download, VERIFY, then swap & relaunch."""
    if _update_lock.locked():
        return web.json_response({"ok": False, "error": "An update is already in progress"}, status=409)
    async with _update_lock:
        return await _apply_update()


async def _apply_update() -> web.Response:
    try:
        info = await asyncio.to_thread(_check_github_update)
    except Exception as exc:
        return web.json_response({"ok": False, "error": f"Update check failed: {exc}"}, status=500)

    if not info.get("ok"):
        return web.json_response(info, status=400)
    asset_url = info.get("asset_api_url") or info.get("asset_download_url")
    if not asset_url:
        return web.json_response({"ok": False, "error": info.get("asset_error") or "No matching portable asset found"}, status=404)

    if not getattr(sys, "frozen", False):
        return web.json_response({
            "ok": True,
            "mode": "browser",
            "release_url": info["release_url"],
            "message": "Running from source — open the Release URL or run git pull.",
        })
    if not info.get("sums_url"):
        return web.json_response({
            "ok": False,
            "error": "This release has no SHA256SUMS, so it can't be verified. Download it manually from the release page.",
            "release_url": info["release_url"],
        }, status=400)

    tok = _get_github_token()
    tmp_dir = Path(tempfile.mkdtemp(prefix="loy_update_"))
    asset_name = str(info.get("asset_name") or "update.zip")
    zip_path = tmp_dir / Path(asset_name).name

    try:
        sums = await asyncio.to_thread(_github_download, info["sums_url"], None, tok, 64 * 1024)
        sig = await asyncio.to_thread(_github_download, info["sig_url"], None, tok, 4096) if info.get("sig_url") else None
        zip_sha = (await asyncio.to_thread(_github_download, asset_url, zip_path, tok, 600 * 1024 * 1024)).decode("ascii")
        verdict = verify_release(sums, sig, asset_name, zip_sha)
        log.info("Update %s verified (sha256 %s, signed=%s)", asset_name, zip_sha, verdict["signed"])
    except Exception as exc:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        log.warning("Update download/verification failed: %s", exc)
        return web.json_response({"ok": False, "error": f"Update not installed: {exc}"}, status=500)

    install_path = ""
    log_path = str(DATA_DIR / "update.log")
    cur_pid = os.getpid()

    try:
        if sys.platform == "darwin":
            exe_path = Path(sys.executable).resolve()
            app_bundle = next((p for p in exe_path.parents if p.suffix == ".app"), None)
            if not app_bundle:
                return web.json_response({"ok": False, "error": "Could not locate .app bundle path"}, status=500)

            extract_dir = tmp_dir / "extracted"
            extract_dir.mkdir(exist_ok=True)
            await asyncio.to_thread(subprocess.check_call, ["ditto", "-x", "-k", str(zip_path), str(extract_dir)])
            new_app = next(extract_dir.rglob("*.app"), None)
            if not new_app:
                return web.json_response({"ok": False, "error": "Downloaded archive did not contain a .app bundle"}, status=500)

            # macOS App Translocation (read-only random mount) or a non-writable parent: install to Applications
            is_translocated = "/AppTranslocation/" in str(app_bundle)
            if is_translocated or not os.access(app_bundle.parent, os.W_OK):
                apps_dir = Path("/Applications")
                if apps_dir.is_dir() and os.access(apps_dir, os.W_OK):
                    target_app = apps_dir / new_app.name
                else:
                    user_apps = Path.home() / "Applications"
                    user_apps.mkdir(parents=True, exist_ok=True)
                    target_app = user_apps / new_app.name
            else:
                target_app = app_bundle

            install_path = str(target_app)
            swap_sh = tmp_dir / "swap.sh"
            swap_sh.write_text(_MAC_SWAP_SH, encoding="utf-8")
            swap_sh.chmod(0o700)
            # Gracefully disconnect BLE (waits for any transfer) before launching the swap script
            await shutdown_ble("in-app update")
            subprocess.Popen(
                ["/bin/sh", str(swap_sh), log_path, str(cur_pid), str(new_app), str(target_app), str(tmp_dir)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        elif sys.platform == "win32":
            import zipfile

            exe_path = Path(sys.executable).resolve()
            extract_dir = tmp_dir / "extracted"

            def _extract_win() -> None:
                with zipfile.ZipFile(zip_path, "r") as zf:
                    zf.extractall(extract_dir)

            await asyncio.to_thread(_extract_win)
            new_exe = next(extract_dir.rglob("*.exe"), None)
            if not new_exe:
                return web.json_response({"ok": False, "error": "Downloaded archive did not contain an .exe"}, status=500)

            install_path = str(exe_path)
            bat = tmp_dir / "swap.bat"
            bat.write_text(_WIN_SWAP_BAT, encoding="utf-8")
            await shutdown_ble("in-app update")
            subprocess.Popen(
                ["cmd", "/c", str(bat), str(new_exe), str(exe_path), str(tmp_dir), str(cur_pid)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=0x00000008,
            )
        else:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return web.json_response({
                "ok": True,
                "mode": "browser",
                "release_url": info["release_url"],
                "message": "Auto-replace is supported on macOS & Windows; opened GitHub Release page.",
            })
    except Exception as exc:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        log.exception("Preparing update install failed")
        return web.json_response({"ok": False, "error": f"Install preparation failed: {exc}"}, status=500)

    # Exit shortly after returning HTTP 200 so the swap script replaces & relaunches cleanly
    asyncio.get_running_loop().call_later(0.6, lambda: os._exit(0))
    return web.json_response({
        "ok": True,
        "restarting": True,
        "latest_version": info["latest_version"],
        "install_path": install_path,
        "log_path": log_path,
        "signed": verdict["signed"],
    })


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
            "active_override": state.context_override,
            "weather_cache": state.weather_cache,
        })
    body = await req.json()
    cfg = _load_smart_config()
    for k in DEFAULT_SMART_CONFIG:
        if k in body:
            try:
                cfg[k] = _coerce_smart_value(k, body[k])
            except (TypeError, ValueError):
                return web.json_response({"error": f"Invalid value for {k}"}, status=400)
    if cfg.get("calendar_ics_url") and not re.match(r"^(https?|webcal)://", cfg["calendar_ics_url"], re.I):
        return web.json_response({"error": "Calendar URL must start with https://, http:// or webcal://"}, status=400)
    _save_smart_config(cfg)
    state.broadcast_soon("smart_config_update", cfg)
    state.broadcast_soon("status_update", state.status_dict())
    return web.json_response({"ok": True, "config": cfg})


class _Ticker:
    """Logs the first failure of a periodic step (and recovery), not every 15 s retry."""

    def __init__(self) -> None:
        self.failing: Set[str] = set()

    async def run(self, name: str, coro: Any) -> bool:
        try:
            await coro
        except DisplayNotConnected:
            return False
        except asyncio.CancelledError:
            raise
        except Exception:
            if name not in self.failing:
                log.exception("Smart watcher step '%s' failed (will keep retrying quietly)", name)
                self.failing.add(name)
            return False
        if name in self.failing:
            log.info("Smart watcher step '%s' recovered", name)
            self.failing.discard(name)
        return True


async def _end_context_override() -> None:
    """Context (e.g. ON AIR) ended: hand back to the on-device schedule or the current Mac rule."""
    sched = _load_work_schedule()
    if sched.get("device_mode") and sched.get("flashed_count"):
        async with state.lock:
            t = _require_transport()
            await _exit_modal_mode()
            await _start_device_schedule_locked(t)
        state.set_now_playing("schedule", "On-Device Schedule", subtitle="Resumed after auto mode", source="Auto")
        return
    if sched.get("enabled", True):
        matched = _match_schedule_rule(sched.get("rules", []))
        if matched:
            await _apply_schedule_rule(matched, source="Schedule", persist=AUTO_REFRESH_PERSIST)


async def _smart_watcher_loop() -> None:
    """Background watcher for Zero-Touch Mac Context, Hydration Reminders & Weather Refresh."""
    last_hydration_ts = time.monotonic()
    last_weather_ts = 0.0
    last_cal_clock_ts = 0.0
    last_cycle_ts = 0.0
    cycle_idx = 0
    alerted_events: Set[str] = set()
    ticker = _Ticker()

    while True:
        await asyncio.sleep(15.0)
        try:
            if not state.transport or not state.transport.is_connected:
                continue
            cfg = _load_smart_config()
            now_mono = time.monotonic()
            device_mode = bool(_load_work_schedule().get("device_mode"))

            # 1. Zero-Touch Mac/Windows Context Awareness
            if cfg.get("context_enabled"):
                ctx = await asyncio.to_thread(detect_mac_context, check_calendar=False)
                desired: Optional[str] = None
                target_gif: Optional[str] = None
                if cfg.get("auto_on_air") and ctx.get("in_call"):
                    desired, target_gif = "on_air", "11_mode_on_air_meeting.gif"
                elif cfg.get("auto_idle_brb") and ctx.get("idle_seconds", 0) >= int(cfg.get("idle_minutes", 5)) * 60:
                    desired, target_gif = "idle_brb", "17_mode_away_brb.gif"
                elif cfg.get("auto_coding_app") and ctx.get("is_coding"):
                    desired, target_gif = "coding", "10_mode_coding.gif"

                if desired != state.context_override:
                    if desired and target_gif:
                        ok = await ticker.run("context", _apply_schedule_rule(
                            {
                                "id": f"ctx_{desired}", "name": f"Auto: {desired.replace('_', ' ').title()}",
                                "kind": "gif", "target": target_gif,
                                "brightness": 14 if desired == "on_air" else None,
                            },
                            source="Auto", persist=AUTO_REFRESH_PERSIST,
                        ))
                    else:
                        ok = await ticker.run("context-end", _end_context_override())
                    if ok:  # only record the override once it is really on the panel; otherwise retry next tick
                        state.context_override = desired
            elif state.context_override:
                state.context_override = None

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
                        gif_b = render_notification_gif("MEETING", f"IN {ev['minutes_left']}M", style="meeting", size=state.width)
                        if await ticker.run("calendar-alert", _flash_temporary_gif(gif_b, label="Calendar Alert", duration_s=12.0, brightness=14)):
                            alerted_events.add(ev_key)
                if len(alerted_events) > 500:
                    alerted_events.clear()

            # 2. Periodic Hydration Reminder
            if cfg.get("hydration_enabled"):
                interval_s = max(5, int(cfg.get("hydration_interval_min", 60))) * 60
                if now_mono - last_hydration_ts >= interval_s:
                    last_hydration_ts = now_mono
                    await ticker.run("hydration", _flash_temporary_gif(render_hydration_gif(size=state.width), label="Hydration Reminder", duration_s=12.0))

            # Periodic re-renders never override an explicit auto mode or the on-device schedule
            idle_for_refresh = not state.context_override and not device_mode

            # 3. Periodic Weather Clock Auto-Refresh (every 30 min)
            if cfg.get("weather_enabled") and idle_for_refresh and now_mono - last_weather_ts >= 1800.0:
                last_weather_ts = now_mono
                await ticker.run("weather", _upload_weather_clock(str(cfg.get("weather_city", "Berlin")), source="Auto", persist=AUTO_REFRESH_PERSIST))

            # 4. Workday Calendar + RTC Clock Auto-Refresh (Mon-Fri 08:00-19:00, every 5 min)
            if cfg.get("calendar_clock_enabled") and not cfg.get("auto_cycle_enabled") and idle_for_refresh:
                now_dt = datetime.datetime.now()
                if now_dt.weekday() < 5 and 8 <= now_dt.hour < 19 and now_mono - last_cal_clock_ts >= 300.0:
                    last_cal_clock_ts = now_mono
                    await ticker.run("calendar-clock", _upload_calendar_clock(
                        str(cfg.get("calendar_ics_url") or ""), str(cfg.get("calendar_name") or ""),
                        source="Auto", persist=AUTO_REFRESH_PERSIST,
                    ))

            # 5. One-Tap Auto-Cycle (Calendar Clock -> Weather Clock -> Synthwave every 5 min).
            #    The Mac scheduler pauses while auto-cycle is on, so steps are not overridden.
            if cfg.get("auto_cycle_enabled") and idle_for_refresh and now_mono - last_cycle_ts >= 300.0:
                last_cycle_ts = now_mono
                step = cycle_idx % 3
                cycle_idx += 1
                if step == 0:
                    await ticker.run("cycle", _upload_calendar_clock(str(cfg.get("calendar_ics_url") or ""), str(cfg.get("calendar_name") or ""), source="Auto-Cycle", persist=AUTO_REFRESH_PERSIST))
                elif step == 1:
                    await ticker.run("cycle", _upload_weather_clock(str(cfg.get("weather_city", "Berlin")), source="Auto-Cycle", persist=AUTO_REFRESH_PERSIST))
                else:
                    await ticker.run("cycle", _apply_schedule_rule(
                        {"id": "cycle_synth", "name": "Synthwave", "kind": "hw_clock", "target": "hw_synthwave_sun", "color": "#38bdf8", "accent": "#ec4899"},
                        source="Auto-Cycle", persist=AUTO_REFRESH_PERSIST, track=False,
                    ))
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Smart watcher tick failed")


async def shutdown_ble(reason: str = "shutdown", lock_timeout: float = 8.0) -> None:
    """
    Gracefully disconnect BLE (CLAUDE.md constraint 5). Safe to call more than once.
    Stops auto-reconnect, cancels any in-flight transfer between frames, waits for the BLE
    lock so a frame is never cut in half, then disconnects.
    """
    state.auto_reconnect = False
    t = state.transport
    if t is None:
        return
    t.cancel_transfer()
    acquired = False
    try:
        try:
            await asyncio.wait_for(state.lock.acquire(), timeout=lock_timeout)
            acquired = True
        except asyncio.TimeoutError:
            log.warning("BLE lock still busy after %.1fs during %s; disconnecting anyway", lock_timeout, reason)
        if t.is_connected:
            log.info("Disconnecting BLE (%s)", reason)
            await t.disconnect()
    except Exception:
        log.exception("BLE disconnect during %s failed", reason)
    finally:
        if acquired:
            state.lock.release()


async def _shutdown_ble_hook(_app: web.Application) -> None:
    await _stop_bg_tasks(_app)
    await shutdown_ble("server shutdown")


async def _start_bg_tasks(app: web.Application) -> None:
    app["reconnect_task"] = asyncio.create_task(_auto_reconnect_loop())
    app["scheduler_task"] = asyncio.create_task(_work_scheduler_loop())
    app["smart_task"] = asyncio.create_task(_smart_watcher_loop())


async def _stop_bg_tasks(app: web.Application) -> None:
    for key in ("reconnect_task", "scheduler_task", "smart_task"):
        task = app.get(key)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


def create_app() -> web.Application:
    app = web.Application(client_max_size=32 * 1024 * 1024, middlewares=[errors_and_headers_middleware, local_auth_middleware])
    app.on_startup.append(_start_bg_tasks)
    # on_shutdown runs for SIGINT/SIGTERM (web.run_app) and AppRunner.cleanup() (desktop app)
    app.on_shutdown.append(_shutdown_ble_hook)
    app.on_cleanup.append(_stop_bg_tasks)
    app.router.add_get("/", handle_index)
    app.router.add_get("/ws", handle_ws)
    app.router.add_static("/static", STATIC_DIR)
    if GIFS_DIR.exists():
        app.router.add_static("/gifs", GIFS_DIR)
    USER_GIFS_DIR.mkdir(parents=True, exist_ok=True)
    app.router.add_static("/user_gifs", USER_GIFS_DIR)
    app.router.add_get("/api/auth/status", handle_auth_status)
    app.router.add_post("/api/auth/login", handle_auth_login)
    app.router.add_post("/api/auth/set_password", handle_auth_set_password)
    app.router.add_post("/api/auth/logout", handle_auth_logout)
    app.router.add_get("/api/auth/sessions", handle_auth_sessions)
    app.router.add_post("/api/auth/sessions", handle_auth_sessions)
    app.router.add_get("/api/status", handle_status)
    app.router.add_get("/api/phone_connect", handle_phone_connect)
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
    app.router.add_post("/api/display/repair", handle_display_repair)
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
    app.router.add_post("/api/work_schedule/device_mode", handle_work_schedule_device_mode)
    app.router.add_post("/api/notify", handle_notify)
    app.router.add_post("/api/hydration_now", handle_hydration_now)
    app.router.add_post("/api/weather_clock", handle_weather_clock)
    app.router.add_post("/api/calendar_clock", handle_calendar_clock)
    app.router.add_post("/api/auto_cycle", handle_auto_cycle)
    app.router.add_get("/api/calendars", handle_calendars_list)
    app.router.add_get("/api/update/check", handle_update_check)
    app.router.add_post("/api/update/token", handle_update_token)
    app.router.add_post("/api/update/apply", handle_update_apply)
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
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    print(f"LOY Studio 2.0 running locally at http://127.0.0.1:{port}")
    if state.lan_enabled:
        lan_ip = _detect_lan_ip()
        print(f"Phone / LAN access enabled (password protected): http://{lan_ip}:{port}")
    web.run_app(create_app(), host=host, port=port)


if __name__ == "__main__":
    main()
