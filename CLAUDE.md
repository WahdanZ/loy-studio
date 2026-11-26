# CLAUDE.md — LOY Studio Project Guide

This file gives Claude (Claude Code / Claude Chat) complete architectural context, hardware constraints, and review guidelines for **LOY Studio**.

---

## 1. What This Project Is

**LOY Studio** is a 100% offline, privacy-first controller for `64×64` RGB LED Matrix pixel displays (`EXYC-A` / `YS*L` BLE protocol). It ships as:
1. **Portable Desktop App** (`desktop_app.py` + `build_portable.py` via PyInstaller & `pywebview`) — `.app` on macOS (with native `NSStatusBar` menu bar icon & EventKit Calendar picker) and `.exe` on Windows, with an **in-app auto-updater** backed by GitHub Releases.
2. **Responsive Web / Mobile PWA** (`app/static/index.html`, `app.js`, `style.css`) — local `aiohttp` server (`127.0.0.1:18420`, optional `--lan` with PIN/session auth + WebSocket live sync across Mac, Menu Bar, iPhone, and Google Home).
3. **Local Google Home / Nest Hub Matter Bridge** (`matter/bridge.mjs` using `@matter/main` + `@matter/nodejs`) — exposes the display and preset modes as local Matter switches.

---

## 2. Repository Map

| Path | Responsibility |
|---|---|
| `protocol/` | Pure-Python BLE protocol library: packet framing, `YSTP01` image encoding, multi-region hardware RTC clocks, GIF/text programs, `TLV 0x16` (`PERIOD_PRO`) on-device Flash schedules, `BleTransport`, and `MockTransport`. |
| `app/server.py` | Main `aiohttp` HTTP + WebSocket backend (`APP_VERSION`, BLE state & lock, Slot 1↔2 double-buffering, `state.now_playing` sync, LAN auth, smart automation loop, on-device Flash scheduling, and in-app self-updater). |
| `app/creative.py` | Procedural pixel effects, image/video-to-GIF conversion, palette extraction, local system stats, and macOS Now Playing helper. |
| `app/smart_features.py` | Open-Meteo weather clock rendering (using `truststore` + `certifi` for corporate/Keychain SSL), macOS EventKit & `.ics` Calendar clock/alerts, active app context detection, and hydration/notification GIF generators. |
| `app/static/` | Vanilla JS/CSS/HTML v4.0 "Effortless Control" UI: 4 tabs (`Now Playing`, `Gallery`, `Create Studio`, `Auto Routines`), live screen bezel mirror with ticking clock overlay, multi-touch mobile pixel editor, storyboard grid, and device/update settings. |
| `desktop_app.py` | Launches the local `aiohttp` server in a background thread, opens `pywebview`, and registers the native macOS `▣ LOY` menu bar menu. |
| `build_portable.py` | PyInstaller bundler for macOS `.app` (patches `Info.plist` permissions + version + ad-hoc `codesign`) and Windows `.exe`. |
| `matter/bridge.mjs` | Local Matter bridge exposing display power & modes to Google Home / Nest Hub. |
| `app/security.py` / `app/storage.py` / `app/update_signing.py` | LAN auth (PBKDF2 password, per-device sessions, rate limit, Host/Origin guards) / atomic JSON persistence / updater SHA256SUMS + Ed25519 verification. |
| `tests/` | `test_protocol.py` (protocol, framing, MockTransport, safety guards) and `test_server.py` (slot invariants, auth middleware, scheduler, updater signing). |
| `.github/workflows/build-portable.yml` | CI test job + macOS/Windows portable release builds triggered on `v*` tags. |

---

## 3. Critical Hardware & Protocol Constraints (Do Not Violate)

When reviewing code or proposing improvements, keep these real hardware limits in mind:

1. **BLE is write-only for pixels**: The `64×64` panel has no framebuffer readback command. Live screen mirroring in the UI is tracked via `state.now_playing` + WebSocket broadcasts across all connected clients.
2. **Sliding window MUST stay `swnd = 1`**: Setting `swnd > 1` wedges the device's BLE RX buffer. Only `ys_l_fast` / `conservative` / `safe_ack` profiles with `swnd = 1` are reliable. `swnd > 1` profiles live in `EXPERIMENTAL_PROFILES` and are refused by `BleTransport.set_profile()` (MockTransport only).
3. **Zero-Flicker Double-Buffering (`Slot 1 ↔ Slot 2` + `Slot 3` RAM alerts)**:
   - Normal uploads (`GIF`, `Clock`, `Weather`, `Calendar`, `Canvas`) write silently (`show_now=False`) to the inactive slot (`state.next_back_slot()`), then switch atomically via `cmd_dispatch_program(slot)` + `cmd_pgm_play(model=1, index=slot - 1)`. This prevents the hardware from flashing its built-in factory intro screen during transfers.
   - Temporary alerts (Drink Water, Webhooks, Meeting Alerts) use **RAM Slot 3** (`data_save=1`) and restore `state.active_slot` + delete Slot 3 when finished or dismissed.
