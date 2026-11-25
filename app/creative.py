"""
LOY Studio 2.0 Creative Engine — 100% local processing.

Provides:
  - 12 procedural animation generators (Fire, Matrix, Starfield, Rain, Snow,
    Fireworks, Lava Lamp, Plasma, Rainbow, Bouncing Shapes, Particles, Retro Space)
  - Pixel Avatar Creator
  - Image Studio (crop, aspect, brightness/contrast/saturation, quantization, dithering)
  - Video Studio (local FFmpeg -> 64x64 optimized looping GIF)
  - Color Palette Extractor
  - Offline Live Widgets (Pomodoro, macOS CPU/RAM, macOS Now Playing, Focus/Meeting, Info Card)
"""

from __future__ import annotations

import colorsys
import io
import math
import os
import random
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageEnhance

RGB = Tuple[int, int, int]


def parse_hex_rgb(hex_color: str, default: RGB = (5, 217, 232)) -> RGB:
    c = (hex_color or "").strip().lstrip("#")
    if len(c) != 6:
        return default
    try:
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    except ValueError:
        return default


def frames_to_gif_bytes(frames: List[Image.Image], delay_ms: int = 90) -> bytes:
    if not frames:
        raise ValueError("No frames to encode")
    pal_frames = [f.convert("P", palette=Image.Palette.ADAPTIVE) for f in frames]
    buf = io.BytesIO()
    pal_frames[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=pal_frames[1:],
        duration=max(30, int(delay_ms)),
        loop=0,
        disposal=2,
    )
    return buf.getvalue()


# ---------------------------------------------------------------------------
# 12 Procedural Animation Generators
# ---------------------------------------------------------------------------

PROCEDURAL_EFFECTS = [
    {"id": "fire", "name": "Campfire Flame", "category": "ambient"},
    {"id": "matrix", "name": "Matrix Rain", "category": "retro"},
    {"id": "starfield", "name": "Starfield Warp", "category": "space"},
    {"id": "rain", "name": "Neon Rain", "category": "ambient"},
    {"id": "snow", "name": "Winter Snow", "category": "seasonal"},
    {"id": "fireworks", "name": "Fireworks", "category": "seasonal"},
    {"id": "lava", "name": "Lava Lamp", "category": "ambient"},
    {"id": "plasma", "name": "Plasma Waves", "category": "abstract"},
    {"id": "rainbow", "name": "Rainbow Gradient", "category": "abstract"},
    {"id": "bouncing", "name": "Bouncing Shapes", "category": "retro"},
    {"id": "particles", "name": "Pixel Particles", "category": "ambient"},
    {"id": "space", "name": "Retro Space", "category": "space"},
]


def _lerp_rgb(a: RGB, b: RGB, t: float) -> RGB:
    t = max(0.0, min(1.0, t))
    return (
        int(a[0] + (b[0] - a[0]) * t),
        int(a[1] + (b[1] - a[1]) * t),
        int(a[2] + (b[2] - a[2]) * t),
    )


