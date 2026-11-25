# LOY Studio 2.0 — Product Architecture, Design System & Feasibility Report

## 1. Architecture Decision: Web-First Mac Gateway (No Flutter)

LOY Studio 2.0 runs as a **single responsive web application** served by the Mac's local Python gateway (`app/server.py`), which owns the CoreBluetooth connection (`protocol/transport.py`), local Pillow/FFmpeg media pipeline (`app/creative.py`), and local JSON library.

- **Desktop Browser**: Full creative studio with left sidebar, canvas-first editor, keyboard shortcuts, and diagnostics.
- **Mobile / Tablet Browser (iOS Safari & Android Chrome)**: Touch-friendly responsive layout with bottom tab navigation, safe-area insets, touch drawing, sticky **Send to Display** action, and PWA home-screen installability (`manifest.json`, `icon-192.png`, `icon-512.png`, `sw.js`).
- **Real-Time Sync**: `/ws` WebSocket broadcasts live BLE status, brightness, active transfer progress, library updates, and connected client lists across all open desktop & mobile browsers simultaneously.
- **LAN Security**: Localhost (`127.0.0.1`) is trusted automatically; LAN access is opt-in (`--lan`) and protected by a persisted cryptographic pairing token (`app/.pairing_token`), embedded in the **Connect from Phone** QR code (`/?pair=<token>` -> `HttpOnly; SameSite=Lax` cookie).
- **PWA Secure-Context Note**: Service Worker registration requires `localhost` or HTTPS. On plain HTTP LAN (`http://192.168.x.x:18420`), LOY Studio still works fully (including iOS "Add to Home Screen" standalone mode and `localStorage` draft recovery), while gracefully skipping SW registration without errors.

## 2. Design Concepts Explored & Selected Direction

1. **Canvas-First Creative Studio (Selected)**: Inspired by Procreate/Aseprite/Apple dark mode. Neutral obsidian surfaces, system sans-serif UI typography (monospace restricted to Developer Mode), pixel artwork as the focal centerpiece, optional physical LED dot-matrix simulation, 6 focused workspaces (`Home`, `Create`, `Explore`, `Library`, `Live`, `Device`).
2. **Minimalist Device-First Controller**: Fast for power/brightness, too cramped for pixel/animation creation.
3. **Content-First Visual Gallery**: Great for browsing GIFs, weak for multi-tool pixel editing.
4. **Modular Professional Workspace**: Powerful on desktop, overly dense for mobile touch screens.

## 3. Hardware Feasibility Matrix (`64×64` BLE-Only Display)

| Feature | Execution Layer | Autonomous Offline (No BLE)? | Status |
|---|---|---|---|
| Power, Brightness, Rotation, Mirror | Native Firmware (`TLV 0x04/0x06/0x2F/0x3A`) | Yes | VERIFIED STATIC / UNTESTED HW |
| Built-in Hardware Clock (`TLV 0x33 type=0`) | Native Firmware RTC | Yes | VERIFIED STATIC / UNTESTED HW |
| Standalone `text_clock` Shapes (7 layouts) | Native Firmware RTC + `YSTP01` shape BG | Yes | VERIFIED STATIC / UNTESTED HW |
| Built-in Games (`TLV 0x30`), Countdown, Stopwatch, Scoreboard | Native Firmware | Yes (games need D-Pad input) | VERIFIED STATIC / UNTESTED HW |
| Music Mic Spectrum (`led_mp=1`) & Ambient Light | Native Firmware (`TLV 0x33 type=4,5`) | Yes | VERIFIED STATIC / UNTESTED HW |
| Static Images (`YSTP01`), GIFs, Image Sequences, Video-to-GIF, Procedural GIFs, QR Studio, Avatars | Host-generated -> On-device flash | Yes (plays autonomously after upload) | VERIFIED RUNTIME (MOCK) |
| Multi-Slot Playlist (`TLV 0x36`) | Native Firmware Flash | Yes | VERIFIED STATIC / UNTESTED HW |
| Pomodoro, macOS CPU/RAM, macOS Now Playing, Live BLE Drawing | Host-generated -> Periodic BLE push | **No (requires Mac + BLE connected)** | VERIFIED RUNTIME (MOCK) |

## 4. BLE Performance Profiles & Verification Checklist

| Profile | BLE Chunk | Sliding Window (`swnd_max`) | Write Mode | Hardware Verified? |
|---|---|---|---|---|
| `conservative` (default) | 180 B | 1 (stop-and-wait) | Without Response | UNTESTED on physical HW |
| `balanced` | 248 B | 5 | Without Response | UNTESTED (candidate for `EX*` < V2.8.9#V25) |
| `fast` | 509 B | 10 | Without Response | UNTESTED (candidate for `EX*` >= V2.8.9#V25) |
| `safe_ack` | 180 B | 1 | With Response | UNTESTED (compatibility fallback) |

**Hardware Verification Checklist**:
1. Run `Device → Scan Displays` and connect to your `64×64` `YS*`/`TL*` panel.
2. Run `Device → Run Throughput Benchmark` on `conservative`, then test `balanced` and `fast` and record measured B/s.
3. Verify Power, Brightness, Solid RGB colors (confirm R/B channel order), a `64×64` Procedural GIF, a Video-to-GIF clip, and a Standalone Live RTC Hardware Clock.
4. Start with `./macos/loy_ctl.sh start-lan`, scan the **Connect from Phone** QR code on iPhone/Android, and verify real-time WebSocket sync between phone and Mac.
