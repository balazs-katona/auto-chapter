# SPDX-FileCopyrightText: 2026 Balazs Katona
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for Orchestrator.detect_movie: when a movie is skipped, and where the
search for chapters stops before the end credits.

This decides what gets written into the files, and had no tests (0.2.34).
The credits lookup and the window search are replaced by stand-ins, so neither
ffmpeg nor Plex is involved; the stand-in records the scan boundary it got.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from auto_chapter import orchestrator
from auto_chapter.config import Config, PlexConfig
from auto_chapter.detector.windowed import WindowMatch
from auto_chapter.models import CreditsSource, DetectionMethod, Movie
from auto_chapter.orchestrator import Orchestrator


def _movie(minutes: float = 120) -> Movie:
    return Movie(title="Film", year=1986, duration=minutes * 60, local_path=Path("/m/Film.mkv"),
                 plex_path="/p/Film.mkv", chapter_count=0)


@pytest.fixture
def run(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """`run.credits` is what the credits lookup finds (seconds or None),
    `run.matches` what the window search yields, `run.streaming` whether Plex
    reports a session; `run.ends` records each scan boundary that was used."""
    state = SimpleNamespace(credits=None, matches=[], streaming=False, ends=[], stopped=False)

    def resolve_credits(_path, _plex_movie):
        if state.credits is None:
            return None, CreditsSource.NONE
        return state.credits, CreditsSource.PLEX

    def iter_chapter_points(_path, *, duration, effective_end, cfg, on_window_start):
        state.ends.append(effective_end)
        yield from state.matches

    monkeypatch.setattr(orchestrator.credits, "resolve_credits", resolve_credits)
    monkeypatch.setattr(orchestrator, "iter_chapter_points", iter_chapter_points)
    monkeypatch.setattr(orchestrator, "force_stopped", lambda: state.stopped)

    config = Config(plex=PlexConfig(url="http://plex:32400", token="t"), paths=[])
    plex = SimpleNamespace(has_active_stream=lambda _mv: state.streaming)
    state.orch = Orchestrator(config, plex)
    return state


def _names(result) -> list[tuple[str, float]]:
    return [(c.name, c.timestamp) for c in result.chapters]


def test_with_end_credits_the_search_stops_one_gap_before_them(run) -> None:
    run.credits = 6600.0
    run.matches = [WindowMatch(method=DetectionMethod.BLACK, timestamp=312.0)]

    result = run.orch.detect_movie(_movie())

    assert run.ends == [6300.0]  # credits - min gap (300 s)
    assert _names(result) == [("Auto Chapter 1", 0.0), ("Auto Chapter 2", 312.0),
                              ("Credits", 6600.0)]


def test_without_end_credits_the_last_15_minutes_are_left_out(run) -> None:
    result = run.orch.detect_movie(_movie(minutes=120))

    assert run.ends == [7200.0 - 900.0]  # duration - end margin
    assert _names(result) == [("Auto Chapter 1", 0.0)]  # and no Credits chapter


def test_a_movie_being_watched_is_not_scanned(run, capsys) -> None:
    run.streaming = True

    result = run.orch.detect_movie(_movie())

    assert result.chapters == []
    assert run.ends == []
    assert "active or paused Plex session" in capsys.readouterr().out


def test_a_movie_shorter_than_the_minimum_is_not_scanned(run, capsys) -> None:
    result = run.orch.detect_movie(_movie(minutes=20))  # minimum: 30

    assert result.chapters == []
    assert run.ends == []
    assert "shorter than 30 minutes" in capsys.readouterr().out


def test_credits_too_early_to_leave_anything_to_scan_skip_the_movie(run, capsys) -> None:
    run.credits = 500.0  # 500 - 300 = 200 s, not more than one gap

    result = run.orch.detect_movie(_movie())

    assert result.chapters == []
    assert run.ends == []
    assert "not enough scannable content" in capsys.readouterr().out


def test_a_cancelled_detection_returns_no_chapters(run) -> None:
    """A partial list must never reach the write step."""
    run.credits = 6600.0
    run.matches = [WindowMatch(method=DetectionMethod.BLACK, timestamp=312.0)]
    run.stopped = True

    assert run.orch.detect_movie(_movie()).chapters == []


# --- write_movie: the last checks before the file is touched ---------------


@pytest.fixture
def writes(monkeypatch: pytest.MonkeyPatch, run: SimpleNamespace) -> list:
    """Every call that would really write a file is recorded here instead."""
    calls: list = []

    def write_chapters_and_remux(path, chapters, *, language):
        calls.append((path, [c.name for c in chapters], language))
        return SimpleNamespace(success=True, error=None)

    monkeypatch.setattr(orchestrator, "write_chapters_and_remux", write_chapters_and_remux)
    return calls


def _detected(run: SimpleNamespace):
    run.credits = 6600.0
    return run.orch.detect_movie(_movie())


def test_the_detected_chapters_are_written(run, writes) -> None:
    result = run.orch.write_movie(_detected(run))

    assert result.written is True
    assert writes == [(Path("/m/Film.mkv"), ["Auto Chapter 1", "Credits"], "und")]


def test_nothing_is_written_when_someone_started_watching_meanwhile(run, writes) -> None:
    detected = _detected(run)
    run.streaming = True  # started after detection, before writing

    result = run.orch.write_movie(detected)

    assert result.written is False
    assert writes == []


def test_an_empty_detection_is_never_written(run, writes) -> None:
    run.streaming = True
    empty = run.orch.detect_movie(_movie())  # skipped: being watched
    run.streaming = False

    result = run.orch.write_movie(empty)

    assert result.written is False
    assert writes == []
