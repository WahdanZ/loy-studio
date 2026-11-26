"""
Automated protocol unit & integration tests against recovered LOY PLAY v1.7.15 fixtures.
"""

from __future__ import annotations

import asyncio
import datetime
import struct
import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from protocol import (
    CMD_TYPE_GET,
    CMD_TYPE_SET,
    FLAG_DEFAULT,
    FLAG_NO_ACK,
    MockTransport,
    PacketParser,
    TLV,
    build_frame,
    build_gif_program_frames,
    build_image_program_frames,
    build_text_clock_program_frames,
    build_text_program_frames,
    encode_text_clock_placeholders,
    cmd_delete_all,
    cmd_game_action,
    cmd_game_launch,
    cmd_get_dev_info_all,
    cmd_rt_draw_pixels,
    cmd_rt_draw_rect,
    cmd_set_ambient_light,
    cmd_set_ble_password,
    cmd_set_brightness,
    cmd_set_clock_mode,
    cmd_set_countdown,
    cmd_set_global_ani,
    cmd_set_mirror,
    cmd_set_music_spectrum,
    cmd_set_power,
    cmd_set_rotation,
    cmd_set_scoreboard,
    cmd_set_stopwatch,
    cmd_sync_time,
    compute_checksum,
    decode_response,
    decode_tlv_length,
    encode_solid_color_ystp01,
    encode_tlv_length,
    encode_ystp01,
    pil_image_to_ystp01,
    split_ble_writes,
    verify_checksum,
)


def test_tlv_length_encoding_and_decoding():
    for val, expected in [
        (0, b"\x00"),
        (127, b"\x7f"),
        (128, b"\x81\x80"),
        (255, b"\x81\xff"),
        (256, b"\x82\x00\x01"),
        (960, b"\x82\xc0\x03"),
    ]:
        enc = encode_tlv_length(val)
        assert enc == expected
        dec, consumed = decode_tlv_length(enc)
        assert dec == val
        assert consumed == len(expected)


def test_get_dev_info_all_exact_wire_fixture():
    """
    Verify exact wire bytes against module ac01 + a829 Ce('dev_info_all') + $e():
      Header: AA 55 FF FF 0E 00 01 00 C1 03
      TLV:    0A 00 1B 00 04 00 06 00 (8 bytes)
      body_len = 8 + 2 (chk) + 4 = 14 (0x000E)
    """
    frame = cmd_get_dev_info_all(sno=1)
    assert frame[:10] == bytes.fromhex("aa 55 ff ff 0e 00 01 00 c1 03")
    assert frame[10:-2] == bytes.fromhex("0a 00 1b 00 04 00 06 00")
    assert verify_checksum(frame)
    expected_chk = sum(frame[:-2]) & 0xFFFF
    assert struct.unpack_from("<H", frame, len(frame) - 2)[0] == expected_chk


def test_power_and_brightness_exact_wire_fixtures():
    # Power OFF: TLV 04 02 00 00
    f_off = cmd_set_power(False, sno=2)
    assert f_off[:10] == bytes.fromhex("aa 55 ff ff 0a 00 02 00 c1 02")
    assert f_off[10:-2] == bytes.fromhex("04 02 00 00")
    assert verify_checksum(f_off)

    # Power ON: TLV 04 02 00 01
    f_on = cmd_set_power(True, sno=3)
    assert f_on[10:-2] == bytes.fromhex("04 02 00 01")
    assert verify_checksum(f_on)

    # Brightness 15: TLV 06 02 00 0F
    f_bri = cmd_set_brightness(15, sno=4)
    assert f_bri[10:-2] == bytes.fromhex("06 02 00 0f")
    assert verify_checksum(f_bri)

    # Clamping bounds (0 -> 1, 99 -> 15)
    assert cmd_set_brightness(0)[10:-2] == bytes.fromhex("06 02 00 01")
    assert cmd_set_brightness(99)[10:-2] == bytes.fromhex("06 02 00 0f")


def test_rotation_mirror_delete_sync_time():
    assert cmd_set_rotation(90)[10:-2] == bytes.fromhex("2f 01 01")
    assert cmd_set_rotation(270)[10:-2] == bytes.fromhex("2f 01 03")
    assert cmd_set_mirror(True)[10:-2] == bytes.fromhex("3a 01 01")
    assert cmd_delete_all()[10:-2] == bytes.fromhex("08 02 00 ff")

    dt = datetime.datetime(2026, 11, 24, 10, 30, 45, 500000)
    f_time = cmd_sync_time(dt, sno=5)
    assert f_time[10:12] == bytes([TLV.TIMING, 9])
    yr_off, mo, dy, hr, mi, sc, ms = struct.unpack_from("<HBBBBBH", f_time, 12)
    assert (yr_off, mo, dy, hr, mi, sc, ms) == (26, 11, 24, 10, 30, 45, 500)


