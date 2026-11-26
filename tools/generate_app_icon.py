#!/usr/bin/env python3
"""
Generate the LOY Studio app icon across all platforms (zero external assets):
  - assets/app_icon.icns   (macOS .app bundle)
  - assets/app_icon.ico    (Windows .exe)
  - app/static/icon-512.png (PWA 512x512)
  - app/static/icon-192.png (PWA / Apple Touch Icon 192x192)
  - app/static/favicon.png  (Browser tab favicon 64x64)
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Tuple

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = ROOT / "assets"
STATIC_DIR = ROOT / "app" / "static"


def _lerp_rgb(c1: Tuple[int, int, int], c2: Tuple[int, int, int], t: float) -> Tuple[int, int, int]:
    return (
        int(c1[0] + (c2[0] - c1[0]) * t),
        int(c1[1] + (c2[1] - c1[1]) * t),
        int(c1[2] + (c2[2] - c1[2]) * t),
    )


def render_master_icon(size: int = 1024) -> Image.Image:
    """Render a crisp 1024x1024 macOS-style OLED LED-Matrix app icon."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    # macOS Big Sur / Sequoia icon safe margin (~10% inset)
    pad = int(size * 0.095)
    radius = int(size * 0.215)

    # 1. Soft drop shadow behind squircle
    shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle([pad, pad + 18, size - pad, size - pad + 18], radius=radius, fill=(0, 0, 0, 150))
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=26))
    img = Image.alpha_composite(img, shadow)

    # 2. Squircle outer bezel with subtle vertical metallic-slate gradient
    bezel = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bezel)
    top_c, bot_c = (28, 34, 52), (10, 12, 20)
    grad = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for y in range(pad, size - pad):
        t = (y - pad) / max(1, (size - 2 * pad))
        r, g, b = _lerp_rgb(top_c, bot_c, t)
        gd.line([(pad, y), (size - pad, y)], fill=(r, g, b, 255))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([pad, pad, size - pad, size - pad], radius=radius, fill=255)
    bezel.paste(grad, (0, 0), mask)

    # Crisp luminous rim highlight
    bd.rounded_rectangle([pad, pad, size - pad, size - pad], radius=radius, outline=(90, 140, 235, 95), width=6)
    img = Image.alpha_composite(img, bezel)

    # 3. Recessed OLED matrix screen frame
    inner_pad = int(size * 0.165)
    inner_r = int(size * 0.14)
    screen = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    scd = ImageDraw.Draw(screen)
    scd.rounded_rectangle(
        [inner_pad, inner_pad, size - inner_pad, size - inner_pad],
        radius=inner_r,
        fill=(6, 8, 14, 255),
        outline=(45, 58, 86, 170),
        width=4,
    )
    img = Image.alpha_composite(img, screen)

    # 4. 6x6 RGB LED Matrix Pattern:
    #    - Bold glowing cyan->blue->violet "L" on left/bottom
    #    - Vibrant neon magenta/amber 2x2 pixel spark in top-right
    #    - Dim recessed gunmetal LED sockets for unlit cells
    grid_n = 6
    grid_pad = int(size * 0.225)
    grid_span = size - 2 * grid_pad
    step = grid_span / grid_n
    led_r = int(step * 0.34)

    lit_map = {
        # Vertical stem of "L" (cols 0-1, rows 0-5)
        (0, 0): (6, 224, 245),
        (1, 0): (20, 200, 250),
        (0, 1): (14, 195, 252),
        (1, 1): (35, 175, 255),
        (0, 2): (35, 165, 255),
        (1, 2): (59, 145, 255),
        (0, 3): (65, 130, 255),
        (1, 3): (92, 118, 255),
        (0, 4): (102, 105, 255),
        (1, 4): (125, 98, 255),
        (0, 5): (138, 92, 246),
        (1, 5): (155, 88, 246),
        # Bottom bar of "L" (cols 2-5, rows 4-5)
        (2, 4): (148, 92, 248),
        (3, 4): (176, 84, 242),
        (4, 4): (205, 76, 228),
        (5, 4): (232, 68, 200),
        (2, 5): (168, 84, 245),
        (3, 5): (198, 76, 232),
        (4, 5): (228, 68, 202),
        (5, 5): (248, 70, 168),
        # Glowing pixel spark in top-right (cols 3-5, rows 0-2)
        (4, 0): (255, 215, 75),
        (3, 1): (255, 145, 65),
        (4, 1): (255, 245, 165),
        (5, 1): (255, 110, 140),
        (4, 2): (255, 80, 155),
    }

    bloom_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    bl_d = ImageDraw.Draw(bloom_layer)
    led_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ld = ImageDraw.Draw(led_layer)

    for row in range(grid_n):
        for col in range(grid_n):
            cx = int(round(grid_pad + (col + 0.5) * step))
            cy = int(round(grid_pad + (row + 0.5) * step))
            rgb = lit_map.get((col, row))
            if rgb is None:
                # Subtle unlit hardware LED diode socket
                ld.ellipse([cx - led_r, cy - led_r, cx + led_r, cy + led_r], fill=(22, 27, 42, 255), outline=(34, 42, 64, 255), width=2)
            else:
                r, g, b = rgb
                # Wide soft LED bloom
                br = int(led_r * 1.85)
                bl_d.ellipse([cx - br, cy - br, cx + br, cy + br], fill=(r, g, b, 155))
                # Solid glowing LED body
                ld.ellipse([cx - led_r, cy - led_r, cx + led_r, cy + led_r], fill=(r, g, b, 255))
                # Bright hot-center core
                cr = int(led_r * 0.55)
                core = _lerp_rgb(rgb, (255, 255, 255), 0.58)
                ld.ellipse([cx - cr, cy - cr, cx + cr, cy + cr], fill=(*core, 245))
                # Crisp specular dot
                sr = max(4, int(led_r * 0.22))
                ld.ellipse([cx - sr - 4, cy - sr - 5, cx + sr - 4, cy + sr - 5], fill=(255, 255, 255, 215))

    bloom_layer = bloom_layer.filter(ImageFilter.GaussianBlur(radius=30))
    img = Image.alpha_composite(img, bloom_layer)
    img = Image.alpha_composite(img, led_layer)
    return img


