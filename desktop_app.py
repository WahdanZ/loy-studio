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
import atexit
import json
import logging
import socket
import sys
import threading
import time
import urllib.error
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
from app.server import create_app, shutdown_ble, state

# Long enough for an in-flight frame + graceful BLE disconnect (see shutdown_ble lock_timeout)
SERVER_STOP_TIMEOUT_S = 12.0


def _stop_server(stop_evt: threading.Event, thread: threading.Thread) -> None:
    """Signal the server thread to disconnect BLE and stop, then wait for it. Idempotent."""
    stop_evt.set()
    if thread.is_alive() and thread is not threading.current_thread():
        thread.join(timeout=SERVER_STOP_TIMEOUT_S)


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
        state.bound_port = port  # used by the phone-pairing QR code
        app = create_app()
        app_runner = web.AppRunner(app)
        await app_runner.setup()
        site = web.TCPSite(app_runner, host=host, port=port)
        await site.start()
        ready_evt.set()
        while not stop_evt.is_set():
            await asyncio.sleep(0.25)
        # Clean BLE disconnect on app exit so hardware never stays stuck half-open.
        # (app_runner.cleanup() also runs the on_shutdown hook; shutdown_ble is idempotent.)
        await shutdown_ble("desktop app exit")
        await app_runner.cleanup()

    try:
        loop.run_until_complete(runner())
    finally:
        loop.close()


# Retain references so macOS Cocoa never garbage-collects the menu bar item
_MACOS_STATUS_ITEM = None
_MACOS_MENU_HANDLER = None


# Set by the menu bar once it exists: shows action failures instead of swallowing them.
_MENU_FEEDBACK = None


def _post_local_async(base_url: str, endpoint: str, payload: dict) -> None:
    def _worker() -> None:
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                f"{base_url}{endpoint}",
                data=data,
                headers={"Content-Type": "application/json", "X-LOY-Source": "Menu Bar"},
                method="POST",
            )
            # GIF/clock uploads over BLE can take ~10-30 s
            with urllib.request.urlopen(req, timeout=60):
                pass
        except urllib.error.HTTPError as exc:
            try:
                msg = json.loads(exc.read().decode("utf-8")).get("error", str(exc))
            except Exception:
                msg = str(exc)
            logging.getLogger("loy.menubar").warning("%s failed: %s", endpoint, msg)
            if _MENU_FEEDBACK:
                _MENU_FEEDBACK(f"⚠ {msg[:60]}")
        except Exception as exc:
            logging.getLogger("loy.menubar").warning("%s failed: %s", endpoint, exc)
            if _MENU_FEEDBACK:
                _MENU_FEEDBACK(f"⚠ {str(exc)[:60]}")

    threading.Thread(target=_worker, daemon=True).start()


def _status_line(base_url: str) -> str:
    try:
        with urllib.request.urlopen(f"{base_url}/api/status", timeout=3) as resp:
            st = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return "○ LOY Studio server not responding"
    if not st.get("connected"):
        return "○ Display not connected"
    title = (st.get("now_playing") or {}).get("title") or "Ready"
    power = "" if st.get("power_on", True) else " · screen off"
    return f"● {st.get('device_name') or 'Display'} · {title[:40]}{power} · ☀ {st.get('brightness', '?')}/15"