def test_rt_draw_rect_and_single_pixel():
    # Fill 32x32 red rect
    f_rect = cmd_rt_draw_rect(0, 0, 31, 31, (255, 0, 0), sno=10)
    assert f_rect[10:-2] == bytes.fromhex("32 0d 01 ff 00 00 00 00 00 00 00 1f 00 1f 00")
    assert verify_checksum(f_rect)

    # Single pixel at (5, 7) green, unack=True -> flags = 0x81
    f_pix = cmd_rt_draw_pixels([(5, 7)], (0, 255, 0), sno=11, unack=True)
    assert f_pix[8] == FLAG_NO_ACK
    # TLV 0x32, len 13, type=0, RGB=00 FF 00, x0=5, y0=7, x1=5, y1=7, bitmap=0x80
    assert f_pix[10:-2] == bytes.fromhex("32 0d 00 00 ff 00 05 00 07 00 05 00 07 00 80")
    assert verify_checksum(f_pix)


def test_builtin_modes():
    f_clk = cmd_set_clock_mode(fmt=2, clock_style=1, date_style=0, color=(255, 128, 0))
    assert f_clk[10:-2] == bytes.fromhex("33 07 00 02 01 00 ff 80 00")
    assert cmd_set_countdown(1, 2, 3)[10:-2] == bytes.fromhex("33 05 01 00 01 02 03")
    assert cmd_set_stopwatch(0)[10:-2] == bytes.fromhex("33 02 02 00")
    assert cmd_set_scoreboard(12, 7)[10:-2] == bytes.fromhex("33 06 03 00 0c 00 07 00")
    assert cmd_set_music_spectrum(style=2, use_device_mic=True)[10:-2] == bytes.fromhex("33 02 04 82")
    assert cmd_set_ambient_light(style_light=1, style_val=0, color=(255, 0, 128))[10:-2] == bytes.fromhex("33 06 05 01 00 ff 00 80")
    assert cmd_game_launch(0)[10:-2] == bytes.fromhex("30 01 80")
    assert cmd_game_action("up")[10:-2] == bytes.fromhex("30 01 26")
    assert cmd_game_action("exit")[10:-2] == bytes.fromhex("30 01 65")
    assert cmd_set_global_ani(disable=True)[10:-2] == bytes.fromhex("38 01 00")
    assert cmd_set_ble_password(2)[10:-2] == bytes.fromhex("37 01 02")


def test_ystp01_indexed_and_rgb24():
    # Solid red 16x16 -> 1 unique color -> bpp=1, palette=2*3=6B, pixels=2*16=32B -> total 15+6+32=53B
    ystp = encode_solid_color_ystp01(16, 16, 255, 0, 0)
    assert ystp[:6] == b"YSTP01"
    w, h, frames, valid, bpp = struct.unpack_from("<HHHHB", ystp, 6)
    assert (w, h, frames, valid, bpp) == (16, 16, 1, 16, 1)
    assert ystp[15:18] == bytes([255, 0, 0])  # palette entry 0 = Red
    assert len(ystp) == 15 + 6 + 32

    # >256 unique colors -> bpp=24
    rgb = bytearray()
    for y in range(16):
        for x in range(17):
            rgb.extend([x * 15, y * 15, (x + y) % 256])
    ystp24 = encode_ystp01([bytes(rgb)], 17, 16)
    assert ystp24[14] == 24
    assert len(ystp24) == 15 + 17 * 16 * 3


def test_pil_image_and_program_framing():
    img = Image.new("RGB", (32, 32), (0, 0, 255))
    ystp = pil_image_to_ystp01(img, 32, 32)
    frames = build_image_program_frames(ystp, 32, 32, program_id=1, start_sno=1)
    assert len(frames) >= 1
    for fr in frames:
        assert verify_checksum(fr)
        assert fr[:2] == b"\xaa\x55"

    # Native GIF passthrough
    fake_gif = b"GIF89a" + b"\x00" * 1500
    gif_frames = build_gif_program_frames(fake_gif, 32, 32, program_id=2, start_sno=10)
    # Larger than 960 bytes -> multi-frame upload (header, item_hdr, chunk0, chunk1, show_now)
    assert len(gif_frames) == 5
    for idx, fr in enumerate(gif_frames):
        assert verify_checksum(fr)
        assert struct.unpack_from("<H", fr, 6)[0] == 10 + idx

    # Text program
    txt_frames = build_text_program_frames("HELLO", 32, 32, color=(255, 0, 0), start_sno=20)
    assert len(txt_frames) >= 1
    assert all(verify_checksum(fr) for fr in txt_frames)


