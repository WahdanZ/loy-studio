# Hardware Compatibility & Existing Implementations

## 1. OEM Ecosystem Identification

- **Manufacturer**: **Shenzhen Yanse Technology Co., Ltd.** (`com.yskj.loy` = Yanse Keji / PopLED). **[VERIFIED STATIC + WEB RESEARCH]**
- **Consumer Brands Using This Same OEM Controller**:
  - **LOY PLAY** / **LOY SPACE** (official companion apps)
  - **Nyan Gear** LED backpacks
  - **Gelrova** LED backpacks / signs
  - Generic "Devil Eye" automotive flexible LED matrix displays and pixel art frames advertising Bluetooth names starting with **`YS`** or **`TL`**.
- **Supported Display Resolutions**: Dynamically reported by the hardware via `param_dev` (TLV `0x1B`), commonly `16x16`, `32x32`, `64x64`, `32x16`, `64x16`, `96x16`, and circular panels (`shape = 1`). **[VERIFIED STATIC]**

## 2. Comparison with Existing Open-Source Projects

| Project | Target Hardware | Protocol Compatibility | Notes |
|---|---|---|---|
| [`stared/nyan-led-skills`](https://github.com/stared/nyan-led-skills) | YS LED backpacks (Nyan Gear / LOY) | **Compatible** (`0000FFF0`/`FFF1`/`FFF2`, `AA 55 FF FF`, 16-bit additive checksum) | Confirms our static recovery independently. |
| [`insanemal/popledder`](https://github.com/insanemal/popledder) | LOY SPACE / LOY PLAY panels | **Compatible** (PopLED / YS ecosystem) | Community alternative controller for LOY panels. |
| [`markusressel/idotmatrix-api-client`](https://github.com/markusressel/idotmatrix-api-client) | iDotMatrix (`IDM-*`) | **NOT compatible** | Uses a completely different GATT service (`000000FA`) and framing format despite similar physical appearance. |
| [`sphings79/ha-ble-led-pixel-display`](https://github.com/ink-ble/ha-ble-led-pixel-display) | iPixel Color / BGLight / BK Light | **NOT compatible** | Different UUIDs and framing. |
| [`chameleonbr/LED_UNION`](https://github.com/chameleonbr/LED_UNION) | Multi-vendor PWA (ELK/MELK/etc.) | **Not verified** | Different primary target protocols. |

## 3. Reusable Components & Licenses

Our clean-room implementation in `protocol/` and `app/` uses only standard permissive Python libraries:
- `bleak` (MIT License) — Cross-platform BLE (CoreBluetooth on macOS, BlueZ on Linux, WinRT on Windows)
- `Pillow` (MIT-CMU License) — Image/GIF decoding and resizing
- `aiohttp` (Apache 2.0 License) — Localhost-only HTTP server for LOY Studio
- `pytest` (MIT License) — Automated protocol unit tests

## 4. Platform Evaluation for Independent App (Phase 8)

| Option | macOS / iOS BLE Reality | Verdict |
|---|---|---|
| **Local Python + Bleak Backend + Offline Web UI (`app/`)** | Uses CoreBluetooth natively on macOS (no Web Bluetooth browser block), works on Linux/Windows, 100% offline, instant developer packet inspector | **Selected & Implemented** |
| **Flutter (`flutter_blue_plus`)** | Strong mobile option for Android + iOS; iOS hides MAC addresses (uses peripheral UUIDs), requires `NSBluetoothAlwaysUsageDescription` | Recommended next step for mobile using our `protocol/` spec |
| **Web Bluetooth / PWA** | Unsupported in Safari (macOS/iOS); Chrome-only on desktop/Android | Rejected as primary due to Apple platform restriction |
| **Native Android / iOS** | Best OS integration, highest maintenance cost (2 codebases) | Optional if mobile-only native apps are needed |