def generate_procedural(
    effect: str,
    width: int = 64,
    height: int = 64,
    *,
    color1: RGB = (5, 217, 232),
    color2: RGB = (255, 42, 109),
    density: float = 0.6,
    n_frames: int = 16,
    seed: int = 42,
) -> List[Image.Image]:
    rng = random.Random(seed)
    n_frames = max(4, min(32, int(n_frames)))
    density = max(0.1, min(1.0, float(density)))
    frames: List[Image.Image] = []

    if effect == "fire":
        heat = [[0.0] * width for _ in range(height)]
        for step in range(n_frames + 8):
            for x in range(width):
                heat[height - 1][x] = rng.uniform(160, 255) if rng.random() < (0.4 + 0.6 * density) else 80.0
            for y in range(height - 2, -1, -1):
                for x in range(width):
                    avg = (heat[y + 1][(x - 1) % width] + heat[y + 1][x] + heat[y + 1][(x + 1) % width] + heat[min(height - 1, y + 2)][x]) / 4.0
                    heat[y][x] = max(0.0, avg - rng.uniform(4, 15))
            if step >= 8:
                img = Image.new("RGB", (width, height), (0, 0, 0))
                px = img.load()
                for y in range(height):
                    for x in range(width):
                        v = min(1.0, heat[y][x] / 255.0)
                        px[x, y] = _lerp_rgb((0, 0, 0), color2, v * 1.6) if v < 0.6 else _lerp_rgb(color2, (255, 240, 180), (v - 0.6) / 0.4)
                frames.append(img)

    elif effect == "matrix":
        stride = max(2, int(4 - 2 * density))
        cols = [rng.randint(0, height - 1) for _ in range(width)]
        lens = [rng.randint(5, max(8, height // 2)) for _ in range(width)]
        for f in range(n_frames):
            img = Image.new("RGB", (width, height), (0, 0, 0))
            px = img.load()
            for x in range(0, width, stride):
                head = (cols[x] + f * max(1, height // n_frames)) % height
                for k in range(lens[x]):
                    y = (head - k) % height
                    px[x, y] = (220, 255, 230) if k == 0 else _lerp_rgb(color1, (0, 12, 6), k / lens[x])
            frames.append(img)

    elif effect == "starfield":
        count = int(20 + 60 * density)
        stars = [(rng.uniform(0, 2 * math.pi), rng.uniform(0.05, 1.0)) for _ in range(count)]
        cx, cy = width / 2.0, height / 2.0
        max_r = max(width, height) * 0.65
        for f in range(n_frames):
            img = Image.new("RGB", (width, height), (2, 4, 12))
            px = img.load()
            for ang, base in stars:
                d = (base + f / n_frames) % 1.0
                r = (d ** 1.6) * max_r
                x, y = int(cx + r * math.cos(ang)), int(cy + r * math.sin(ang))
                if 0 <= x < width and 0 <= y < height:
                    px[x, y] = _lerp_rgb((20, 30, 60), color1, d)
            frames.append(img)

    elif effect == "rain":
        count = int(16 + 48 * density)
        drops = [(rng.randint(0, width - 1), rng.randint(0, height - 1), rng.randint(3, 6)) for _ in range(count)]
        for f in range(n_frames):
            img = Image.new("RGB", (width, height), (3, 6, 16))
            d = ImageDraw.Draw(img)
            for x, y0, ln in drops:
                y = (y0 + f * 4) % height
                d.line([(x, max(0, y - ln)), (x, y)], fill=color1)
                if y >= height - 3:
                    d.point([(max(0, x - 1), height - 1), (min(width - 1, x + 1), height - 1)], fill=color2)
            frames.append(img)

    elif effect == "snow":
        count = int(18 + 50 * density)
        flakes = [(rng.randint(0, width - 1), rng.randint(0, height - 1), rng.uniform(0, 6.28)) for _ in range(count)]
        for f in range(n_frames):
            img = Image.new("RGB", (width, height), (4, 8, 20))
            px = img.load()
            for x0, y0, ph in flakes:
                y = (y0 + f * 2) % height
                x = int(x0 + 2.0 * math.sin(ph + f * 0.5)) % width
                px[x, y] = (235, 245, 255)
            frames.append(img)

    elif effect == "fireworks":
        bursts = [(rng.randint(width // 4, 3 * width // 4), rng.randint(height // 4, 3 * height // 4), f0) for f0 in (0, n_frames // 2)]
        sparks = [rng.uniform(0, 2 * math.pi) for _ in range(int(12 + 24 * density))]
        for f in range(n_frames):
            img = Image.new("RGB", (width, height), (0, 0, 6))
            px = img.load()
            for bx, by, f0 in bursts:
                age = (f - f0) % n_frames
                t = age / max(1, n_frames - 1)
                rad = t * (min(width, height) * 0.42)
                col = _lerp_rgb(color2, color1, t) if t < 0.7 else _lerp_rgb(color1, (0, 0, 6), (t - 0.7) / 0.3)
                for ang in sparks:
                    x = int(bx + rad * math.cos(ang))
                    y = int(by + rad * math.sin(ang) + (t * t * 6))
                    if 0 <= x < width and 0 <= y < height:
                        px[x, y] = col
            frames.append(img)

    elif effect == "lava":
        blobs = [(rng.uniform(0.2, 0.8) * width, rng.uniform(0.2, 0.8) * height, rng.uniform(0, 6.28)) for _ in range(4)]
        for f in range(n_frames):
            img = Image.new("RGB", (width, height), (8, 4, 16))
            px = img.load()
            ang = 2 * math.pi * (f / n_frames)
            pts = [(bx + 8 * math.sin(ang + ph), by + 10 * math.cos(ang + ph * 1.3)) for bx, by, ph in blobs]
            for y in range(height):
                for x in range(width):
                    s = sum(90.0 / (12.0 + (x - bx) ** 2 + (y - by) ** 2) for bx, by in pts)
                    if s > 1.2:
                        px[x, y] = _lerp_rgb(color2, color1, min(1.0, (s - 1.2) * 0.8))
            frames.append(img)

    elif effect == "plasma":
        for f in range(n_frames):
            img = Image.new("RGB", (width, height))
            px = img.load()
            ph = 2 * math.pi * (f / n_frames)
            for y in range(height):
                for x in range(width):
                    v = (
                        math.sin(x * 0.14 + ph)
                        + math.sin(y * 0.14 - ph)
                        + math.sin((x + y) * 0.1 + ph)
                    ) / 3.0
                    px[x, y] = _lerp_rgb(color1, color2, (v + 1.0) * 0.5)
            frames.append(img)

    elif effect == "rainbow":
        for f in range(n_frames):
            img = Image.new("RGB", (width, height))
            px = img.load()
            phase = f / n_frames
            for y in range(height):
                for x in range(width):
                    hue = ((x + y) / max(1, (width + height) * 0.75) - phase) % 1.0
                    r, g, b = colorsys.hsv_to_rgb(hue, 0.95, 1.0)
                    px[x, y] = (int(r * 255), int(g * 255), int(b * 255))
            frames.append(img)

    elif effect == "bouncing":
        n_shapes = int(3 + 5 * density)
        objs = [(rng.uniform(6, width - 6), rng.uniform(6, height - 6), rng.uniform(0, 6.28), rng.randint(3, 6)) for _ in range(n_shapes)]
        for f in range(n_frames):
            img = Image.new("RGB", (width, height), (4, 6, 14))
            d = ImageDraw.Draw(img)
            t = 2 * math.pi * (f / n_frames)
            for idx, (ox, oy, ph, r) in enumerate(objs):
                cx = int(width / 2 + (width / 2 - r - 2) * math.sin(t + ph))
                cy = int(height / 2 + (height / 2 - r - 2) * math.cos(t * 2 + ph))
                col = color1 if idx % 2 == 0 else color2
                if idx % 2 == 0:
                    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col)
                else:
                    d.rectangle([cx - r, cy - r, cx + r, cy + r], outline=col, width=2)
            frames.append(img)

    elif effect == "particles":
        count = int(14 + 36 * density)
        pts = [(rng.uniform(0, 6.28), rng.uniform(4, min(width, height) * 0.44), rng.choice([color1, color2])) for _ in range(count)]
        cx, cy = width / 2, height / 2
        for f in range(n_frames):
            img = Image.new("RGB", (width, height), (2, 4, 10))
            d = ImageDraw.Draw(img)
            ang_off = 2 * math.pi * (f / n_frames)
            for a, r, col in pts:
                x = int(cx + r * math.cos(a + ang_off))
                y = int(cy + r * math.sin(a + ang_off))
                d.rectangle([x, y, min(width - 1, x + 1), min(height - 1, y + 1)], fill=col)
            frames.append(img)

    else:  # "space"
        stars = [(rng.randint(0, width - 1), rng.randint(0, height - 1), rng.randint(0, n_frames - 1)) for _ in range(int(24 + 40 * density))]
        for f in range(n_frames):
            img = Image.new("RGB", (width, height), (3, 3, 14))
            d = ImageDraw.Draw(img)
            for sx, sy, ph in stars:
                b = 120 + int(135 * (0.5 + 0.5 * math.sin(2 * math.pi * ((f + ph) / n_frames))))
                img.putpixel((sx, sy), (b, b, 255))
            # Orbiting planet + ring
            cx, cy = width // 2, height // 2
            pr = max(6, min(width, height) // 5)
            d.ellipse([cx - pr, cy - pr, cx + pr, cy + pr], fill=color1, outline=color2)
            d.arc([cx - pr - 6, cy - 3, cx + pr + 6, cy + 5], start=0, end=180, fill=color2, width=2)
            # Small moon
            ang = 2 * math.pi * (f / n_frames)
            mx = int(cx + (pr + 10) * math.cos(ang))
            my = int(cy + (pr + 6) * math.sin(ang))
            d.ellipse([mx - 2, my - 2, mx + 2, my + 2], fill=(240, 240, 255))
            frames.append(img)

    return frames


# ---------------------------------------------------------------------------
# Pixel Avatar Creator
# ---------------------------------------------------------------------------

def generate_avatar(
    seed: int = 1,
    *,
    width: int = 64,
    height: int = 64,
    primary: RGB = (5, 217, 232),
    accent: RGB = (255, 42, 109),
    bg: RGB = (10, 14, 24),
) -> Image.Image:
    """Generate a symmetric 8-bit pixel character avatar scaled crisply to (width, height)."""
    rng = random.Random(seed)
    grid = 12
    small = Image.new("RGB", (grid, grid), bg)
    px = small.load()
    for y in range(2, grid - 1):
        for x in range(2, grid // 2):
            if rng.random() > 0.45:
                col = primary if rng.random() > 0.25 else accent
                px[x, y] = col
                px[grid - 1 - x, y] = col
    # Crisp eyes
    px[4, 4] = (255, 255, 255)
    px[grid - 5, 4] = (255, 255, 255)
    px[4, 5] = (15, 20, 30)
    px[grid - 5, 5] = (15, 20, 30)
    return small.resize((width, height), Image.Resampling.NEAREST)


# ---------------------------------------------------------------------------
# Image Studio & Color Palette Extraction
# ---------------------------------------------------------------------------

def process_image_studio(
    raw_bytes: bytes,
    width: int = 64,
    height: int = 64,
    *,
    fit: str = "cover",
    brightness: float = 1.0,
    contrast: float = 1.0,
    saturation: float = 1.0,
    colors: int = 64,
    dither: bool = False,
    bg: RGB = (0, 0, 0),
) -> Image.Image:
    with Image.open(io.BytesIO(raw_bytes)) as src:
        img = src.convert("RGB")
        sw, sh = img.size
        if fit == "cover":
            tr = width / height
            sr = sw / max(1, sh)
            if sr > tr:
                nw = int(sh * tr)
                img = img.crop(((sw - nw) // 2, 0, (sw - nw) // 2 + nw, sh))
            else:
                nh = int(sw / tr)
                img = img.crop((0, (sh - nh) // 2, sw, (sh - nh) // 2 + nh))
            img = img.resize((width, height), Image.Resampling.LANCZOS)
        elif fit == "contain":
            canvas = Image.new("RGB", (width, height), bg)
            copy = img.copy()
            copy.thumbnail((width, height), Image.Resampling.LANCZOS)
            canvas.paste(copy, ((width - copy.width) // 2, (height - copy.height) // 2))
            img = canvas
        else:
            img = img.resize((width, height), Image.Resampling.LANCZOS)

        if brightness != 1.0:
            img = ImageEnhance.Brightness(img).enhance(brightness)
        if contrast != 1.0:
            img = ImageEnhance.Contrast(img).enhance(contrast)
        if saturation != 1.0:
            img = ImageEnhance.Color(img).enhance(saturation)

        if 2 <= colors <= 256:
            d_mode = Image.Dither.FLOYDSTEINBERG if dither else Image.Dither.NONE
            img = img.quantize(colors=colors, dither=d_mode).convert("RGB")
        return img


def extract_palette(raw_bytes: bytes, n_colors: int = 12) -> List[str]:
    n_colors = max(2, min(24, int(n_colors)))
    with Image.open(io.BytesIO(raw_bytes)) as src:
        small = src.convert("RGB").resize((64, 64), Image.Resampling.BILINEAR)
        q = small.quantize(colors=n_colors, method=Image.Quantize.MEDIANCUT)
        pal = q.getpalette()[: n_colors * 3]
        out: List[str] = []
        for i in range(0, len(pal), 3):
            out.append(f"#{pal[i]:02x}{pal[i+1]:02x}{pal[i+2]:02x}")
        return out


# ---------------------------------------------------------------------------
# Video Studio (Local FFmpeg -> 64x64 Optimized GIF)
# ---------------------------------------------------------------------------

def convert_video_to_gif(
    raw_bytes: bytes,
    filename: str,
    width: int = 64,
    height: int = 64,
    *,
    start_s: float = 0.0,
    duration_s: float = 3.0,
    fps: int = 10,
) -> bytes:
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        raise RuntimeError("Local ffmpeg is not installed. Install with: brew install ffmpeg")

    ext = Path(filename).suffix.lower() or ".mp4"
    duration_s = max(0.5, min(10.0, float(duration_s)))
    fps = max(2, min(20, int(fps)))

    with tempfile.TemporaryDirectory(prefix="loy_vid_") as td:
        in_path = Path(td) / f"input{ext}"
        out_path = Path(td) / "output.gif"
        in_path.write_bytes(raw_bytes)

        vf = (
            f"fps={fps},"
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},"
            f"split[s0][s1];[s0]palettegen=max_colors=64:stats_mode=diff[p];"
            f"[s1][p]paletteuse=dither=bayer:bayer_scale=3"
        )
        cmd = [
            ffmpeg_bin, "-y",
            "-ss", str(max(0.0, float(start_s))),
            "-t", str(duration_s),
            "-i", str(in_path),
            "-vf", vf,
            "-loop", "0",
            str(out_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if proc.returncode != 0 or not out_path.exists():
            raise RuntimeError(f"ffmpeg conversion failed: {proc.stderr[-300:]}")
        return out_path.read_bytes()


# ---------------------------------------------------------------------------
# Offline Local Live Widgets (macOS System Stats, Now Playing, Pomodoro, Status)
# ---------------------------------------------------------------------------

def get_local_system_stats() -> Dict[str, Any]:
    load1, load5, load15 = os.getloadavg()
    cpus = os.cpu_count() or 1
    cpu_pct = min(100, int(round((load1 / cpus) * 100)))
    ram_pct = 0
    try:
        out = subprocess.check_output(["vm_stat"], text=True, timeout=2)
        stats: Dict[str, int] = {}
        for line in out.splitlines()[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                num = "".join(c for c in v if c.isdigit())
                if num:
                    stats[k.strip()] = int(num)
        active = stats.get("Pages active", 0) + stats.get("Pages wired down", 0)
        free = stats.get("Pages free", 0) + stats.get("Pages inactive", 0) + stats.get("Pages speculative", 0)
        total = active + free
        if total > 0:
            ram_pct = min(100, int(round((active / total) * 100)))
    except Exception:
        pass
    return {"cpu_pct": cpu_pct, "ram_pct": ram_pct, "load1": round(load1, 2), "cpus": cpus}


def get_macos_now_playing() -> Dict[str, str]:
    """Query local macOS Spotify or Apple Music via osascript (100% offline)."""
    for app_name in ("Spotify", "Music"):
        script = (
            f'if application "{app_name}" is running then '
            f'tell application "{app_name}" to if player state is playing then '
            f'return (name of current track) & " - " & (artist of current track)'
        )
        try:
            out = subprocess.check_output(["osascript", "-e", script], text=True, stderr=subprocess.DEVNULL, timeout=1.5).strip()
            if out:
                return {"app": app_name, "track": out}
        except Exception:
            continue
    return {"app": "None", "track": "Nothing Playing"}
