#!/usr/bin/env python3
"""
LOY Studio 2.0 — Smart Desk Features, Mac Context Awareness, Webhook Alerts & Weather Clock.

100% local by default:
  - Mac Context Detection: active call/camera/mic, frontmost app (Coding/Meeting), HID idle time,
    and optional Apple Calendar upcoming meeting check.
  - Temporary Pixel Alert GIFs for `POST /api/notify` (success, error, warning, info, meeting, water).
  - Hydration water-drop reminder GIF generator.
  - Opt-in keyless Open-Meteo Weather fetcher + animated 64x64 pixel weather background for
    autonomous Hardware RTC Clocks (`type_bg = 4`).
"""

from __future__ import annotations

import datetime
import io
import json
import math
import re
import ssl
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw

RGB = Tuple[int, int, int]

CODING_APPS = {
    "code", "visual studio code", "cursor", "antigravity", "xcode", "terminal",
    "iterm2", "warp", "ghostty", "alacritty", "kitty", "pycharm", "intellij idea",
    "webstorm", "goland", "clion", "zed", "sublime text", "neovide",
}

MEETING_APPS = {
    "zoom.us", "facetime", "microsoft teams", "MSTeams", "webex", "around", "tuple",
}

# Crisp 5x7 pixel bitmap font for razor-sharp 64x64 alerts & weather readouts
FONT_5X7: Dict[str, List[str]] = {
    "A": [".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"],
    "B": ["####.", "#...#", "#...#", "####.", "#...#", "#...#", "####."],
    "C": [".####", "#....", "#....", "#....", "#....", "#....", ".####"],
    "D": ["####.", "#...#", "#...#", "#...#", "#...#", "#...#", "####."],
    "E": ["#####", "#....", "#....", "####.", "#....", "#....", "#####"],
    "F": ["#####", "#....", "#....", "####.", "#....", "#....", "#...."],
    "G": [".####", "#....", "#....", "#..##", "#...#", "#...#", ".####"],
    "H": ["#...#", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"],
    "I": [".###.", "..#..", "..#..", "..#..", "..#..", "..#..", ".###."],
    "J": ["..###", "...#.", "...#.", "...#.", "#..#.", "#..#.", ".##.."],
    "K": ["#...#", "#..#.", "#.#..", "##...", "#.#..", "#..#.", "#...#"],
    "L": ["#....", "#....", "#....", "#....", "#....", "#....", "#####"],
    "M": ["#...#", "##.##", "#.#.#", "#...#", "#...#", "#...#", "#...#"],
    "N": ["#...#", "##..#", "#.#.#", "#..##", "#...#", "#...#", "#...#"],
    "O": [".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."],
    "P": ["####.", "#...#", "#...#", "####.", "#....", "#....", "#...."],
    "Q": [".###.", "#...#", "#...#", "#...#", "#.#.#", "#..#.", ".##.#"],
    "R": ["####.", "#...#", "#...#", "####.", "#.#..", "#..#.", "#...#"],
    "S": [".####", "#....", "#....", ".###.", "....#", "....#", "####."],
    "T": ["#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."],
    "U": ["#...#", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."],
    "V": ["#...#", "#...#", "#...#", "#...#", "#...#", ".#.#.", "..#.."],
    "W": ["#...#", "#...#", "#...#", "#...#", "#.#.#", "##.##", "#...#"],
    "X": ["#...#", ".#.#.", "..#..", "..#..", "..#..", ".#.#.", "#...#"],
    "Y": ["#...#", "#...#", ".#.#.", "..#..", "..#..", "..#..", "..#.."],
    "Z": ["#####", "....#", "...#.", "..#..", ".#...", "#....", "#####"],
    "0": [".###.", "#..##", "#.#.#", "#.#.#", "##..#", "#...#", ".###."],
    "1": ["..#..", ".##..", "..#..", "..#..", "..#..", "..#..", ".###."],
    "2": [".###.", "#...#", "....#", "..##.", ".#...", "#....", "#####"],
    "3": ["####.", "....#", "...#.", "..##.", "....#", "#...#", ".###."],
    "4": ["#...#", "#...#", "#...#", "#####", "....#", "....#", "....#"],
    "5": ["#####", "#....", "####.", "....#", "....#", "#...#", ".###."],
    "6": [".###.", "#....", "####.", "#...#", "#...#", "#...#", ".###."],
    "7": ["#####", "....#", "...#.", "..#..", ".#...", ".#...", ".#..."],
    "8": [".###.", "#...#", "#...#", ".###.", "#...#", "#...#", ".###."],
    "9": [".###.", "#...#", "#...#", ".####", "....#", "....#", ".###."],
    ":": [".....", "..#..", "..#..", ".....", "..#..", "..#..", "....."],
    "-": [".....", ".....", ".....", ".###.", ".....", ".....", "....."],
    "+": [".....", "..#..", "..#..", "#####", "..#..", "..#..", "....."],
    "!": ["..#..", "..#..", "..#..", "..#..", "..#..", ".....", "..#.."],
    "?": [".###.", "#...#", "....#", "..##.", "..#..", ".....", "..#.."],
    ".": [".....", ".....", ".....", ".....", ".....", ".....", "..#.."],
    "%": ["##..#", "##.#.", "..#..", "..#..", ".#.##", "#..##", "....."],
    "°": [".##..", "#..#.", "#..#.", ".##..", ".....", ".....", "....."],
    " ": [".....", ".....", ".....", ".....", ".....", ".....", "....."],
}


def draw_bitmap_str(img: Image.Image, text: str, y: int, color: RGB, *, center: bool = True, x: int = 2) -> None:
    s = str(text or "").upper()[:10]
    if not s:
        return
    total_w = len(s) * 6 - 1
    sx = max(1, (img.width - total_w) // 2) if center else x
    px = img.load()
    for idx, ch in enumerate(s):
        glyph = FONT_5X7.get(ch, FONT_5X7[" "])
        gx = sx + idx * 6
        for ry, row in enumerate(glyph):
            for rx, bit in enumerate(row):
                if bit == "#":
                    tx, ty = gx + rx, y + ry
                    if 0 <= tx < img.width and 0 <= ty < img.height:
                        px[tx, ty] = color


def _frames_to_gif(frames: List[Image.Image], duration_ms: int = 100) -> bytes:
    pal = [f.convert("P", palette=Image.Palette.ADAPTIVE, colors=48, dither=Image.Dither.NONE) for f in frames]
    buf = io.BytesIO()
    pal[0].save(
        buf, format="GIF", save_all=True, append_images=pal[1:],
        duration=duration_ms, loop=0, disposal=2, optimize=True,
    )
    return buf.getvalue()


# ---------------------------------------------------------------------------
# 1. Local macOS Context Detection (Zero Cloud)
# ---------------------------------------------------------------------------

def get_macos_idle_seconds() -> int:
    """Return user HID idle time in seconds via ioreg (no permissions needed)."""
    try:
        out = subprocess.check_output(["ioreg", "-c", "IOHIDSystem", "-d", "4"], text=True, stderr=subprocess.DEVNULL, timeout=1.5)
        m = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', out)
        if m:
            return int(int(m.group(1)) / 1_000_000_000)
    except Exception:
        pass
    return 0


def get_macos_camera_or_call_active() -> bool:
    """Detect whether macOS camera hardware or active call host process (CptHost/FaceTime) is active."""
    # 1. Check Apple Silicon / Intel camera active state in ioreg
    try:
        out = subprocess.check_output(
            ["ioreg", "-r", "-n", "AppleH13CamIn", "-d", "1"], text=True, stderr=subprocess.DEVNULL, timeout=1.2
        )
        if '"FrontCameraStreaming" = Yes' in out or '"PowerState" = 4' in out:
            return True
    except Exception:
        pass
    # 2. Check Zoom active meeting host process `CptHost` or active FaceTime call
    try:
        ps = subprocess.check_output(["ps", "-ax", "-o", "comm="], text=True, stderr=subprocess.DEVNULL, timeout=1.2)
        if "CptHost" in ps:  # Zoom only spawns CptHost while inside a live meeting!
            return True
    except Exception:
        pass
    return False


def get_macos_frontmost_app() -> str:
    """Return the name of the frontmost macOS application."""
    script = 'tell application "System Events" to get name of first application process whose frontmost is true'
    try:
        return subprocess.check_output(["osascript", "-e", script], text=True, stderr=subprocess.DEVNULL, timeout=1.5).strip()
    except Exception:
        return ""


_cal_cache: Dict[str, Any] = {"ts": 0.0, "event": None}


def get_upcoming_calendar_event(within_minutes: int = 15) -> Optional[Dict[str, Any]]:
    """Query local macOS Calendar.app for an event starting within `within_minutes` (cached 120s)."""
    now = time.time()
    if now - _cal_cache["ts"] < 120:
        return _cal_cache["event"]
    script = f'''
    set nowDate to current date
    set endDate to nowDate + ({int(within_minutes)} * minutes)
    tell application "Calendar"
        repeat with cal in calendars
            set evs to (every event of cal whose start date ≥ nowDate and start date ≤ endDate)
            if (count of evs) > 0 then
                set ev to item 1 of evs
                set minsLeft to round (((start date of ev) - nowDate) / 60)
                return (summary of ev) & "||" & (minsLeft as string)
            end if
        end repeat
    end tell
    return ""
    '''
    event = None
    try:
        out = subprocess.check_output(["osascript", "-e", script], text=True, stderr=subprocess.DEVNULL, timeout=2.5).strip()
        if "||" in out:
            title, mins = out.split("||", 1)
            event = {"title": title.strip()[:18], "minutes_left": max(0, int(float(mins.strip())))}
    except Exception:
        pass
    _cal_cache["ts"] = now
    _cal_cache["event"] = event
    return event


def _get_windows_context() -> Tuple[str, int]:
    """Return (foreground_window_title, idle_seconds) on Windows via Win32 ctypes."""
    try:
        import ctypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
        idle_s = 0
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
            ms = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
            idle_s = max(0, int(ms / 1000))

        hwnd = ctypes.windll.user32.GetForegroundWindow()
        buf = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, 256)
        return buf.value.strip(), idle_s
    except Exception:
        return "", 0


def detect_mac_context(*, check_calendar: bool = False) -> Dict[str, Any]:
    if sys.platform == "win32":
        front_app, idle_s = _get_windows_context()
        front_lower = front_app.lower()
        in_call = any(k in front_lower for k in ("zoom", "teams", "meet", "webex", "slack huddle"))
        is_coding = any(k in front_lower for k in CODING_APPS)
        return {
            "front_app": front_app,
            "in_call": in_call,
            "is_coding": is_coding,
            "idle_seconds": idle_s,
            "next_event": None,
        }
    front_app = get_macos_frontmost_app()
    front_lower = front_app.lower()
    in_call = get_macos_camera_or_call_active() or (front_lower in MEETING_APPS)
    is_coding = any(k in front_lower for k in CODING_APPS)
    idle_s = get_macos_idle_seconds()
    next_event = get_upcoming_calendar_event(10) if check_calendar else None
    return {
        "front_app": front_app,
        "in_call": in_call,
        "is_coding": is_coding,
        "idle_seconds": idle_s,
        "next_event": next_event,
    }


# ---------------------------------------------------------------------------
# 2. Animated Notification / Webhook Alert GIFs & Hydration GIF
# ---------------------------------------------------------------------------

STYLE_THEMES: Dict[str, Tuple[RGB, RGB]] = {
    "success": ((35, 235, 115), (10, 80, 35)),
    "error":   ((255, 50, 70), (95, 12, 20)),
    "warning": ((255, 195, 35), (95, 65, 8)),
    "meeting": ((255, 45, 125), (90, 12, 45)),
    "water":   ((5, 217, 232), (8, 65, 95)),
    "info":    ((65, 155, 255), (15, 45, 95)),
}


def render_notification_gif(
    title: str = "ALERT",
    subtitle: str = "",
    style: str = "info",
    size: int = 64,
) -> bytes:
    """Generate a punchy 10-frame animated 64x64 alert GIF for `POST /api/notify`."""
    fg, dim = STYLE_THEMES.get(style, STYLE_THEMES["info"])
    frames: List[Image.Image] = []
    n = 10
    for f in range(n):
        img = Image.new("RGB", (size, size), (0, 0, 0))
        d = ImageDraw.Draw(img)
        pulse = 0.5 + 0.5 * math.sin(2 * math.pi * (f / n))
        border_col = tuple(min(255, int(dim[i] + (fg[i] - dim[i]) * pulse)) for i in range(3))

        # Pulsing double neon frame
        d.rounded_rectangle([1, 1, size - 2, size - 2], radius=5, outline=border_col, width=2)
        cx, cy = size // 2, 20

        if style == "success":
            d.ellipse([cx - 10, cy - 10, cx + 10, cy + 10], outline=fg, width=2)
            d.line([(cx - 5, cy), (cx - 1, cy + 4), (cx + 6, cy - 4)], fill=(255, 255, 255), width=2)
        elif style == "error":
            d.ellipse([cx - 10, cy - 10, cx + 10, cy + 10], outline=fg, width=2)
            d.line([(cx - 4, cy - 4), (cx + 4, cy + 4)], fill=(255, 255, 255), width=2)
            d.line([(cx + 4, cy - 4), (cx - 4, cy + 4)], fill=(255, 255, 255), width=2)
        elif style == "warning":
            d.polygon([(cx, cy - 10), (cx - 11, cy + 9), (cx + 11, cy + 9)], outline=fg)
            d.line([(cx, cy - 4), (cx, cy + 2)], fill=(255, 255, 255), width=2)
            d.rectangle([cx - 1, cy + 5, cx + 1, cy + 6], fill=(255, 255, 255))
        elif style == "meeting":
            shake = -1 if f % 2 == 0 else 1
            d.ellipse([cx - 7 + shake, cy - 8, cx + 7 + shake, cy + 6], fill=fg)
            d.rectangle([cx - 9 + shake, cy + 3, cx + 9 + shake, cy + 6], fill=fg)
            d.ellipse([cx - 2, cy + 7, cx + 2, cy + 10], fill=(255, 255, 255))
        elif style == "water":
            dy = (f % 5)
            d.polygon([(cx, cy - 10 + dy), (cx - 7, cy + 2 + dy), (cx + 7, cy + 2 + dy)], fill=fg)
            d.ellipse([cx - 7, cy - 2 + dy, cx + 7, cy + 9 + dy], fill=fg)
        else:
            r = 7 + int(pulse * 2)
            d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=fg, width=2)
            d.ellipse([cx - 2, cy - 2, cx + 2, cy + 2], fill=(255, 255, 255))

        if subtitle:
            draw_bitmap_str(img, title, 38, (255, 255, 255))
            draw_bitmap_str(img, subtitle, 49, fg)
        else:
            draw_bitmap_str(img, title, 43, (255, 255, 255))
        frames.append(img)
    return _frames_to_gif(frames, duration_ms=95)


def render_hydration_gif(size: int = 64) -> bytes:
    """Generate a smooth 12-frame animated water-drop hydration reminder GIF."""
    frames: List[Image.Image] = []
    n = 12
    for f in range(n):
        img = Image.new("RGB", (size, size), (0, 0, 0))
        d = ImageDraw.Draw(img)

        # Pixel glass cup
        d.line([(20, 18), (22, 43), (42, 43), (44, 18)], fill=(140, 225, 255), width=2)
        # Water level inside cup with gentle wave
        for x in range(23, 42):
            wy = 30 + int(round(math.sin((x * 0.45) + (f / n) * 2 * math.pi) * 1.4))
            d.line([(x, wy), (x, 41)], fill=(5, 185, 245))
            img.putpixel((x, wy), (190, 250, 255))

        # Falling water droplet from top into glass
        drop_y = 6 + int((f / n) * 20)
        if drop_y < 26:
            d.polygon([(32, drop_y - 4), (29, drop_y + 1), (35, drop_y + 1)], fill=(60, 230, 255))
            d.ellipse([29, drop_y, 35, drop_y + 5], fill=(60, 230, 255))
        else:
            # Splash ripples
            rad = (f - 6) * 2
            d.arc([32 - rad, 27, 32 + rad, 31], start=180, end=360, fill=(210, 255, 255))

        draw_bitmap_str(img, "DRINK H2O", 51, (60, 230, 255))
        frames.append(img)
    return _frames_to_gif(frames, duration_ms=100)


# ---------------------------------------------------------------------------
# 3. Opt-In Keyless Open-Meteo Weather + Animated Pixel Weather Clock BG
# ---------------------------------------------------------------------------

def _build_ssl_context() -> ssl.SSLContext:
    """Use OS native trust store (macOS Keychain / Windows Cert Store, including corporate CAs) or bundled certifi."""
    try:
        import truststore  # type: ignore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:
        pass
    try:
        import certifi  # type: ignore

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def fetch_open_meteo_weather(city: str) -> Dict[str, Any]:
    """Fetch current temperature & WMO weather code from keyless Open-Meteo API."""
    ctx = _build_ssl_context()
    q = urllib.parse.quote(city.strip() or "Berlin")
    geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={q}&count=1&language=en&format=json"
    with urllib.request.urlopen(geo_url, timeout=6, context=ctx) as r:
        geo = json.loads(r.read().decode("utf-8"))
    results = geo.get("results") or []
    if not results:
        raise ValueError(f"City not found: {city}")
    loc = results[0]
    lat, lon = loc["latitude"], loc["longitude"]
    name = loc.get("name", city)

    wx_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,weather_code,is_day"
    with urllib.request.urlopen(wx_url, timeout=6, context=ctx) as r:
        wx = json.loads(r.read().decode("utf-8"))
    cur = wx.get("current") or {}
    code = int(cur.get("weather_code", 0))
    temp_c = round(float(cur.get("temperature_2m", 20.0)))
    is_day = bool(cur.get("is_day", 1))
    kind = _wmo_to_kind(code, is_day)
    return {"city": name, "temp_c": temp_c, "weather_code": code, "kind": kind, "is_day": is_day}


def _wmo_to_kind(code: int, is_day: bool) -> str:
    if code == 0:
        return "clear_day" if is_day else "clear_night"
    if code in (1, 2, 3, 45, 48):
        return "cloudy"
    if code in (71, 73, 75, 77, 85, 86):
        return "snow"
    if code in (95, 96, 99):
        return "storm"
    return "rain"


def render_weather_clock_bg_gif(temp_c: int, kind: str = "clear_day", city: str = "", size: int = 64) -> bytes:
    """
    Render a 12-frame 64x64 animated pixel weather scene in the lower half (y=32..63)
    leaving the top half clean for the autonomous hardware RTC `{hh}:{mm}` clock (`type_bg = 4`).
    """
    frames: List[Image.Image] = []
    n = 12
    temp_str = f"{temp_c:+d}°C" if temp_c < 0 else f"{temp_c}°C"
    city_short = (city or "").strip()[:6].upper()

    for f in range(n):
        img = Image.new("RGB", (size, size), (0, 0, 0))
        d = ImageDraw.Draw(img)
        t = f / n

        # Subtle horizon divider between top RTC clock (y=2..28) and bottom weather scene (y=31..62)
        d.line([(4, 29), (size - 5, 29)], fill=(25, 45, 70))

        # Left side (x=4..24, y=33..58): animated weather icon
        icx, icy = 14, 46
        if kind == "clear_day":
            d.ellipse([icx - 6, icy - 6, icx + 6, icy + 6], fill=(255, 205, 35))
            for k in range(8):
                ang = 2 * math.pi * (k / 8.0 + t * 0.25)
                x0, y0 = int(icx + 8 * math.cos(ang)), int(icy + 8 * math.sin(ang))
                x1, y1 = int(icx + 11 * math.cos(ang)), int(icy + 11 * math.sin(ang))
                d.line([(x0, y0), (x1, y1)], fill=(255, 235, 110))
        elif kind == "clear_night":
            d.ellipse([icx - 7, icy - 7, icx + 7, icy + 7], fill=(255, 225, 110))
            d.ellipse([icx - 3, icy - 9, icx + 9, icy + 3], fill=(0, 0, 0))
            if f % 2 == 0:
                img.putpixel((icx - 10, icy - 7), (200, 220, 255))
                img.putpixel((icx + 9, icy + 6), (255, 255, 200))
        elif kind == "cloudy":
            dx = int(round(math.sin(2 * math.pi * t) * 2))
            d.ellipse([icx - 9 + dx, icy - 3, icx + 1 + dx, icy + 6], fill=(165, 185, 210))
            d.ellipse([icx - 4 + dx, icy - 7, icx + 7 + dx, icy + 5], fill=(215, 230, 245))
            d.rectangle([icx - 7 + dx, icy + 1, icx + 7 + dx, icy + 6], fill=(195, 210, 230))
        elif kind == "snow":
            d.ellipse([icx - 8, icy - 9, icx + 6, icy - 1], fill=(190, 205, 225))
            for k, sx in enumerate((icx - 6, icx - 1, icx + 4)):
                sy = icy + 1 + ((f * 2 + k * 4) % 11)
                if sy < 61:
                    d.rectangle([sx, sy, sx + 1, sy + 1], fill=(240, 250, 255))
        elif kind == "storm":
            d.ellipse([icx - 8, icy - 9, icx + 7, icy - 1], fill=(110, 120, 145))
            if f % 3 == 0:
                d.line([(icx, icy - 1), (icx - 3, icy + 5), (icx + 2, icy + 5), (icx - 1, icy + 11)], fill=(255, 235, 60), width=2)
        else:  # rain
            d.ellipse([icx - 8, icy - 9, icx + 7, icy - 1], fill=(135, 165, 205))
            for k, rx in enumerate((icx - 6, icx - 1, icx + 4)):
                ry = icy + ((f * 3 + k * 4) % 11)
                if ry < 60:
                    d.line([(rx, ry), (rx - 1, min(61, ry + 3))], fill=(40, 185, 255))

        # Right side (x=27..63): crisp temperature + up to 6-char city name
        draw_bitmap_str(img, temp_str, 35, (255, 255, 255), center=False, x=28)
        if city_short:
            draw_bitmap_str(img, city_short, 48, (90, 200, 255), center=False, x=27)

        frames.append(img)
    return _frames_to_gif(frames, duration_ms=110)