4. **On-Device Persistent Schedules**: Stored in hardware Flash Slots `4..8` (`SCHEDULE_SLOTS`) via `encode_period_pro_tlv()` (`TLV 0x16`), intended for autonomous offline date/weekday/time switching without the Mac running. **Disabled by default** (`DEVICE_SCHEDULE_ENABLED`, opt in with `LOY_EXPERIMENTAL_DEVICE_SCHEDULE=1`): on EXYC-A V2.8.7 the flashed schedule showed the factory logo and left Slot 2 switching broken even after `delete_all` + power cycle. They never overlap live Slots 1↔2 or alert Slot 3. The slot map is defined ONLY at the top of `app/server.py` (`LIVE_SLOTS`, `ALERT_SLOT`, `SCHEDULE_SLOTS`, `BENCHMARK_SLOT`); all live uploads must go through `present_program()`.
5. **Graceful BLE Disconnect Required**: Abruptly killing the process without calling `transport.disconnect()` can leave the hardware in a half-open BLE state requiring a 3-second USB power cycle.
6. **`pgm_play` selects by LIST POSITION, not slot id** (verified on EXYC-A V2.8.7): `cmd_pgm_play(model=1, index)` plays the `index`-th program in the panel's internal list; `cmd_dispatch_program(id)` switches by id but only temporarily (the panel falls back to the `pgm_play` selection). `pgm_play(model=2, ids)` is rejected. So keep Slot 1 and Slot 2 as the first two list entries, written in that order after any wipe. **Never write a RAM program (`data_save=1`) over a slot holding a Flash program**: it leaves a dead list entry that shifts every later switch until `delete_all`. Live slots are always Flash; RAM is only for alert Slot 3, cleared first. **Recovery** (verified on hardware): the panel's own Format Flash command (TLV `0x08` value `03`, a normal SET frame) resets a scrambled program list; it erases every stored program. It is exposed as `cmd_format_flash()` and the Mac-only **Repair Display** button (`POST /api/display/repair`).
7. **Never touch OTA firmware commands (`cmd_type = 4`)**: Risk of permanently bricking the LED panel. `build_frame()` raises if asked to build one.

---

## 4. Development & Verification Commands

```bash
# Run protocol tests
.venv/bin/pytest -q tests/

# Syntax checks (same checks CI runs)
.venv/bin/python -m py_compile app/server.py app/smart_features.py desktop_app.py build_portable.py
node --check app/static/app.js

# Run local web server (dev mode)
.venv/bin/python app/server.py --port 18420
```

---

## 5. Security, Privacy & Repo Rules

- **Zero PII / secrets in Git**: Never commit local user paths, BLE MAC addresses/serial numbers, GitHub PATs (`.github_token`), LAN auth files (`.lan_auth.json`), or personal schedules/configs.
- **Persistent macOS `.app` data**: Frozen macOS builds store user data in `~/Library/Application Support/LOY Studio` (never inside `.app/Contents/MacOS/`) so in-app updates never wipe settings or break codesigning.
- **No external stock images or unnecessary dependencies**: Keep the app lightweight, offline-capable, and dependency-minimal.

---

## 6. How to Give Useful Feedback on This Repo

When asked to review LOY Studio, focus on:
1. **Architecture & Maintainability**: `app/server.py` (~3,500 lines) and `app/static/app.js` (~2,500 lines) have grown organically — identify clean module boundaries, state-management improvements, and dead code without breaking the PyInstaller bundle.
2. **BLE Reliability & Concurrency**: Locking around `state.lock`, reconnect/disconnect edge cases, timeout recovery, and Slot 1/2/3 state consistency.
3. **Security & Networking**: LAN auth middleware, WebSocket token validation, in-app updater integrity (`swap.sh` / `swap.bat`), and XSS/input sanitization in the web UI.
4. **UX & Cross-Device Sync**: Latency, error surfacing, mobile touch ergonomics, and keeping Mac / Menu Bar / iPhone / Matter bridge states in sync.
5. **Testing Gaps**: High-value `MockTransport` + `aiohttp.test_utils.TestClient` integration tests beyond `tests/test_protocol.py`.
