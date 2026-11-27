"""
Berlin U-Bahn / S-Bahn departures for the 64x64 panel (Transit Clock).

Data: the free, keyless BVG HAFAS wrapper https://v6.bvg.transport.rest (no account, like Open-Meteo).
Layout (64x64):
  y=4..28   left clean for the autonomous hardware `{hh}:{mm}` clock
  y=31..35  station name (dim)
  y=40/49/58 three departure rows: coloured line badge, destination, departure time (HH:MM)

Departure times are absolute (HH:MM), not "in 3 min": the panel is only redrawn every few minutes
(each redraw writes Flash), and absolute times stay correct between redraws while the clock ticks.
"""

from __future__ import annotations

import datetime
import json
import re
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw

API_BASE = "https://v6.bvg.transport.rest"
DEFAULT_PRODUCTS = ("subway", "suburban")
ALL_PRODUCTS = ("subway", "suburban", "tram", "bus", "regional", "ferry", "express")

RGB = Tuple[int, int, int]

# Official-ish BVG / S-Bahn Berlin line colours.
LINE_COLORS: Dict[str, RGB] = {
    "U1": (125, 173, 76), "U2": (218, 66, 30), "U3": (22, 104, 61), "U4": (240, 215, 34),
    "U5": (126, 83, 48), "U6": (140, 109, 171), "U7": (82, 141, 186), "U8": (34, 79, 134),
    "U9": (243, 121, 29),
    "S1": (222, 77, 164), "S2": (0, 95, 39), "S25": (0, 95, 39), "S26": (0, 95, 39),
    "S3": (10, 76, 153), "S41": (173, 89, 55), "S42": (203, 100, 24), "S45": (205, 156, 83),
    "S46": (205, 156, 83), "S47": (205, 156, 83), "S5": (235, 116, 5), "S7": (129, 109, 166),
    "S75": (129, 109, 166), "S8": (102, 170, 34), "S85": (102, 170, 34), "S9": (153, 39, 70),
}
PRODUCT_COLORS: Dict[str, RGB] = {
    "subway": (34, 79, 134), "suburban": (0, 130, 60), "tram": (190, 20, 20), "bus": (150, 40, 130),
    "regional": (200, 30, 30), "ferry": (40, 120, 200), "express": (120, 120, 120),
}

# Full 3x5 pixel font (4 px advance): 16 characters fit across 64 px.
FONT_3X5: Dict[str, Tuple[str, ...]] = {
    "A": ("010", "101", "111", "101", "101"), "B": ("110", "101", "110", "101", "110"),
    "C": ("011", "100", "100", "100", "011"), "D": ("110", "101", "101", "101", "110"),
    "E": ("111", "100", "110", "100", "111"), "F": ("111", "100", "110", "100", "100"),
    "G": ("011", "100", "101", "101", "011"), "H": ("101", "101", "111", "101", "101"),
    "I": ("111", "010", "010", "010", "111"), "J": ("001", "001", "001", "101", "010"),
    "K": ("101", "101", "110", "101", "101"), "L": ("100", "100", "100", "100", "111"),
    "M": ("101", "111", "111", "101", "101"), "N": ("110", "101", "101", "101", "101"),
    "O": ("010", "101", "101", "101", "010"), "P": ("110", "101", "110", "100", "100"),
    "Q": ("010", "101", "101", "110", "011"), "R": ("110", "101", "110", "101", "101"),
    "S": ("011", "100", "010", "001", "110"), "T": ("111", "010", "010", "010", "010"),
    "U": ("101", "101", "101", "101", "111"), "V": ("101", "101", "101", "101", "010"),
    "W": ("101", "101", "111", "111", "101"), "X": ("101", "101", "010", "101", "101"),
    "Y": ("101", "101", "010", "010", "010"), "Z": ("111", "001", "010", "100", "111"),
    "0": ("111", "101", "101", "101", "111"), "1": ("010", "110", "010", "010", "111"),
    "2": ("111", "001", "111", "100", "111"), "3": ("111", "001", "111", "001", "111"),
    "4": ("101", "101", "111", "001", "001"), "5": ("111", "100", "111", "001", "111"),
    "6": ("111", "100", "111", "101", "111"), "7": ("111", "001", "001", "010", "010"),
    "8": ("111", "101", "111", "101", "111"), "9": ("111", "101", "111", "001", "111"),
    ":": ("000", "010", "000", "010", "000"), "-": ("000", "000", "111", "000", "000"),
    ".": ("000", "000", "000", "000", "010"), "/": ("001", "001", "010", "100", "100"),
    "+": ("000", "010", "111", "010", "000"), "!": ("010", "010", "010", "000", "010"),
    "(": ("010", "100", "100", "100", "010"), ")": ("010", "001", "001", "001", "010"),
    " ": ("000", "000", "000", "000", "000"),
}

