#!/usr/bin/env python3
"""
LOY Studio — Portable Desktop App Launcher (macOS & Windows).

Runs the offline BLE gateway & web studio locally on 127.0.0.1:18420 (with
optional --lan for phone Wi-Fi control) and opens LOY Studio in a native
desktop window via pywebview (falling back to the system browser if needed).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

try:
    import truststore  # type: ignore

    truststore.inject_into_ssl()
except Exception:
    pass

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from aiohttp import web
from app.server import create_app, state


def _find_free_port(preferred: int = 18420) -> int:
    for p in (preferred, preferred + 1, preferred + 2, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", p))
                return s.getsockname()[1]
            except OSError:
                continue
    return preferred


def _run_server_thread(host: str, port: int, ready_evt: threading.Event, stop_evt: threading.Event) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def runner() -> None:
        state.lan_enabled = host == "0.0.0.0"
        state.port = port
        app = create_app()
        app_runner = web.AppRunner(app)
        await app_runner.setup()
        site = web.TCPSite(app_runner, host=host, port=port)
        await site.start()
        ready_evt.set()
        while not stop_evt.is_set():
            await asyncio.sleep(0.25)
        # Clean BLE disconnect on app exit so hardware never stays stuck half-open
        if state.transport and state.transport.is_connected:
            try:
                await state.transport.disconnect()
            except Exception:
                pass
        await app_runner.cleanup()

    try:
        loop.run_until_complete(runner())
    finally:
        loop.close()


# Retain references so macOS Cocoa never garbage-collects the menu bar item
_MACOS_STATUS_ITEM = None
_MACOS_MENU_HANDLER = None


def _post_local_async(base_url: str, endpoint: str, payload: dict) -> None:
    def _worker() -> None:
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                f"{base_url}{endpoint}",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=12):
                pass
        except Exception:
            pass

    threading.Thread(target=_worker, daemon=True).start()


def _install_macos_menubar(base_url: str, win) -> None:
    """Attach a native macOS top Menu Bar icon (`▣ LOY`) directly inside LOY Studio.app."""
    if sys.platform != "darwin":
        return
    try:
        import objc  # type: ignore
        from AppKit import (  # type: ignore
            NSApplication,
            NSMenu,
            NSMenuItem,
            NSStatusBar,
            NSVariableStatusItemLength,
        )
        from Foundation import NSObject  # type: ignore
        from PyObjCTools import AppHelper  # type: ignore
    except Exception:
        return

    actions: dict[int, callable] = {}

    class _MenuTarget(NSObject):
        @objc.IBAction
        def handleItem_(self, sender) -> None:
            fn = actions.get(int(sender.tag()))
            if fn:
                fn()

    def _build_on_main() -> None:
        global _MACOS_STATUS_ITEM, _MACOS_MENU_HANDLER
        handler = _MenuTarget.alloc().init()
        _MACOS_MENU_HANDLER = handler

        bar = NSStatusBar.systemStatusBar()
        item = bar.statusItemWithLength_(NSVariableStatusItemLength)
        item.button().setTitle_("▣ LOY")
        _MACOS_STATUS_ITEM = item

        menu = NSMenu.alloc().init()
        tag_counter = [100]

        def add_action(target_menu, title: str, fn) -> None:
            t_id = tag_counter[0]
            tag_counter[0] += 1
            actions[t_id] = fn
            mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, "handleItem:", "")
            mi.setTarget_(handler)
            mi.setTag_(t_id)
            target_menu.addItem_(mi)

        def add_submenu(parent_menu, title: str) -> object:
            sub = NSMenu.alloc().initWithTitle_(title)
            mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, None, "")
            mi.setSubmenu_(sub)
            parent_menu.addItem_(mi)
            return sub

        def show_window() -> None:
            NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            try:
                win.show()
                win.restore()
            except Exception:
                pass

        add_action(menu, "Open LOY Studio Window", show_window)
        menu.addItem_(NSMenuItem.separatorItem())

        # Clocks submenu (quick 1-click switch!)
        clocks_menu = add_submenu(menu, "🕒 Clocks & Live Widgets")
        add_action(clocks_menu, "📅 Workday Calendar + Clock", lambda: _post_local_async(base_url, "/api/calendar_clock", {}))
        add_action(clocks_menu, "🌤️ Live Weather + Clock", lambda: _post_local_async(base_url, "/api/weather_clock", {}))
        add_action(clocks_menu, "🌅 Synthwave Sun Clock", lambda: _post_local_async(base_url, "/api/hw_text_clock", {"shape": "hw_synthwave_sun", "color": "#38bdf8", "accent": "#ec4899"}))
        add_action(clocks_menu, "💻 Hacker Terminal Clock", lambda: _post_local_async(base_url, "/api/hw_text_clock", {"shape": "hw_matrix_terminal", "color": "#10b981", "accent": "#34d399"}))
        add_action(clocks_menu, "✨ Starlight Night Clock", lambda: _post_local_async(base_url, "/api/hw_text_clock", {"shape": "hw_galaxy_stars", "color": "#fde047", "accent": "#38bdf8"}))

        # Work Modes submenu
        modes_menu = add_submenu(menu, "⚡ Work Modes & Pixel Art")
        for label, fname in [
            ("🎯 Deep Focus", "09_mode_deep_focus.gif"),
            ("👨‍💻 Coding (</CODE>)", "10_mode_coding.gif"),
            ("🔴 ON AIR (Meeting)", "11_mode_on_air_meeting.gif"),
            ("⛔ Do Not Disturb", "12_mode_do_not_disturb.gif"),
            ("☕ Coffee Break", "13_mode_coffee_break.gif"),
            ("🎧 Lo-Fi Vibe", "16_mode_music_vibe.gif"),
            ("🎮 Gaming", "15_mode_gaming_chill.gif"),
            ("⏳ Away / BRB", "17_mode_away_brb.gif"),
            ("🌙 Done / Night", "18_mode_done_offline.gif"),
            ("👾 Cyber Eyes", "01_cyber_eyes.gif"),
            ("🟩 Matrix Rain", "03_matrix_rain.gif"),
            ("🌈 Rainbow Wave", "05_rainbow_wave.gif"),
        ]:
            add_action(modes_menu, label, lambda f=fname: _post_local_async(base_url, "/api/gif_pack/send", {"filename": f}))

        # Top-level favorites for instant 1-click switching!
        menu.addItem_(NSMenuItem.separatorItem())
        add_action(menu, "📅 Workday Calendar + Clock", lambda: _post_local_async(base_url, "/api/calendar_clock", {}))
        add_action(menu, "🌤️ Live Weather + Clock", lambda: _post_local_async(base_url, "/api/weather_clock", {}))
        add_action(menu, "🔴 ON AIR (Meeting)", lambda: _post_local_async(base_url, "/api/gif_pack/send", {"filename": "11_mode_on_air_meeting.gif"}))
        add_action(menu, "👨‍💻 Coding Mode", lambda: _post_local_async(base_url, "/api/gif_pack/send", {"filename": "10_mode_coding.gif"}))
        add_action(menu, "💧 Drink Water Flash", lambda: _post_local_async(base_url, "/api/hydration_now", {"duration_s": 10}))
        menu.addItem_(NSMenuItem.separatorItem())

        # Brightness submenu
        bri_menu = add_submenu(menu, "☀️ Brightness")
        for b_lbl, b_val in [("Night (2)", 2), ("Low (5)", 5), ("Medium (9)", 9), ("High (12)", 12), ("Max (15)", 15)]:
            add_action(bri_menu, b_lbl, lambda v=b_val: _post_local_async(base_url, "/api/control", {"action": "brightness", "value": v}))

        add_action(menu, "⏻ Screen On", lambda: _post_local_async(base_url, "/api/control", {"action": "power", "on": True}))
        add_action(menu, "⏻ Screen Off", lambda: _post_local_async(base_url, "/api/control", {"action": "power", "on": False}))
        menu.addItem_(NSMenuItem.separatorItem())
        add_action(menu, "Quit LOY Studio", lambda: NSApplication.sharedApplication().terminate_(None))

        item.setMenu_(menu)

    AppHelper.callAfter(_build_on_main)


def main() -> None:
    parser = argparse.ArgumentParser(description="LOY Studio Portable Desktop App")
    parser.add_argument("--port", type=int, default=18420)
    parser.add_argument("--no-lan", action="store_true", help="Bind to 127.0.0.1 only")
    parser.add_argument("--browser", action="store_true", help="Open in default browser instead of native window")
    args = parser.parse_args()

    port = _find_free_port(args.port)
    host = "127.0.0.1" if args.no_lan else "0.0.0.0"
    url = f"http://127.0.0.1:{port}"

    ready_evt = threading.Event()
    stop_evt = threading.Event()
    t = threading.Thread(target=_run_server_thread, args=(host, port, ready_evt, stop_evt), daemon=True)
    t.start()
    ready_evt.wait(timeout=10.0)

    if not args.browser:
        try:
            import webview  # type: ignore

            win = webview.create_window(
                "LOY Studio — 64×64 LED Display",
                url,
                width=1240,
                height=840,
                min_size=(400, 640),
                background_color="#08090d",
            )
            webview.start(func=lambda: _install_macos_menubar(url, win))
            stop_evt.set()
            t.join(timeout=3.0)
            return
        except Exception:
            pass

    webbrowser.open(url)
    print(f"LOY Studio running at {url} (Ctrl+C to quit)")
    try:
        while t.is_alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        stop_evt.set()
        t.join(timeout=3.0)


if __name__ == "__main__":
    main()
