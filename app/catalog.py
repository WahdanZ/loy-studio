"""
Single source of truth for the built-in GIF pack: display name, emoji, category and a
one-line description for every GIF in `gifs/`. The server uses it for "Now Playing" titles
and the Gallery; the UI reads it from /api/gif_pack (never parses file names itself).
"""

from __future__ import annotations

import re
from typing import Dict, List, TypedDict


class GifMeta(TypedDict):
    label: str
    emoji: str
    category: str
    blurb: str


#: Gallery filter order (id, label)
CATEGORIES: List[tuple[str, str]] = [
    ("work", "Work"),
    ("chill", "Chill"),
    ("fun", "Fun"),
    ("seasonal", "Seasonal"),
]

GIF_CATALOG: Dict[str, GifMeta] = {
    # ---- original pixel art ----
    "01_cyber_eyes.gif": {"label": "Cyber Eyes", "emoji": "👀", "category": "fun", "blurb": "Glowing robot eyes that look around"},
    "02_pulsing_heart.gif": {"label": "Pulsing Heart", "emoji": "❤️", "category": "fun", "blurb": "A beating pixel heart"},
    "03_matrix_rain.gif": {"label": "Matrix Rain", "emoji": "🟩", "category": "chill", "blurb": "Falling green code"},
    "04_retro_fire.gif": {"label": "Retro Fire", "emoji": "🔥", "category": "chill", "blurb": "Cozy pixel flames"},
    "05_rainbow_wave.gif": {"label": "Rainbow Wave", "emoji": "🌈", "category": "chill", "blurb": "Smooth colour waves"},
    "06_pacman_chomp.gif": {"label": "Chomp", "emoji": "🟡", "category": "fun", "blurb": "Retro arcade chomper"},
    "07_equalizer_bars.gif": {"label": "Equalizer", "emoji": "🎚️", "category": "fun", "blurb": "Bouncing music bars"},
    "08_starfield_warp.gif": {"label": "Starfield Warp", "emoji": "✨", "category": "chill", "blurb": "Flying through stars"},
    # ---- work modes ----
    "09_mode_deep_focus.gif": {"label": "Deep Focus", "emoji": "🎯", "category": "work", "blurb": "Heads-down, please don't interrupt"},
    "10_mode_coding.gif": {"label": "Coding", "emoji": "👨‍💻", "category": "work", "blurb": "Terminal with live typing"},
    "11_mode_on_air_meeting.gif": {"label": "On Air", "emoji": "🔴", "category": "work", "blurb": "In a call or recording"},
    "12_mode_do_not_disturb.gif": {"label": "Do Not Disturb", "emoji": "⛔", "category": "work", "blurb": "Busy right now"},
    "13_mode_coffee_break.gif": {"label": "Coffee Break", "emoji": "☕", "category": "work", "blurb": "Steaming cup, back soon"},
    "14_mode_pomodoro.gif": {"label": "Pomodoro", "emoji": "🍅", "category": "work", "blurb": "Focus sprint in progress"},
    "15_mode_gaming_chill.gif": {"label": "Gaming", "emoji": "🎮", "category": "fun", "blurb": "Game time"},
    "16_mode_music_vibe.gif": {"label": "Lo-Fi Vibe", "emoji": "🎧", "category": "chill", "blurb": "Music and good vibes"},
    "17_mode_away_brb.gif": {"label": "Be Right Back", "emoji": "⏳", "category": "work", "blurb": "Stepped away"},
    "18_mode_done_offline.gif": {"label": "Done for Today", "emoji": "🌙", "category": "work", "blurb": "Offline, see you tomorrow"},
    # ---- work modes pack 2 ----
    "19_mode_lunch.gif": {"label": "Lunch", "emoji": "🍽️", "category": "work", "blurb": "Out for lunch"},
    "20_mode_workout.gif": {"label": "Workout", "emoji": "🏋️", "category": "work", "blurb": "Training, back after"},
    "21_mode_reading.gif": {"label": "Reading", "emoji": "📖", "category": "work", "blurb": "Quiet reading time"},
    "22_mode_sleep.gif": {"label": "Sleep", "emoji": "😴", "category": "chill", "blurb": "Moon and floating Zzz"},
    "23_mode_meeting_soon.gif": {"label": "Meeting Soon", "emoji": "📅", "category": "work", "blurb": "A meeting is about to start"},
    "24_mode_recording.gif": {"label": "Recording", "emoji": "⏺️", "category": "work", "blurb": "Camera rolling, keep quiet"},
    "25_mode_out_of_office.gif": {"label": "Out of Office", "emoji": "🌴", "category": "work", "blurb": "On holiday"},
    "26_mode_on_my_way.gif": {"label": "On My Way", "emoji": "🚗", "category": "work", "blurb": "Commuting"},
    "27_mode_ship_it.gif": {"label": "Ship It", "emoji": "🚀", "category": "work", "blurb": "Release went out"},
    "28_mode_build_failed.gif": {"label": "Build Failed", "emoji": "❌", "category": "work", "blurb": "Something broke"},
    # ---- scenes ----
    "29_aquarium.gif": {"label": "Aquarium", "emoji": "🐠", "category": "chill", "blurb": "Fish, bubbles and seaweed"},
    "30_rain_window.gif": {"label": "Rainy Window", "emoji": "🌧️", "category": "chill", "blurb": "Rain over city lights"},
    "31_sleeping_cat.gif": {"label": "Sleeping Cat", "emoji": "🐈", "category": "chill", "blurb": "A cat napping"},
    "32_ocean_waves.gif": {"label": "Ocean Sunset", "emoji": "🌊", "category": "chill", "blurb": "Waves under a setting sun"},
    "33_cloudy_sky.gif": {"label": "Cloudy Sky", "emoji": "⛅", "category": "chill", "blurb": "Clouds drifting by"},
    "34_falling_blocks.gif": {"label": "Falling Blocks", "emoji": "🧱", "category": "fun", "blurb": "Colour blocks stacking up"},
    # ---- seasonal ----
    "35_ramadan_lantern.gif": {"label": "Ramadan Lantern", "emoji": "🏮", "category": "seasonal", "blurb": "Swinging fanous under a crescent"},
    "36_eid_mubarak.gif": {"label": "Eid Mubarak", "emoji": "🌙", "category": "seasonal", "blurb": "Crescent and sparkles"},
    "37_new_year.gif": {"label": "New Year", "emoji": "🎆", "category": "seasonal", "blurb": "Fireworks"},
    "38_birthday.gif": {"label": "Birthday", "emoji": "🎂", "category": "seasonal", "blurb": "Cake with flickering candles"},
    "39_winter.gif": {"label": "Winter", "emoji": "⛄", "category": "seasonal", "blurb": "Snowman in falling snow"},
    "40_halloween.gif": {"label": "Halloween", "emoji": "🎃", "category": "seasonal", "blurb": "Glowing pumpkin and bats"},
}


def pretty_name(filename: str) -> str:
    """Fallback name for GIFs not in the catalog (user-saved GIFs)."""
    stem = re.sub(r"\.gif$", "", filename, flags=re.I)
    stem = re.sub(r"^\d+_", "", stem)
    stem = re.sub(r"^mode_", "", stem)
    return stem.replace("_", " ").replace("-", " ").strip().title() or filename


def gif_meta(filename: str) -> GifMeta:
    meta = GIF_CATALOG.get(filename)
    if meta:
        return meta
    return {"label": pretty_name(filename), "emoji": "🖼️", "category": "mine", "blurb": "Saved by you"}
