#!/usr/bin/env python3
"""Build a self-contained portable LOY Studio Desktop App (.app on macOS, .exe on Windows)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SEP = ";" if sys.platform == "win32" else ":"

cmd = [
    sys.executable,
    "-m",
    "PyInstaller",
    "--noconfirm",
    "--clean",
    "--windowed",
    "--name",
    "LOY Studio",
    "--add-data",
    f"{ROOT / 'app' / 'static'}{SEP}app/static",
    "--add-data",
    f"{ROOT / 'gifs'}{SEP}gifs",
    "--hidden-import",
    "bleak",
    "--hidden-import",
    "aiohttp",
    "--hidden-import",
    "PIL",
    "--hidden-import",
    "qrcode",
    "--hidden-import",
    "truststore",
    "--hidden-import",
    "certifi",
    "--collect-data",
    "certifi",
]

if sys.platform == "win32":
    cmd.append("--onefile")
    cmd += ["--hidden-import", "bleak.backends.winrt"]
elif sys.platform == "darwin":
    cmd += [
        "--hidden-import", "bleak.backends.corebluetooth",
        "--hidden-import", "EventKit",
        "--hidden-import", "PyObjCTools.AppHelper",
    ]
else:
    cmd += ["--hidden-import", "bleak.backends.bluezdbus"]

cmd.append(str(ROOT / "desktop_app.py"))
print("Running:", " ".join(cmd))
subprocess.check_call(cmd, cwd=str(ROOT))

if sys.platform == "darwin":
    import plistlib
    import re

    app_ver = "4.3.3"
    try:
        srv_txt = (ROOT / "app" / "server.py").read_text(encoding="utf-8")
        m = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', srv_txt)
        if m:
            app_ver = m.group(1)
    except Exception:
        pass

    app_bundle = ROOT / "dist" / "LOY Studio.app"
    plist_path = app_bundle / "Contents" / "Info.plist"
    if plist_path.exists():
        with plist_path.open("rb") as f:
            plist = plistlib.load(f)
        plist["NSBluetoothAlwaysUsageDescription"] = "LOY Studio uses Bluetooth LE to connect to your 64x64 LED pixel display."
        plist["NSBluetoothPeripheralUsageDescription"] = "LOY Studio uses Bluetooth LE to connect to your 64x64 LED pixel display."
        plist["NSLocalNetworkUsageDescription"] = "LOY Studio uses local Wi-Fi so your phone can control the LED display."
        plist["NSCalendarsUsageDescription"] = "LOY Studio checks upcoming meetings to flash 5-minute alerts on your LED display."
        plist["NSCalendarsFullAccessUsageDescription"] = "LOY Studio checks upcoming meetings to flash 5-minute alerts on your LED display."
        plist["NSAppleEventsUsageDescription"] = "LOY Studio reads active app and Calendar info for automatic desk modes."
        plist["CFBundleDisplayName"] = "LOY Studio"
        plist["CFBundleIdentifier"] = "com.loystudio.desktop"
        plist["CFBundleShortVersionString"] = app_ver
        plist["CFBundleVersion"] = app_ver
        with plist_path.open("wb") as f:
            plistlib.dump(plist, f)
        subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(app_bundle)], check=False)
        print(f"Patched Info.plist (v{app_ver}) with macOS Bluetooth & Local Network permissions.")

print("\nBuild complete! Check dist/ for your portable LOY Studio app.")
