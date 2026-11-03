# SPDX-FileCopyrightText: 2026 Balazs Katona
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the windowed detection: what happens when ffmpeg cannot read a window.

Before 0.2.24 a failed ffmpeg run (exit code != 0, e.g. an I/O error on a
CIFS mount that drops for a moment) was read as "no scene change in this
window". The detection went on and returned a shorter chapter list -- in the
worst case only "Auto Chapter 1" + "Credits" -- and Generate/Update would have
written that into the file without a question. Now the movie is left out and
nothing is written.

ffmpeg itself is never started here; run_window_detection is replaced.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from auto_chapter.config import DetectionConfig
from auto_chapter.detector import windowed
from auto_chapter.detector.ffmpeg import FFmpegResult
from auto_chapter.detector.windowed import WindowMatch, build_chapter_list, iter_chapter_points
from auto_chapter.errors import MovieReadError, short_error
from auto_chapter.models import DetectionMethod
from auto_chapter.plex_client import _all_auto_chapter_names

MOVIE = Path("/mnt/nas/Filmek/Film.mkv")

# One fade to black at 312s, as blackdetect reports it (relative to the seek point).
BLACK_AT_12S = "[blackdetect @ 0x1] black_start:10.0 black_end:12.0 black_duration:2.0\n"
READ_ERROR = (
    "[in#0 @ 0x55f731996940] Error opening input: Input/output error\n"
    "Error opening input file /mnt/nas/Filmek/Film.mkv.\n"
)


def _fake_ffmpeg(results: list[FFmpegResult]):
    def run_window_detection(*_args, **_kwargs) -> FFmpegResult:
        return results.pop(0)

    return run_window_detection


def test_a_window_ffmpeg_cannot_read_stops_the_whole_movie(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        windowed.ffmpeg,
        "run_window_detection",
        _fake_ffmpeg([
            FFmpegResult(stdout="", stderr=BLACK_AT_12S, returncode=0),
            FFmpegResult(stdout="", stderr=READ_ERROR, returncode=254),
        ]),
    )

    points = iter_chapter_points(MOVIE, duration=7200, effective_end=6300, cfg=DetectionConfig())

    assert next(points).timestamp == pytest.approx(312.0)  # the good window still counts
    with pytest.raises(MovieReadError) as err:
        next(points)
    # The user sees exactly this sentence, in a batch and in Find by title.
    assert short_error(err.value) == (
        "Couldn't read the movie file, nothing was written: "
        "File system error. Check that the drive is connected and healthy."
    )


