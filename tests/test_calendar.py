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


def test_finished_meeting_is_not_announced():
    ev = sf._pick_meetings([(_at(9, 0), _at(10, 0), "Done")], NOW, HORIZON, "test")
    assert ev["minutes_left"] < 0 and "current" not in ev  # stays on the day bar only
    assert sf._pick_meetings([], NOW, HORIZON, "test") is None


def _loop_ms(gif_bytes):
    im = Image.open(io.BytesIO(gif_bytes))
    total = 0
    for i in range(im.n_frames):
        im.seek(i)
        total += im.info.get("duration", 0)
    return total


def test_render_shows_both_meetings_as_pages():
    ev = {"title": "Sync", "minutes_left": 105, "source": "t", "current": {"title": "Review", "ends_in": 105}}
    # Pillow merges identical frames, so compare loop length: one page = 10 x 260 ms.
    assert _loop_ms(sf.render_calendar_clock_bg_gif(ev, now=NOW)) == 5200  # current meeting page, then next
    assert _loop_ms(sf.render_calendar_clock_bg_gif({"title": "X", "minutes_left": 5, "source": "t"})) == 2600
    # A long title scrolls until fully shown: "NOW DESIGN REVIEW" (17 chars) + 3 gap = 20 frames.
    long_ev = {"title": "", "minutes_left": -1, "source": "t", "current": {"title": "Design review", "ends_in": 30}}
    assert _loop_ms(sf.render_calendar_clock_bg_gif(long_ev, now=NOW)) == 20 * 260
    assert _loop_ms(sf.render_calendar_clock_bg_gif(None)) == 2600


def test_day_list_has_all_of_todays_meetings_including_past():
    items = [
        (_at(9, 0), _at(9, 30), "Standup"),  # already over
        (_at(10, 15), _at(12, 15), "Design review"),
        (_at(16, 0), None, "No end"),  # assumed 30 min
        (NOW - dt.timedelta(days=1), NOW - dt.timedelta(days=1, minutes=-30), "Yesterday"),
    ]
    ev = sf._pick_meetings(items, NOW, HORIZON, "test")
    assert ev["day"] == [(540, 570, "Standup"), (615, 735, "Design review"), (960, 990, "No end")]


def test_day_bar_kept_when_all_meetings_are_done():
    ev = sf._pick_meetings([(_at(8, 0), _at(9, 0), "Done")], NOW, HORIZON, "test")
    assert ev["day"] == [(480, 540, "Done")] and ev["minutes_left"] < 0 and ev["title"] == ""
    assert _loop_ms(sf.render_calendar_clock_bg_gif(ev, now=NOW)) == 2600


def test_day_bar_draws_meetings_and_now_line():
    ev = sf._pick_meetings([(_at(10, 15), _at(12, 15), "Review"), (_at(13, 0), _at(14, 0), "Sync")], NOW, HORIZON, "t")
    im = Image.open(io.BytesIO(sf.render_calendar_clock_bg_gif(ev, now=NOW))).convert("RGB")
    # 11:00 on the top bar (x = 2 + 55) is the in-progress meeting colour; 13:30 on the bottom bar is lit.
    assert im.getpixel((2 + 11 * 5, 34))[0] > 200
    assert sum(im.getpixel((2 + (90 * 5) // 60, 44))) > 150
    assert sum(im.getpixel((2 + 4 * 5, 34))) < 120  # 04:00: empty track
