#!/usr/bin/env python3
"""
LOY PLAY Independent BLE Proof-of-Concept Controller (CLI).

100% local, offline, privacy-first controller for YS / YSTP01 BLE pixel displays.
Zero cloud servers, zero accounts, zero analytics, zero location tracking.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# Ensure project root is importable when executed directly
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from protocol import (
    BleTransport,
    MockTransport,
    build_gif_program_frames,
    build_image_program_frames,
    build_text_clock_program_frames,
    build_text_program_frames,
    cmd_delete_all,
    cmd_get_dev_info_all,
    cmd_rt_draw_pixels,
    cmd_rt_draw_rect,
    cmd_set_brightness,
    cmd_set_clock_mode,
    cmd_set_countdown,
    cmd_set_mirror,
    cmd_set_power,
    cmd_set_rotation,
    cmd_set_scoreboard,
    cmd_sync_time,
    encode_solid_color_ystp01,
    load_image_as_ystp01,
)


def parse_hex_color(value: str) -> Tuple[int, int, int]:
    v = value.strip().lstrip("#")
    named = {
        "red": "ff0000",
        "green": "00ff00",
        "blue": "0000ff",
        "white": "ffffff",
        "black": "000000",
        "yellow": "ffff00",
        "cyan": "00ffff",
        "magenta": "ff00ff",
    }
    v = named.get(v.lower(), v)
    if len(v) != 6:
        raise ValueError(f"Expected 6-digit hex color or named color, got: {value}")
    return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)


def make_packet_logger(verbose: bool):
    def _log(direction: str, raw: bytes, decoded: Optional[Dict[str, Any]]) -> None:
        if not verbose:
            return
        preview = raw.hex(" ")
        if len(preview) > 120:
            preview = preview[:117] + "..."
        print(f"  [{direction}] ({len(raw)}B) {preview}")
        if decoded:
            print(f"       decoded: {decoded}")
    return _log


async def resolve_address(args: argparse.Namespace) -> str:
    if args.mock:
        return "MOCK-DISPLAY"
    if args.address:
        return args.address
    print("Scanning for YS/TL BLE pixel displays (5s)...")
    devices = await BleTransport.scan(timeout=5.0)
    if not devices:
        raise RuntimeError(
            "No YS/TL BLE display found. Power on the display and ensure it is not connected to another phone, or pass --address."
        )
    print(f"Auto-selected: {devices[0].name} ({devices[0].address}, RSSI {devices[0].rssi} dBm)")
    return devices[0].address


async def fetch_dimensions(transport, args: argparse.Namespace) -> Tuple[int, int]:
    if args.width and args.height:
        return args.width, args.height
    resp = await transport.send_frame(cmd_get_dev_info_all(sno=transport.next_sno()))
    if resp and resp.get("data") and resp["data"].get("param_dev"):
        p = resp["data"]["param_dev"]
        return p.width, p.height
    return 32, 32


async def run_command(args: argparse.Namespace) -> int:
    if args.command == "scan":
        devices = await BleTransport.scan(timeout=args.timeout, include_all=args.all)
        if not devices:
            print("No matching BLE devices found.")
            return 0
        for d in devices:
            print(f"{d.address:40s}  RSSI={d.rssi:4d} dBm  name={d.name}")
        return 0

    transport = MockTransport(width=args.width or 32, height=args.height or 32) if args.mock else BleTransport()
    transport.set_packet_logger(make_packet_logger(args.verbose))

    address = await resolve_address(args)
    await transport.connect(address)
    try:
        cmd = args.command
        if cmd == "info":
            resp = await transport.send_frame(cmd_get_dev_info_all(sno=transport.next_sno()))
            print(json.dumps(resp, default=lambda o: o.__dict__, indent=2))

        elif cmd == "power":
            on = args.state == "on"
            resp = await transport.send_frame(cmd_set_power(on, sno=transport.next_sno()))
            print(f"Power {'ON' if on else 'OFF'} -> {resp}")

        elif cmd == "brightness":
            resp = await transport.send_frame(cmd_set_brightness(args.level, sno=transport.next_sno()))
            print(f"Brightness {args.level}/15 -> {resp}")

        elif cmd == "rotate":
            resp = await transport.send_frame(cmd_set_rotation(args.degrees, sno=transport.next_sno()))
            print(f"Rotation {args.degrees} deg -> {resp}")

        elif cmd == "mirror":
            enabled = args.state == "on"
            resp = await transport.send_frame(cmd_set_mirror(enabled, sno=transport.next_sno()))
            print(f"Mirror {'ON' if enabled else 'OFF'} -> {resp}")

        elif cmd == "sync-time":
            resp = await transport.send_frame(cmd_sync_time(sno=transport.next_sno()))
            print(f"Time synced -> {resp}")

        elif cmd == "color":
            r, g, b = parse_hex_color(args.color)
            w, h = await fetch_dimensions(transport, args)
            if args.realtime:
                resp = await transport.send_frame(
                    cmd_rt_draw_rect(0, 0, w - 1, h - 1, (r, g, b), sno=transport.next_sno())
                )
                print(f"RT Fill #{r:02x}{g:02x}{b:02x} ({w}x{h}) -> {resp}")
            else:
                ystp = encode_solid_color_ystp01(w, h, r, g, b)
                frames = build_image_program_frames(
                    ystp, w, h, program_id=args.slot, start_sno=transport.next_sno()
                )
                res = await transport.send_frames(frames)
                print(f"Uploaded solid color #{r:02x}{g:02x}{b:02x} ({len(frames)} frame(s)) -> {res[-1]}")

        elif cmd == "pixel":
            r, g, b = parse_hex_color(args.color)
            resp = await transport.send_frame(
                cmd_rt_draw_pixels([(args.x, args.y)], (r, g, b), sno=transport.next_sno())
            )
            print(f"Pixel ({args.x}, {args.y}) #{r:02x}{g:02x}{b:02x} -> {resp}")

        elif cmd == "image":
            w, h = await fetch_dimensions(transport, args)
            ystp = load_image_as_ystp01(args.path, w, h, gamma=args.gamma, keep_aspect=args.keep_aspect)
            frames = build_image_program_frames(
                ystp, w, h, program_id=args.slot, start_sno=transport.next_sno()
            )
            res = await transport.send_frames(
                frames, progress_cb=lambda cur, tot: print(f"  Sent frame {cur}/{tot}", end="\r")
            )
            print(f"\nUploaded image {args.path} ({len(ystp)}B YSTP01, {len(frames)} frames) -> {res[-1]}")

        elif cmd == "gif":
            w, h = await fetch_dimensions(transport, args)
            raw_gif = Path(args.path).read_bytes()
            frames = build_gif_program_frames(
                raw_gif, w, h, program_id=args.slot, start_sno=transport.next_sno(), speed=args.speed
            )
            res = await transport.send_frames(
                frames, progress_cb=lambda cur, tot: print(f"  Sent frame {cur}/{tot}", end="\r")
            )
            print(f"\nUploaded native GIF {args.path} ({len(raw_gif)}B, {len(frames)} frames) -> {res[-1]}")

        elif cmd == "text":
            w, h = await fetch_dimensions(transport, args)
            fg = parse_hex_color(args.color)
            bg = parse_hex_color(args.bg)
            frames = build_text_program_frames(
                args.message,
                w,
                h,
                color=fg,
                bg_color=bg,
                font_size=args.size,
                scroll=args.scroll,
                speed=args.speed,
                program_id=args.slot,
                start_sno=transport.next_sno(),
            )
            res = await transport.send_frames(frames)
            print(f"Uploaded text '{args.message}' ({len(frames)} frames) -> {res[-1]}")

        elif cmd == "clock":
            resp = await transport.send_frame(cmd_set_clock_mode(fmt=args.style, sno=transport.next_sno()))
            print(f"Clock mode style={args.style} -> {resp}")

        elif cmd == "hw-clock":
            w, h = await fetch_dimensions(transport, args)
            if args.sync:
                await transport.send_frame(cmd_sync_time(sno=transport.next_sno()))
            fg = parse_hex_color(args.color)
            bg = parse_hex_color(args.bg)
            bg_res = Path(args.bg_gif).read_bytes() if args.bg_gif else None
            frames = build_text_clock_program_frames(
                args.template,
                w,
                h,
                color=fg,
                bg_color=bg,
                font_size=args.size,
                scroll=args.scroll,
                program_id=args.slot,
                start_sno=transport.next_sno(),
                background_resource=bg_res,
            )
            res = await transport.send_frames(frames)
            print(f"Uploaded standalone hardware clock '{args.template}' ({len(frames)} frames) -> {res[-1]}")

        elif cmd == "countdown":
            resp = await transport.send_frame(
                cmd_set_countdown(args.hours, args.minutes, args.seconds, sno=transport.next_sno())
            )
            print(f"Countdown {args.hours:02d}:{args.minutes:02d}:{args.seconds:02d} -> {resp}")

        elif cmd == "scoreboard":
            resp = await transport.send_frame(
                cmd_set_scoreboard(args.red, args.blue, sno=transport.next_sno())
            )
            print(f"Scoreboard {args.red} - {args.blue} -> {resp}")

        elif cmd == "delete-all":
            resp = await transport.send_frame(cmd_delete_all(sno=transport.next_sno()))
            print(f"Deleted all programs -> {resp}")

    finally:
        await transport.disconnect()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="loy_cli",
        description="Privacy-first, offline BLE controller for YS / LOY PLAY LED pixel displays.",
    )
    p.add_argument("-a", "--address", help="BLE device address / CoreBluetooth UUID")
    p.add_argument("--width", type=int, help="Override display width (auto-queried if omitted)")
    p.add_argument("--height", type=int, help="Override display height (auto-queried if omitted)")
    p.add_argument("--mock", action="store_true", help="Use in-memory MockTransport (no hardware required)")
    p.add_argument("-v", "--verbose", action="store_true", help="Log raw TX/RX BLE frames in hex")

    sub = p.add_subparsers(dest="command", required=True)

    s_scan = sub.add_parser("scan", help="Scan for nearby BLE pixel displays")
    s_scan.add_argument("--timeout", type=float, default=5.0)
    s_scan.add_argument("--all", action="store_true", help="Show all BLE peripherals, not just YS/TL")

    sub.add_parser("info", help="Query device info, dimensions, power, and brightness")

    s_pow = sub.add_parser("power", help="Turn display on or off")
    s_pow.add_argument("state", choices=["on", "off"])

    s_bri = sub.add_parser("brightness", help="Set brightness (1..15)")
    s_bri.add_argument("level", type=int)

    s_rot = sub.add_parser("rotate", help="Set rotation (0, 90, 180, 270)")
    s_rot.add_argument("degrees", type=int, choices=[0, 90, 180, 270])

    s_mir = sub.add_parser("mirror", help="Toggle horizontal mirror")
    s_mir.add_argument("state", choices=["on", "off"])

    sub.add_parser("sync-time", help="Sync display RTC to host system clock")

    s_col = sub.add_parser("color", help="Fill display with a solid color")
    s_col.add_argument("color", help="Color name (red/green/blue/...) or hex (#ff0000)")
    s_col.add_argument("--slot", type=int, default=1)
    s_col.add_argument("--realtime", action="store_true", help="Use RT_DRAW rect instead of YSTP01 program")

    s_pix = sub.add_parser("pixel", help="Draw a single pixel in real time")
    s_pix.add_argument("x", type=int)
    s_pix.add_argument("y", type=int)
    s_pix.add_argument("color", default="red")

    s_img = sub.add_parser("image", help="Upload PNG/JPG/BMP image as YSTP01")
    s_img.add_argument("path")
    s_img.add_argument("--slot", type=int, default=1)
    s_img.add_argument("--gamma", type=float, default=1.0)
    s_img.add_argument("--keep-aspect", action="store_true")

    s_gif = sub.add_parser("gif", help="Upload raw GIF file directly")
    s_gif.add_argument("path")
    s_gif.add_argument("--slot", type=int, default=1)
    s_gif.add_argument("--speed", type=int, default=9)

    s_txt = sub.add_parser("text", help="Display static or scrolling text")
    s_txt.add_argument("message")
    s_txt.add_argument("--color", default="white")
    s_txt.add_argument("--bg", default="black")
    s_txt.add_argument("--size", type=int, default=16)
    s_txt.add_argument("--scroll", choices=["left", "right", "up", "static"], default="left")
    s_txt.add_argument("--speed", type=int, default=9)
    s_txt.add_argument("--slot", type=int, default=1)

    s_clk = sub.add_parser("clock", help="Switch to built-in clock mode")
    s_clk.add_argument("--style", type=int, default=0)

    s_hwc = sub.add_parser("hw-clock", help="Upload custom standalone hardware-ticking text_clock program")
    s_hwc.add_argument("template", nargs="?", default="{hh}:{mm}:{ss}", help="Template using {hh},{mm},{ss},{YYYY},{MM},{DD},{w}")
    s_hwc.add_argument("--color", default="cyan")
    s_hwc.add_argument("--bg", default="black")
    s_hwc.add_argument("--size", type=int, default=16)
    s_hwc.add_argument("--scroll", choices=["static", "left", "right", "up"], default="static")
    s_hwc.add_argument("--bg-gif", help="Optional background GIF file path")
    s_hwc.add_argument("--slot", type=int, default=1)
    s_hwc.add_argument("--no-sync", dest="sync", action="store_false", help="Skip RTC time sync before upload")

    s_cd = sub.add_parser("countdown", help="Start built-in countdown timer")
    s_cd.add_argument("hours", type=int)
    s_cd.add_argument("minutes", type=int)
    s_cd.add_argument("seconds", type=int)

    s_sb = sub.add_parser("scoreboard", help="Update built-in scoreboard")
    s_sb.add_argument("red", type=int)
    s_sb.add_argument("blue", type=int)

    sub.add_parser("delete-all", help="Delete all stored programs on the display")
    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return asyncio.run(run_command(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