def test_ffmpeg_stopped_by_ctrl_c_is_a_cancel_not_a_read_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ctrl+C terminates the running ffmpeg (exit code != 0). That is the user
    cancelling, which has its own "Cancelled" message -- not a broken file."""
    stopped = False

    def run_window_detection(*_args, **_kwargs) -> FFmpegResult:
        nonlocal stopped
        stopped = True  # as if Ctrl+C arrived while this window ran
        return FFmpegResult(stdout="", stderr="", returncode=-15)

    monkeypatch.setattr(windowed.ffmpeg, "run_window_detection", run_window_detection)
    monkeypatch.setattr(windowed, "force_stopped", lambda: stopped)

    points = list(
        iter_chapter_points(MOVIE, duration=7200, effective_end=6300, cfg=DetectionConfig())
    )

    assert points == []


def test_the_chapter_names_written_are_the_ones_recognised_as_our_own() -> None:
    """The scans tell the app's own chapters from other sources by their names, so
    the names written must be exactly those (they were configurable until 0.2.26)."""
    chapters = build_chapter_list(
        [WindowMatch(method=DetectionMethod.BLACK, timestamp=312.0)], credits_time=6600.0
    )

    names = [c.name for c in chapters]
    assert names == ["Auto Chapter 1", "Auto Chapter 2", "Credits"]
    assert _all_auto_chapter_names([SimpleNamespace(title=n) for n in names])


# ---------------------------------------------------------------------------
# Where the windows are and what wins inside one (0.2.34)
#
# This logic decides where the chapters land in the files, and had no tests.
# ffmpeg is replaced by a script: for each window it returns what blackdetect /
# scdet would print, and every window asked for is recorded.
# ---------------------------------------------------------------------------


def _scdet(at: float, score: float) -> str:
    return f"lavfi.scd.time: {at}\nlavfi.scd.score: {score}\n"


def _black(end: float) -> str:
    return f"black_start:{end - 1} black_end:{end} black_duration:1.0\n"


class _Script:
    """Stands in for ffmpeg.run_window_detection; `outputs` are its stderr texts."""

    def __init__(self, *outputs: str) -> None:
        self.outputs = list(outputs)
        self.windows: list[tuple[float, float]] = []

    def __call__(self, _path, *, seek_seconds, window_seconds, **_kwargs) -> FFmpegResult:
        self.windows.append((seek_seconds, window_seconds))
        stderr = self.outputs.pop(0) if self.outputs else ""
        return FFmpegResult(stdout="", stderr=stderr, returncode=0)


def _points(monkeypatch: pytest.MonkeyPatch, script: _Script, effective_end: float) -> list:
    monkeypatch.setattr(windowed.ffmpeg, "run_window_detection", script)
    return list(
        iter_chapter_points(MOVIE, duration=7200, effective_end=effective_end, cfg=DetectionConfig())
    )


def test_the_next_window_starts_one_gap_after_the_last_chapter(monkeypatch) -> None:
    script = _Script(_black(20.0), _black(40.0))

    points = _points(monkeypatch, script, effective_end=1200)

    # Chapter at 300+20=320; the next search starts 300s (min gap) later, at 620.
    assert [p.timestamp for p in points] == [pytest.approx(320.0), pytest.approx(660.0)]
    assert [seek for seek, _ in script.windows][:3] == [300.0, 620.0, 960.0]


def test_a_window_without_a_match_is_followed_directly_by_the_next(monkeypatch) -> None:
    script = _Script("", "")  # nothing found anywhere

    assert _points(monkeypatch, script, effective_end=1200) == []
    # 300-600, 600-900, 900-1200: no gap left unsearched, nothing past the end.
    assert script.windows == [(300.0, 300.0), (600.0, 300.0), (900.0, 300.0)]


def test_a_fade_to_black_wins_over_a_stronger_cut_in_the_same_window(monkeypatch) -> None:
    script = _Script(_scdet(10.0, 90.0) + _black(200.0))

    points = _points(monkeypatch, script, effective_end=900)

    assert points[0].method is DetectionMethod.BLACK
    assert points[0].timestamp == pytest.approx(500.0)


def test_without_a_fade_the_strongest_cut_in_the_window_is_used(monkeypatch) -> None:
    script = _Script(_scdet(10.0, 15.0) + _scdet(150.0, 42.0) + _scdet(250.0, 30.0))

    points = _points(monkeypatch, script, effective_end=900)

    assert points[0].method is DetectionMethod.SCDET
    assert points[0].timestamp == pytest.approx(450.0)
    assert points[0].score == pytest.approx(42.0)


def test_the_last_window_stops_at_the_end_and_a_short_rest_is_skipped(monkeypatch) -> None:
    script = _Script("", "", "")

    _points(monkeypatch, script, effective_end=1000)  # 300-600, 600-900, then 100s left

    assert script.windows == [(300.0, 300.0), (600.0, 300.0), (900.0, 100.0)]

    script = _Script("", "", "")
    _points(monkeypatch, script, effective_end=920)  # only 20s after 900: not searched

    assert script.windows == [(300.0, 300.0), (600.0, 300.0)]


def test_the_progress_callback_gets_each_window_start(monkeypatch) -> None:
    script = _Script()
    starts: list[float] = []
    monkeypatch.setattr(windowed.ffmpeg, "run_window_detection", script)

    list(iter_chapter_points(MOVIE, duration=7200, effective_end=900, cfg=DetectionConfig(),
                             on_window_start=starts.append))

    assert starts == [300.0, 600.0]
