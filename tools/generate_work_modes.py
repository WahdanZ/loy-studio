#!/usr/bin/env python3
"""
Generate 10 crisp, looping 64x64 pixel-art Work Mode GIFs for YS / LOY PLAY LED displays.

Saved directly into `gifs/` (09_mode_* .. 18_mode_*) so they immediately appear in
LOY Studio 2.0's Explore / Local GIF Pack gallery and upload cleanly in ~2-6 seconds.
"""

from __future__ import annotations

import colorsys
import math
from pathlib import Path
from typing import Dict, List, Tuple

from PIL import Image, ImageDraw

OUT_DIR = Path(__file__).resolve().parent.parent / "gifs"
SIZE = 64

# Crisp 5x7 pixel bitmap font (no anti-aliasing, razor-sharp on LED matrices)
FONT_5X7: Dict[str, List[str]] = {
    "A": [".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"],
    "B": ["####.", "#...#", "#...#", "####.", "#...#", "#...#", "####."],
    "C": [".####", "#....", "#....", "#....", "#....", "#....", ".####"],
    "D": ["####.", "#...#", "#...#", "#...#", "#...#", "#...#", "####."],
    "E": ["#####", "#....", "#....", "####.", "#....", "#....", "#####"],
    "F": ["#####", "#....", "#....", "####.", "#....", "#....", "#...."],
    "G": [".####", "#....", "#....", "#..##", "#...#", "#...#", ".####"],
    "H": ["#...#", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"],
    "I": [".###.", "..#..", "..#..", "..#..", "..#..", "..#..", ".###."],
    "K": ["#...#", "#..#.", "#.#..", "##...", "#.#..", "#..#.", "#...#"],
    "L": ["#....", "#....", "#....", "#....", "#....", "#....", "#####"],
    "M": ["#...#", "##.##", "#.#.#", "#...#", "#...#", "#...#", "#...#"],
    "N": ["#...#", "##..#", "#.#.#", "#..##", "#...#", "#...#", "#...#"],
    "O": [".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."],
    "P": ["####.", "#...#", "#...#", "####.", "#....", "#....", "#...."],
    "R": ["####.", "#...#", "#...#", "####.", "#.#..", "#..#.", "#...#"],
    "S": [".####", "#....", "#....", ".###.", "....#", "....#", "####."],
    "T": ["#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."],
    "U": ["#...#", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."],
    "V": ["#...#", "#...#", "#...#", "#...#", "#...#", ".#.#.", "..#.."],
    "W": ["#...#", "#...#", "#...#", "#...#", "#.#.#", "##.##", "#...#"],
    "Y": ["#...#", "#...#", ".#.#.", "..#..", "..#..", "..#..", "..#.."],
    "Z": ["#####", "....#", "...#.", "..#..", ".#...", "#....", "#####"],
    "<": ["...#.", "..#..", ".#...", "#....", ".#...", "..#..", "...#."],
    ">": [".#...", "..#..", "...#.", "....#", "...#.", "..#..", ".#..."],
    "/": ["....#", "...#.", "...#.", "..#..", ".#...", ".#...", "#...."],
    " ": [".....", ".....", ".....", ".....", ".....", ".....", "....."],
}


def draw_pixel_text(
    img: Image.Image,
    text: str,
    y: int,
    color: Tuple[int, int, int],
    *,
    scale: int = 1,
    spacing: int = 1,
    center: bool = True,
    x: int = 0,
) -> None:
    text = text.upper()
    char_w = 5 * scale
    total_w = len(text) * (char_w + spacing * scale) - spacing * scale
    start_x = (img.width - total_w) // 2 if center else x
    px = img.load()
    for idx, ch in enumerate(text):
        glyph = FONT_5X7.get(ch, FONT_5X7[" "])
        gx = start_x + idx * (char_w + spacing * scale)
        for row_i, row in enumerate(glyph):
            for col_i, bit in enumerate(row):
                if bit == "#":
                    for sy in range(scale):
                        for sx in range(scale):
                            tx = gx + col_i * scale + sx
                            ty = y + row_i * scale + sy
                            if 0 <= tx < img.width and 0 <= ty < img.height:
                                px[tx, ty] = color


def save_gif(frames: List[Image.Image], path: Path, duration_ms: int = 100) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pal_frames = [
        f.convert("P", palette=Image.Palette.ADAPTIVE, colors=48, dither=Image.Dither.NONE)
        for f in frames
    ]
    pal_frames[0].save(
        path,
        format="GIF",
        save_all=True,
        append_images=pal_frames[1:],
        duration=duration_ms,
        loop=0,
        disposal=2,
        optimize=True,
    )


def make_mode_deep_focus() -> List[Image.Image]:
    """09: Deep Focus — pulsing cyan target rings + orbiting dot + FOCUS label."""
    frames: List[Image.Image] = []
    cx, cy = 32, 26
    n = 16
    for f in range(n):
        img = Image.new("RGB", (SIZE, SIZE), (0, 0, 0))
        d = ImageDraw.Draw(img)
        t = f / n
        pulse = 0.5 + 0.5 * math.sin(2 * math.pi * t)

        # Outer subtle ring
        r_out = 19
        c_dim = (0, int(65 + 45 * pulse), int(105 + 65 * pulse))
        d.ellipse([cx - r_out, cy - r_out, cx + r_out, cy + r_out], outline=c_dim)

        # Middle pulsing ring
        r_mid = 12 + int(round(pulse * 2))
        d.ellipse([cx - r_mid, cy - r_mid, cx + r_mid, cy + r_mid], outline=(5, 217, 232))

        # Crosshair ticks
        for ang_deg in (0, 90, 180, 270):
            rad = math.radians(ang_deg)
            x0, y0 = int(cx + 15 * math.cos(rad)), int(cy + 15 * math.sin(rad))
            x1, y1 = int(cx + 21 * math.cos(rad)), int(cy + 21 * math.sin(rad))
            d.line([x0, y0, x1, y1], fill=(0, 160, 200))

        # Orbiting focus spark
        ang = 2 * math.pi * t
        ox = int(round(cx + r_out * math.cos(ang)))
        oy = int(round(cy + r_out * math.sin(ang)))
        d.rectangle([ox - 1, oy - 1, ox + 1, oy + 1], fill=(255, 255, 255))

        # Glowing core
        cr = 4 if pulse > 0.5 else 3
        d.ellipse([cx - cr, cy - cr, cx + cr, cy + cr], fill=(0, 240, 255))
        d.ellipse([cx - 1, cy - 1, cx + 1, cy + 1], fill=(255, 255, 255))

        draw_pixel_text(img, "FOCUS", 52, (5, 217, 232))
        frames.append(img)
    return frames


def make_mode_coding() -> List[Image.Image]:
    """10: Coding / Dev Mode — dark terminal window with typing syntax lines & cursor."""
    frames: List[Image.Image] = []
    # (row_y, segments: [(length, color)])
    code_lines = [
        (17, [(8, (190, 90, 255)), (2, (0, 0, 0)), (16, (5, 217, 232))]),
        (23, [(4, (0, 0, 0)), (10, (50, 235, 130)), (2, (0, 0, 0)), (18, (255, 210, 80))]),
        (29, [(4, (0, 0, 0)), (14, (5, 217, 232)), (2, (0, 0, 0)), (12, (255, 90, 140))]),
        (35, [(8, (0, 0, 0)), (20, (50, 235, 130))]),
        (41, [(4, (0, 0, 0)), (12, (190, 90, 255)), (2, (0, 0, 0)), (10, (255, 255, 255))]),
    ]
    n = 16
    for f in range(n):
        img = Image.new("RGB", (SIZE, SIZE), (0, 0, 0))
        d = ImageDraw.Draw(img)

        # Terminal frame
        d.rounded_rectangle([3, 4, 60, 48], radius=3, fill=(8, 12, 18), outline=(35, 75, 100))
        d.line([4, 12, 59, 12], fill=(35, 75, 100))
        # Traffic lights
        d.rectangle([7, 7, 8, 8], fill=(255, 75, 85))
        d.rectangle([11, 7, 12, 8], fill=(255, 200, 50))
        d.rectangle([15, 7, 16, 8], fill=(40, 220, 110))

        # Reveal characters progressively across frames
        budget = int(((f + 2) / n) * 145)
        used = 0
        cursor_pos = (8, 17)
        for ly, segs in code_lines:
            cx = 8
            for seg_len, col in segs:
                if used >= budget:
                    break
                take = min(seg_len, budget - used)
                if col != (0, 0, 0) and take > 0:
                    d.rectangle([cx, ly, cx + take - 1, ly + 2], fill=col)
                cx += take
                used += take
                cursor_pos = (min(56, cx + 1), ly)
            if used >= budget:
                break

        # Blinking block cursor
        if f % 2 == 0:
            bx, by = cursor_pos
            d.rectangle([bx, by, min(57, bx + 2), by + 2], fill=(50, 255, 140))

        draw_pixel_text(img, "</CODE>", 53, (50, 235, 130))
        frames.append(img)
    return frames


def make_mode_on_air() -> List[Image.Image]:
    """11: On Air / Meeting — studio red glowing badge + animated mic & sound waves."""
    frames: List[Image.Image] = []
    n = 12
    for f in range(n):
        img = Image.new("RGB", (SIZE, SIZE), (0, 0, 0))
        d = ImageDraw.Draw(img)
        t = f / n
        pulse = 0.5 + 0.5 * math.sin(2 * math.pi * t)
        red_glow = int(145 + 110 * pulse)

        # Glowing studio border
        d.rounded_rectangle([2, 2, 61, 61], radius=5, outline=(red_glow, 12, 22))
        d.rounded_rectangle([4, 4, 59, 59], radius=4, outline=(red_glow // 2, 0, 8))

        # Pixel microphone icon centered at (32, 24)
        d.rounded_rectangle([28, 11, 36, 26], radius=4, fill=(255, 45, 65), outline=(255, 190, 195))
        # Mic grille lines
        d.line([30, 15, 34, 15], fill=(120, 10, 20))
        d.line([30, 18, 34, 18], fill=(120, 10, 20))
        d.line([30, 21, 34, 21], fill=(120, 10, 20))
        # Mic U-stand
        d.arc([24, 16, 40, 32], start=0, end=180, fill=(230, 230, 240))
        d.line([32, 32, 32, 38], fill=(230, 230, 240))
        d.line([26, 38, 38, 38], fill=(230, 230, 240))

        # Animated expanding sound waves left & right
        wave_phase = f % 3
        for w_idx, rad in enumerate((7, 11, 15)):
            col = (255, 60, 80) if w_idx == wave_phase else (110, 15, 25)
            d.arc([32 - rad - 5, 19 - rad // 2, 32 + rad - 5, 19 + rad // 2], start=130, end=230, fill=col)
            d.arc([32 - rad + 5, 19 - rad // 2, 32 + rad + 5, 19 + rad // 2], start=310, end=50, fill=col)

        # Solid red pill + white ON AIR text
        d.rounded_rectangle([8, 44, 55, 56], radius=3, fill=(red_glow, 12, 24))
        draw_pixel_text(img, "ON AIR", 47, (255, 255, 255))
        frames.append(img)
    return frames


def make_mode_dnd() -> List[Image.Image]:
    """12: Do Not Disturb — bold pulsing crimson no-entry badge + rotating caution ticks."""
    frames: List[Image.Image] = []
    cx, cy = 32, 26
    n = 12
    for f in range(n):
        img = Image.new("RGB", (SIZE, SIZE), (0, 0, 0))
        d = ImageDraw.Draw(img)
        t = f / n
        pulse = 0.5 + 0.5 * math.sin(2 * math.pi * t)
        r = 17

        # Rotating outer caution dots
        for k in range(8):
            ang = 2 * math.pi * (k / 8.0 + t * 0.25)
            dx = int(round(cx + 22 * math.cos(ang)))
            dy = int(round(cy + 22 * math.sin(ang)))
            col = (255, 70, 50) if (k + f) % 2 == 0 else (110, 15, 15)
            if 0 <= dx < SIZE and 0 <= dy < SIZE:
                img.putpixel((dx, dy), col)

        # Bold crimson circle
        red_v = int(185 + 70 * pulse)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(red_v, 10, 20), outline=(255, 90, 90))
        d.ellipse([cx - r + 4, cy - r + 4, cx + r - 4, cy + r - 4], fill=(15, 0, 4))

        # Horizontal white minus bar (classic DND icon)
        d.rounded_rectangle([cx - 10, cy - 3, cx + 10, cy + 3], radius=2, fill=(255, 255, 255))

        draw_pixel_text(img, "DND", 51, (255, 65, 75))
        frames.append(img)
    return frames


def make_mode_coffee_break() -> List[Image.Image]:
    """13: Coffee Break — warm pixel mug with rising animated steam + BREAK label."""
    frames: List[Image.Image] = []
    n = 16
    for f in range(n):
        img = Image.new("RGB", (SIZE, SIZE), (0, 0, 0))
        d = ImageDraw.Draw(img)
        t = f / n

        # Rising animated curly steam wisps (3 columns)
        for idx, sx in enumerate((23, 30, 37)):
            for step in range(11):
                sy = 20 - step - (f % 3)
                if 4 <= sy <= 20:
                    wiggle = int(round(math.sin((step + f * 0.8 + idx * 2.0) * 0.65) * 1.6))
                    fade = max(40, int(230 * (1.0 - (20 - sy) / 18.0)))
                    img.putpixel((sx + wiggle, sy), (fade, fade, int(fade * 0.85)))

        # Coffee mug body
        d.rounded_rectangle([17, 23, 43, 44], radius=4, fill=(215, 130, 55), outline=(255, 190, 110))
        # Coffee liquid top rim
        d.rectangle([19, 25, 41, 27], fill=(95, 48, 20))
        # Mug handle
        d.arc([39, 26, 51, 39], start=270, end=90, fill=(255, 190, 110), width=2)
        # Saucer
        d.line([13, 46, 47, 46], fill=(180, 120, 70))
        # Warm highlight stripe
        d.line([21, 30, 21, 40], fill=(255, 225, 175))

        pulse = 0.5 + 0.5 * math.sin(2 * math.pi * t)
        draw_pixel_text(img, "BREAK", 52, (255, int(170 + 60 * pulse), 70))
        frames.append(img)
    return frames


def make_mode_pomodoro() -> List[Image.Image]:
    """14: Pomodoro / Flow — sweeping circular progress ring + pixel tomato/timer + FLOW."""
    frames: List[Image.Image] = []
    cx, cy = 32, 26
    n = 16
    for f in range(n):
        img = Image.new("RGB", (SIZE, SIZE), (0, 0, 0))
        d = ImageDraw.Draw(img)

        # Background ring track
        r = 19
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(45, 25, 25), width=3)

        # Sweeping progress arc (orange -> red-pink)
        sweep = int(((f + 1) / n) * 360)
        d.arc([cx - r, cy - r, cx + r, cy + r], start=-90, end=-90 + sweep, fill=(255, 70, 55), width=3)

        # Leading bright dot
        ang = math.radians(-90 + sweep)
        lx = int(round(cx + (r - 1) * math.cos(ang)))
        ly = int(round(cy + (r - 1) * math.sin(ang)))
        d.ellipse([lx - 2, ly - 2, lx + 2, ly + 2], fill=(255, 240, 150))

        # Inner pixel tomato / timer core
        d.ellipse([cx - 10, cy - 8, cx + 10, cy + 10], fill=(220, 35, 45))
        # Green stem leaves
        d.line([cx, cy - 11, cx, cy - 7], fill=(50, 220, 90), width=2)
        d.line([cx - 4, cy - 9, cx + 4, cy - 9], fill=(50, 220, 90))
        # Shine
        d.rectangle([cx - 6, cy - 4, cx - 4, cy - 2], fill=(255, 165, 165))

        draw_pixel_text(img, "FLOW", 52, (255, 110, 75))
        frames.append(img)
    return frames


def make_mode_gaming() -> List[Image.Image]:
    """15: Gaming / Chill Mode — neon cyberpunk gamepad with flashing buttons + PLAY."""
    frames: List[Image.Image] = []
    n = 12
    btn_colors = [(255, 42, 109), (5, 217, 232), (255, 215, 0), (50, 240, 120)]
    for f in range(n):
        img = Image.new("RGB", (SIZE, SIZE), (0, 0, 0))
        d = ImageDraw.Draw(img)

        # Gamepad silhouette
        d.rounded_rectangle([8, 16, 55, 38], radius=10, fill=(28, 22, 58), outline=(170, 70, 255), width=2)
        d.ellipse([8, 24, 24, 43], fill=(28, 22, 58), outline=(170, 70, 255), width=2)
        d.ellipse([39, 24, 55, 43], fill=(28, 22, 58), outline=(170, 70, 255), width=2)
        d.rectangle([15, 20, 48, 36], fill=(28, 22, 58))

        # Glowing D-pad (left)
        d_col = (5, 217, 232) if f % 2 == 0 else (90, 245, 255)
        d.rectangle([18, 23, 21, 33], fill=d_col)
        d.rectangle([14, 27, 25, 30], fill=d_col)

        # Flashing ABXY buttons (right)
        coords = [(44, 23), (48, 27), (44, 31), (40, 27)]
        for i, (bx, by) in enumerate(coords):
            active = (f % 4) == i
            col = (255, 255, 255) if active else btn_colors[i]
            d.rectangle([bx - 1, by - 1, bx + 1, by + 1], fill=col)

        # Start/Select center LEDs
        d.line([29, 30, 31, 30], fill=(255, 42, 109))
        d.line([33, 30, 35, 30], fill=(5, 217, 232))

        hue = (f / n) % 1.0
        r, g, b = [int(c * 255) for c in colorsys.hsv_to_rgb(hue, 0.85, 1.0)]
        draw_pixel_text(img, "PLAY", 51, (r, g, b))
        frames.append(img)
    return frames


def make_mode_music_vibe() -> List[Image.Image]:
    """16: Music / Lo-Fi Vibe — pixel headphones + bouncing neon equalizer + VIBE."""
    frames: List[Image.Image] = []
    n = 14
    for f in range(n):
        img = Image.new("RGB", (SIZE, SIZE), (0, 0, 0))
        d = ImageDraw.Draw(img)

        # Headphones headband
        d.arc([16, 6, 48, 34], start=180, end=360, fill=(190, 95, 255), width=3)
        # Earcups
        d.rounded_rectangle([12, 19, 19, 32], radius=3, fill=(5, 217, 232), outline=(255, 255, 255))
        d.rounded_rectangle([45, 19, 52, 32], radius=3, fill=(255, 42, 109), outline=(255, 255, 255))

        # Bouncing neon equalizer bars between & below earcups
        n_bars = 7
        for b in range(n_bars):
            phase = (f / n) * 2 * math.pi + b * 0.9
            bh = 4 + int((0.5 + 0.5 * math.sin(phase)) * 15)
            bx = 21 + b * 3
            hue = (b / n_bars + f / (n * 2)) % 1.0
            r, g, bl = [int(c * 255) for c in colorsys.hsv_to_rgb(hue, 1.0, 1.0)]
            d.rectangle([bx, 45 - bh, bx + 1, 45], fill=(r, g, bl))
            d.rectangle([bx, 45 - bh - 2, bx + 1, 45 - bh - 2], fill=(255, 255, 255))

        draw_pixel_text(img, "VIBE", 52, (255, 90, 210))
        frames.append(img)
    return frames


def make_mode_brb() -> List[Image.Image]:
    """17: Be Right Back — animated hourglass with falling amber sand + BRB label."""
    frames: List[Image.Image] = []
    n = 14
    for f in range(n):
        img = Image.new("RGB", (SIZE, SIZE), (0, 0, 0))
        d = ImageDraw.Draw(img)
        frac = f / (n - 1)

        # Hourglass wooden caps
        d.rectangle([19, 6, 45, 9], fill=(255, 185, 50))
        d.rectangle([19, 43, 45, 46], fill=(255, 185, 50))

        # Glass outline (top & bottom triangles)
        d.polygon([(21, 10), (43, 10), (32, 26)], outline=(140, 210, 255))
        d.polygon([(32, 26), (21, 42), (43, 42)], outline=(140, 210, 255))

        # Top sand shrinking
        top_h = int((1.0 - frac) * 12)
        if top_h > 1:
            half_w = int((top_h / 12.0) * 8)
            d.polygon([(32 - half_w, 24 - top_h), (32 + half_w, 24 - top_h), (32, 24)], fill=(255, 190, 40))

        # Falling sand stream
        for sy in range(26, 42, 2):
            if (sy + f) % 4 < 2:
                img.putpixel((32, sy), (255, 225, 100))

        # Bottom sand growing
        bot_h = max(1, int(frac * 11))
        bot_w = int((bot_h / 11.0) * 9) + 1
        d.polygon([(32, 41 - bot_h), (32 - bot_w, 41), (32 + bot_w, 41)], fill=(255, 170, 30))

        draw_pixel_text(img, "BRB", 52, (255, 195, 55))
        frames.append(img)
    return frames


def make_mode_done_night() -> List[Image.Image]:
    """18: Done / Night Mode — glowing crescent moon + twinkling stars + floating Zzz."""
    frames: List[Image.Image] = []
    stars = [(10, 10), (50, 9), (55, 24), (9, 32), (47, 37), (18, 40)]
    n = 16
    for f in range(n):
        img = Image.new("RGB", (SIZE, SIZE), (0, 0, 0))
        d = ImageDraw.Draw(img)
        t = f / n

        # Crescent moon
        d.ellipse([18, 12, 42, 36], fill=(255, 220, 95))
        d.ellipse([25, 9, 47, 31], fill=(0, 0, 0))

        # Twinkling pixel stars
        for idx, (sx, sy) in enumerate(stars):
            tw = 0.5 + 0.5 * math.sin(2 * math.pi * t + idx * 1.3)
            v = int(70 + 185 * tw)
            img.putpixel((sx, sy), (v, v, min(255, v + 30)))
            if tw > 0.75:
                for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    if 0 <= sx + dx < SIZE and 0 <= sy + dy < SIZE:
                        img.putpixel((sx + dx, sy + dy), (v // 2, v // 2, v))

        # Floating Z letter drifting up-right
        zy = 22 - (f % 8)
        zx = 41 + (f % 8) // 2
        draw_pixel_text(img, "Z", zy, (120, 165, 255), center=False, x=zx)

        draw_pixel_text(img, "DONE", 52, (130, 165, 255))
        frames.append(img)
    return frames


WORK_MODES = [
    ("09_mode_deep_focus.gif", make_mode_deep_focus, 90),
    ("10_mode_coding.gif", make_mode_coding, 110),
    ("11_mode_on_air_meeting.gif", make_mode_on_air, 100),
    ("12_mode_do_not_disturb.gif", make_mode_dnd, 100),
    ("13_mode_coffee_break.gif", make_mode_coffee_break, 110),
    ("14_mode_pomodoro.gif", make_mode_pomodoro, 110),
    ("15_mode_gaming_chill.gif", make_mode_gaming, 110),
    ("16_mode_music_vibe.gif", make_mode_music_vibe, 90),
    ("17_mode_away_brb.gif", make_mode_brb, 110),
    ("18_mode_done_offline.gif", make_mode_done_night, 120),
]


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for filename, fn, dur_ms in WORK_MODES:
        frames = fn()
        out_path = OUT_DIR / filename
        save_gif(frames, out_path, duration_ms=dur_ms)
        kb = out_path.stat().st_size / 1024.0
        print(f"Generated {out_path.name:28s} ({len(frames):2d} frames, {kb:5.1f} KB, {dur_ms}ms/frame)")


if __name__ == "__main__":
    main()