def save_icns(master: Image.Image, out_icns: Path) -> None:
    out_icns.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin" and shutil.which("iconutil"):
        tmp = Path(tempfile.mkdtemp(prefix="loy_iconset_"))
        try:
            iconset = tmp / "app_icon.iconset"
            iconset.mkdir()
            specs = [
                ("icon_16x16.png", 16),
                ("icon_16x16@2x.png", 32),
                ("icon_32x32.png", 32),
                ("icon_32x32@2x.png", 64),
                ("icon_128x128.png", 128),
                ("icon_128x128@2x.png", 256),
                ("icon_256x256.png", 256),
                ("icon_256x256@2x.png", 512),
                ("icon_512x512.png", 512),
                ("icon_512x512@2x.png", 1024),
            ]
            for name, px in specs:
                master.resize((px, px), Image.Resampling.LANCZOS).save(iconset / name, format="PNG")
            subprocess.check_call(["iconutil", "-c", "icns", str(iconset), "-o", str(out_icns)])
            return
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    master.save(out_icns, format="ICNS")


def main() -> None:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    STATIC_DIR.mkdir(parents=True, exist_ok=True)

    master = render_master_icon(1024)
    icns_path = ASSETS_DIR / "app_icon.icns"
    ico_path = ASSETS_DIR / "app_icon.ico"
    png512_path = STATIC_DIR / "icon-512.png"
    png192_path = STATIC_DIR / "icon-192.png"
    fav_path = STATIC_DIR / "favicon.png"

    save_icns(master, icns_path)
    master.save(ico_path, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    master.resize((512, 512), Image.Resampling.LANCZOS).save(png512_path, format="PNG", optimize=True)
    master.resize((192, 192), Image.Resampling.LANCZOS).save(png192_path, format="PNG", optimize=True)
    master.resize((64, 64), Image.Resampling.LANCZOS).save(fav_path, format="PNG", optimize=True)

    for p in (icns_path, ico_path, png512_path, png192_path, fav_path):
        print(f"Generated {p.relative_to(ROOT)} ({p.stat().st_size:,} B)")


if __name__ == "__main__":
    main()
