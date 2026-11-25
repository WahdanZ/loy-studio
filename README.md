# LOY Studio — Offline `64×64` LED Pixel Display Controller & Creative Studio

A 100% local, privacy-first controller, creative pixel studio, and smart-desk automation suite for **`64×64` RGB LED Matrix Displays** over **Bluetooth Low Energy (BLE)**. Control your display from macOS or any phone on your local Wi-Fi — with zero cloud accounts, zero telemetry, and zero external dependencies.

---

## App Screenshots

### Desktop Studio
| Home Dashboard & Instant Desk Modes | 15 Live Autonomous Hardware Clocks |
| :---: | :---: |
| ![Desktop Home](docs/screenshots/desktop_home.png) | ![Desktop Clocks](docs/screenshots/desktop_clocks.png) |

### Mobile PWA (iPhone / Android over Local Wi-Fi)
| Mobile Home & Floating Glass Dock | Touch & Multi-Touch Pixel Studio |
| :---: | :---: |
| <img src="docs/screenshots/mobile_home.png" width="300" alt="Mobile Home" /> | <img src="docs/screenshots/mobile_create.png" width="300" alt="Mobile Create Studio" /> |

---

## Supported Display Hardware

- **Display Panel**: `64×64` RGB LED Pixel Matrix (`4,096` addressable LEDs, Bluetooth LE `YS*` / `EXYC-A` compatible panels)
- **On-Device Capabilities**:
  - Autonomous hardware **Real-Time Clock (RTC)** that keeps ticking offline
  - Static pixel artwork, multi-frame **animated GIFs**, and **layered clock + GIF** compositing (animated background behind clock or masked inside clock digits)
  - Built-in firmware clock faces, hardware brightness (`0–15`), orientation, screen power, arcade games, and room-audio mic visualizer

---

## Key Features & Upgrades

- **Mobile-First v3.0 OLED UI/UX**:
  - Floating 5-tab iOS-style glass dock on phones (`Home`, `Explore`, center `Create` FAB, `Schedule`, `Smart`) and full sidebar workspace on desktop.
  - Password-protected local Wi-Fi access (`--lan`) with QR code pairing — loopback (`127.0.0.1`) auto-trusted on Mac, phones unlock once with your password.
- **Multi-Touch & Fullscreen Drawing Studio**:
  - Responsive `64×64` LED simulator canvas with **Full Draw** edge-to-edge fullscreen mode and an inline quick color strip.
  - **Multi-touch support**: draw with multiple fingers simultaneously, **2-finger pinch-to-resize & pan** on floating text/emojis, and **Procreate-style 2-finger tap to Undo**.
  - **Movable Text & Animated Emojis**: drag & drop custom text or 20 emojis anywhere on the canvas, apply live motion effects (`Pulse`, `Bounce`, `Wiggle`, `Float`, `Spin`, `Sparkle`), or pick from 8 one-tap **Animated Emoji GIFs** + 6 editable **Pixel Art Templates**.
  - **Visual Animation Storyboard & 1-Tap Pixel Scenes**: 4-column visual frame grid with live **Speed + Brightness** sliders, 6 one-tap looping `64×64` scenes (**Synthwave Highway**, **Kaleidoscope**, **Plasma Rainbow**, **Cozy Campfire**, **Cyber Rain City**, **Pixel Aquarium**), an **Animated Neon Sign Builder** (`OPEN`, `CLOSED`, `NEW`, `LIVE`, custom text), onion skinning, mirror symmetry, GIF export, and **Photo / Video-to-LED** converter.
- **15 Autonomous Hardware RTC Clocks**:
  - Synthwave Sunset, Neon Ring, Sci-Fi HUD, Hacker Terminal, Retro Handheld, Starlight Night, Arcade Board, Heartbeat EKG, Gradient Pill, Flip Cards, Cyber Frame, Giant Stacked, HH:MM + Sec, Date + Time, and Minimal Big.
  - Combine any clock with an animated GIF **behind the clock** or **masked inside the digits**.
- **18 Built-In `64×64` Work Modes & Pixel Art GIFs**:
  - 10 Desk Work Modes (`Deep Focus`, `Coding`, `ON AIR`, `DND`, `Coffee Break`, `Pomodoro Flow`, `Gaming`, `Lo-Fi Vibe`, `BRB`, `Done/Night`) + 8 animated pixel visuals (Cyber Eyes, Matrix Rain, Retro Fire, Starfield, Rainbow Wave, Pac-Man, Equalizer).
- **Time & Day Auto-Mode Scheduler**:
  - 24-hour visual timeline, per-day (`M–S`) toggle pills, automatic brightness scheduling (e.g., night dimming), 1-click weekly presets, and per-rule live preview.
- **Smart Desk Automation & macOS Menu Bar**:
  - Zero-touch Mac context detection (auto `ON AIR` when Camera/Zoom is active, auto `Coding` in IDEs/Terminals, auto `BRB` when idle, Apple Calendar 5-minute meeting alerts).
  - Webhook alert flash (`POST /api/notify`) that displays a temporary alert and auto-restores the previous mode, plus optional keyless Open-Meteo Pixel Weather + RTC Clock and Hydration reminders.
  - Native macOS Menu Bar controller (`macos/menubar_app.py`) and `launchd` login auto-start (`macos/loy_ctl.sh`).

---

## Tech Stack & Framework

| Layer | Technology |
| :--- | :--- |
| **Backend Gateway** | Python 3.12, **FastAPI**, **Uvicorn**, WebSockets |
| **Bluetooth LE Transport** | **Bleak** (macOS CoreBluetooth) with reliable ACK-paced streaming & auto-reconnect |
| **Graphics & Media Engine** | **Pillow (PIL)** for `64×64` rendering, clock compositing, quantization & GIF encoding; optional **FFmpeg** for video import |
| **Frontend UI / PWA** | Vanilla **HTML5 / CSS3 / JavaScript** (zero build step), Canvas 2D LED simulator, Service Worker PWA |
| **macOS Integration** | **rumps** Menu Bar app + `launchd` service (`macos/loy_ctl.sh`) |
| **Testing** | **pytest** automated protocol & framing test suite |

---

## Project Structure

```text
.
├── app/                # FastAPI server, creative engine, smart desk automation & PWA frontend
├── protocol/           # BLE framing, codecs, display commands & transport layer
├── gifs/               # 18 built-in 64×64 pixel-art & Work Mode animated GIFs
├── macos/              # macOS Menu Bar app, launchd plist template & loy_ctl.sh helper
├── poc/                # Command-line controller (loy_cli.py)
├── tools/              # Pixel-art GIF generators & utility scripts
├── docs/screenshots/   # Desktop & mobile UI screenshots
└── tests/              # Automated pytest suite
```

---

## Quick Start

```bash
# 1. Create virtualenv & install dependencies
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Run unit tests
pytest -q tests/test_protocol.py

# 3. Start LOY Studio (localhost only, or add --lan for password-protected Wi-Fi phone access)
python app/server.py --lan --port 18420

# 4. Optional: launch macOS Menu Bar app or enable auto-start at login
./macos/loy_ctl.sh menubar
./macos/loy_ctl.sh install-login

# 5. Optional: trigger a temporary alert from any script
./macos/loy_ctl.sh notify "BUILD OK" "ALL PASS" success
```