_UMLAUTS = str.maketrans({"Ä": "AE", "Ö": "OE", "Ü": "UE", "ä": "AE", "ö": "OE", "ü": "UE", "ß": "SS", "é": "E", "è": "E"})


def text_width(text: str) -> int:
    return max(0, len(text) * 4 - 1)


def draw_small(img: Image.Image, text: str, x: int, y: int, color: RGB) -> int:
    """Draw 3x5 text; returns the x after the last glyph."""
    px = img.load()
    w, h = img.size
    cx = x
    for ch in text:
        glyph = FONT_3X5.get(ch, FONT_3X5[" "])
        for ry, row in enumerate(glyph):
            for rx, bit in enumerate(row):
                if bit == "1" and 0 <= cx + rx < w and 0 <= y + ry < h:
                    px[cx + rx, y + ry] = color
        cx += 4
    return cx


def clean_name(name: str) -> str:
    """'S+U Alexanderplatz Bhf (Berlin)' -> 'ALEXANDERPLATZ'; 'U Turmstr.' -> 'TURMSTR'."""
    s = str(name or "").translate(_UMLAUTS)
    s = re.sub(r"\((Berlin|Bln)\)", "", s, flags=re.I)
    s = re.sub(r"^\s*(S\+U|U\+S|S|U)\s+", "", s)
    s = re.sub(r"\b(Bhf|Bahnhof)\b", "", s, flags=re.I)
    s = s.upper().replace(".", "")
    s = re.sub(r"[^A-Z0-9 :/+()\-]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _get_json(url: str, timeout: float = 8.0) -> Any:
    from app.smart_features import _build_ssl_context  # Keychain / corporate CA aware, like the weather clock

    req = urllib.request.Request(url, headers={"User-Agent": "LOY-Studio (offline LED display app)"})
    with urllib.request.urlopen(req, timeout=timeout, context=_build_ssl_context()) as r:
        return json.loads(r.read(2 * 1024 * 1024).decode("utf-8"))


def search_stops(query: str, limit: int = 8) -> List[Dict[str, Any]]:
    """Stations matching `query` -> [{id, name, products: [...]}] (stops only, no addresses/POIs)."""
    q = str(query or "").strip()
    if len(q) < 2:
        return []
    params = urllib.parse.urlencode({"query": q, "results": max(1, min(int(limit), 15)), "poi": "false", "addresses": "false", "fuzzy": "true"})
    data = _get_json(f"{API_BASE}/locations?{params}")
    out = []
    for loc in data if isinstance(data, list) else []:
        if loc.get("type") not in ("stop", "station") or not loc.get("id"):
            continue
        prods = [k for k, v in (loc.get("products") or {}).items() if v]
        out.append({"id": str(loc["id"]), "name": str(loc.get("name") or ""), "products": prods})
    return out


def _parse_when(raw: Optional[str]) -> Optional[datetime.datetime]:
    if not raw:
        return None
    try:
        return datetime.datetime.fromisoformat(str(raw)).astimezone().replace(tzinfo=None)
    except ValueError:
        return None


def parse_departures(
    data: Any, *, products: Sequence[str] = DEFAULT_PRODUCTS, direction_filter: str = "",
    now: Optional[datetime.datetime] = None, min_ahead_min: int = 0,
) -> List[Dict[str, Any]]:
    """Normalise a /departures response: soonest first, filtered by product and direction."""
    now = now or datetime.datetime.now()
    deps = data.get("departures", []) if isinstance(data, dict) else (data or [])
    want = {p for p in products if p in ALL_PRODUCTS} or set(DEFAULT_PRODUCTS)
    needles = [n.strip().upper() for n in str(direction_filter or "").split(",") if n.strip()]
    out = []
    for d in deps:
        line = d.get("line") or {}
        product = str(line.get("product") or "")
        if product not in want:
            continue
        cancelled = bool(d.get("cancelled"))
        when = _parse_when(d.get("when")) or _parse_when(d.get("plannedWhen"))
        if when is None or when < now + datetime.timedelta(minutes=min_ahead_min):
            continue
        direction = clean_name(d.get("direction") or "")
        if needles and not any(n.translate(_UMLAUTS) in direction for n in needles):
            continue
        delay = d.get("delay")
        out.append({
            "line": str(line.get("name") or "?").replace(" ", "").upper()[:3],
            "product": product,
            "direction": direction,
            "when": when,
            "time": when.strftime("%H:%M"),
            "delay_min": int(round(delay / 60)) if isinstance(delay, (int, float)) else 0,
            "cancelled": cancelled,
        })
    out.sort(key=lambda x: x["when"])
    return out


def fetch_departures(
    stop_id: str, *, products: Sequence[str] = DEFAULT_PRODUCTS, direction_filter: str = "",
    duration_min: int = 45, results: int = 30,
) -> List[Dict[str, Any]]:
    sid = re.sub(r"[^0-9A-Za-z:]", "", str(stop_id or ""))
    if not sid:
        raise ValueError("Pick a station first")
    want = {p for p in products if p in ALL_PRODUCTS} or set(DEFAULT_PRODUCTS)
    params = {"duration": int(duration_min), "results": int(results), "remarks": "false", "linesOfStops": "false"}
    params.update({p: ("true" if p in want else "false") for p in ALL_PRODUCTS})
    data = _get_json(f"{API_BASE}/stops/{urllib.parse.quote(sid)}/departures?{urllib.parse.urlencode(params)}")
    return parse_departures(data, products=products, direction_filter=direction_filter, min_ahead_min=1)


def line_color(line: str, product: str) -> RGB:
    return LINE_COLORS.get(line, PRODUCT_COLORS.get(product, (90, 90, 90)))


def render_transit_bg_gif(station: str, departures: List[Dict[str, Any]], size: int = 64, rows: int = 3) -> bytes:
    """Transit Clock background (bottom half). Single frame: nothing animates, so the GIF stays tiny."""
    from app.smart_features import _frames_to_gif

    img = Image.new("RGB", (size, size), (0, 0, 0))
    d = ImageDraw.Draw(img)
    name = clean_name(station)[:16]
    draw_small(img, name, max(0, (size - text_width(name)) // 2), 31, (110, 120, 140))

    if not departures:
        msg = "NO TRAINS"
        draw_small(img, msg, (size - text_width(msg)) // 2, 47, (120, 120, 120))
        return _frames_to_gif([img], duration_ms=1000)

    for i, dep in enumerate(departures[:rows]):
        y = 40 + i * 9
        line = dep["line"][:3]
        badge_w = text_width(line) + 3
        d.rectangle([0, y - 1, badge_w - 1, y + 5], fill=line_color(line, dep["product"]))
        draw_small(img, line, 2, y, (255, 255, 255))

        if dep.get("cancelled"):
            t, tcol = "AUSF", (255, 55, 70)  # Ausfall (cancelled)
        else:
            t = dep["time"]
            tcol = (255, 190, 60) if dep.get("delay_min", 0) >= 3 else (255, 255, 255)
        tx = size - text_width(t)
        draw_small(img, t, tx, y, tcol)

        room = (tx - 2 - (badge_w + 2) + 1) // 4
        draw_small(img, dep["direction"][: max(0, room)], badge_w + 2, y, (190, 200, 215))
    return _frames_to_gif([img], duration_ms=1000)


def stops_nearby(
    latitude: float, longitude: float, *, products: Sequence[str] = DEFAULT_PRODUCTS, limit: int = 5, radius_m: int = 1500,
) -> List[Dict[str, Any]]:
    """Closest stations to a point that serve `products` -> [{id, name, distance_m, walk_min, products}].

    Coordinates are sent to the BVG API only for this lookup and never stored (only the chosen stop is saved).
    """
    lat, lon = float(latitude), float(longitude)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("Invalid location")
    params = urllib.parse.urlencode({
        "latitude": f"{lat:.5f}", "longitude": f"{lon:.5f}", "results": 25, "distance": int(radius_m),
        "linesOfStops": "false", "poi": "false", "stops": "true",
    })
    data = _get_json(f"{API_BASE}/locations/nearby?{params}")
    want = {p for p in products if p in ALL_PRODUCTS} or set(DEFAULT_PRODUCTS)
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for loc in data if isinstance(data, list) else []:
        prods = [k for k, v in (loc.get("products") or {}).items() if v]
        if loc.get("type") not in ("stop", "station") or not loc.get("id") or not (want & set(prods)):
            continue
        key = clean_name(loc.get("name") or "")
        if key in seen:  # "S+U Alexanderplatz" and its tram platforms are one place for the user
            continue
        seen.add(key)
        dist = int(loc.get("distance") or 0)
        out.append({
            "id": str(loc["id"]), "name": str(loc.get("name") or ""), "products": prods,
            "distance_m": dist, "walk_min": max(1, round(dist / 80)),  # ~4.8 km/h
        })
    out.sort(key=lambda x: x["distance_m"])
    return out[: max(1, int(limit))]


def stops_near_address(address: str, *, products: Sequence[str] = DEFAULT_PRODUCTS) -> Dict[str, Any]:
    """Geocode a street address in the BVG area, then return the closest stations to it."""
    q = str(address or "").strip()
    if len(q) < 3:
        raise ValueError("Type your street and house number")
    params = urllib.parse.urlencode({"query": q, "results": 1, "addresses": "true", "poi": "false", "stops": "false"})
    data = _get_json(f"{API_BASE}/locations?{params}")
    hit = next((x for x in (data if isinstance(data, list) else []) if x.get("latitude") is not None), None)
    if not hit:
        raise ValueError(f"Address not found: {q}")
    return {
        "address": str(hit.get("address") or hit.get("name") or q),
        "stops": stops_nearby(hit["latitude"], hit["longitude"], products=products),
    }
