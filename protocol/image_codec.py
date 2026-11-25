"""
YSTP01 image and animation container encoder.

Recovered from module a829 functions W(), K(), Y(), J() (VERIFIED STATIC):
  Header (15 bytes):
    [0..5]   ASCII "YSTP01"
    [6..7]   width       (uint16 LE)
    [8..9]   height      (uint16 LE)
    [10..11] frame_count (uint16 LE)
    [12..13] len_valid   (uint16 LE, typically width for horizontal or height for vertical)
    [14]     bpp         (uint8: 1, 2, 4, 8 for indexed palette, or 24 for raw RGB24)
  Palette (if bpp <= 8):
    3 * (1 << bpp) bytes, each entry stored as [B, G, R] or [R, G, B]?
    Let's verify exact byte order from a829 lines 212, 226, 240, 246:
    - Input BMP pixel is BGR (b, g, r) at p[3*x + 0..2].
    - Line 212: f = bytes_read_int(p, 3*x, 3, bigEndian=True) => (B << 16) | (G << 8) | R.
    - Line 226: bytes_write_int(out, 15 + 3*i, G[i], 3, bigEndian=False) =>
      byte 0 = R (G[i] & 0xFF), byte 1 = G ((G[i] >> 8) & 0xFF), byte 2 = B ((G[i] >> 16) & 0xFF)!
    - Line 246 (24-bpp): out[m+3*x] = v[3*x+2] (R), out[m+3*x+1] = v[3*x+1] (G), out[m+3*x+2] = v[3*x] (B)!
    So BOTH palette entries and 24-bpp pixels are stored in standard R, G, B order!
  Pixel Data:
    Top-down row order (y = 0 .. height-1), left-to-right (x = 0 .. width-1).
    - If bpp <= 8: packed LSB-first per row, padded to whole bytes per row:
      row_stride = ceil(width * bpp / 8)
    - If bpp == 24: 3 bytes per pixel (R, G, B), row_stride = width * 3.
"""

from __future__ import annotations

import math
import struct
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from PIL import Image


def _build_gamma_lut(gamma: float) -> bytes:
    if gamma == 1.0:
        return bytes(range(256))
    lut = bytearray(256)
    for a in range(256):
        val = int(256.0 * (((a + 0.5) / 256.0) ** gamma) - 0.5)
        lut[a] = max(0, min(255, val))
    return bytes(lut)


def encode_ystp01(
    frames_rgb: Sequence[bytes],
    width: int,
    height: int,
    *,
    gamma: float = 1.0,
    len_valid: Optional[int] = None,
    force_rgb24: bool = False,
) -> bytes:
    """
    Encode one or more RGB24 frames (top-down, width * height * 3 bytes each)
    into the proprietary YSTP01 binary container.
    """
    if not frames_rgb:
        raise ValueError("frames_rgb must contain at least one frame")
    expected_len = width * height * 3
    for idx, fr in enumerate(frames_rgb):
        if len(fr) != expected_len:
            raise ValueError(
                f"Frame {idx} has {len(fr)} bytes, expected {expected_len} ({width}x{height}x3)"
            )

    lut = _build_gamma_lut(gamma)
    processed: List[bytes] = [
        bytes(lut[b] for b in fr) if gamma != 1.0 else fr for fr in frames_rgb
    ]

    # Determine optimal bpp (1, 2, 4, 8, or 24)
    palette: List[Tuple[int, int, int]] = []
    palette_map: dict[Tuple[int, int, int], int] = {}
    bpp = 24 if force_rgb24 else 1

    if not force_rgb24:
        max_colors = 1 << bpp
        overflow = False
        for fr in processed:
            for i in range(0, expected_len, 3):
                rgb = (fr[i], fr[i + 1], fr[i + 2])
                if rgb not in palette_map:
                    palette_map[rgb] = len(palette)
                    palette.append(rgb)
                    if len(palette) > max_colors:
                        bpp <<= 1
                        if bpp > 8:
                            bpp = 24
                            overflow = True
                            break
                        max_colors = 1 << bpp
            if overflow:
                break

    frame_count = len(processed)
    valid_len = width if len_valid is None else len_valid
    palette_bytes_len = (3 * (1 << bpp)) if bpp <= 8 else 0
    row_stride = (width * 3) if bpp == 24 else math.ceil(width * bpp / 8)
    pixel_bytes_len = row_stride * height * frame_count

    out = bytearray(15 + palette_bytes_len + pixel_bytes_len)
    out[0:6] = b"YSTP01"
    struct.pack_into("<HHHHB", out, 6, width, height, frame_count, valid_len, bpp)

    if bpp <= 8:
        for idx, (r, g, b) in enumerate(palette):
            off = 15 + 3 * idx
            out[off] = r
            out[off + 1] = g
            out[off + 2] = b

    pos = 15 + palette_bytes_len
    for fr in processed:
        if bpp == 24:
            out[pos : pos + expected_len] = fr
            pos += expected_len
        else:
            for y in range(height):
                row_off = y * width * 3
                acc = 0
                bits = 0
                for x in range(width):
                    p_off = row_off + x * 3
                    idx = palette_map[(fr[p_off], fr[p_off + 1], fr[p_off + 2])]
                    acc |= idx << bits
                    bits += bpp
                    if bits == 8:
                        out[pos] = acc
                        pos += 1
                        acc = 0
                        bits = 0
                if bits > 0:
                    out[pos] = acc
                    pos += 1

    return bytes(out)


def encode_solid_color_ystp01(
    width: int, height: int, r: int, g: int, b: int, gamma: float = 1.0
) -> bytes:
    """Create a 1-frame solid color YSTP01 payload."""
    frame = bytes([r & 0xFF, g & 0xFF, b & 0xFF]) * (width * height)
    return encode_ystp01([frame], width, height, gamma=gamma)


def pil_image_to_ystp01(
    img: Image.Image,
    width: int,
    height: int,
    *,
    gamma: float = 1.0,
    keep_aspect: bool = False,
) -> bytes:
    """Resize a PIL Image (single frame or animated) to (width, height) and encode as YSTP01."""
    n_frames = getattr(img, "n_frames", 1)
    frames_rgb: List[bytes] = []
    for i in range(n_frames):
        if n_frames > 1:
            img.seek(i)
        rgb = img.convert("RGB")
        if keep_aspect:
            canvas = Image.new("RGB", (width, height), (0, 0, 0))
            copy = rgb.copy()
            copy.thumbnail((width, height), Image.Resampling.NEAREST)
            ox = (width - copy.width) // 2
            oy = (height - copy.height) // 2
            canvas.paste(copy, (ox, oy))
            rgb = canvas
        else:
            rgb = rgb.resize((width, height), Image.Resampling.NEAREST)
        frames_rgb.append(rgb.tobytes())
    return encode_ystp01(frames_rgb, width, height, gamma=gamma)


def load_image_as_ystp01(
    path: str | Path,
    width: int,
    height: int,
    *,
    gamma: float = 1.0,
    keep_aspect: bool = False,
) -> bytes:
    """Load an image file (PNG/JPG/BMP/GIF) and encode as YSTP01."""
    with Image.open(path) as img:
        return pil_image_to_ystp01(img, width, height, gamma=gamma, keep_aspect=keep_aspect)
