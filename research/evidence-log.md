# Evidence Log

| # | Conclusion | Classification | Source File / Class / Module | Verification |
|---|---|---|---|---|
| 1 | XAPK SHA-256 is `a8ed4558aa67936df32062dd21bd535a97afb9b2fb773b6675f4314da40120b1` | VERIFIED STATIC | `LOY+PLAY_1.7.15_APKPure.xapk` | `shasum -a 256` |
| 2 | App is DCloud uni-app hybrid (`__UNI__60DF8F1`) with unencrypted JS in `app-service.js` | VERIFIED STATIC | `assets/data/dcloud_control.xml`, `assets/apps/__UNI__60DF8F1/www/app-service.js` | File inspection |
| 3 | Daily telemetry upload (`systemAjax.json` incl. `deviceId`, GPS `lat`/`lon`, LED `id_dev`, `use_times`) to `store-cdn.popled.cn/api/material_Base/set_recode` | VERIFIED STATIC | `app-service.js` modules `7835` (L440–509), `6ac7` (L270–281, L343–356) | Code inspection |
| 4 | Server-side hardware authorization kill-switch (`device_check`) & GeoJSON geofencing (`devZoneInfo`) | VERIFIED STATIC | `app-service.js` modules `6f64` (`compareMac`), `6ac7` (L45–85), `7835` (L937–987) | Code inspection |
| 5 | Silent account creation using `deviceId` / `md5(deviceId)` in non-CN locales | VERIFIED STATIC | `app-service.js` module `7835` (L138–183, `touristModel`) | Code inspection |
| 6 | Hardcoded `WX_SECRET`, `pub_appout` password, `app_key`, `MD5_key` | VERIFIED STATIC | `AndroidManifest.xml` (L75), `app-service.js` modules `6ac7` (L290–291), `29a0` | Code inspection |
| 7 | Empty accept-all `X509TrustManager` class present in APK | VERIFIED STATIC | `classes.dex` -> `com.yskj.loy.CustomTrustMgr` | JADX decompilation |
| 8 | `usesCleartextTraffic="true"` and HTTP call to `itunes.apple.com` | VERIFIED STATIC | `AndroidManifest.xml` (L48), `app-service.js` module `5728` | Code inspection |
| 9 | Remote `.wgt` hot-code update mechanism via `store-cdn.popled.cn/api/device/get_app_file` | VERIFIED STATIC | `app-service.js` module `6ac7` (L296–302) | Code inspection |
| 10 | BLE GATT UUIDs: Service `0000FFF0`, Notify `0000FFF1`, Write `0000FFF2`; prefixes `YS`, `TL` | VERIFIED STATIC | `app-service.js` modules `29a0`, `2658`, `aa14` | Code inspection + community cross-check (`nyan-led-skills`) |
| 11 | Wire framing: `AA 55 FF FF <body_len LE> <sno LE> <flags> <cmd_type> <TLVs> <sum16 LE>` | VERIFIED STATIC | `app-service.js` modules `a829` (`$e`), `3341` (`parse_ack_ex`), `f426` | Unit tested (`tests/test_protocol.py`) |
| 12 | `YSTP01` image container (15B header + 1/2/4/8bpp RGB palette or 24bpp RGB) & native GIF passthrough | VERIFIED STATIC | `app-service.js` module `a829` (`W`, `K`, `Y`, `J`, `Z`) | Unit tested (`tests/test_protocol.py`) |
| 13 | All 11 protocol unit tests and CLI/MockTransport flows pass | VERIFIED RUNTIME (MOCK) | `tests/test_protocol.py`, `poc/loy_cli.py` | `pytest -v` (11 passed) |
| 14 | Physical LED display (`YS**********L`, `64×64`, `EXYC-A#CXB3BRI7BTVOICE3_V2.8.7#V13`) BLE handshake, `sync_time`, `brightness`, GIF upload (`01_cyber_eyes.gif`), and multi-region live hardware RTC clock (`hw_neon_ring`, 9/9 frames ACKed) | VERIFIED RUNTIME | Live CoreBluetooth BLE session (`MTU=247`, `FFF2` write / `FFF1` indicate) | `ys_l_fast` (`180B`, `2ms`, `swnd=1`) measured `1,895.4 B/s` (4096B benchmark, 0 errors); `conservative` (`15ms`) measured `1,953.6 B/s` |
| 15 | `EXYC-A` sliding-window (`swnd > 1`) incompatibility & recovery | VERIFIED RUNTIME | Live hardware test (`ys_l_swnd3`, `balanced`) on `YS*L` (`EXYC-A`) | Pipelining (`swnd > 1`) stalls `FFF1` RX indications until physical USB power-cycle + macOS Bluetooth reset; guarded in `app/server.py` and `index.html` |
| 16 | Program background compositing (`TLV 0x1C` `type_bg = 4`), digit masking (`type_bg = 1`), & continuous `DISPATCH` (`02 <slot> 00 FF`) | VERIFIED STATIC / RUNTIME | `decompiled/js_modules/981c.js`, `a829.js`, and live `YS*L` playback | `type_bg=0` suppresses background region; `type_bg=4` composites `background=1` artwork behind `text_clock`; `type_bg=1` masks GIF inside digits |
