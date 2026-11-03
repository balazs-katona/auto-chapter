# SPDX-FileCopyrightText: 2026 Balazs Katona
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the chapter preview: the words must say what the numbers really are.

The table's last column used to be "Confidence" -- but the number is ffmpeg
scdet's score, how much the picture changed between two frames (0-100 %), not
how sure the app is. And the line above the table used internal names
("black=11, scdet=4") while the table said "Fade-to-black" / "Visual cut".
"""

from pathlib import Path

from rich.console import Console

from auto_chapter.display import chapter_summary, render_chapter_table
from auto_chapter.models import Chapter, DetectionMethod, Movie

START = Chapter(name="Auto Chapter 1", timestamp=0.0, method=DetectionMethod.START)
CREDITS = Chapter(name="Credits", timestamp=7000.0, method=DetectionMethod.CREDITS)


def _black(t: float) -> Chapter:
    return Chapter(name="Auto Chapter", timestamp=t, method=DetectionMethod.BLACK)


def _cut(t: float, score: float) -> Chapter:
    return Chapter(name="Auto Chapter", timestamp=t, method=DetectionMethod.SCDET, score=score)


def test_the_table_calls_the_score_picture_change() -> None:
    console = Console(record=True, width=120)
    movie = Movie(title="Film", year=1986, duration=7200.0, local_path=Path("/m/Film.mkv"),
                  plex_path="/p/Film.mkv", chapter_count=0)

    render_chapter_table(console, movie, [START, _cut(1327.0, 17.4), CREDITS])

    text = console.export_text()
    assert "Picture change" in text
    assert "17%" in text
    assert "Confidence" not in text


def test_summary_uses_the_words_of_the_table() -> None:
    chapters = [START, *[_black(t) for t in range(11)], *[_cut(t, 15) for t in range(4)], CREDITS]

    assert chapter_summary(chapters) == (
        "17 chapters detected (11 fade-to-black, 4 visual cuts, plus opening and end credits)"
    )


def test_summary_without_end_credits_and_with_a_single_cut() -> None:
    assert chapter_summary([START, _cut(300, 20)]) == (
        "2 chapters detected (1 visual cut, plus opening)"
    )


def test_summary_when_only_the_opening_and_credits_were_found() -> None:
    assert chapter_summary([START, CREDITS]) == (
        "2 chapters detected (opening and end credits only)"
    )
