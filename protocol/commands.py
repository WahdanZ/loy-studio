"""
Command builders for the YS / YSTP01 BLE Pixel LED Display Protocol.

Every TLV tag, byte layout, and default value is directly traced to
module a829 (LOY PLAY v1.7.15).
"""

from __future__ import annotations

import datetime
import math
import struct
from typing import Iterable, List, Optional, Sequence, Tuple

from .constants import (
    CMD_TYPE_GET,
    CMD_TYPE_SET,
    DEFAULT_DATA_CHUNK_SIZE,
    FLAG_DEFAULT,
    FLAG_NO_ACK,
    TLV,
)
from .framing import build_frame, encode_tlv


def _rgb_to_le24(r: int, g: int, b: int) -> bytes:
    """Encode (R, G, B) as 3 bytes matching module 7835 rbg_to_color() written LE."""
    return bytes([r & 0xFF, g & 0xFF, b & 0xFF])


# ---------------------------------------------------------------------------
# GET / Query commands (cmd_type = 3)
# ---------------------------------------------------------------------------

def cmd_get_dev_info_all(sno: int = 1, dev_key: str = "") -> bytes:
    """
    Query dev_info (10), param_dev (27), power (4), and light (6) in one frame.
    This is the exact initialization command sent after BLE notification enable
    (module ac01 -> a829 Ce('dev_info_all')).
    """
    key_bytes = dev_key.encode("ascii") if dev_key else b""
    tlv = (
        encode_tlv(TLV.DEV_INFO, key_bytes)
        + encode_tlv(TLV.PARAM_DEV, b"")
        + encode_tlv(TLV.POWER, b"")
        + encode_tlv(TLV.LIGHT, b"")
    )
    return build_frame(tlv, sno=sno, cmd_type=CMD_TYPE_GET)


def cmd_get_dev_info(sno: int = 1, dev_key: str = "") -> bytes:
    tlv = encode_tlv(TLV.DEV_INFO, dev_key.encode("ascii"))
    return build_frame(tlv, sno=sno, cmd_type=CMD_TYPE_GET)


def cmd_get_param_dev(sno: int = 1) -> bytes:
    return build_frame(encode_tlv(TLV.PARAM_DEV, b""), sno=sno, cmd_type=CMD_TYPE_GET)


def cmd_get_power(sno: int = 1) -> bytes:
    return build_frame(encode_tlv(TLV.POWER, b""), sno=sno, cmd_type=CMD_TYPE_GET)


def cmd_get_light(sno: int = 1) -> bytes:
    return build_frame(encode_tlv(TLV.LIGHT, b""), sno=sno, cmd_type=CMD_TYPE_GET)


def cmd_get_rotate(sno: int = 1) -> bytes:
    return build_frame(encode_tlv(TLV.ROTATE, b""), sno=sno, cmd_type=CMD_TYPE_GET)


def cmd_get_mirror(sno: int = 1) -> bytes:
    return build_frame(encode_tlv(TLV.MIRROR, b""), sno=sno, cmd_type=CMD_TYPE_GET)


# ---------------------------------------------------------------------------
# Basic SET commands (cmd_type = 2)
# ---------------------------------------------------------------------------

def cmd_set_power(on: bool, sno: int = 1) -> bytes:
    """Power display on (True -> 1) or off (False -> 0). TLV 0x04."""
    tlv = encode_tlv(TLV.POWER, bytes([0, 1 if on else 0]))
    return build_frame(tlv, sno=sno, cmd_type=CMD_TYPE_SET)


def cmd_set_brightness(level: int, sno: int = 1) -> bytes:
    """
    Set fixed brightness level (1..15). TLV 0x06.
    Note: module a829 line 1027 clamps 0 -> 15; valid range is 1..15.
    """
    clamped = max(1, min(15, int(level)))
    tlv = encode_tlv(TLV.LIGHT, bytes([0, clamped]))
    return build_frame(tlv, sno=sno, cmd_type=CMD_TYPE_SET)


def cmd_set_brightness_schedule(
    entries: Sequence[Tuple[str, int]], sno: int = 1
) -> bytes:
    """
    Configure native hardware time-based brightness schedule (TLV 0x06 type=1, a829 lines 1028-1043).
    Runs autonomously on the display's internal RTC even when the Mac is asleep.
    entries: sequence of ("HH:MM", level_1_to_15), e.g. [("08:00", 12), ("22:00", 2)].
    """
    if not entries:
        raise ValueError("entries must contain at least one (HH:MM, level) pair")
    body = bytearray([len(entries) & 0xFF])
    for hhmm, lvl in entries:
        parts = hhmm.strip().split(":")
        if len(parts) != 2:
            raise ValueError(f"Expected HH:MM, got {hhmm}")
        hh, mm = int(parts[0]) & 0xFF, int(parts[1]) & 0xFF
        val = max(1, min(15, int(lvl)))
        body.extend([val, hh, mm])
    return build_frame(encode_tlv(TLV.LIGHT, bytes(body)), sno=sno, cmd_type=CMD_TYPE_SET)


