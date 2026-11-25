"""
Decoder for device -> host BLE notification responses (module 3341).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .constants import (
    RESP_TYPE_GET_ACK,
    RESP_TYPE_SET_ACK,
    RESP_TYPE_UPGRADE_ACK,
    RESP_TYPE_UPLOAD,
    TLV,
)
from .framing import ParsedPacket, decode_tlv_length


@dataclass
class DeviceInfo:
    id_dev: str
    version: str
    model: str
    expend: str = ""
    cid: str = ""
    diy: str = ""
    other: List[str] = field(default_factory=list)


@dataclass
class DeviceParams:
    width: int
    height: int
    type_color: Optional[int] = None
    gray: Optional[int] = None


def _decode_tlv_value(tag: int, value: bytes) -> Dict[str, Any]:
    if tag == TLV.DEV_INFO:
        text = value.decode("ascii", errors="replace")
        parts = text.split(",")
        if len(parts) >= 4:
            info = DeviceInfo(
                id_dev=parts[0],
                version=f"{parts[1]}_{parts[2]}",
                model=parts[3],
                expend=parts[4] if len(parts) > 4 else "",
                cid=parts[5] if len(parts) > 5 else "",
            )
            if len(parts) > 6:
                for extra in parts[6:]:
                    if extra.endswith("]"):
                        info.diy = extra
                    else:
                        info.other.append(extra)
            return {"dev_info": info}
        return {"dev_info_raw": text}

    if tag == TLV.PARAM_DEV:
        if len(value) >= 5:
            sub = value[0]
            w, h = struct.unpack_from("<HH", value, 1)
            if sub == 0 and len(value) >= 16:
                type_color = max(1, value[7])
                gray = value[8] - 1
                return {"param_dev": DeviceParams(width=w, height=h, type_color=type_color, gray=gray)}
            return {"param_dev": DeviceParams(width=w, height=h)}
        return {"param_dev": None}

    if tag == TLV.POWER:
        if len(value) >= 2 and value[0] == 0:
            return {"power": {"on": bool(value[1]), "type": value[1]}}
        return {"power_raw": value.hex()}

    if tag == TLV.LIGHT:
        if len(value) >= 2 and value[0] == 0:
            return {"light": {"level": value[1]}}
        return {"light_raw": value.hex()}

    if tag == TLV.ROTATE and len(value) >= 1:
        return {"rotate": value[0] * 90}

    if tag == TLV.MIRROR and len(value) >= 1:
        return {"mirror": bool(value[0])}

    if tag == TLV.PGM_PLAY and len(value) >= 2:
        return {
            "pgm_play": {
                "model": value[0],
                "index": value[1],
                "ids_pro": [b + 1 for b in value[2:]],
            }
        }

    return {f"tlv_{tag}": value.hex()}


def decode_response(pkt: ParsedPacket) -> Dict[str, Any]:
    """Decode a validated ParsedPacket into a structured dictionary."""
    result: Dict[str, Any] = {
        "sno": pkt.sno,
        "cmd_type": pkt.cmd_type,
        "raw_hex": pkt.raw.hex(),
    }

    if pkt.cmd_type in (RESP_TYPE_SET_ACK, RESP_TYPE_UPGRADE_ACK):
        if not pkt.payload:
            result["status"] = "failed"
            result["errcode"] = 255
        elif pkt.payload[0] == 0:
            result["status"] = "ok"
        else:
            result["status"] = "failed"
            result["errcode"] = pkt.payload[0]
        return result

    if pkt.cmd_type in (RESP_TYPE_GET_ACK, RESP_TYPE_UPLOAD):
        pos = 0
        items: Dict[str, Any] = {}
        while pos < len(pkt.payload):
            tag = pkt.payload[pos]
            pos += 1
            if pos >= len(pkt.payload):
                break
            length, consumed = decode_tlv_length(pkt.payload, pos)
            pos += consumed
            val = pkt.payload[pos : pos + length]
            pos += length
            items.update(_decode_tlv_value(tag, val))
        result["status"] = "ok"
        result["data"] = items
        if pkt.cmd_type == RESP_TYPE_UPLOAD:
            result["unsolicited"] = True
        return result

    result["status"] = "unknown"
    result["payload_hex"] = pkt.payload.hex()
    return result
