#!/usr/bin/env python3
"""
Generate a pack of 8 looping pixel-art animated GIFs for YS / LOY PLAY LED displays.

All GIFs are saved as crisp, looping GIF89a files in `gifs/` (default 32x32).
Run with `--size 64` or `--size 16` to regenerate for another panel resolution.
"""

from __future__ import annotations

import argparse
import colorsys
import math
import random
from pathlib import Path
from typing import List

from PIL import Image, ImageDraw

OUT_DIR = Path(__file__).resolve().parent.parent / "gifs"


def save_gif(frames: List[Image.Image], path: Path, duration_ms: int = 100) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pal_frames = [f.convert("P", palette=Image.Palette.ADAPTIVE) for f in frames]
    pal_frames[0].save(
        path,
        format="GIF",
        save_all=True,
        append_images=pal_frames[1:],
        duration=duration_ms,
        loop=0,
        disposal=2,
    )


def make_cyber_eyes(size: int) -> List[Image.Image]:
    """Glowing neon cyan/pink cyberpunk eyes that look around and blink."""
    frames: List[Image.Image] = []
    # (pupil_dx, pupil_dy, eyelid_close_ratio)
    seq = [
        (0, 0, 0.0), (0, 0, 0.0), (1, 0, 0.0), (2, 0, 0.0), (2, 0, 0.0),
        (1, 0, 0.0), (0, 0, 0.0), (-1, 0, 0.0), (-2, 0, 0.0), (-2, 0, 0.0),
        (-1, 0, 0.0), (0, 0, 0.0), (0, 0, 0.4), (0, 0, 0.9), (0, 0, 0.4), (0, 0, 0.0),
    ]
    for dx, dy, close in seq:
        img = Image.new("RGB", (size, size), (0, 0, 0))
        d = ImageDraw.Draw(img)
        ew = max(4, size // 3)
        eh = max(4, int(size * 0.38))
        y_mid = size // 2
        cur_h = max(1, int(eh * (1.0 - close)))
        for cx in (size // 4 + 1, size - size // 4 - 2):
            x0, x1 = cx - ew // 2, cx + ew // 2
            y0, y1 = y_mid - cur_h // 2, y_mid + cur_h // 2
            d.rounded_rectangle([x0, y0, x1, y1], radius=max(1, ew // 4), fill=(5, 217, 232), outline=(0, 110, 140))
            if close < 0.6:
                pr = max(1, ew // 5)
                px = cx + dx
                py = y_mid + dy
                d.ellipse([px - pr, py - pr, px + pr, py + pr], fill=(255, 42, 109))
                if 0 <= px - 1 < size and 0 <= py - 1 < size:
                    img.putpixel((px - 1, py - 1), (255, 255, 255))
        frames.append(img)
    return frames


def make_pulsing_heart(size: int) -> List[Image.Image]:
    """Beating neon pixel heart with twinkling sparkles."""
    frames: List[Image.Image] = []
    scales = [0.85, 0.95, 1.08, 1.15, 1.05, 0.92, 0.85, 0.85]
    sparkles = [(3, 4), (size - 4, 5), (4, size - 5), (size - 5, size - 4)]
    for f_idx, sc in enumerate(scales):
        img = Image.new("RGB", (size, size), (0, 0, 0))
        px = img.load()
        for y in range(size):
            for x in range(size):
                nx = ((x / (size - 1)) * 2.7 - 1.35) / sc
                ny = (1.15 - (y / (size - 1)) * 2.4) / sc
                v = (nx * nx + ny * ny - 1.0) ** 3 - nx * nx * (ny ** 3)
                if v <= 0:
                    glow = min(255, int(180 + 75 * sc))
                    px[x, y] = (255, 20 if v > -0.15 else 60, glow // 2)
        sx, sy = sparkles[f_idx % len(sparkles)]
        px[sx, sy] = (255, 255, 200)
        frames.append(img)
    return frames


def make_matrix_rain(size: int) -> List[Image.Image]:
    """Falling green Matrix code rain."""
    rng = random.Random(42)
    cols = [rng.randint(0, size - 1) for _ in range(size)]
    speeds = [rng.choice([1, 1, 2]) for _ in range(size)]
    lengths = [rng.randint(4, max(6, size // 2)) for _ in range(size)]
    frames: List[Image.Image] = []
    for step in range(24):
        img = Image.new("RGB", (size, size), (0, 0, 0))
        px = img.load()
        for x in range(0, size, 2):
            head = (cols[x] + step * speeds[x]) % size
            length = lengths[x]
            for k in range(length):
                y = (head - k) % size
                if k == 0:
                    px[x, y] = (200, 255, 210)
                else:
                    fade = max(20, int(240 * (1.0 - k / length)))
                    px[x, y] = (0, fade, 20)
        frames.append(img)
    return frames


def make_retro_fire(size: int) -> List[Image.Image]:
    """Animated pixel campfire flame."""
    rng = random.Random(7)
    heat = [[0.0] * size for _ in range(size)]
    palette = []
    for i in range(256):
        if i < 85:
            palette.append((i * 3, 0, 0))
        elif i < 170:
            palette.append((255, (i - 85) * 3, 0))
        else:
            palette.append((255, 255, (i - 170) * 3))
    frames: List[Image.Image] = []
    for step in range(30):
        for x in range(size):
            heat[size - 1][x] = rng.uniform(180, 255)
        for y in range(size - 2, -1, -1):
            for x in range(size):
                avg = (
                    heat[y + 1][(x - 1) % size]
                    + heat[y + 1][x]
                    + heat[y + 1][(x + 1) % size]
                    + heat[min(size - 1, y + 2)][x]
                ) / 4.0
                heat[y][x] = max(0.0, avg - rng.uniform(5, 18))
        if step >= 10:  # skip warm-up
            img = Image.new("RGB", (size, size), (0, 0, 0))
            px = img.load()
            for y in range(size):
                for x in range(size):
                    px[x, y] = palette[min(255, int(heat[y][x]))]
            frames.append(img)
    return frames


def make_rainbow_wave(size: int) -> List[Image.Image]:
    """Smooth looping diagonal neon rainbow wave."""
    frames: List[Image.Image] = []
    n_frames = 16
    for f in range(n_frames):
        img = Image.new("RGB", (size, size))
        px = img.load()
        phase = f / n_frames
        for y in range(size):
            for x in range(size):
                hue = ((x + y) / (size * 1.5) - phase) % 1.0
                r, g, b = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
                px[x, y] = (int(r * 255), int(g * 255), int(b * 255))
        frames.append(img)
    return frames


def make_pacman_chomp(size: int) -> List[Image.Image]:
    """Chomping yellow pixel character eating glowing dots."""
    frames: List[Image.Image] = []
    n_frames = 16
    r = max(4, size // 3)
    cy = size // 2
    for f in range(n_frames):
        img = Image.new("RGB", (size, size), (0, 0, 0))
        d = ImageDraw.Draw(img)
        # Moving dots
        for k in range(4):
            dx = (size - ((f * 2 + k * (size // 4)) % size))
            if dx > size // 2 + 1:
                d.rectangle([dx - 1, cy - 1, dx + 1, cy + 1], fill=(255, 220, 180))
        # Pacman body
        mouth = 38 if (f % 4) in (0, 1) else 8
        cx = size // 3
        d.pieslice([cx - r, cy - r, cx + r, cy + r], start=mouth, end=360 - mouth, fill=(255, 220, 0))
        d.rectangle([cx, cy - r // 2 - 1, cx + 1, cy - r // 2], fill=(0, 0, 0))
        frames.append(img)
    return frames


def make_equalizer_bars(size: int) -> List[Image.Image]:
    """Bouncing colorful audio spectrum equalizer bars."""
    frames: List[Image.Image] = []
    bars = 8
    bar_w = max(2, size // bars)
    n_frames = 16
    for f in range(n_frames):
        img = Image.new("RGB", (size, size), (0, 0, 0))
        d = ImageDraw.Draw(img)
        for b in range(bars):
            val = 0.5 + 0.45 * math.sin(2 * math.pi * (f / n_frames) + b * 0.9)
            h = max(2, int(val * (size - 2)))
            x0 = b * bar_w + 1
            x1 = min(size - 1, x0 + bar_w - 2)
            for y in range(size - h, size):
                ratio = (size - y) / size
                if ratio > 0.75:
                    col = (255, 42, 109)
                elif ratio > 0.45:
                    col = (255, 213, 0)
                else:
                    col = (0, 245, 155)
                d.line([(x0, y), (x1, y)], fill=col)
            # Peak dot
            py = max(0, size - h - 2)
            d.line([(x0, py), (x1, py)], fill=(255, 255, 255))
        frames.append(img)
    return frames


def make_starfield_warp(size: int) -> List[Image.Image]:
    """Hyperspace starfield flying outward from center."""
    rng = random.Random(99)
    stars = [(rng.uniform(0, 2 * math.pi), rng.uniform(0.1, 1.0)) for _ in range(28)]
    frames: List[Image.Image] = []
    n_frames = 16
    cx, cy = size / 2.0, size / 2.0
    max_r = size * 0.65
    for f in range(n_frames):
        img = Image.new("RGB", (size, size), (2, 4, 12))
        px = img.load()
        for ang, base_d in stars:
            d = (base_d + f / n_frames) % 1.0
            dist = (d ** 1.6) * max_r
            x = int(round(cx + dist * math.cos(ang)))
            y = int(round(cy + dist * math.sin(ang)))
            if 0 <= x < size and 0 <= y < size:
                b = min(255, int(60 + 195 * d))
                px[x, y] = (b, b, 255)
        frames.append(img)
    return frames


GENERATORS = [
    ("01_cyber_eyes.gif", "Cyber Eyes", make_cyber_eyes, 120),
    ("02_pulsing_heart.gif", "Pulsing Heart", make_pulsing_heart, 110),
    ("03_matrix_rain.gif", "Matrix Rain", make_matrix_rain, 90),
    ("04_retro_fire.gif", "Retro Fire", make_retro_fire, 85),
    ("05_rainbow_wave.gif", "Rainbow Wave", make_rainbow_wave, 80),
    ("06_pacman_chomp.gif", "Chomp Dots", make_pacman_chomp, 100),
    ("07_equalizer_bars.gif", "Equalizer Bars", make_equalizer_bars, 90),
    ("08_starfield_warp.gif", "Starfield Warp", make_starfield_warp, 75),
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate pixel-art GIF pack for LOY PLAY displays.")
    parser.add_argument("--size", type=int, default=32, help="Square pixel size (default 32)")
    args = parser.parse_args()

    for filename, title, fn, delay in GENERATORS:
        frames = fn(args.size)
        out_path = OUT_DIR / filename
        save_gif(frames, out_path, duration_ms=delay)
        print(f"Generated {out_path.name:22s} ({title:15s}) -> {len(frames):2d} frames, {out_path.stat().st_size:5d} bytes")


if __name__ == "__main__":
    main()
