# LOY Studio 2.0 — Privacy-First Pixel Display Studio & YS/YSTP01 BLE Protocol

A complete reverse-engineering & privacy audit of **LOY PLAY v1.7.15 (`com.yskj.loy`)**, a clean-room Python **YS / YSTP01** BLE protocol library, CLI controller, and **LOY Studio 2.0** — a 100% local, privacy-first web & macOS controller verified on **`64×64` `EXYC-A` (`YS*L`)** hardware.

## Highlights

- **100% Offline & Private by Default**: Zero telemetry, zero accounts, zero mandatory cloud calls (optional `--lan` mode uses cryptographic QR token pairing over local Wi-Fi).
- **15 Autonomous Hardware RTC Clocks**: Synthwave Sunset, Neon Ring, Sci-Fi HUD, Hacker Terminal, Retro Handheld LCD, Starlight Night, Arcade Scoreboard, Heartbeat EKG, Gradient Pill, Flip Cards & more — plus **Animated GIFs Behind (`type_bg = 4`)** or **Masked Inside Clock Digits (`type_bg = 1`)** ticking offline on the panel's own RTC.
- **18 Built-In `64×64` Pixel-Art GIFs**: Includes 10 Work Modes (`Deep Focus`, `Coding`, `ON AIR`, `DND`, `Coffee Break`, `Pomodoro Flow`, `Gaming`, `Music Vibe`, `BRB`, `Done/Night`) + Cyber Eyes, Matrix Rain, Retro Fire, Starfield Warp, Rainbow Wave, Pac-Man & Equalizer.
- **Time & Day Auto-Mode Scheduler**: Dedicated **`◷ Schedule`** tab with a 24-hour visual timeline, per-day chips (`M–S`), brightness automation, and 1-click weekly presets.
- **Smart Desk Suite**:
  - **Zero-Touch Mac Context**: Auto `ON AIR` when macOS Camera / Zoom (`CptHost`) is active, Auto `Coding` in IDEs/Terminals, Auto `BRB` on HID idle, and Apple Calendar 5-min meeting alerts.
  - **Webhook Alert Flash (`POST /api/notify`)**: Flash temporary animated alerts in RAM Slot 2 and auto-restore Slot 1.
  - **Live Pixel Weather + RTC Clock** (opt-in keyless Open-Meteo), **Hydration Reminder**, **FFmpeg Video-to-LED Studio**, and **1-Click Hardware Mic Visualizer**.
- **Native macOS Integration**: Top Menu Bar app (`macos/menubar_app.py`) + `launchd` login auto-start (`macos/loy_ctl.sh`).

## Project Structure

```text
.
├── app/                # LOY Studio 2.0 server, creative engine, smart desk features & PWA UI
├── protocol/           # Clean-room YS / YSTP01 BLE framing, codecs, commands & transport
├── poc/                # CLI controller (loy_cli.py)
├── gifs/               # 18 crisp 64×64 pixel-art & Work Mode animated GIFs
├── macos/              # macOS Menu Bar controller, launchd plist & loy_ctl.sh helper
├── tools/              # Packet capture decoder & pixel-art GIF generators
├── research/           # 12 evidence-backed reverse-engineering & privacy audit reports
└── tests/              # Automated pytest protocol & framing suite
```

## Quick Start

```bash
# 1. Create virtualenv & install dependencies
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Run unit tests
pytest -q tests/test_protocol.py

# 3. Start LOY Studio 2.0 (localhost only, or add --lan for Phone QR pairing)
python app/server.py --lan --port 18420

# 4. Optional: launch macOS Menu Bar icon or enable auto-start at login
./macos/loy_ctl.sh menubar
./macos/loy_ctl.sh install-login

# 5. Optional: trigger a temporary 8s alert from any script
./macos/loy_ctl.sh notify "BUILD OK" "ALL PASS" success
```
