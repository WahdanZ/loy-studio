#!/usr/bin/env python3
"""
LOY Studio 2.0 — Native macOS Menu Bar Controller.

Provides 1-click Work Modes, Creative Live Hardware Clocks, Weather Clock,
Onboard Mic Visualizer, Temporary Alerts, Smart Context Toggle, Power & Brightness
directly from the macOS top menu bar (talks locally to 127.0.0.1:18420).

Run via: ./macos/loy_ctl.sh menubar
"""

from __future__ import annotations

import json
import urllib.request
import webbrowser

try:
    import rumps
except ImportError:
    raise SystemExit("Install rumps first: uv pip install --python .venv/bin/python rumps")

BASE_URL = "http://127.0.0.1:18420"

WORK_MODE_ITEMS = [
    ("Deep Focus", "09_mode_deep_focus.gif"),
    ("Coding / Dev", "10_mode_coding.gif"),
    ("ON AIR / Meeting", "11_mode_on_air_meeting.gif"),
    ("Do Not Disturb", "12_mode_do_not_disturb.gif"),
    ("Coffee Break", "13_mode_coffee_break.gif"),
    ("Pomodoro Flow", "14_mode_pomodoro.gif"),
    ("Gaming / Chill", "15_mode_gaming_chill.gif"),
    ("Music / Lo-Fi Vibe", "16_mode_music_vibe.gif"),
    ("Be Right Back (BRB)", "17_mode_away_brb.gif"),
    ("Done / Night", "18_mode_done_offline.gif"),
]

CLOCK_ITEMS = [
    ("Synthwave Sunset", "hw_synthwave_sun"),
    ("Neon Ring", "hw_neon_ring"),
    ("Sci-Fi Cockpit HUD", "hw_pipboy_hud"),
    ("Hacker Terminal", "hw_matrix_terminal"),
    ("Retro Handheld LCD", "hw_gameboy_retro"),
    ("Starlight Moon Night", "hw_galaxy_stars"),
    ("Arcade Scoreboard", "hw_arcade_cabinet"),
    ("Neon Heartbeat EKG", "hw_heart_bezel"),
]


def _post(path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=12) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get(path: str) -> dict:
    with urllib.request.urlopen(f"{BASE_URL}{path}", timeout=3) as resp:
        return json.loads(resp.read().decode("utf-8"))


class LoyMenuBarApp(rumps.App):
    def __init__(self) -> None:
        super().__init__("LOY", quit_button="Quit Menu Bar")
        self.status_item = rumps.MenuItem("Status: Checking...")
        self.ctx_toggle = rumps.MenuItem("Zero-Touch Mac Context", callback=self._toggle_ctx)

        modes_menu = rumps.MenuItem("Work Modes")
        for label, fname in WORK_MODE_ITEMS:
            modes_menu.add(rumps.MenuItem(label, callback=lambda _s, f=fname: self._safe_post("/api/gif_pack/send", {"file": f})))

        clocks_menu = rumps.MenuItem("Creative Live Clocks")
        for label, shape in CLOCK_ITEMS:
            clocks_menu.add(rumps.MenuItem(label, callback=lambda _s, sh=shape: self._safe_post("/api/hw_text_clock", {"shape": sh, "sync_first": True})))
        clocks_menu.add(None)
        clocks_menu.add(rumps.MenuItem("Matrix Rain Behind Clock", callback=lambda _: self._safe_post("/api/hw_text_clock", {"shape": "hw_minimal", "bg_gif": "03_matrix_rain.gif", "bg_mode": "behind"})))
        clocks_menu.add(rumps.MenuItem("Fire Digits Clock (Mask)", callback=lambda _: self._safe_post("/api/hw_text_clock", {"shape": "hw_minimal", "bg_gif": "04_retro_fire.gif", "bg_mode": "mask"})))
        clocks_menu.add(rumps.MenuItem("Live Pixel Weather Clock", callback=lambda _: self._safe_post("/api/weather_clock", {})))

        bri_menu = rumps.MenuItem("Brightness")
        for label, lvl in [("Night (2)", 2), ("Low (5)", 5), ("Medium (9)", 9), ("High (12)", 12), ("Max (15)", 15)]:
            bri_menu.add(rumps.MenuItem(label, callback=lambda _s, l=lvl: self._ctrl({"action": "brightness", "level": l})))

        self.menu = [
            self.status_item,
            None,
            rumps.MenuItem("Open LOY Studio Web UI", callback=lambda _: webbrowser.open(BASE_URL)),
            rumps.MenuItem("Apply Current Scheduled Mode", callback=lambda _: self._safe_post("/api/work_schedule/apply", {})),
            self.ctx_toggle,
            None,
            modes_menu,
            clocks_menu,
            None,
            rumps.MenuItem("Room Audio Mic Visualizer", callback=lambda _: self._ctrl({"action": "music_spectrum", "style": 0, "use_device_mic": True})),
            rumps.MenuItem("Flash DONE ✓ Alert (8s)", callback=lambda _: self._safe_post("/api/notify", {"title": "DONE", "subtitle": "READY", "style": "success", "duration_s": 8})),
            rumps.MenuItem("Flash Drink H2O Reminder", callback=lambda _: self._safe_post("/api/hydration_now", {"duration_s": 10})),
            None,
            bri_menu,
            rumps.MenuItem("Power On", callback=lambda _: self._ctrl({"action": "power", "on": True})),
            rumps.MenuItem("Power Off", callback=lambda _: self._ctrl({"action": "power", "on": False})),
        ]

    def _safe_post(self, path: str, payload: dict) -> None:
        try:
            _post(path, payload)
            self._refresh(None)
        except Exception as exc:
            rumps.notification("LOY Studio", "Action failed", str(exc))

    def _ctrl(self, payload: dict) -> None:
        self._safe_post("/api/control", payload)

    def _toggle_ctx(self, sender: rumps.MenuItem) -> None:
        new_val = not bool(sender.state)
        self._safe_post("/api/smart_config", {"context_enabled": new_val})
        sender.state = int(new_val)

    @rumps.timer(10)
    def _refresh(self, _sender) -> None:
        try:
            st = _get("/api/status")
            if st.get("connected"):
                self.title = "LOY ●"
                self.status_item.title = f"{st.get('device_name')} · Bri {st.get('brightness')}/15"
            else:
                self.title = "LOY ○"
                self.status_item.title = "Display Disconnected"
            sc = _get("/api/smart_config")
            self.ctx_toggle.state = int(bool(sc.get("config", {}).get("context_enabled")))
        except Exception:
            self.title = "LOY ✕"
            self.status_item.title = "Gateway Offline (port 18420)"


if __name__ == "__main__":
    LoyMenuBarApp().run()
