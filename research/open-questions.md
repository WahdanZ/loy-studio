# Open Questions & Items Requiring Hardware / Runtime Verification

1. **Physical Hardware Confirmation (`UNTESTED`)**:
   - Confirm whether your specific display's firmware uses standard 180-byte BLE writes or supports high-speed 248/509-byte writes (`4156.compatibleManage`).
   - Confirm RGB channel ordering on your physical panel for both `YSTP01` uploads and `rt_draw` commands (verify with `loy_cli.py color red` and `loy_cli.py color blue`).
2. **BLE Write Response Mode (`INFERRED`)**:
   - `BleTransport` writes to `0000FFF2` with `response=False` (Write Without Response) for throughput, matching typical BLE UART-style characteristics. If your specific panel requires Write With Response on `FFF2`, toggle `response=True` in `protocol/transport.py`.
3. **Sliding Window Throughput for Large GIFs (`VERIFIED STATIC`, `UNTESTED`)**:
   - `BleTransport.send_frames()` currently uses reliable stop-and-wait per 960-byte protocol frame (matching LOY PLAY's `send_pkt()` mode). Implementing sliding window (`swnd_max = 3..10` from module `3132`) can speed up very large multi-frame GIF uploads once baseline stop-and-wait is verified on hardware.
4. **Runtime Wiring of `com.yskj.loy.CustomTrustMgr` (`UNKNOWN`)**:
   - The empty accept-all `X509TrustManager` class exists in `classes.dex`, but dynamic interception (e.g., mitmproxy on a test Android device) is required to confirm whether runtime HTTPS requests actually bind to it or use the default system trust store.
5. **BLE Password-Protected Displays (`VERIFIED STATIC`, `UNTESTED`)**:
   - If a display returns error code `14` (`passwordAuthen`), `cmd_get_dev_info_all(dev_key=...)` or TLV `0x37` (`pwd`) is required. Needs hardware with password enabled to test.