def _install_macos_menubar(base_url: str, win, on_quit=None) -> None:
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

        # Live status line (current mode + connection), refreshed every 5 s
        status_mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Connecting…", None, "")
        status_mi.setEnabled_(False)
        menu.addItem_(status_mi)
        menu.addItem_(NSMenuItem.separatorItem())
        feedback_until = [0.0]

        def show_feedback(text: str) -> None:
            feedback_until[0] = time.monotonic() + 8.0
            AppHelper.callAfter(status_mi.setTitle_, text)
            AppHelper.callAfter(item.button().setTitle_, "▣ LOY ⚠")

        global _MENU_FEEDBACK
        _MENU_FEEDBACK = show_feedback

        def _poll_status() -> None:
            while True:
                if time.monotonic() >= feedback_until[0]:
                    line = _status_line(base_url)
                    AppHelper.callAfter(status_mi.setTitle_, line)
                    AppHelper.callAfter(item.button().setTitle_, "▣ LOY" if line.startswith("●") else "▣ LOY ○")
                time.sleep(5.0)

        threading.Thread(target=_poll_status, daemon=True).start()

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
            ("🍽️ Lunch", "19_mode_lunch.gif"),
            ("🏋️ Workout", "20_mode_workout.gif"),
            ("📖 Reading", "21_mode_reading.gif"),
            ("📅 Meeting Soon", "23_mode_meeting_soon.gif"),
            ("⏺️ Recording", "24_mode_recording.gif"),
            ("🌴 Out of Office", "25_mode_out_of_office.gif"),
            ("🚗 On My Way", "26_mode_on_my_way.gif"),
            ("🚀 Ship It", "27_mode_ship_it.gif"),
            ("❌ Build Failed", "28_mode_build_failed.gif"),
            ("🎧 Lo-Fi Vibe", "16_mode_music_vibe.gif"),
            ("🎮 Gaming", "15_mode_gaming_chill.gif"),
            ("⏳ Away / BRB", "17_mode_away_brb.gif"),
            ("😴 Sleep", "22_mode_sleep.gif"),
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
            add_action(bri_menu, b_lbl, lambda v=b_val: _post_local_async(base_url, "/api/control", {"action": "brightness", "level": v}))

        add_action(menu, "⏻ Screen On", lambda: _post_local_async(base_url, "/api/control", {"action": "power", "on": True}))
        add_action(menu, "⏻ Screen Off", lambda: _post_local_async(base_url, "/api/control", {"action": "power", "on": False}))
        menu.addItem_(NSMenuItem.separatorItem())
        def quit_app() -> None:
            # NSApp.terminate_ calls exit() without returning to our code, so disconnect BLE
            # first (off the main thread so the UI doesn't freeze), then terminate.
            def _worker() -> None:
                if on_quit:
                    on_quit()
                AppHelper.callAfter(lambda: NSApplication.sharedApplication().terminate_(None))

            threading.Thread(target=_worker, daemon=True).start()

        add_action(menu, "Quit LOY Studio", quit_app)

        item.setMenu_(menu)

    AppHelper.callAfter(_build_on_main)


def _self_test() -> int:
    """CI smoke test for the frozen bundle: imports, routes, bundled assets, writable data dir.
    Never touches BLE, the network, or the GUI."""
    from app import server as srv

    problems = []
    try:
        app = srv.create_app()
        routes = {r.resource.canonical for r in app.router.routes() if r.resource is not None}
        for must in ("/api/status", "/api/connect", "/api/update/apply", "/ws"):
            if must not in routes:
                problems.append(f"missing route {must}")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"create_app failed: {exc!r}")
    if not (srv.STATIC_DIR / "index.html").is_file():
        problems.append(f"index.html not bundled at {srv.STATIC_DIR}")
    if not any(srv.GIFS_DIR.glob("*.gif")):
        problems.append(f"GIF pack not bundled at {srv.GIFS_DIR}")
    try:
        probe = srv.DATA_DIR / ".self_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        problems.append(f"data dir not writable: {exc}")
    print(json.dumps({"ok": not problems, "version": srv.APP_VERSION, "data_dir": str(srv.DATA_DIR), "problems": problems}))
    return 0 if not problems else 1


def main() -> None:
    if "--self-test" in sys.argv:
        sys.exit(_self_test())
    parser = argparse.ArgumentParser(description="LOY Studio Portable Desktop App")
    parser.add_argument("--port", type=int, default=18420)
    parser.add_argument("--no-lan", action="store_true", help="Bind to 127.0.0.1 only")
    parser.add_argument("--browser", action="store_true", help="Open in default browser instead of native window")
    args = parser.parse_args()

    port = _find_free_port(args.port)
    # Phones can reach the app on Wi-Fi, but LAN access stays LOCKED until the owner sets a
    # Wi-Fi password on this Mac (no default password). --no-lan binds to this Mac only.
    host = "127.0.0.1" if args.no_lan else "0.0.0.0"
    url = f"http://127.0.0.1:{port}"

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ready_evt = threading.Event()
    stop_evt = threading.Event()
    t = threading.Thread(target=_run_server_thread, args=(host, port, ready_evt, stop_evt), daemon=True)
    t.start()
    ready_evt.wait(timeout=10.0)
    stop_server = lambda: _stop_server(stop_evt, t)  # noqa: E731
    # Last line of defence: any normal interpreter exit still disconnects BLE gracefully
    atexit.register(stop_server)

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
            webview.start(func=lambda: _install_macos_menubar(url, win, on_quit=stop_server))
            stop_server()
            return
        except Exception:
            pass

    webbrowser.open(url)
    print(f"LOY Studio running at {url} (Ctrl+C to quit)")
    try:
        while t.is_alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        stop_server()


if __name__ == "__main__":
    main()
