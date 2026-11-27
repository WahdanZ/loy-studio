"""Calendar clock: a meeting in progress stays on screen until it ends; the next one follows it."""
import datetime as dt
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image

from app import smart_features as sf

NOW = dt.datetime(2026, 11, 27, 10, 30)
HORIZON = NOW + dt.timedelta(hours=8)


def _at(h, m):
    return NOW.replace(hour=h, minute=m)


def test_meeting_in_progress_is_kept_with_next_meeting():
    items = [
        (_at(12, 15), _at(13, 0), "Later sync"),
        (_at(10, 15), _at(12, 15), "Design review"),  # 2-hour meeting, started 15 min ago
    ]
    ev = sf._pick_meetings(items, NOW, HORIZON, "test")
    assert ev["current"] == {"title": "Design review", "ends_in": 105}
    assert ev["title"] == "Later sync" and ev["minutes_left"] == 105


def test_earliest_upcoming_wins_regardless_of_order():
    items = [(_at(12, 15), None, "Later"), (_at(10, 45), None, "Soon")]
    ev = sf._pick_meetings(items, NOW, HORIZON, "test")
    assert ev["title"] == "Soon" and ev["minutes_left"] == 15 and "current" not in ev


def test_only_in_progress_never_triggers_start_alert():
    ev = sf._pick_meetings([(_at(10, 0), _at(11, 0), "Standup")], NOW, HORIZON, "test")
    assert ev["current"]["ends_in"] == 30
    assert ev["minutes_left"] < 0  # the alert loop checks 0 <= minutes_left <= 5


def test_finished_meeting_is_ignored():
    assert sf._pick_meetings([(_at(9, 0), _at(10, 0), "Done")], NOW, HORIZON, "test") is None


def _loop_ms(gif_bytes):
    im = Image.open(io.BytesIO(gif_bytes))
    total = 0
    for i in range(im.n_frames):
        im.seek(i)
        total += im.info.get("duration", 0)
    return total


def test_render_shows_both_meetings_as_pages():
    ev = {"title": "Later sync", "minutes_left": 105, "source": "t", "current": {"title": "Design review", "ends_in": 105}}
    # Pillow merges identical frames, so compare loop length: one page = 10 x 260 ms.
    assert _loop_ms(sf.render_calendar_clock_bg_gif(ev)) == 5200  # current meeting page, then next
    assert _loop_ms(sf.render_calendar_clock_bg_gif({"title": "X", "minutes_left": 5, "source": "t"})) == 2600
    assert _loop_ms(sf.render_calendar_clock_bg_gif(None)) == 2600
