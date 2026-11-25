# Proprietary Protocol Specification — YS / YSTP01

Recovered from `assets/apps/__UNI__60DF8F1/www/app-service.js` modules `a829` (encoder), `3341` (decoder), and `f426` (byte utilities). **[VERIFIED STATIC]**

## 1. Byte Order

All multi-byte integers in headers, lengths, coordinates, and checksums are **little-endian (LE)** (`f426.bytes_write_int` / `bytes_read_int` default `bigEndian = false`).

## 2. Wire Frame Structure

```
Offset  Size  Field      Description
------  ----  ---------  ------------------------------------------------------
0       2     magic      0x55AA LE -> wire bytes [0xAA, 0x55]
2       2     target     0xFFFF (broadcast / default target)
4       2     body_len   uint16 LE = len(tlv_payload) + (2 if checksum) + 4
6       2     sno        uint16 LE sequence number (1..65535)
8       1     flags      bit7: checksum present; bit6: ACK requested; bit0: 1
                         0xC1 (193) = checksum + ACK (standard)
                         0x81 (129) = checksum, no ACK (used for unack rt_draw)
9       1     cmd_type   Host->Dev: 2=SET, 3=GET, 4=UPGRADE, 7=DOWNLOAD
                         Dev->Host: 130=SET_ACK, 131=GET_ACK, 132=UPG_ACK, 6=UPLOAD
10      N     payload    One or more concatenated TLV records
10+N    2     checksum   uint16 LE = sum(frame[0 .. 10+N-1]) & 0xFFFF (if bit7)
```

Total wire frame length = `body_len + 6`.

## 3. TLV Encoding

Each TLV record consists of:
- `tag` (1 byte, uint8)
- `length` (1, 2, or 3 bytes — ASN.1-like variable length from `a829.D()`):
  - `L < 128`: `[L]`
  - `128 <= L < 256`: `[0x81, L]`
  - `256 <= L <= 65535`: `[0x82, L & 0xFF, (L >> 8) & 0xFF]`
- `value` (`L` bytes)

## 4. Checksum Algorithm

Simple 16-bit unsigned additive sum modulo 65536 over all preceding bytes in the frame (starting at `0xAA` byte 0):

```python
checksum = sum(frame[:-2]) & 0xFFFF
```

Verified identically in both encoder (`a829` lines 917–922) and decoder (`3341` lines 415–419).

## 5. Encryption / Compression

- **Encryption**: **None.** All BLE packets are unencrypted plaintext binary TLV frames. **[VERIFIED STATIC]**
- **Compression**: **None** at the transport layer. Image payloads use indexed color palettes (1/2/4/8 bpp) in `YSTP01` or native GIF LZW compression when raw GIF files are passed through. **[VERIFIED STATIC]**

## 6. Device Responses & Error Codes (`3341.p()`, `4156.dataParseError()`)

- **SET ACK (`cmd_type = 130 / 0x82`)** or **UPGRADE ACK (`132 / 0x84`)**:
  - `payload[0] == 0x00`: Success (`ok`).
  - `payload[0] != 0x00`: Failure with error code `payload[0]`:
    - `3`: Non-fatal / retryable error (`4156`: `s = 3 != i`).
    - `10`: Data error — aborts transfer.
    - `14`: Password authentication required (`passwordAuthen = true`).
    - `111`: Unsupported feature.
    - `255`: Empty/malformed response.
- **GET ACK (`cmd_type = 131 / 0x83`)**:
  - Contains one or more TLV records corresponding to the queried tags (e.g., `0x0A`, `0x1B`, `0x04`, `0x06`).
- **Unsolicited Upload (`cmd_type = 6`)**:
  - Game score updates (TLV `0x0A`, 5 bytes: `game_id + 128`, `score u32LE`), playlist state (TLV `0x36`), or IO events.
