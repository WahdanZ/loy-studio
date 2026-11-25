# Dynamic Verification & Hardware Testing Plan

Because physical LED hardware was not connected to this host during static analysis, **all hardware interactions are currently classified as `VERIFIED STATIC` / `UNTESTED` at runtime** (verified in software via `MockTransport` and unit tests). Follow this reproducible protocol with your own display to promote commands to `VERIFIED RUNTIME`.

## 1. Prerequisites

- Your YS / LOY PLAY pixel LED display powered on (and disconnected from any phone).
- macOS / Linux / Windows machine with Bluetooth enabled and this repository's `.venv` activated:
  ```bash
  source .venv/bin/activate
  ```
- Optional (for comparing against the official Android app): an Android test device with Developer Options -> **Enable Bluetooth HCI snoop log** turned on, plus `adb` and Wireshark.

## 2. Controlled Hardware Experiments (Offline, Zero Internet)

Run each step with `-v` to log exact TX/RX wire frames, and disable Wi-Fi/internet on your host to confirm 100% offline operation:

1. **Discover Display**:
   ```bash
   python poc/loy_cli.py scan
   ```
   Record the advertised name (`YS...` or `TL...`) and address/UUID.
2. **Connect & Query Device Info** (verifies `dev_info_all` handshake & screen resolution):
   ```bash
   python poc/loy_cli.py -v info
   ```
3. **Change Brightness**:
   ```bash
   python poc/loy_cli.py -v brightness 3
   python poc/loy_cli.py -v brightness 15
   ```
4. **Display Solid Red, Green, and Blue**:
   ```bash
   python poc/loy_cli.py -v color red
   python poc/loy_cli.py -v color green
   python poc/loy_cli.py -v color blue
   ```
   *(If red and blue appear swapped on your specific panel hardware, note it in `open-questions.md` — `rt_draw` vs `YSTP01` RGB channel ordering can be verified here.)*
5. **Display a Single Illuminated Pixel**:
   ```bash
   python poc/loy_cli.py -v pixel 0 0 red
   python poc/loy_cli.py -v pixel 15 15 green
   ```
6. **Upload a Small GIF Animation**:
   ```bash
   python poc/loy_cli.py -v gif path/to/sample.gif
   ```
7. **Display Scrolling Text**:
   ```bash
   python poc/loy_cli.py -v text "HELLO OFFLINE" --color cyan --scroll left --speed 10
   ```
8. **Power & Orientation**:
   ```bash
   python poc/loy_cli.py -v rotate 90
   python poc/loy_cli.py -v rotate 0
   python poc/loy_cli.py -v power off
   python poc/loy_cli.py -v power on
   ```
9. **Interactive Studio Verification**:
   ```bash
   python app/server.py
   # Open http://127.0.0.1:8765 -> Scan BLE -> Connect -> Draw & Send
   ```

## 3. Capturing Official App Traffic (Android HCI Snoop)

If any command behaves unexpectedly on your firmware version:
1. On Android test device: Enable Developer Options -> **Bluetooth HCI snoop log**.
2. Toggle Bluetooth off/on, open LOY PLAY, perform a single action (e.g., change brightness).
3. Pull the bugreport/snoop log via ADB:
   ```bash
   adb bugreport captures/bugreport.zip
   ```
4. Open `FS/data/misc/bluetooth/logs/btsnoop_hci.log` in Wireshark, filter `btatt.opcode == 0x52 || btatt.opcode == 0x1b`, copy the `btatt.value` hex stream, and decode it with:
   ```bash
   python tools/decode_capture.py "<hex bytes>"
   ```
5. **Safety**: Never run firmware upgrade (`cmd_type = 4`) or flash format (`TLV 0x08 -> 0x03`) during exploratory testing.
