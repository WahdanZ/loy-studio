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
    cmd += ["--hidden-import", "bleak.backends.corebluetooth", "--hidden-import", "EventKit"]
else:
    cmd += ["--hidden-import", "bleak.backends.bluezdbus"]

cmd.append(str(ROOT / "desktop_app.py"))
print("Running:", " ".join(cmd))
subprocess.check_call(cmd, cwd=str(ROOT))

if sys.platform == "darwin":
    import plistlib

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
        with plist_path.open("wb") as f:
            plistlib.dump(plist, f)
        subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(app_bundle)], check=False)
        print("Patched Info.plist with macOS Bluetooth & Local Network permissions.")

print("\nBuild complete! Check dist/ for your portable LOY Studio app.")
