# Image & Animation Format Specification (`YSTP01` & Native GIF)

Recovered from `app-service.js` module `a829` functions `W()`, `K()`, `Y()`, `J()`, `Z()`, and module `7835.rgbtobmp()`. **[VERIFIED STATIC]**

## 1. Supported Graphic Resource Formats

When uploading a graphic item (`type_item == "graphic"`, `item_type = 10`), the firmware accepts **two** payload formats inside TLV `0x13` chunks:

1. **Native GIF Passthrough** (`a829` line 274):
   - If the payload begins with ASCII `"GIF"` (`0x47 0x49 0x46`), LOY PLAY sends the raw GIF file bytes directly to the display without re-encoding.
   - Frame timing is governed by the GIF's own GCE delays and/or TLV `0x14` (`speed`, `time_stay = 0`).
2. **Proprietary `YSTP01` Container** (`a829` lines 222–254):
   - Used for static images, pixel-art drawings, and multi-frame bitmap animations.

## 2. `YSTP01` Binary Layout

```
Offset          Size             Field        Description
--------------  ---------------  -----------  ---------------------------------------
0               6                magic        ASCII "YSTP01"
6               2                width        uint16 LE (pixels)
8               2                height       uint16 LE (pixels)
10              2                frame_count  uint16 LE (1 for static, N for animation)
12              2                len_valid    uint16 LE (typically width, or height for vertical scroll)
14              1                bpp          Bits per pixel: 1, 2, 4, 8, or 24
15              P                palette      If bpp <= 8: P = 3 * (1 << bpp) bytes (RGB24 entries)
                                              If bpp == 24: P = 0
15 + P          row_stride*H*F   pixels       Frame-by-frame, top-down, left-to-right
```

## 3. Color Palette & Pixel Packing

- **Auto-Quantization (`a829.Y()`, lines 197–221)**:
  - Counts unique RGB24 colors across all frames:
    - `<= 2` colors -> `bpp = 1` (palette = 6 bytes)
    - `<= 4` colors -> `bpp = 2` (palette = 12 bytes)
    - `<= 16` colors -> `bpp = 4` (palette = 48 bytes)
    - `<= 256` colors -> `bpp = 8` (palette = 768 bytes)
    - `> 256` colors -> `bpp = 24` (no palette; raw RGB24)
- **Channel Order**:
  - In `a829.K()`, input is a 24-bit bottom-up BMP (BGR).
  - In `a829.J()` (lines 212, 226, 246):
    - Palette entries are written as `[R, G, B]` (3 bytes per color).
    - 24-bpp pixels are written as `[R, G, B]` (3 bytes per pixel).
- **Row Orientation & Packing**:
  - Top-down (`y = 0` to `height - 1`), left-to-right (`x = 0` to `width - 1`).
  - For `bpp <= 8`, pixel indices are packed **LSB-first** into each byte (`acc |= idx << bit_pos; bit_pos += bpp`), padded to a whole byte boundary at the end of every row (`row_stride = ceil(width * bpp / 8)`).
- **Gamma Correction (`a829.W()`, lines 149–166)**:
  - Applies LUT `out[v] = clamp(256 * ((v + 0.5) / 256)^gamma - 0.5)` with `gamma = 1.0` (standard) or `1.6` (when `gray > 0`).
