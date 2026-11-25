# Command Reference — YS / YSTP01 Protocol

Every command below is **VERIFIED STATIC** from `app-service.js` modules `a829` (encoder), `3341` (decoder), and `29a0` (Vuex command templates). Runtime verification status is marked `UNTESTED` pending physical hardware testing.

| Feature | Direction / `cmd_type` | TLV Tag | Value Layout | Source Evidence | Status |
|---|---|---|---|---|---|
| Query All Init Info (`dev_info_all`) | GET (`3`) | `0x0A`, `0x1B`, `0x04`, `0x06` | `0A <klen> [key] 1B 00 04 00 06 00` | `ac01`, `a829:839-847` | VERIFIED STATIC / UNTESTED |
| Query Device Info | GET (`3`) | `0x0A` (10) | `[]` or ASCII `dev_key` | `a829:829-837`, `3341:187-208` | VERIFIED STATIC / UNTESTED |
| Query Display Params | GET (`3`) | `0x1B` (27) | `[]` -> returns `sub(1), width(u16LE), height(u16LE), ...` | `a829:820`, `3341:90-128` | VERIFIED STATIC / UNTESTED |
| Power On / Off | SET (`2`) | `0x04` (4) | `00 <0=off \| 1=on>` | `a829:1004-1007` | VERIFIED STATIC / UNTESTED |
| Brightness (Fixed) | SET (`2`) | `0x06` (6) | `00 <level 1..15>` (`0` clamped to `15`) | `a829:1023-1027` | VERIFIED STATIC / UNTESTED |
| Screen Rotation | SET (`2`) | `0x2F` (47) | `<0=0°, 1=90°, 2=180°, 3=270°>` | `a829:1122-1127` | VERIFIED STATIC / UNTESTED |
| Horizontal Mirror | SET (`2`) | `0x3A` (58) | `<0=off \| 1=on>` | `a829:1257-1260` | VERIFIED STATIC / UNTESTED |
| RTC Time Sync (`timing`) | SET (`2`) | `0x05` (5) | `year-2000(u16LE), mon, day, h, m, s, ms(u16LE)` | `a829:1225-1230` | VERIFIED STATIC / UNTESTED |
| Real-Time Fill Rect (`rt_draw`) | SET (`2`) | `0x32` (50) | `01 R G B 00 x0(u16) y0(u16) x1(u16) y1(u16)` | `a829:1150-1153` | VERIFIED STATIC / UNTESTED |
| Real-Time Draw Pixels (`rt_draw`) | SET (`2`) | `0x32` (50) | `00 R G B x0(u16) y0(u16) x1(u16) y1(u16) <1bpp MSB bitmap>` | `a829:795-805, 1146-1178` | VERIFIED STATIC / UNTESTED |
| Built-in Clock (`ani_num`) | SET (`2`) | `0x33` (51) | `00 <fmt> [clock_style, date_style, R, G, B]` | `a829:1181-1188` | VERIFIED STATIC / UNTESTED |
| Built-in Countdown (`ani_num`) | SET (`2`) | `0x33` (51) | `01 <fmt> <h> <m> <s>` | `a829:1188` | VERIFIED STATIC / UNTESTED |
| Built-in Scoreboard (`ani_num`) | SET (`2`) | `0x33` (51) | `03 <fmt> <score_r u16LE> <score_b u16LE>` | `a829:1189` | VERIFIED STATIC / UNTESTED |
| Music Spectrum (`ani_num`) | SET (`2`) | `0x33` (51) | `04 <style \| (led_mp<<7)> [bars...]` | `a829:1186, 1190-1193` | VERIFIED STATIC / UNTESTED |
| Ambient Light (`ani_num`) | SET (`2`) | `0x33` (51) | `05 <style_light> <style_val> ...` | `a829:1186, 1194-1200` | VERIFIED STATIC / UNTESTED |
| Built-in Games (`game`) | SET (`2`) | `0x30` (48) | `<opt: 37=L,38=U,39=R,40=D,115=start,112=pause,100=done,101=exit \| id+128>` | `a829:694-695, 1129-1134` | VERIFIED STATIC / UNTESTED |
| Delete All Programs | SET (`2`) | `0x08` (8) | `00 FF` | `a829:969` | VERIFIED STATIC / UNTESTED |
| Delete Specific Programs | SET (`2`) | `0x08` (8) | `00 <id1-1> <id2-1> ...` | `a829:972-993` | VERIFIED STATIC / UNTESTED |
| Format Flash | SET (`2`) | `0x08` (8) | `03` (DO NOT USE casually) | `a829:968` | VERIFIED STATIC / UNTESTED |
| Switch Program (`dispatch`) | SET (`2`) | `0x18` (24) | `02 <id_pro-1> <ignore_cmd> <mode> [val u16LE]` | `a829:1047-1067` | VERIFIED STATIC / UNTESTED |
| Playlist Play (`pgm_play`) | SET (`2`) | `0x36` (54) | `<model> <index> [id_pro-1 ...]` | `a829:806-816` | VERIFIED STATIC / UNTESTED |
| Upload Image / GIF / Text Program | SET (`2`) | `0x19/0x09/0x0C/0x1C/0x0D/0x1D` + `0x14/0x11` + `0x12/0x13` chunks | See `protocol-specification.md` & `image-format.md` | `a829:114-142, 481-622` | VERIFIED STATIC / UNTESTED |
| BLE Password (`pwd`) | SET (`2`) | `0x37` (55) | `<type 0..3> [ascii_pwd]` | `a829:1262-1270` | VERIFIED STATIC / UNTESTED |

## Example Wire Packets (Unit-Tested Fixtures)

- **Init (`dev_info_all`, `sno=1`)**:
  `aa 55 ff ff 0e 00 01 00 c1 03 0a 00 1b 00 04 00 06 00 ff 03`
- **Power OFF (`sno=2`)**:
  `aa 55 ff ff 0a 00 02 00 c1 02 04 02 00 00 d1 03`
- **Brightness 15/15 (`sno=4`)**:
  `aa 55 ff ff 0a 00 04 00 c1 02 06 02 00 0f e4 03`
- **RT Fill Red 32x32 (`sno=10`)**:
  `aa 55 ff ff 15 00 0a 00 c1 02 32 0d 01 ff 00 00 00 00 00 00 00 1f 00 1f 00 62 05`
