"""Transit Clock: BVG departures parsing, nearby stations and the panel drawing (no network)."""
import datetime as dt
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image

from app import transit as t

NOW = dt.datetime(2026, 11, 27, 14, 0)


def _dep(line, product, direction, hhmm, delay=0, cancelled=None):
    # Offset-aware time in the machine's own zone (CI runs on UTC), like real API answers parsed locally.
    h, m = map(int, hhmm.split(":"))
    iso = NOW.replace(hour=h, minute=m).astimezone().isoformat()
    return {
        "when": iso, "plannedWhen": iso,
        "delay": delay, "direction": direction, "cancelled": cancelled,
        "line": {"name": line, "product": product},
    }


def test_clean_name():
    assert t.clean_name("S+U Alexanderplatz Bhf (Berlin)") == "ALEXANDERPLATZ"
    assert t.clean_name("U Turmstr.") == "TURMSTR"
    assert t.clean_name("S Schöneweide") == "SCHOENEWEIDE"


def test_parse_filters_products_direction_and_past(monkeypatch):
    data = {"departures": [
        _dep("M5", "tram", "Turmstr.", "14:05"),
        _dep("U8", "subway", "S+U Wittenau (Berlin)", "14:07", delay=180),
        _dep("S9", "suburban", "Flughafen BER", "14:04"),
        _dep("U2", "subway", "Pankow", "13:58"),  # already gone
        _dep("U8", "subway", "Hermannstr.", "14:09", cancelled=True),
    ]}
    out = t.parse_departures(data, now=NOW, products=("subway", "suburban"))
    assert [(d["line"], d["time"]) for d in out] == [("S9", "14:04"), ("U8", "14:07"), ("U8", "14:09")]
    assert out[1]["delay_min"] == 3 and out[2]["cancelled"]
    only = t.parse_departures(data, now=NOW, direction_filter="wittenau")
    assert [d["direction"] for d in only] == ["WITTENAU"]


def test_stops_nearby_filters_and_dedupes(monkeypatch):
    fake = [
        {"type": "stop", "id": "1", "name": "S+U Alexanderplatz Bhf (Berlin)", "distance": 692, "products": {"subway": True, "suburban": True}},
        {"type": "stop", "id": "2", "name": "S+U Alexanderplatz Bhf (Berlin)", "distance": 720, "products": {"subway": True}},
        {"type": "stop", "id": "3", "name": "Tram only (Berlin)", "distance": 50, "products": {"tram": True}},
        {"type": "stop", "id": "4", "name": "U Rosa-Luxemburg-Platz (Berlin)", "distance": 283, "products": {"subway": True}},
    ]
    monkeypatch.setattr(t, "_get_json", lambda url, timeout=8.0: fake)
    out = t.stops_nearby(52.5, 13.4)
    assert [(s["id"], s["walk_min"]) for s in out] == [("4", 4), ("1", 9)]


def test_render_draws_badges_and_times():
    deps = t.parse_departures({"departures": [
        _dep("U8", "subway", "Wittenau", "14:07"), _dep("S9", "suburban", "Flughafen BER", "14:09", delay=300),
    ]}, now=NOW)
    im = Image.open(io.BytesIO(t.render_transit_bg_gif("U Hermannplatz (Berlin)", deps))).convert("RGB")
    assert im.getpixel((0, 40)) == t.LINE_COLORS["U8"]  # U8 badge, row 1
    assert im.getpixel((0, 49)) == t.LINE_COLORS["S9"]  # S9 badge, row 2
    assert all(im.getpixel((x, y)) == (0, 0, 0) for x in range(64) for y in range(0, 29))  # clock area untouched
    empty = Image.open(io.BytesIO(t.render_transit_bg_gif("X", []))).convert("RGB")
    assert any(empty.getpixel((x, 48)) != (0, 0, 0) for x in range(64))  # "NO TRAINS"
