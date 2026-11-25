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
]

if sys.platform == "win32":
    cmd.append("--onefile")
    cmd += ["--hidden-import", "bleak.backends.winrt"]
elif sys.platform == "darwin":
    cmd += ["--hidden-import", "bleak.backends.corebluetooth"]
else:
    cmd += ["--hidden-import", "bleak.backends.bluezdbus"]

cmd.append(str(ROOT / "desktop_app.py"))
print("Running:", " ".join(cmd))
subprocess.check_call(cmd, cwd=str(ROOT))
print("\nBuild complete! Check dist/ for your portable LOY Studio app.")
