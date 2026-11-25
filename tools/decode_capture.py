#!/usr/bin/env python3
"""
Decode raw hex dumps of YS / YSTP01 BLE packets (from HCI snoop / nRF Connect).

Usage:
  python3 tools/decode_capture.py "aa 55 ff ff 0a 00 01 00 c1 02 06 02 00 0f ..."
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from protocol import PacketParser, decode_response, decode_tlv_length, TLV


def describe_tlvs(payload: bytes) -> list[dict]:
    out = []
    pos = 0
    while pos < len(payload):
        tag = payload[pos]
        pos += 1
        if pos >= len(payload):
            break
        length, consumed = decode_tlv_length(payload, pos)
        pos += consumed
        val = payload[pos : pos + length]
        pos += length
        try:
            name = TLV(tag).name
        except ValueError:
            name = f"UNKNOWN_{tag}"
        out.append({"tag": tag, "name": name, "len": length, "value_hex": val.hex(" ")})
    return out


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: decode_capture.py <hex-string | file.hex>", file=sys.stderr)
        return 1
    arg = sys.argv[1]
    raw_text = Path(arg).read_text() if Path(arg).is_file() else " ".join(sys.argv[1:])
    clean = "".join(c for c in raw_text if c in "0123456789abcdefABCDEF")
    data = bytes.fromhex(clean)

    parser = PacketParser()
    packets = parser.feed(data)
    if not packets:
        print("No valid YS frames (AA 55 ... with valid checksum) found.")
        return 1

    for idx, pkt in enumerate(packets):
        print(f"=== Frame #{idx + 1} (len={len(pkt.raw)}B, sno={pkt.sno}, flags=0x{pkt.flags:02X}, cmd_type={pkt.cmd_type}) ===")
        if pkt.cmd_type in (130, 131, 132, 6):
            print("  Response:", json.dumps(decode_response(pkt), default=lambda o: o.__dict__))
        else:
            print("  TLVs:", json.dumps(describe_tlvs(pkt.payload), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
