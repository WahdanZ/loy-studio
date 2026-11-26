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
import socket
import sys
import threading
import time
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

            webview.create_window(
                "LOY Studio — 64×64 LED Display",
                url,
                width=1240,
                height=840,
                min_size=(400, 640),
                background_color="#08090d",
            )
            webview.start()
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