def test_hardware_text_clock_opcodes_and_framing():
    encoded = encode_text_clock_placeholders("{YYYY}-{MM}-{DD} {hh}:{mm}:{ss}")
    assert encoded == (
        bytes([6, 4, 0]) + b"-" + bytes([6, 2, 1]) + b"-" + bytes([6, 2, 2])
        + b" " + bytes([6, 2, 3]) + b":" + bytes([6, 2, 4]) + b":" + bytes([6, 2, 5])
    )
    frames = build_text_clock_program_frames(
        "{hh}:{mm}:{ss}", 64, 64, color=(5, 217, 232), font_size=16, start_sno=30
    )
    assert len(frames) >= 1
    assert all(verify_checksum(fr) for fr in frames)
    assert bytes([6, 2, 3]) in b"".join(frames)

    # With GIF background resource (behind: type_bg=4)
    bg_gif = b"GIF89a" + b"\x00" * 100
    frames_bg = build_text_clock_program_frames(
        "{hh}:{mm}", 64, 64, background_resource=bg_gif, bg_mode="behind", start_sno=40
    )
    assert len(frames_bg) >= 1
    assert all(verify_checksum(fr) for fr in frames_bg)
    assert b"GIF89a" in b"".join(frames_bg)
    assert b"\x1c\x06\x04\x00\x01\x00\x03\x00" in frames_bg[0]

    # With GIF masked inside clock digits (mask: type_bg=1)
    frames_mask = build_text_clock_program_frames(
        "{hh}:{mm}", 64, 64, background_resource=bg_gif, bg_mode="mask", start_sno=50
    )
    assert all(verify_checksum(fr) for fr in frames_mask)
    assert b"\x1c\x06\x01\x00\x01\x00\x03\x00" in frames_mask[0]


def test_packet_parser_fragmented_and_noisy_stream():
    f1 = cmd_set_power(True, sno=100)
    f2 = cmd_set_brightness(8, sno=101)
    stream = b"\x00\x11\xaa" + f1 + b"\xff" + f2
    parser = PacketParser()
    found = []
    # Feed 3 bytes at a time to simulate BLE notification fragmentation
    for i in range(0, len(stream), 3):
        found.extend(parser.feed(stream[i : i + 3]))
    assert len(found) == 2
    assert found[0].sno == 100
    assert found[1].sno == 101


def test_ble_write_chunking():
    frame = build_frame(b"\x01" * 400, sno=1, cmd_type=CMD_TYPE_SET)
    chunks = split_ble_writes(frame, mtu_payload=180)
    assert len(chunks) == 3
    assert b"".join(chunks) == frame


def test_mock_transport_end_to_end():
    async def _run():
        t = MockTransport(width=32, height=32)
        await t.connect()
        info = await t.send_frame(cmd_get_dev_info_all(sno=t.next_sno()))
        assert info["status"] == "ok"
        assert info["data"]["dev_info"].id_dev == "YS00000001"
        assert info["data"]["param_dev"].width == 32
        assert info["data"]["power"]["on"] is True
        assert info["data"]["light"]["level"] == 12

        await t.send_frame(cmd_set_brightness(5, sno=t.next_sno()))
        assert t.brightness == 5
        await t.send_frame(cmd_set_power(False, sno=t.next_sno()))
        assert t.power_on is False
        await t.disconnect()

    asyncio.run(_run())


def test_sliding_window_profiles_and_cancellation():
    async def _run():
        t = MockTransport(width=64, height=64, profile="balanced")
        await t.connect()
        assert t.profile.ble_chunk_size == 248
        assert t.profile.swnd_max == 5

        # Multi-frame GIF (>4 frames triggers sliding-window path)
        fake_gif = b"GIF89a" + b"\x00" * 5000
        frames = build_gif_program_frames(fake_gif, 64, 64, start_sno=1)
        assert len(frames) > 4
        res = await t.send_frames(frames, label="Test SWND")
        assert len(res) == len(frames)
        assert all(r["status"] == "ok" for r in res)
        assert t.transfer.percent == 100.0
        assert not t.transfer.active

        # Profile switch to fast
        t.set_profile("fast")
        assert t.profile.ble_chunk_size == 509 and t.profile.swnd_max == 10
        await t.disconnect()

    asyncio.run(_run())


def test_hardware_profiles_never_use_sliding_window():
    """CLAUDE.md constraint 2: swnd > 1 wedges the panel's BLE RX buffer."""
    from protocol import BLE_PROFILES, EXPERIMENTAL_PROFILES, BleTransport, recommend_profile
    from protocol.transport import HIGH_SPEED_PREFIXES

    assert all(p.swnd_max == 1 for p in BLE_PROFILES.values())
    for name in ("YS64L", "EXYC-A", "", "XYZ"):
        for ver in ("", "EXYC-A V1", *HIGH_SPEED_PREFIXES):
            assert BLE_PROFILES[recommend_profile(name, ver)].swnd_max == 1
    t = BleTransport()
    for name in EXPERIMENTAL_PROFILES:
        with pytest.raises(ValueError):
            t.set_profile(name)
    assert BleTransport(profile="balanced").profile.swnd_max == 1  # constructor falls back safely


def test_ota_upgrade_frames_are_refused():
    """CLAUDE.md constraint 6: cmd_type 4 (OTA) can brick the panel."""
    with pytest.raises(ValueError):
        build_frame(b"", sno=1, cmd_type=4)
