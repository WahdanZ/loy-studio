# BLE Communication Analysis — LOY PLAY v1.7.15

## 1. Communication Mechanism

- **Transport**: **Bluetooth Low Energy (BLE) GATT** only. No Wi-Fi, local HTTP, TCP/UDP, MQTT, or Bluetooth Classic RFCOMM is used for display control. **[VERIFIED STATIC]**
- **Implementation Layer**: 100% JavaScript in `app-service.js` (modules `29a0`, `2658`, `aa14`, `0075`, `3132`, `4156`, `a829`, `3341`, `f426`) invoking DCloud's native Android BLE bridge (`io.dcloud.feature.bluetooth.BluetoothFeature`). **[VERIFIED STATIC]**

## 2. GATT Profile & Discovery Filters

| Parameter | Value | Source | Status |
|---|---|---|---|
| Primary Service UUID | `0000FFF0-0000-1000-8000-00805F9B34FB` | `29a0` | VERIFIED STATIC |
| Notify Characteristic UUID | `0000FFF1-0000-1000-8000-00805F9B34FB` | `29a0`, `2658` | VERIFIED STATIC |
| Write Characteristic UUID | `0000FFF2-0000-1000-8000-00805F9B34FB` | `29a0`, `aa14` | VERIFIED STATIC |
| Default Name Prefixes | `["YS", "TL"]` (remotely overridable via `filter_bt_pre`) | `29a0`, `6ac7` | VERIFIED STATIC |
| Pairing / Bonding | None required at BLE GATT layer; optional app-level password via TLV `0x37` (`pwd`) | `2658`, `a829` | VERIFIED STATIC |

## 3. Connection & Initialization Sequence

Traced directly through `2658` -> `6ac7` -> `ac01` -> `aa14`:
1. **Scan**: `uni.startBluetoothDevicesDiscovery({ allowDuplicatesKey: false })`. Filter devices whose `name` or `localName` starts with `"YS"` or `"TL"`, deduplicate by name, sort by descending RSSI (`2658.bleDataList`).
2. **Connect**: `uni.createBLEConnection({ deviceId })` with up to 3 retries and 15 s timeout (`2658.createBLEConnection`).
3. **Stop Scan**: `uni.stopBluetoothDevicesDiscovery()`.
4. **Enable Notifications**: Wait 1500 ms (`getNotifyBLECharacteristic`), then call `uni.notifyBLECharacteristicValueChange` on `0000FFF1` (`state: true`), retrying on `errCode == 10004` up to 15 s (`2658`).
5. **Initial Handshake (`ac01` -> `6ac7.getDevInitInfoCmd`)**:
   - Sends a single GET frame (`cmd_type = 3`) requesting `dev_info_all`:
     - TLV `0x0A` (`dev_info`, optionally carrying `ble_password`)
     - TLV `0x1B` (`param_dev` — returns display `width` and `height`)
     - TLV `0x04` (`power`)
     - TLV `0x06` (`light`)
   - Exact wire bytes (with `sno=1`): `AA 55 FF FF 0E 00 01 00 C1 03 0A 00 1B 00 04 00 06 00 FF 03`.
6. **Speed / MTU Adaptation (`4156.compatibleManage`)**:
   - Default BLE write chunk size (`write_max_byte`): **180 bytes** (`29a0.size_ble`).
   - For high-speed firmware prefixes (`EXYS-AS`, `EXYB-AS`, `EXHM_AS`, `EXYX-B`, `EXYX-BS`, `EXEYE-AS`, `EXYD-CS`, `EXSZ-A`, `EXSZ-AS`, `EXP6-A`, `EXP6-AS`):
     - Version `< V2.8.9#V25`: `write_max_byte = 248`, `swnd_max = 5`.
     - Version `>= V2.8.9#V25`: `write_max_byte = 509`, `swnd_max = 10`.
   - Write pacing (`write_time`): 10 ms (Android, `'L'` devices) or 30 ms (legacy/slow devices).

## 4. Fragmentation, ACKs, Sliding Window & Retries

- **Protocol-Level Chunking (`a829`)**: Large payloads (images, GIFs, text, OTA) are split into **960-byte** data chunks (`A = 960`), each wrapped in its own `AA 55` protocol frame with incrementing `sno`.
- **BLE GATT Chunking (`aa14.send_pkt_ble`)**: Each protocol frame is further sliced into `write_max_byte` (180 / 248 / 509) chunks written sequentially to `0000FFF2`.
- **Stop-and-Wait vs. Sliding Window (`aa14`, `3132`, `0075`)**:
  - Small transfers (`<= 4` frames, `min_packet = 4`) or `type_streamsend == 0`: Stop-and-wait per frame with 5 s timeout and up to 3 retries (`send_pkt`).
  - Large transfers (`> 4` frames, `type_streamsend == 2`): Sliding window (`3132.swnd_*`, window size 3–10) after receiving `first_ack` on frame 0; retransmission timer 700 ms (Android) / 2000 ms (iOS).
- **Notification Reassembly (`2658` + `0075` + `3341`)**:
  - Incoming `0000FFF1` notifications are buffered in `last_buf` (up to 256 B carry-over) until a complete `AA 55` frame with valid 16-bit checksum is assembled.
  - Special case (`2658` line 164): If an unsolicited notification starts with ASCII `"AT"` (`0x41 0x54`) when `last_buf == null`, the app treats it as a BLE module disconnect/reset event and triggers reconnect.