def cmd_set_power_schedule(
    periods: Sequence[str], sno: int = 1
) -> bytes:
    """
    Configure native hardware auto power on/off schedule (TLV 0x04, a829 lines 1008-1018).
    Runs autonomously on the display's internal RTC.
    periods: sequence of "HH:MM-HH:MM" strings, e.g. ["08:00-23:00"].
    """
    if not periods:
        raise ValueError("periods must contain at least one HH:MM-HH:MM window")
    body = bytearray([len(periods) & 0xFF, 127])  # 127 = all 7 days of week
    for p in periods:
        if len(p) != 11 or p[2] != ":" or p[5] != "-" or p[8] != ":":
            raise ValueError(f"Expected HH:MM-HH:MM, got {p}")
        body.extend([int(p[0:2]), int(p[3:5]), int(p[6:8]), int(p[9:11])])
    return build_frame(encode_tlv(TLV.POWER, bytes(body)), sno=sno, cmd_type=CMD_TYPE_SET)


def cmd_set_rotation(degrees: int, sno: int = 1) -> bytes:
    """Set screen rotation (0, 90, 180, 270 degrees). TLV 0x2F."""
    step = (int(degrees) // 90) % 4
    tlv = encode_tlv(TLV.ROTATE, bytes([step]))
    return build_frame(tlv, sno=sno, cmd_type=CMD_TYPE_SET)


def cmd_set_mirror(enabled: bool, sno: int = 1) -> bytes:
    """Enable/disable horizontal mirror. TLV 0x3A."""
    tlv = encode_tlv(TLV.MIRROR, bytes([1 if enabled else 0]))
    return build_frame(tlv, sno=sno, cmd_type=CMD_TYPE_SET)


def cmd_sync_time(dt: Optional[datetime.datetime] = None, sno: int = 1) -> bytes:
    """Synchronize display RTC to host time. TLV 0x05 (a829 lines 1225-1230)."""
    now = dt or datetime.datetime.now()
    payload = struct.pack(
        "<HBBBBBH",
        max(0, now.year - 2000),
        now.month,
        now.day,
        now.hour,
        now.minute,
        now.second,
        now.microsecond // 1000,
    )
    return build_frame(encode_tlv(TLV.TIMING, payload), sno=sno, cmd_type=CMD_TYPE_SET)


def cmd_delete_all(sno: int = 1) -> bytes:
    """Delete all stored programs on the display. TLV 0x08."""
    return build_frame(encode_tlv(TLV.DELETE, bytes([0, 0xFF])), sno=sno, cmd_type=CMD_TYPE_SET)


def cmd_delete_programs(program_ids_1based: Sequence[int], sno: int = 1) -> bytes:
    """Delete specific 1-based program IDs. TLV 0x08."""
    ids = bytes((p - 1) & 0xFF for p in program_ids_1based)
    return build_frame(encode_tlv(TLV.DELETE, bytes([0]) + ids), sno=sno, cmd_type=CMD_TYPE_SET)


def cmd_dispatch_program(
    program_id: int,
    *,
    play_fixed_time: Optional[int] = None,
    play_loop: Optional[int] = None,
    ignore_pgm_cmd: int = 0,
    sno: int = 1,
) -> bytes:
    """Switch display to a stored program (1-based ID). TLV 0x18 (a829 lines 1047-1067)."""
    body = bytearray([2, (program_id - 1) & 0xFF, ignore_pgm_cmd & 0xFF])
    if play_fixed_time is not None:
        body.append(1)
        body.extend(struct.pack("<H", play_fixed_time))
    elif play_loop is not None:
        body.append(0)
        body.extend(struct.pack("<H", play_loop))
    else:
        body.append(0xFF)
    return build_frame(encode_tlv(TLV.DISPATCH, bytes(body)), sno=sno, cmd_type=CMD_TYPE_SET)


def cmd_pgm_play(
    model: int = 0,
    index: int = 0,
    ids_pro: Optional[Sequence[int]] = None,
    sno: int = 1,
) -> bytes:
    """Configure playlist playback mode (0=sequence, 1=single, 2=custom). TLV 0x36."""
    body = bytearray([model & 0xFF, index & 0xFF])
    if ids_pro:
        for pid in ids_pro:
            body.append((pid - 1) & 0xFF)
    return build_frame(encode_tlv(TLV.PGM_PLAY, bytes(body)), sno=sno, cmd_type=CMD_TYPE_SET)


# ---------------------------------------------------------------------------
# Real-time drawing (TLV 0x32 / 50)
# ---------------------------------------------------------------------------

def cmd_rt_draw_rect(
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    color: Tuple[int, int, int],
    *,
    sno: int = 1,
    unack: bool = False,
) -> bytes:
    """
    Fill a rectangle (inclusive x0..x1, y0..y1) with RGB color in real time.
    TLV 0x32 type=1, type_rect=0 (a829 line 1153).
    """
    payload = (
        bytes([1])
        + _rgb_to_le24(*color)
        + bytes([0])
        + struct.pack("<HHHH", x0, y0, x1, y1)
    )
    flags = FLAG_NO_ACK if unack else FLAG_DEFAULT
    return build_frame(encode_tlv(TLV.RT_DRAW, payload), sno=sno, cmd_type=CMD_TYPE_SET, flags=flags)


def cmd_rt_draw_pixels(
    pixels: Iterable[Tuple[int, int]],
    color: Tuple[int, int, int],
    *,
    sno: int = 1,
    unack: bool = False,
) -> bytes:
    """
    Draw one or more (x, y) pixels in a single RGB color in real time.
    Computes minimal bounding box and packs 1-bit-per-pixel MSB-first bitmap
    (a829 be() + case 32 type=16, lines 795-805 & 1155-1178).
    """
    pts = list(pixels)
    if not pts:
        raise ValueError("pixels must not be empty")
    x0 = min(p[0] for p in pts)
    y0 = min(p[1] for p in pts)
    x1 = max(p[0] for p in pts)
    y1 = max(p[1] for p in pts)
    w = x1 - x0 + 1
    h = y1 - y0 + 1

    grid = [0] * (w * h)
    for x, y in pts:
        grid[(y - y0) * w + (x - x0)] = 1

    bitmap = bytearray()
    for row in range(h):
        acc = 0
        bit = 7
        base = row * w
        for col in range(w):
            acc += grid[base + col] << bit
            if bit == 0:
                bitmap.append(acc)
                acc = 0
                bit = 7
            else:
                bit -= 1
        if bit < 7:
            bitmap.append(acc)

    payload = (
        bytes([0])
        + _rgb_to_le24(*color)
        + struct.pack("<HHHH", x0, y0, x1, y1)
        + bytes(bitmap)
    )
    flags = FLAG_NO_ACK if unack else FLAG_DEFAULT
    return build_frame(encode_tlv(TLV.RT_DRAW, payload), sno=sno, cmd_type=CMD_TYPE_SET, flags=flags)


# ---------------------------------------------------------------------------
# Built-in modes: Clock, Countdown, Scoreboard (TLV 0x33 / 51)
# ---------------------------------------------------------------------------

def cmd_set_clock_mode(
    fmt: int = 0,
    *,
    clock_style: Optional[int] = None,
    date_style: Optional[int] = None,
    color: Optional[Tuple[int, int, int]] = None,
    sno: int = 1,
) -> bytes:
    """Switch display to built-in clock mode (TLV 0x33 type=0)."""
    body = bytearray([0, fmt & 0xFF])
    if clock_style is not None and date_style is not None and color is not None:
        body.append(clock_style & 0xFF)
        body.append(date_style & 0xFF)
        body.extend(_rgb_to_le24(*color))
    return build_frame(encode_tlv(TLV.ANI_NUM, bytes(body)), sno=sno, cmd_type=CMD_TYPE_SET)


def cmd_set_countdown(
    hours: int, minutes: int, seconds: int, fmt: int = 0, sno: int = 1
) -> bytes:
    """Start built-in countdown timer (TLV 0x33 type=1)."""
    body = bytes([1, fmt & 0xFF, hours & 0xFF, minutes & 0xFF, seconds & 0xFF])
    return build_frame(encode_tlv(TLV.ANI_NUM, body), sno=sno, cmd_type=CMD_TYPE_SET)


def cmd_set_scoreboard(
    score_red: int, score_blue: int, fmt: int = 0, sno: int = 1
) -> bytes:
    """Update built-in scoreboard (TLV 0x33 type=3)."""
    body = struct.pack("<BBHH", 3, fmt & 0xFF, score_red & 0xFFFF, score_blue & 0xFFFF)
    return build_frame(encode_tlv(TLV.ANI_NUM, body), sno=sno, cmd_type=CMD_TYPE_SET)


def cmd_set_stopwatch(fmt: int = 0, sno: int = 1) -> bytes:
    """Control built-in stopwatch (TLV 0x33 type=2, a829 line 1188)."""
    return build_frame(encode_tlv(TLV.ANI_NUM, bytes([2, fmt & 0xFF])), sno=sno, cmd_type=CMD_TYPE_SET)


def cmd_set_music_spectrum(
    style: int = 0,
    *,
    bars: Optional[Sequence[int]] = None,
    use_device_mic: bool = True,
    sno: int = 1,
    unack: bool = False,
) -> bytes:
    """
    Music rhythm / audio spectrum mode (TLV 0x33 type=4, a829 lines 1186, 1190-1193).
    - use_device_mic=True sets bit 7 of style (led_mp=1) so the display reacts to its onboard mic.
    - use_device_mic=False sends up to 100 bar amplitude bytes (0..255) from the host.
    """
    style_byte = (style & 0x7F) | (0x80 if use_device_mic else 0)
    body = bytearray([4, style_byte])
    if not use_device_mic:
        vals = list(bars[:100]) if bars is not None else [0] * 20
        body.extend(max(0, min(255, int(v))) for v in vals)
    flags = FLAG_NO_ACK if unack else FLAG_DEFAULT
    return build_frame(encode_tlv(TLV.ANI_NUM, bytes(body)), sno=sno, cmd_type=CMD_TYPE_SET, flags=flags)


def cmd_set_ambient_light(
    style_light: int = 0,
    style_val: int = 0,
    *,
    color: Tuple[int, int, int] = (255, 255, 255),
    colors: Optional[Sequence[Tuple[int, int, int]]] = None,
    speed: int = 128,
    sno: int = 1,
) -> bytes:
    """
    Ambient / mood light mode (TLV 0x33 type=5, a829 lines 1186, 1194-1200).
    - style_light=0: dynamic effect (style_val, speed, optional up to 8 RGB colors).
    - style_light=1: solid color mood light (style_val, RGB color).
    """
    sl = 1 if style_light == 1 else 0
    body = bytearray([5, sl, style_val & 0xFF])
    if sl == 0:
        body.append(speed & 0xFF)
        if colors:
            for rgb in list(colors)[:8]:
                body.extend(_rgb_to_le24(*rgb))
    else:
        body.extend(_rgb_to_le24(*color))
    return build_frame(encode_tlv(TLV.ANI_NUM, bytes(body)), sno=sno, cmd_type=CMD_TYPE_SET)


_GAME_OPT_CODES = {
    "left": 37,
    "up": 38,
    "right": 39,
    "down": 40,
    "start": 115,
    "pause": 112,
    "done": 100,
    "exit": 101,
}


def cmd_game_launch(game_id: int, sno: int = 1) -> bytes:
    """Launch a built-in hardware game by ID (TLV 0x30, a829 line 1131: id + 128)."""
    return build_frame(
        encode_tlv(TLV.GAME, bytes([(int(game_id) + 128) & 0xFF])),
        sno=sno,
        cmd_type=CMD_TYPE_SET,
    )


def cmd_game_action(opt: str, sno: int = 1, unack: bool = False) -> bytes:
    """Send D-pad / control input to a running built-in game (TLV 0x30, a829 lines 694-695)."""
    code = _GAME_OPT_CODES.get(opt.lower())
    if code is None:
        raise ValueError(f"Unknown game opt '{opt}'. Valid: {sorted(_GAME_OPT_CODES)}")
    flags = FLAG_NO_ACK if unack else FLAG_DEFAULT
    return build_frame(encode_tlv(TLV.GAME, bytes([code])), sno=sno, cmd_type=CMD_TYPE_SET, flags=flags)


def cmd_set_global_ani(
    val: int = 0,
    speed: int = 9,
    time_stay: int = 3,
    *,
    disable: bool = False,
    sno: int = 1,
) -> bytes:
    """Configure global transition animation (TLV 0x38, a829 lines 1232-1241)."""
    if disable:
        return build_frame(encode_tlv(TLV.GLOBAL_ANI, bytes([0])), sno=sno, cmd_type=CMD_TYPE_SET)
    flags_mask = 0
    b_val = 0
    b_spd = 0
    b_stay = 0
    if val >= 0:
        flags_mask |= 1
        b_val = val & 0xFF
    if speed >= 0:
        flags_mask |= 2
        b_spd = speed & 0xFF
    if time_stay >= 0:
        flags_mask |= 4
        b_stay = time_stay & 0xFF
    return build_frame(
        encode_tlv(TLV.GLOBAL_ANI, bytes([flags_mask, b_val, b_spd, b_stay])),
        sno=sno,
        cmd_type=CMD_TYPE_SET,
    )


def cmd_set_ble_password(pwd_type: int, password: str = "", sno: int = 1) -> bytes:
    """
    Manage BLE hardware password (TLV 0x37, a829 lines 1262-1270).
    pwd_type: 0=verify/unlock, 1=set password, 2=clear/disable, 3=query.
    """
    if pwd_type < 0 or pwd_type > 3:
        raise ValueError("pwd_type must be 0..3")
    body = bytes([pwd_type]) + (password.encode("ascii") if pwd_type < 2 else b"")
    return build_frame(encode_tlv(TLV.PWD, body), sno=sno, cmd_type=CMD_TYPE_SET)


# ---------------------------------------------------------------------------
# Multi-frame Program Uploads (YSTP01 Images, Native GIFs, Text)
# ---------------------------------------------------------------------------

def _build_program_header_tlv(
    id_pro_0: int,
    width: int,
    height: int,
    *,
    data_save: int = 0,
    cover: bool = True,
    play_loop: int = 1,
    play_fixed_time: Optional[int] = None,
    type_bg: int = 0,
) -> bytes:
    """Build program header TLV bundle (module a829 ce() + re())."""
    parts: List[bytes] = []
    if id_pro_0 != 0xFF:
        tag = TLV.COVER_PRO if cover else TLV.DELETE
        parts.append(encode_tlv(tag, bytes([0, id_pro_0])))
    parts.append(encode_tlv(TLV.DATA_SAVE, bytes([1 - data_save])))
    parts.append(encode_tlv(TLV.ID_PRO, bytes([id_pro_0])))

    if play_fixed_time is not None and play_fixed_time > 0:
        play_mode = 1
        play_val = play_fixed_time
    else:
        play_mode = 0
        play_val = play_loop
    parts.append(encode_tlv(TLV.PRO_PROP, struct.pack("<BBHH", type_bg & 0xFF, play_mode, play_val, 3)))
    parts.append(encode_tlv(TLV.ID_RECT, bytes([0])))
    parts.append(encode_tlv(TLV.RECT_POS, struct.pack("<HHHHB", 0, 0, width, height, 0)))
    return b"".join(parts)


def _build_data_chunk_tlvs(
    raw_data: bytes,
    id_pro_0: int,
    id_rect_0: int,
    id_item_0: int,
    *,
    data_save: int = 0,
    chunk_size: int = DEFAULT_DATA_CHUNK_SIZE,
) -> List[bytes]:
    """Split resource payload into TLV 0x12 + 0x13 chunk bundles (module a829 R())."""
    total_chunks = max(1, math.ceil(len(raw_data) / chunk_size))
    bundles: List[bytes] = []
    prefix = (
        encode_tlv(TLV.DATA_SAVE, bytes([1 - data_save]))
        + encode_tlv(TLV.ID_PRO, bytes([id_pro_0]))
        + encode_tlv(TLV.ID_RECT, bytes([id_rect_0]))
        + encode_tlv(TLV.ID_ITEM, bytes([id_item_0]))
    )
    for idx in range(total_chunks):
        chunk = raw_data[idx * chunk_size : (idx + 1) * chunk_size]
        meta = encode_tlv(
            TLV.DATA_META,
            struct.pack("<HHHB", total_chunks, idx, chunk_size, 0),
        )
        data_tlv = encode_tlv(TLV.DATA_CHUNK, chunk)
        bundles.append(prefix + meta + data_tlv)
    return bundles


def _wrap_program_bundles(
    bundles: Sequence[bytes],
    start_sno: int,
    max_single_pkt_tlv: int = 997,
) -> List[bytes]:
    """
    Wrap chronological TLV bundles into one or more wire frames (module a829 de()).
    If total TLV length <= max_single_pkt_tlv, coalesces into a single frame;
    otherwise emits one frame per bundle with incrementing sno.
    """
    total_len = sum(len(b) for b in bundles)
    if total_len <= max_single_pkt_tlv:
        return [build_frame(b"".join(bundles), sno=start_sno, cmd_type=CMD_TYPE_SET)]
    frames: List[bytes] = []
    sno = start_sno
    for bundle in bundles:
        frames.append(build_frame(bundle, sno=sno, cmd_type=CMD_TYPE_SET))
        sno = (sno + 1) & 0xFFFF
    return frames


def build_graphic_program_frames(
    resource_bytes: bytes,
    width: int,
    height: int,
    *,
    program_id: int = 1,
    start_sno: int = 1,
    speed: int = 9,
    time_stay: int = 3,
    type_ani: int = 1,
    play_loop: int = 1,
    play_fixed_time: Optional[int] = None,
    show_now: bool = True,
    data_save: int = 0,
    chunk_size: int = DEFAULT_DATA_CHUNK_SIZE,
) -> List[bytes]:
    """
    Build the complete sequence of BLE wire frames to upload a graphic resource
    (either a YSTP01 container or raw native GIF bytes starting with b'GIF').
    """
    id_pro_0 = (program_id - 1) & 0xFF
    bundles: List[bytes] = []

    # 1. Program header
    bundles.append(
        _build_program_header_tlv(
            id_pro_0,
            width,
            height,
            data_save=data_save,
            play_loop=play_loop,
            play_fixed_time=play_fixed_time,
        )
    )

    # 2. Graphic item header (module a829 V() + O())
    item_ctrl = bytearray([0, 1, 0, 10])  # play_loop=1, item_type=10 (graphic)
    if play_fixed_time is not None and play_fixed_time > 0:
        item_ctrl[0] = 1
        struct.pack_into("<H", item_ctrl, 1, play_fixed_time)
    else:
        item_ctrl[0] = 0
        struct.pack_into("<H", item_ctrl, 1, play_loop)

    item_header = (
        encode_tlv(TLV.DATA_SAVE, bytes([1 - data_save]))
        + encode_tlv(TLV.ID_PRO, bytes([id_pro_0]))
        + encode_tlv(TLV.ID_RECT, bytes([0]))
        + encode_tlv(TLV.ID_ITEM, bytes([0]))
        + encode_tlv(TLV.ANIMATE, bytes([type_ani & 0xFF, speed & 0xFF, time_stay & 0xFF]))
        + encode_tlv(TLV.ITEM_CTRL, bytes(item_ctrl))
    )
    bundles.append(item_header)

    # 3. Data chunks
    bundles.extend(
        _build_data_chunk_tlvs(
            resource_bytes,
            id_pro_0,
            0,
            0,
            data_save=data_save,
            chunk_size=chunk_size,
        )
    )

    # 4. Optional show_now dispatch TLV (a829 line 20460: a=0xFF len=4 = play continuously)
    if show_now:
        if play_fixed_time is not None and play_fixed_time > 0:
            disp = struct.pack("<BBBBH", 2, id_pro_0, 0, 1, play_fixed_time)
        else:
            disp = struct.pack("<BBBB", 2, id_pro_0, 0, 0xFF)
        bundles.append(encode_tlv(TLV.DISPATCH, disp))

    return _wrap_program_bundles(bundles, start_sno=start_sno)


def build_image_program_frames(
    ystp01_bytes: bytes,
    width: int,
    height: int,
    **kwargs,
) -> List[bytes]:
    """Alias for uploading a YSTP01 image/animation payload."""
    if not ystp01_bytes.startswith(b"YSTP01"):
        raise ValueError("Payload does not start with YSTP01 magic")
    return build_graphic_program_frames(ystp01_bytes, width, height, **kwargs)


def build_gif_program_frames(
    gif_bytes: bytes,
    width: int,
    height: int,
    **kwargs,
) -> List[bytes]:
    """Upload a raw GIF file directly using the hardware's native GIF passthrough."""
    if not gif_bytes.startswith(b"GIF"):
        raise ValueError("Payload does not start with GIF header")
    kwargs.setdefault("time_stay", 0)
    return build_graphic_program_frames(gif_bytes, width, height, **kwargs)


def encode_text_clock_placeholders(template: str) -> bytes:
    """
    Encode a hardware `text_clock` template string with live firmware RTC opcodes.
    Exact byte sequences recovered from module a829 lines 316-340 (VERIFIED STATIC):
      {YYYY} -> 06 04 00   {YY} -> 06 02 00
      {MM}   -> 06 02 01   {DD} -> 06 02 02
      {hh}   -> 06 02 03   {mm} -> 06 02 04   {ss} -> 06 02 05
      {w}    -> 06 00 06 02 07 + weekday GB bytes
    """
    raw = template.encode("gb18030", errors="replace")
    replacements = [
        (b"{YYYY}", bytes([6, 4, 0])),
        (b"{YY}", bytes([6, 2, 0])),
        (b"{MM}", bytes([6, 2, 1])),
        (b"{DD}", bytes([6, 2, 2])),
        (b"{hh}", bytes([6, 2, 3])),
        (b"{mm}", bytes([6, 2, 4])),
        (b"{ss}", bytes([6, 2, 5])),
        (
            b"{w}",
            bytes([6, 0, 6, 2, 7, 210, 187, 182, 254, 200, 253, 203, 196, 206, 229, 193, 249, 200, 213]),
        ),
    ]
    for token, opcode in replacements:
        raw = raw.replace(token, opcode)
    return raw


def build_text_clock_program_frames(
    template: str,
    width: int,
    height: int,
    *,
    color: Tuple[int, int, int] = (5, 217, 232),
    bg_color: Tuple[int, int, int] = (0, 0, 0),
    font_size: int = 16,
    scroll: str = "static",
    speed: int = 9,
    time_stay: int = 3,
    align_h: str = "center",
    align_v: str = "center",
    program_id: int = 1,
    start_sno: int = 1,
    show_now: bool = True,
    data_save: int = 0,
    background_resource: Optional[bytes] = None,
    bg_mode: str = "behind",
    chunk_size: int = DEFAULT_DATA_CHUNK_SIZE,
) -> List[bytes]:
    """
    Build wire frames for a standalone hardware-ticking `text_clock` program
    (module a829 se() + te(s.text_clock)). Once uploaded, the display's own RTC
    updates {hh}, {mm}, {ss}, {YYYY}, {MM}, {DD}, {w} without BLE connected.
    Optionally includes a background image/GIF resource (region 1, background=1):
      - bg_mode="behind": type_bg=4 (composites artwork/GIF behind clock text)
      - bg_mode="mask":   type_bg=1 (masks artwork/GIF *inside* the clock digits, 981c.js)
    """
    ani_map = {"left": 0, "static": 1, "up": 4, "right": 27}
    type_ani = ani_map.get(scroll, 1)
    interval = height if type_ani == 4 else width
    is_mask = background_resource is not None and bg_mode == "mask"
    eff_color = (255, 255, 255) if is_mask else color

    h_map = {"left": 0, "center": 1, "right": 2}
    v_map = {"top": 0, "center": 1, "bottom": 2}
    align_hdr = bytes([3, 0, v_map.get(align_v, 1), 3, 1, h_map.get(align_h, 1)])
    color_hdr = bytes([0]) + _rgb_to_le24(*eff_color) + _rgb_to_le24(*bg_color)
    font_hdr = bytes([1, 1, 0, font_size & 0xFF])
    clock_stream = align_hdr + color_hdr + font_hdr + encode_text_clock_placeholders(template)

    id_pro_0 = (program_id - 1) & 0xFF
    eff_type_bg = (1 if is_mask else 4) if background_resource is not None else 0
    prog_hdr = _build_program_header_tlv(
        id_pro_0, width, height, data_save=data_save, type_bg=eff_type_bg
    )
    if background_resource is not None:
        # Add second region (id_rect=1, background=1) per a829 lines 524-528
        prog_hdr += encode_tlv(TLV.ID_RECT, bytes([1])) + encode_tlv(
            TLV.RECT_POS, struct.pack("<HHHHB", 0, 0, width, height, 1)
        )
    bundles: List[bytes] = [prog_hdr]

    # Region 0: text_clock item (item_type = 6)
    item_ctrl = struct.pack(
        "<BHBBBBBH",
        0,
        1,
        6,
        1,
        0,
        font_size & 0xFF,
        0,
        interval & 0xFFFF,
    )
    item_header = (
        encode_tlv(TLV.DATA_SAVE, bytes([1 - data_save]))
        + encode_tlv(TLV.ID_PRO, bytes([id_pro_0]))
        + encode_tlv(TLV.ID_RECT, bytes([0]))
        + encode_tlv(TLV.ID_ITEM, bytes([0]))
        + encode_tlv(TLV.ANIMATE, bytes([type_ani, speed & 0xFF, time_stay & 0xFF]))
        + encode_tlv(TLV.ITEM_CTRL, item_ctrl)
    )
    bundles.append(item_header)
    bundles.extend(
        _build_data_chunk_tlvs(clock_stream, id_pro_0, 0, 0, data_save=data_save, chunk_size=chunk_size)
    )

    # Optional Region 1: background graphic / GIF (item_type = 10)
    if background_resource is not None:
        bg_ctrl = bytes([0, 1, 0, 10])
        bg_hdr = (
            encode_tlv(TLV.DATA_SAVE, bytes([1 - data_save]))
            + encode_tlv(TLV.ID_PRO, bytes([id_pro_0]))
            + encode_tlv(TLV.ID_RECT, bytes([1]))
            + encode_tlv(TLV.ID_ITEM, bytes([0]))
            + encode_tlv(TLV.ANIMATE, bytes([1, speed & 0xFF, 0]))
            + encode_tlv(TLV.ITEM_CTRL, bg_ctrl)
        )
        bundles.append(bg_hdr)
        bundles.extend(
            _build_data_chunk_tlvs(
                background_resource, id_pro_0, 1, 0, data_save=data_save, chunk_size=chunk_size
            )
        )

    if show_now:
        disp = struct.pack("<BBBB", 2, id_pro_0, 0, 0xFF)
        bundles.append(encode_tlv(TLV.DISPATCH, disp))

    return _wrap_program_bundles(bundles, start_sno=start_sno)


def build_multi_region_hw_clock_frames(
    regions: Sequence[dict],
    width: int,
    height: int,
    *,
    program_id: int = 1,
    start_sno: int = 1,
    show_now: bool = True,
    data_save: int = 0,
    background_resource: Optional[bytes] = None,
    bg_mode: str = "behind",
    chunk_size: int = DEFAULT_DATA_CHUNK_SIZE,
) -> List[bytes]:
    """
    Build a multi-region standalone hardware-ticking clock program.
    Each entry in `regions` is a dict:
      {
        "template": "{hh}:{mm}",
        "x": 0, "y": 0, "w": 64, "h": 32,
        "color": (5, 217, 232),
        "bg_color": (0, 0, 0),
        "font_size": 16,
        "scroll": "static",
        "align_h": "center",
        "align_v": "center",
      }
    Optionally overlays on top of (bg_mode="behind", type_bg=4) or masks inside digits
    (bg_mode="mask", type_bg=1) a YSTP01/GIF `background_resource` (final region, background=1).
    """
    if not regions:
        raise ValueError("regions must contain at least one region")

    ani_map = {"left": 0, "static": 1, "up": 4, "right": 27}
    h_map = {"left": 0, "center": 1, "right": 2}
    v_map = {"top": 0, "center": 1, "bottom": 2}
    id_pro_0 = (program_id - 1) & 0xFF
    is_mask = background_resource is not None and bg_mode == "mask"

    # Program header with all regions declared (a829 ce())
    hdr_parts: List[bytes] = []
    if id_pro_0 != 0xFF:
        hdr_parts.append(encode_tlv(TLV.COVER_PRO, bytes([0, id_pro_0])))
    hdr_parts.append(encode_tlv(TLV.DATA_SAVE, bytes([1 - data_save])))
    hdr_parts.append(encode_tlv(TLV.ID_PRO, bytes([id_pro_0])))
    type_bg = (1 if is_mask else 4) if background_resource is not None else 0
    hdr_parts.append(encode_tlv(TLV.PRO_PROP, struct.pack("<BBHH", type_bg, 0, 1, 3)))

    for r_idx, reg in enumerate(regions):
        rx = int(reg.get("x", 0))
        ry = int(reg.get("y", 0))
        rw = int(reg.get("w", width))
        rh = int(reg.get("h", height))
        hdr_parts.append(encode_tlv(TLV.ID_RECT, bytes([r_idx])))
        hdr_parts.append(encode_tlv(TLV.RECT_POS, struct.pack("<HHHHB", rx, ry, rw, rh, 0)))

    bg_rect_idx = len(regions)
    if background_resource is not None:
        hdr_parts.append(encode_tlv(TLV.ID_RECT, bytes([bg_rect_idx])))
        hdr_parts.append(encode_tlv(TLV.RECT_POS, struct.pack("<HHHHB", 0, 0, width, height, 1)))

    bundles: List[bytes] = [b"".join(hdr_parts)]

    # Emit each live hardware text_clock region
    for r_idx, reg in enumerate(regions):
        rw = int(reg.get("w", width))
        rh = int(reg.get("h", height))
        scroll = reg.get("scroll", "static")
        type_ani = ani_map.get(scroll, 1)
        interval = rh if type_ani == 4 else rw
        font_size = int(reg.get("font_size", 16))
        color = (255, 255, 255) if is_mask else tuple(reg.get("color", (5, 217, 232)))
        bg_color = tuple(reg.get("bg_color", (0, 0, 0)))
        align_hdr = bytes([
            3, 0, v_map.get(reg.get("align_v", "center"), 1),
            3, 1, h_map.get(reg.get("align_h", "center"), 1),
        ])
        color_hdr = bytes([0]) + _rgb_to_le24(*color) + _rgb_to_le24(*bg_color)
        font_hdr = bytes([1, 1, 0, font_size & 0xFF])
        stream = align_hdr + color_hdr + font_hdr + encode_text_clock_placeholders(reg["template"])

        item_ctrl = struct.pack("<BHBBBBBH", 0, 1, 6, 1, 0, font_size & 0xFF, 0, interval & 0xFFFF)
        item_header = (
            encode_tlv(TLV.DATA_SAVE, bytes([1 - data_save]))
            + encode_tlv(TLV.ID_PRO, bytes([id_pro_0]))
            + encode_tlv(TLV.ID_RECT, bytes([r_idx]))
            + encode_tlv(TLV.ID_ITEM, bytes([0]))
            + encode_tlv(TLV.ANIMATE, bytes([type_ani, 9, 3]))
            + encode_tlv(TLV.ITEM_CTRL, item_ctrl)
        )
        bundles.append(item_header)
        bundles.extend(
            _build_data_chunk_tlvs(stream, id_pro_0, r_idx, 0, data_save=data_save, chunk_size=chunk_size)
        )

    # Optional shape/bezel/GIF background in final region
    if background_resource is not None:
        bg_hdr = (
            encode_tlv(TLV.DATA_SAVE, bytes([1 - data_save]))
            + encode_tlv(TLV.ID_PRO, bytes([id_pro_0]))
            + encode_tlv(TLV.ID_RECT, bytes([bg_rect_idx]))
            + encode_tlv(TLV.ID_ITEM, bytes([0]))
            + encode_tlv(TLV.ANIMATE, bytes([1, 9, 0]))
            + encode_tlv(TLV.ITEM_CTRL, bytes([0, 1, 0, 10]))
        )
        bundles.append(bg_hdr)
        bundles.extend(
            _build_data_chunk_tlvs(
                background_resource, id_pro_0, bg_rect_idx, 0, data_save=data_save, chunk_size=chunk_size
            )
        )

    if show_now:
        bundles.append(encode_tlv(TLV.DISPATCH, struct.pack("<BBBB", 2, id_pro_0, 0, 0xFF)))

    return _wrap_program_bundles(bundles, start_sno=start_sno)


def build_text_program_frames(
    text: str,
    width: int,
    height: int,
    *,
    color: Tuple[int, int, int] = (255, 255, 255),
    bg_color: Tuple[int, int, int] = (0, 0, 0),
    font_size: int = 16,
    scroll: str = "left",
    speed: int = 9,
    time_stay: int = 3,
    program_id: int = 1,
    start_sno: int = 1,
    show_now: bool = True,
    data_save: int = 0,
    chunk_size: int = DEFAULT_DATA_CHUNK_SIZE,
) -> List[bytes]:
    """
    Build wire frames for a hardware-rendered text program (module a829 se()).
    scroll: 'static' (1), 'left' (0), 'up' (4), 'right' (27).
    """
    ani_map = {"left": 0, "static": 1, "up": 4, "right": 27}
    type_ani = ani_map.get(scroll, 0)
    interval = height if type_ani == 4 else width

    # Encode text using GB18030/GBK (compatible with ASCII & Chinese, matches a829 utf8_bytes_gb)
    encoded_text = text.encode("gb18030", errors="replace")

    # Text formatting header: alignment (center/center) + color + font
    align_hdr = bytes([3, 0, 1, 3, 1, 1])  # page=center(1), line=center(1)
    color_hdr = bytes([0]) + _rgb_to_le24(*color) + _rgb_to_le24(*bg_color)
    font_hdr = bytes([1, 1, 0, font_size & 0xFF])  # code=1 (GB), font=0, size
    text_stream = align_hdr + color_hdr + font_hdr + encoded_text

    id_pro_0 = (program_id - 1) & 0xFF
    bundles: List[bytes] = [
        _build_program_header_tlv(id_pro_0, width, height, data_save=data_save)
    ]

    # Text item header: TLV 0x11 length 10 (a829 line 438)
    item_ctrl = struct.pack(
        "<BHBBBBBH",
        0,          # play_loop mode
        1,          # loop=1
        6,          # item_type = 6 (text)
        1,          # code = 1
        0,          # font = 0
        font_size & 0xFF,
        0,          # rotate flag
        interval & 0xFFFF,
    )
    item_header = (
        encode_tlv(TLV.DATA_SAVE, bytes([1 - data_save]))
        + encode_tlv(TLV.ID_PRO, bytes([id_pro_0]))
        + encode_tlv(TLV.ID_RECT, bytes([0]))
        + encode_tlv(TLV.ID_ITEM, bytes([0]))
        + encode_tlv(TLV.ANIMATE, bytes([type_ani, speed & 0xFF, time_stay & 0xFF]))
        + encode_tlv(TLV.ITEM_CTRL, item_ctrl)
    )
    bundles.append(item_header)
    bundles.extend(
        _build_data_chunk_tlvs(text_stream, id_pro_0, 0, 0, data_save=data_save, chunk_size=chunk_size)
    )

    if show_now:
        disp = struct.pack("<BBBB", 2, id_pro_0, 0, 0xFF)
        bundles.append(encode_tlv(TLV.DISPATCH, disp))

    return _wrap_program_bundles(bundles, start_sno=start_sno)
