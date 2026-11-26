#!/usr/bin/env python3
"""
Generate 22 crisp, looping 64x64 pixel-art GIFs (Work Modes Pack 2, Ambient Scenes & Seasonal)
for LOY Studio LED matrix displays.

Saves directly into `gifs/` with filenames matching `app/catalog.py` exactly.
Every GIF is 64x64, <= 16 frames, looping, and compact (< 10 KB) for fast BLE transfer.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import List, Tuple

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.generate_work_modes import FONT_5X7, OUT_DIR, SIZE, draw_pixel_text, save_gif

# Extend FONT_5X7 with digits, J, Q, X, and punctuation
FONT_5X7.update({
    "0": [".###.", "#..##", "#.#.#", "#.#.#", "##..#", "#...#", ".###."],
    "1": ["..#..", ".##..", "..#..", "..#..", "..#..", "..#..", ".###."],
    "2": [".###.", "#...#", "....#", "..##.", ".#...", "#....", "#####"],
    "3": ["####.", "....#", "....#", ".###.", "....#", "....#", "####."],
    "4": ["#...#", "#...#", "#...#", "#####", "....#", "....#", "....#"],
    "5": ["#####", "#....", "####.", "....#", "....#", "#...#", ".###."],
    "6": [".###.", "#....", "####.", "#...#", "#...#", "#...#", ".###."],
    "7": ["#####", "....#", "...#.", "..#..", ".#...", ".#...", ".#..."],
    "8": [".###.", "#...#", "#...#", ".###.", "#...#", "#...#", ".###."],
    "9": [".###.", "#...#", "#...#", ".####", "....#", "...#.", ".##.."],
    "J": ["..###", "...#.", "...#.", "...#.", "#..#.", "#..#.", ".##.."],
    "Q": [".###.", "#...#", "#...#", "#...#", "#.#.#", "#..#.", ".##.#"],
    "X": ["#...#", "#...#", ".#.#.", "..#..", ".#.#.", "#...#", "#...#"],
    "!": ["..#..", "..#..", "..#..", "..#..", "..#..", ".....", "..#.."],
    "?": [".###.", "#...#", "....#", "..##.", "..#..", ".....", "..#.."],
    ":": [".....", "..#..", "..#..", ".....", "..#..", "..#..", "....."],
    ".": [".....", ".....", ".....", ".....", ".....", "..#..", "..#.."],
    "-": [".....", ".....", ".....", "#####", ".....", ".....", "....."],
})


def _pt(img: Image.Image, x: int, y: int, col: Tuple[int, int, int]) -> None:
    if 0 <= x < SIZE and 0 <= y < SIZE:
        img.putpixel((x, y), col)


# ---------------------------------------------------------------------------
# Work Modes Pack 2 (19..28)
# ---------------------------------------------------------------------------

def make_mode_lunch() -> List[Image.Image]:
    """19: Lunch — steaming warm plate with fork & knife + LUNCH."""
    frames: List[Image.Image] = []
    n = 12
    for f in range(n):
        img = Image.new("RGB", (SIZE, SIZE), (0, 0, 0))
        d = ImageDraw.Draw(img)
        # Steam wisps above plate
        for idx, sx in enumerate((25, 32, 39)):
            for step in range(9):
                sy = 17 - step - (f % 3)
                if 3 <= sy <= 17:
                    wx = sx + int(round(math.sin((step + f * 0.9 + idx * 1.7) * 0.7) * 1.3))
                    v = max(60, int(230 * (1.0 - (17 - sy) / 16.0)))
                    _pt(img, wx, sy, (v, v, int(v * 0.8)))
        # Plate
        d.ellipse([16, 19, 48, 46], fill=(225, 230, 240), outline=(140, 155, 175))
        d.ellipse([21, 23, 43, 42], fill=(235, 130, 45), outline=(255, 185, 85))
        # Garnish dots
        d.rectangle([28, 29, 31, 32], fill=(60, 210, 95))
        d.rectangle([34, 33, 37, 36], fill=(255, 225, 90))
        # Fork (left)
        d.line([9, 20, 9, 44], fill=(185, 200, 220), width=2)
        d.line([7, 19, 7, 26], fill=(185, 200, 220))
        d.line([11, 19, 11, 26], fill=(185, 200, 220))
        # Knife (right)
        d.line([54, 20, 54, 44], fill=(185, 200, 220), width=2)
        d.rectangle([53, 19, 56, 30], fill=(215, 225, 240))
        draw_pixel_text(img, "LUNCH", 52, (255, 195, 70))
        frames.append(img)
    return frames


def make_mode_workout() -> List[Image.Image]:
    """20: Workout — animated lifting dumbbell + energy sparks + WORKOUT."""
    frames: List[Image.Image] = []
    n = 12
    for f in range(n):
        img = Image.new("RGB", (SIZE, SIZE), (0, 0, 0))
        d = ImageDraw.Draw(img)
        t = f / n
        lift = int(round(math.sin(2 * math.pi * t) * 5))
        cy = 27 - lift
        # Dumbbell bar
        d.rectangle([16, cy - 2, 47, cy + 2], fill=(195, 205, 220))
        # Left weight plates
        d.rounded_rectangle([10, cy - 11, 16, cy + 11], radius=2, fill=(40, 220, 145), outline=(140, 255, 205))
        d.rounded_rectangle([5, cy - 8, 10, cy + 8], radius=1, fill=(20, 160, 105))
        # Right weight plates
        d.rounded_rectangle([47, cy - 11, 53, cy + 11], radius=2, fill=(40, 220, 145), outline=(140, 255, 205))
        d.rounded_rectangle([53, cy - 8, 58, cy + 8], radius=1, fill=(20, 160, 105))
        # Energy bursts at top of lift
        if lift >= 2:
            d.line([24, cy - 12, 21, cy - 16], fill=(255, 215, 60))
            d.line([32, cy - 13, 32, cy - 18], fill=(255, 215, 60))
            d.line([40, cy - 12, 43, cy - 16], fill=(255, 215, 60))
        draw_pixel_text(img, "WORKOUT", 52, (50, 235, 155))
        frames.append(img)
    return frames


def make_mode_reading() -> List[Image.Image]:
    """21: Reading — open book with gently turning page & warm glow + READ."""
    frames: List[Image.Image] = []
    n = 12
    for f in range(n):
        img = Image.new("RGB", (SIZE, SIZE), (0, 0, 0))
        d = ImageDraw.Draw(img)
        t = f / n
        # Warm reading lamp glow arc at top
        glow = int(80 + 35 * math.sin(2 * math.pi * t))
        d.ellipse([22, 4, 42, 16], fill=(glow, int(glow * 0.72), 18))
        d.ellipse([28, 7, 36, 13], fill=(255, 225, 120))
        # Book cover
        d.rounded_rectangle([9, 20, 54, 46], radius=3, fill=(55, 95, 185), outline=(110, 160, 255))
        # Left & right pages
        d.rectangle([12, 22, 31, 43], fill=(245, 238, 220))
        d.rectangle([33, 22, 51, 43], fill=(235, 228, 210))
        d.line([32, 21, 32, 45], fill=(90, 110, 150))
        # Text lines on pages
        for ly in (26, 30, 34, 38):
            d.line([15, ly, 28, ly], fill=(120, 125, 145))
            d.line([36, ly, 48, ly], fill=(120, 125, 145))
        # Animated flipping page arc
        flip_x = int(50 - 36 * (f / (n - 1)))
        d.line([32, 43, flip_x, 22], fill=(255, 252, 240), width=2)
        draw_pixel_text(img, "READING", 52, (130, 195, 255))
        frames.append(img)
    return frames


def make_mode_sleep() -> List[Image.Image]:
    """22: Sleep — glowing crescent moon, twinkling stars & floating Zzz."""
    frames: List[Image.Image] = []
    n = 16
    stars = [(10, 10), (50, 12), (14, 44), (54, 38), (44, 6), (8, 28)]
    for f in range(n):
        img = Image.new("RGB", (SIZE, SIZE), (3, 5, 16))
        d = ImageDraw.Draw(img)
        # Twinkling stars
        for idx, (sx, sy) in enumerate(stars):
            br = 110 + int(145 * (0.5 + 0.5 * math.sin(2 * math.pi * (f / n + idx * 0.2))))
            _pt(img, sx, sy, (br, br, min(255, br + 30)))
        # Crescent moon
        d.ellipse([11, 14, 35, 38], fill=(255, 225, 115))
        d.ellipse([19, 11, 41, 33], fill=(3, 5, 16))
        # Floating Z's rising smoothly
        for idx, (bx, by, col) in enumerate([(38, 34, (140, 185, 255)), (45, 24, (180, 210, 255)), (51, 14, (220, 235, 255))]):
            dy = (f + idx * 5) % n
            yy = by - (dy // 3)
            if 6 <= yy <= 40:
                draw_pixel_text(img, "Z", yy, col, center=False, x=bx)
        draw_pixel_text(img, "SLEEP", 52, (150, 185, 255))
        frames.append(img)
    return frames


def make_mode_meeting_soon() -> List[Image.Image]:
    """23: Meeting Soon — calendar card with pulsing alert bell + SOON!."""
    frames: List[Image.Image] = []
    n = 12
    for f in range(n):
        img = Image.new("RGB", (SIZE, SIZE), (0, 0, 0))
        d = ImageDraw.Draw(img)
        t = f / n
        pulse = 0.5 + 0.5 * math.sin(2 * math.pi * t)
        # Calendar body
        d.rounded_rectangle([12, 8, 51, 45], radius=4, fill=(22, 28, 45), outline=(95, 145, 255))
        d.rounded_rectangle([12, 8, 51, 17], radius=3, fill=(235, 65, 85))
        # Rings
        d.rectangle([20, 5, 23, 11], fill=(235, 240, 250))
        d.rectangle([40, 5, 43, 11], fill=(235, 240, 250))
        # Animated shaking bell inside calendar
        shake = -2 if f % 4 == 1 else (2 if f % 4 == 3 else 0)
        bx = 32 + shake
        d.ellipse([bx - 7, 22, bx + 7, 36], fill=(255, 200, 55))
        d.rectangle([bx - 9, 34, bx + 9, 37], fill=(255, 215, 90))
        d.ellipse([bx - 2, 37, bx + 2, 41], fill=(255, 155, 35))
        # Pulsing corner dot
        r_col = (255, int(70 + 140 * pulse), 70)
        d.ellipse([45, 5, 53, 13], fill=r_col)
        draw_pixel_text(img, "SOON!", 52, (255, 205, 65))
        frames.append(img)
    return frames


def make_mode_recording() -> List[Image.Image]:
    """24: Recording — studio viewfinder corners, lens & blinking REC."""
    frames: List[Image.Image] = []
    n = 12
    for f in range(n):
        img = Image.new("RGB", (SIZE, SIZE), (0, 0, 0))
        d = ImageDraw.Draw(img)
        blink = (f // 3) % 2 == 0
        # Viewfinder corner brackets
        c = (190, 200, 215)
        for pts in [
            [(4, 12), (4, 4), (12, 4)],
            [(51, 4), (59, 4), (59, 12)],
            [(4, 51), (4, 59), (12, 59)],
            [(51, 59), (59, 59), (59, 51)],
        ]:
            d.line(pts, fill=c, width=2)
        # Camera body & lens
        d.rounded_rectangle([14, 16, 48, 39], radius=4, fill=(35, 42, 58), outline=(110, 125, 155))
        d.polygon([(48, 22), (56, 18), (56, 37), (48, 33)], fill=(65, 78, 102))
        d.ellipse([23, 20, 39, 36], fill=(10, 15, 28), outline=(80, 195, 255))
        d.ellipse([27, 24, 35, 32], fill=(25, 110, 210))
        _pt(img, 29, 26, (255, 255, 255))
        # Blinking red REC indicator
        dot_col = (255, 35, 55) if blink else (90, 12, 18)
        d.ellipse([13, 46, 21, 54], fill=dot_col)
        draw_pixel_text(img, "REC", 47, (255, 55, 70) if blink else (180, 45, 55), center=False, x=26)
        frames.append(img)
    return frames


def make_mode_out_of_office() -> List[Image.Image]:
    """25: Out of Office — swaying palm tree on warm island + OOO."""
    frames: List[Image.Image] = []
    n = 12
    for f in range(n):
        img = Image.new("RGB", (SIZE, SIZE), (4, 10, 24))
        d = ImageDraw.Draw(img)
        t = f / n
        sway = int(round(math.sin(2 * math.pi * t) * 2.5))
        # Warm sun
        d.ellipse([43, 6, 57, 20], fill=(255, 195, 55))
        # Ocean & sandy island
        d.rectangle([0, 40, 63, 47], fill=(15, 95, 175))
        d.ellipse([10, 35, 44, 48], fill=(235, 195, 105))
        # Curved trunk
        tx, ty = 26 + sway, 17
        d.line([(26, 38), (25 + sway // 2, 27), (tx, ty)], fill=(150, 95, 45), width=3)
        # Palm fronds
        for dx, dy in [(-12, -3), (-10, 4), (12, -2), (10, 5), (0, -8)]:
            d.line([(tx, ty), (tx + dx, ty + dy)], fill=(45, 215, 110), width=2)
        draw_pixel_text(img, "OOO", 52, (255, 215, 95))
        frames.append(img)
    return frames


def make_mode_on_my_way() -> List[Image.Image]:
    """26: On My Way — pixel car cruising on moving road + ON MY WAY."""
    frames: List[Image.Image] = []
    n = 12
    for f in range(n):
        img = Image.new("RGB", (SIZE, SIZE), (4, 8, 20))
        d = ImageDraw.Draw(img)
        bounce = 1 if f % 2 == 0 else 0
        # Road & scrolling lane dashes
        d.rectangle([0, 36, 63, 46], fill=(32, 36, 48))
        shift = (f * 5) % 16
        for x in range(-16, 64, 16):
            rx = x - shift
            d.rectangle([rx, 40, rx + 7, 42], fill=(255, 205, 65))
        # Car body
        cy = 24 + bounce
        d.rounded_rectangle([14, cy + 4, 49, cy + 13], radius=3, fill=(45, 165, 255), outline=(140, 215, 255))
        d.polygon([(21, cy + 4), (26, cy - 3), (40, cy - 3), (44, cy + 4)], fill=(120, 225, 255))
        # Headlight beam
        d.polygon([(49, cy + 6), (62, cy + 3), (62, cy + 12), (49, cy + 10)], fill=(130, 120, 45))
        # Wheels
        d.ellipse([19, cy + 10, 26, cy + 17], fill=(20, 22, 28), outline=(210, 215, 225))
        d.ellipse([38, cy + 10, 45, cy + 17], fill=(20, 22, 28), outline=(210, 215, 225))
        # Speed lines behind car
        d.line([3 + (f % 4), cy + 6, 10 + (f % 4), cy + 6], fill=(95, 195, 255))
        d.line([5 + ((f + 2) % 4), cy + 10, 11 + ((f + 2) % 4), cy + 10], fill=(95, 195, 255))
        draw_pixel_text(img, "ON MY WAY", 52, (90, 215, 255))
        frames.append(img)
    return frames


def make_mode_ship_it() -> List[Image.Image]:
    """27: Ship It — launching pixel rocket with flickering exhaust + SHIP IT!."""
    frames: List[Image.Image] = []
    n = 12
    for f in range(n):
        img = Image.new("RGB", (SIZE, SIZE), (3, 5, 14))
        d = ImageDraw.Draw(img)
        # Scrolling stars downward
        for idx, (sx, sy) in enumerate([(8, 6), (54, 14), (14, 30), (50, 38), (6, 44), (58, 26)]):
            yy = (sy + f * 4) % 48
            _pt(img, sx, yy, (190, 210, 255))
        bob = int(round(math.sin(2 * math.pi * f / n) * 2))
        ry = 14 + bob
        # Rocket fins
        d.polygon([(25, ry + 16), (19, ry + 24), (25, ry + 22)], fill=(245, 70, 95))
        d.polygon([(39, ry + 16), (45, ry + 24), (39, ry + 22)], fill=(245, 70, 95))
        # Rocket body & nose
        d.polygon([(32, ry - 4), (25, ry + 7), (39, ry + 7)], fill=(245, 70, 95))
        d.rectangle([25, ry + 7, 39, ry + 22], fill=(235, 240, 250))
        d.ellipse([29, ry + 10, 35, ry + 16], fill=(45, 195, 255), outline=(120, 140, 175))
        # Flickering exhaust flame
        fl = 7 + (f % 3) * 3
        d.polygon([(28, ry + 23), (36, ry + 23), (32, ry + 23 + fl)], fill=(255, 140, 30))
        d.polygon([(30, ry + 23), (34, ry + 23), (32, ry + 21 + fl // 2)], fill=(255, 240, 120))
        draw_pixel_text(img, "SHIP IT!", 52, (60, 240, 165))
        frames.append(img)
    return frames


def make_mode_build_failed() -> List[Image.Image]:
    """28: Build Failed — flashing crimson alert X + FAILED!."""
    frames: List[Image.Image] = []
    n = 12
    for f in range(n):
        img = Image.new("RGB", (SIZE, SIZE), (0, 0, 0))
        d = ImageDraw.Draw(img)
        pulse = 0.5 + 0.5 * math.sin(2 * math.pi * f / n)
        r = int(160 + 95 * pulse)
        # Flashing border
        d.rounded_rectangle([2, 2, 61, 61], radius=5, outline=(r, 20, 30), width=2)
        # Red circle & bold white X
        d.ellipse([14, 7, 50, 43], fill=(r, 18, 28), outline=(255, 95, 105))
        d.line([(23, 16), (41, 34)], fill=(255, 255, 255), width=4)
        d.line([(41, 16), (23, 34)], fill=(255, 255, 255), width=4)
        draw_pixel_text(img, "FAILED!", 50, (255, 75, 85))
        frames.append(img)
    return frames


# ---------------------------------------------------------------------------
# Ambient Scenes (29..34)
# ---------------------------------------------------------------------------

def make_scene_aquarium() -> List[Image.Image]:
    """29: Aquarium — swimming fish, swaying seaweed & rising bubbles."""
    frames: List[Image.Image] = []
    n = 16
    for f in range(n):
        img = Image.new("RGB", (SIZE, SIZE), (4, 18, 42))
        d = ImageDraw.Draw(img)
        t = f / n
        # Sand bed
        d.rectangle([0, 55, 63, 63], fill=(165, 130, 75))
        # Swaying seaweed stalks
        for sx, h in ((8, 22), (16, 16), (52, 24), (58, 15)):
            for step in range(h):
                wiggle = int(round(math.sin(2 * math.pi * t + step * 0.28) * 1.6))
                _pt(img, sx + wiggle, 54 - step, (30, 190, 95))
                _pt(img, sx + wiggle + 1, 54 - step, (20, 145, 70))
        # Fish 1 (orange, swims right)
        fx1 = (f * 4) % 74 - 10
        fy1 = 20 + int(round(math.sin(2 * math.pi * t) * 2))
        d.ellipse([fx1, fy1, fx1 + 12, fy1 + 7], fill=(255, 135, 40))
        d.polygon([(fx1, fy1 + 3), (fx1 - 5, fy1), (fx1 - 5, fy1 + 7)], fill=(255, 180, 60))
        _pt(img, fx1 + 9, fy1 + 2, (10, 10, 15))
        # Fish 2 (cyan/yellow, swims left)
        fx2 = 64 - ((f * 4 + 28) % 76)
        fy2 = 38 + int(round(math.cos(2 * math.pi * t) * 2))
        d.ellipse([fx2, fy2, fx2 + 10, fy2 + 6], fill=(55, 225, 235))
        d.polygon([(fx2 + 10, fy2 + 3), (fx2 + 14, fy2), (fx2 + 14, fy2 + 6)], fill=(255, 230, 80))
        _pt(img, fx2 + 2, fy2 + 2, (10, 10, 15))
        # Rising bubbles
        for bx, phase in ((12, 0), (34, 5), (49, 10)):
            by = 52 - ((f * 3 + phase * 4) % 48)
            d.ellipse([bx, by, bx + 2, by + 2], outline=(165, 230, 255))
        frames.append(img)
    return frames


def make_scene_rain_window() -> List[Image.Image]:
    """30: Rainy Window — cozy city skyline bokeh behind falling rain streaks."""
    frames: List[Image.Image] = []
    n = 12
    buildings = [(2, 34, 14, (14, 18, 34)), (15, 26, 28, (18, 22, 40)), (30, 30, 44, (12, 16, 30)), (46, 22, 61, (16, 20, 38))]
    lights = [(6, 40, (255, 195, 85)), (10, 48, (255, 195, 85)), (19, 32, (90, 205, 255)), (23, 42, (255, 165, 75)),
              (34, 38, (255, 220, 110)), (39, 48, (90, 205, 255)), (50, 29, (255, 120, 150)), (55, 41, (255, 210, 95))]
    drops = [(6, 2), (16, 14), (24, 6), (34, 20), (42, 4), (52, 16), (11, 34), (28, 38), (47, 30), (58, 10)]
    for f in range(n):
        img = Image.new("RGB", (SIZE, SIZE), (6, 9, 22))
        d = ImageDraw.Draw(img)
        for x0, y0, x1, col in buildings:
            d.rectangle([x0, y0, x1, 63], fill=col)
        for lx, ly, col in lights:
            d.rectangle([lx, ly, lx + 2, ly + 2], fill=col)
        # Diagonal falling rain streaks
        for idx, (dx, dy) in enumerate(drops):
            ry = (dy + f * 5) % 64
            rx = (dx - f) % 64
            d.line([(rx, ry), (rx - 2, ry + 5)], fill=(115, 185, 245))
        # Window mullion frame
        d.rectangle([0, 0, 63, 63], outline=(38, 44, 62), width=3)
        d.line([32, 0, 32, 63], fill=(38, 44, 62), width=2)
        d.line([0, 32, 63, 32], fill=(38, 44, 62), width=2)
        frames.append(img)
    return frames


def make_scene_sleeping_cat() -> List[Image.Image]:
    """31: Sleeping Cat — curled orange pixel cat gently breathing + tiny z."""
    frames: List[Image.Image] = []
    n = 12
    for f in range(n):
        img = Image.new("RGB", (SIZE, SIZE), (6, 7, 16))
        d = ImageDraw.Draw(img)
        t = f / n
        breath = 1 if math.sin(2 * math.pi * t) > 0.2 else 0
        # Cozy rug cushion
        d.ellipse([6, 40, 57, 59], fill=(45, 34, 78), outline=(85, 68, 135))
        # Curled cat body
        d.ellipse([14, 30 - breath, 48, 52], fill=(235, 138, 52))
        # Tabby stripes
        d.line([25, 32 - breath, 27, 39 - breath], fill=(175, 85, 25), width=2)
        d.line([33, 31 - breath, 35, 38 - breath], fill=(175, 85, 25), width=2)
        # Cat head resting on paws
        d.ellipse([12, 24, 31, 41], fill=(245, 152, 62))
        # Pointed ears
        d.polygon([(14, 26), (16, 18), (21, 24)], fill=(245, 152, 62))
        d.polygon([(24, 24), (29, 18), (30, 26)], fill=(245, 152, 62))
        # Closed happy sleeping eyes & pink nose
        d.line([16, 33, 19, 33], fill=(40, 25, 15))
        d.line([23, 33, 26, 33], fill=(40, 25, 15))
        _pt(img, 21, 35, (255, 145, 165))
        # Curled tail with subtle tip twitch
        tw = 1 if f in (4, 5) else 0
        d.arc([32, 34, 53, 52], start=330, end=120, fill=(215, 118, 42), width=3)
        d.rectangle([48, 40 - tw, 52, 43 - tw], fill=(255, 215, 155))
        # Floating tiny z
        zy = 20 - (f % 12)
        if 4 <= zy <= 19:
            draw_pixel_text(img, "Z", zy, (175, 205, 255), center=False, x=38 + (f % 3))
        frames.append(img)
    return frames


def make_scene_ocean_waves() -> List[Image.Image]:
    """32: Ocean Sunset — glowing sun over rolling pixel waves."""
    frames: List[Image.Image] = []
    n = 16
    for f in range(n):
        img = Image.new("RGB", (SIZE, SIZE), (18, 12, 38))
        d = ImageDraw.Draw(img)
        t = f / n
        # Warm sunset sky bands
        d.rectangle([0, 14, 63, 24], fill=(75, 24, 68))
        d.rectangle([0, 24, 63, 33], fill=(165, 52, 72))
        # Glowing sun
        d.ellipse([20, 16, 44, 40], fill=(255, 175, 55))
        d.ellipse([23, 19, 41, 37], fill=(255, 220, 105))
        # Ocean base
        d.rectangle([0, 34, 63, 63], fill=(10, 42, 95))
        # Shimmering sun reflection
        for ry in range(36, 58, 4):
            w = max(3, 14 - (ry - 34) // 2)
            dx = int(round(math.sin(2 * math.pi * t + ry * 0.5) * 2))
            d.line([32 - w + dx, ry, 32 + w + dx, ry], fill=(255, 185, 80))
        # 3 rolling wave crests
        for idx, (wy, col) in enumerate([(40, (25, 115, 195)), (49, (20, 90, 170)), (57, (45, 165, 235))]):
            for x in range(SIZE):
                y = wy + int(round(math.sin(2 * math.pi * (x / 20.0 + t + idx * 0.3)) * 2))
                _pt(img, x, y, col)
                _pt(img, x, y + 1, (165, 235, 255) if (x + f) % 7 == 0 else col)
        frames.append(img)
    return frames


def make_scene_cloudy_sky() -> List[Image.Image]:
    """33: Cloudy Sky — soft drifting pixel clouds over twilight sky & sun."""
    frames: List[Image.Image] = []
    n = 16
    for f in range(n):
        img = Image.new("RGB", (SIZE, SIZE), (18, 52, 112))
        d = ImageDraw.Draw(img)
        d.rectangle([0, 38, 63, 63], fill=(28, 78, 145))
        # Peeking warm sun
        d.ellipse([42, 8, 56, 22], fill=(255, 220, 95))
        # Drifting clouds wrapping seamlessly across 64px
        for cx0, cy, scale, col in [
            (0, 20, 1.0, (235, 242, 255)),
            (32, 34, 0.9, (210, 225, 245)),
            (16, 48, 0.85, (190, 210, 238)),
        ]:
            cx = (cx0 + f * 4) % 64
            for off in (0, -64, 64):
                x = cx + off
                r = int(8 * scale)
                d.ellipse([x - r, cy - r // 2, x + r, cy + r // 2], fill=col)
                d.ellipse([x - r - 5, cy - r // 3 + 2, x + r + 5, cy + r // 2 + 2], fill=col)
                d.ellipse([x - r // 2, cy - r, x + r // 2 + 3, cy + r // 3], fill=col)
        frames.append(img)
    return frames


def make_scene_falling_blocks() -> List[Image.Image]:
    """34: Falling Blocks — colorful square & brick mosaic tiles dropping into place."""
    frames: List[Image.Image] = []
    n = 16
    # Pre-stacked colorful mosaic base rows (2x2 & 2x1 bricks, no tetromino shapes)
    base = [
        (0, 56, 15, 63, (50, 210, 245)),
        (16, 56, 31, 63, (255, 195, 50)),
        (32, 56, 47, 63, (175, 90, 255)),
        (48, 56, 63, 63, (60, 225, 125)),
        (0, 48, 15, 55, (255, 95, 115)),
        (48, 48, 63, 55, (75, 145, 255)),
        (16, 48, 23, 55, (60, 225, 125)),
        (40, 48, 47, 55, (255, 195, 50)),
    ]
    for f in range(n):
        img = Image.new("RGB", (SIZE, SIZE), (6, 7, 14))
        d = ImageDraw.Draw(img)
        # Subtle grid background
        for g in range(8, 64, 8):
            d.line([g, 0, g, 63], fill=(14, 17, 28))
            d.line([0, g, 63, g], fill=(14, 17, 28))
        # Stacked mosaic bricks
        for x0, y0, x1, y1, col in base:
            d.rectangle([x0 + 1, y0 + 1, x1 - 1, y1 - 1], fill=col)
            d.line([x0 + 1, y0 + 1, x1 - 1, y0 + 1], fill=(255, 255, 255))
        # Falling clean 16x8 rectangular mosaic tile landing in the center gap (x=24..39, y=48..55)
        fy = min(48, f * 4)
        col = (255, 145, 45)
        if f >= 13 and f % 2 == 1:
            col = (255, 245, 200)  # brief settle glow
        d.rectangle([25, fy + 1, 38, fy + 7], fill=col)
        d.line([25, fy + 1, 38, fy + 1], fill=(255, 235, 180))
        # Second small square tile trailing behind on the right
        fy2 = min(40, max(0, (f - 4) * 4))
        if f >= 4:
            d.rectangle([49, fy2 + 1, 55, fy2 + 7], fill=(95, 225, 255))
        frames.append(img)
    return frames


# ---------------------------------------------------------------------------
# Seasonal (35..40)
# ---------------------------------------------------------------------------

def make_seasonal_ramadan() -> List[Image.Image]:
    """35: Ramadan Lantern — swinging golden fanous under crescent + RAMADAN."""
    frames: List[Image.Image] = []
    n = 16
    for f in range(n):
        img = Image.new("RGB", (SIZE, SIZE), (5, 7, 22))
        d = ImageDraw.Draw(img)
        t = f / n
        swing = int(round(math.sin(2 * math.pi * t) * 4))
        cx = 32 + swing
        # Crescent moon top-left
        d.ellipse([5, 4, 17, 16], fill=(255, 220, 105))
        d.ellipse([9, 3, 20, 14], fill=(5, 7, 22))
        # Twinkling stars
        for sx, sy, ph in ((52, 8, 0), (56, 20, 5), (10, 26, 9)):
            br = 120 + int(135 * (0.5 + 0.5 * math.sin(2 * math.pi * (t + ph / 16.0))))
            _pt(img, sx, sy, (br, br, 160))
        # Hanging chain from top center
        d.line([(32, 0), (cx, 12)], fill=(215, 175, 65))
        # Fanous dome & top cap
        d.polygon([(cx, 11), (cx - 7, 19), (cx + 7, 19)], fill=(235, 180, 55))
        # Glowing lantern glass body
        glow = 205 + int(50 * math.sin(2 * math.pi * t * 2))
        d.polygon([(cx - 8, 19), (cx + 8, 19), (cx + 6, 39), (cx - 6, 39)], fill=(glow, int(glow * 0.72), 28), outline=(255, 225, 120))
        # Inner candle flame
        d.ellipse([cx - 2, 26, cx + 2, 34], fill=(255, 250, 200))
        # Base
        d.polygon([(cx - 7, 39), (cx + 7, 39), (cx + 9, 44), (cx - 9, 44)], fill=(215, 160, 45))
        draw_pixel_text(img, "RAMADAN", 52, (255, 215, 95))
        frames.append(img)
    return frames


def make_seasonal_eid() -> List[Image.Image]:
    """36: Eid Mubarak — golden crescent moon, sparkles + EID MUBARAK."""
    frames: List[Image.Image] = []
    n = 12
    sparkles = [(10, 10), (52, 12), (16, 28), (48, 28), (32, 5)]
    for f in range(n):
        img = Image.new("RGB", (SIZE, SIZE), (6, 12, 28))
        d = ImageDraw.Draw(img)
        t = f / n
        # Golden crescent centered at top
        d.ellipse([21, 7, 43, 29], fill=(255, 215, 85))
        d.ellipse([28, 5, 48, 25], fill=(6, 12, 28))
        # Twinkling 4-point sparkles
        for idx, (sx, sy) in enumerate(sparkles):
            on = (f + idx) % 3 != 0
            col = (255, 245, 185) if on else (110, 100, 60)
            _pt(img, sx, sy, col)
            if on:
                for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                    _pt(img, sx + dx, sy + dy, (200, 180, 95))
        pulse = 0.5 + 0.5 * math.sin(2 * math.pi * t)
        draw_pixel_text(img, "EID", 37, (255, int(205 + 45 * pulse), 95))
        draw_pixel_text(img, "MUBARAK", 49, (95, 235, 185))
        frames.append(img)
    return frames


def make_seasonal_new_year() -> List[Image.Image]:
    """37: New Year — bright, dense bursting pixel fireworks + NEW YEAR."""
    frames: List[Image.Image] = []
    n = 16
    bursts = [
        (17, 18, 0, (255, 85, 145), (255, 215, 120)),
        (47, 15, 5, (55, 225, 255), (195, 245, 255)),
        (32, 26, 10, (255, 215, 55), (255, 130, 50)),
    ]
    for f in range(n):
        img = Image.new("RGB", (SIZE, SIZE), (3, 4, 14))
        d = ImageDraw.Draw(img)
        for cx, cy, start, col, core_col in bursts:
            age = (f - start) % n
            if age < 12:
                # Bright ignition flash at center
                if age <= 2:
                    r0 = 2 + age
                    d.ellipse([cx - r0, cy - r0, cx + r0, cy + r0], fill=(255, 255, 240))
                rad = 2.0 + age * 1.45
                fade = max(0.35, 1.0 - age / 14.0)
                c = tuple(int(v * fade) for v in col)
                c_in = tuple(int(v * fade) for v in core_col)
                for k in range(12):
                    ang = 2 * math.pi * k / 12.0
                    # Outer 2x2 glowing spark head
                    px = int(round(cx + rad * math.cos(ang)))
                    py = int(round(cy + rad * math.sin(ang)))
                    d.rectangle([px, py, px + 1, py + 1], fill=(255, 255, 255) if age < 4 else c)
                    # Inner trail spark
                    if age >= 2:
                        px2 = int(round(cx + (rad - 3.5) * math.cos(ang)))
                        py2 = int(round(cy + (rad - 3.5) * math.sin(ang)))
                        _pt(img, px2, py2, c_in)
                    if age >= 4:
                        px3 = int(round(cx + (rad - 6.5) * math.cos(ang)))
                        py3 = int(round(cy + (rad - 6.5) * math.sin(ang)))
                        _pt(img, px3, py3, c)
        draw_pixel_text(img, "NEW YEAR", 51, (255, 230, 95))
        frames.append(img)
    return frames


def make_seasonal_birthday() -> List[Image.Image]:
    """38: Birthday — frosted pixel cake with 3 flickering candles + BDAY!."""
    frames: List[Image.Image] = []
    n = 12
    for f in range(n):
        img = Image.new("RGB", (SIZE, SIZE), (6, 4, 14))
        d = ImageDraw.Draw(img)
        # Plate
        d.line([11, 45, 52, 45], fill=(210, 215, 230), width=2)
        # Cake layers
        d.rounded_rectangle([15, 27, 48, 44], radius=3, fill=(235, 115, 155), outline=(255, 175, 200))
        d.rectangle([16, 35, 47, 38], fill=(255, 235, 150))
        # White icing top & drips
        d.rectangle([15, 26, 48, 30], fill=(255, 248, 240))
        for dx in (18, 25, 32, 39, 45):
            d.line([dx, 30, dx, 33], fill=(255, 248, 240))
        # 3 candles + flickering flames
        for idx, cx in enumerate((22, 32, 42)):
            d.rectangle([cx - 1, 17, cx + 1, 26], fill=(95, 205, 255))
            flick = -1 if (f + idx) % 3 == 0 else (1 if (f + idx) % 3 == 1 else 0)
            d.ellipse([cx - 2 + flick, 10, cx + 2 + flick, 16], fill=(255, 165, 35))
            _pt(img, cx + flick, 13, (255, 250, 180))
        draw_pixel_text(img, "BDAY!", 51, (255, 185, 220))
        frames.append(img)
    return frames


def make_seasonal_winter() -> List[Image.Image]:
    """39: Winter — cheerful snowman in gently falling snow."""
    frames: List[Image.Image] = []
    n = 16
    flakes = [(6, 4), (16, 18), (28, 6), (44, 12), (56, 22), (10, 38), (50, 36), (36, 28), (20, 48), (58, 6)]
    for f in range(n):
        img = Image.new("RGB", (SIZE, SIZE), (8, 16, 38))
        d = ImageDraw.Draw(img)
        # Snow ground
        d.rectangle([0, 52, 63, 63], fill=(215, 232, 250))
        # Snowman body & head
        d.ellipse([19, 32, 45, 55], fill=(240, 246, 255))
        d.ellipse([23, 17, 41, 34], fill=(245, 250, 255))
        # Hat
        d.rectangle([22, 16, 42, 18], fill=(35, 42, 58))
        d.rectangle([26, 8, 38, 16], fill=(35, 42, 58))
        d.rectangle([26, 14, 38, 15], fill=(235, 55, 70))
        # Scarf
        d.rectangle([24, 32, 40, 35], fill=(235, 55, 70))
        d.rectangle([36, 35, 39, 42], fill=(235, 55, 70))
        # Eyes, carrot nose & buttons
        _pt(img, 29, 23, (15, 20, 30))
        _pt(img, 35, 23, (15, 20, 30))
        d.line([32, 26, 37, 27], fill=(255, 135, 35), width=2)
        _pt(img, 32, 40, (25, 30, 45))
        _pt(img, 32, 45, (25, 30, 45))
        # Falling snowflakes
        for idx, (fx, fy) in enumerate(flakes):
            sy = (fy + f * 4) % 54
            sx = (fx + int(round(math.sin((f + idx) * 0.7)))) % 64
            _pt(img, sx, sy, (235, 245, 255))
        frames.append(img)
    return frames


def make_seasonal_halloween() -> List[Image.Image]:
    """40: Halloween — glowing carved pumpkin with flickering fire & tiny bats."""
    frames: List[Image.Image] = []
    n = 12
    for f in range(n):
        img = Image.new("RGB", (SIZE, SIZE), (9, 5, 18))
        d = ImageDraw.Draw(img)
        # Pale moon
        d.ellipse([44, 5, 57, 18], fill=(235, 228, 185))
        # Flapping bats
        wing = -2 if f % 2 == 0 else 2
        for bx, by in ((12 + (f % 4), 12), (48 - (f % 4), 20)):
            d.line([(bx - 4, by + wing), (bx, by), (bx + 4, by + wing)], fill=(155, 125, 215))
        # Pumpkin stem & body
        d.rectangle([30, 18, 34, 24], fill=(65, 155, 55))
        d.ellipse([12, 23, 52, 56], fill=(235, 115, 22), outline=(175, 72, 12))
        d.ellipse([21, 23, 43, 56], outline=(190, 82, 16))
        # Flickering carved face
        glow = (255, 235, 95) if f % 3 != 0 else (255, 175, 45)
        d.polygon([(20, 36), (25, 30), (28, 36)], fill=glow)
        d.polygon([(36, 36), (39, 30), (44, 36)], fill=glow)
        d.polygon([(30, 40), (32, 37), (34, 40)], fill=glow)
        d.arc([20, 39, 44, 51], start=10, end=170, fill=glow, width=3)
        frames.append(img)
    return frames


GENERATORS = [
    ("19_mode_lunch.gif", make_mode_lunch, 110),
    ("20_mode_workout.gif", make_mode_workout, 95),
    ("21_mode_reading.gif", make_mode_reading, 130),
    ("22_mode_sleep.gif", make_mode_sleep, 120),
    ("23_mode_meeting_soon.gif", make_mode_meeting_soon, 100),
    ("24_mode_recording.gif", make_mode_recording, 110),
    ("25_mode_out_of_office.gif", make_mode_out_of_office, 120),
    ("26_mode_on_my_way.gif", make_mode_on_my_way, 95),
    ("27_mode_ship_it.gif", make_mode_ship_it, 95),
    ("28_mode_build_failed.gif", make_mode_build_failed, 100),
    ("29_aquarium.gif", make_scene_aquarium, 110),
    ("30_rain_window.gif", make_scene_rain_window, 100),
    ("31_sleeping_cat.gif", make_scene_sleeping_cat, 140),
    ("32_ocean_waves.gif", make_scene_ocean_waves, 110),
    ("33_cloudy_sky.gif", make_scene_cloudy_sky, 130),
    ("34_falling_blocks.gif", make_scene_falling_blocks, 115),
    ("35_ramadan_lantern.gif", make_seasonal_ramadan, 110),
    ("36_eid_mubarak.gif", make_seasonal_eid, 120),
    ("37_new_year.gif", make_seasonal_new_year, 100),
    ("38_birthday.gif", make_seasonal_birthday, 110),
    ("39_winter.gif", make_seasonal_winter, 115),
    ("40_halloween.gif", make_seasonal_halloween, 115),
]


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for filename, fn, duration_ms in GENERATORS:
        frames = fn()
        assert len(frames) <= 16, f"{filename} has {len(frames)} frames (>16)"
        out_path = OUT_DIR / filename
        save_gif(frames, out_path, duration_ms=duration_ms)
        size = out_path.stat().st_size
        print(f"Generated {filename:28s}  {len(frames):2d}f  {size:6d} B ({size / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
