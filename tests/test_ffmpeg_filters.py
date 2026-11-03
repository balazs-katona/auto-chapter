# SPDX-FileCopyrightText: 2026 Balazs Katona
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for ffmpeg_filter_problem: the detection needs the blackdetect and
scdet filters, and an ffmpeg without them (FFmpeg 4.2 has no scdet) would only
fail in the middle of a detection."""

import subprocess
from types import SimpleNamespace

import pytest

from auto_chapter.detector import ffmpeg as ffmpeg_module
from auto_chapter.detector.ffmpeg import REQUIRED_FILTERS, ffmpeg_filter_problem

# Shape of the real `ffmpeg -filters` output: a legend first, then one line per filter.
_HEADER = """Filters:
  T.. = Timeline support
  .S. = Slice threading
  ..C = Command support
  A = Audio input/output
  V = Video input/output
  N = Dynamic number and/or type of input/output
  | = Source or sink filter
 ... acopy             A->A       Copy the input audio unchanged to the output.
 T.. blackdetect       V->V       Detect video intervals that are (almost) black.
 ... crop              V->V       Crop the input video.
"""
_SCDET = " ... scdet              V->V       Detect video scene change\n"


# Real output of FFmpeg 9.0.2 (flag column is 2 characters wide, no "..C" legend line).
_FFMPEG9 = """Filters:
  T.. = Timeline support
  .S. = Slice threading
  A = Audio input/output
  V = Video input/output
  N = Dynamic number and/or type of input/output
  | = Source or sink filter
  ------
 TS aap               AA->A      Apply Affine Projection algorithm to first audio stream.
 .. abench            A->A       Benchmark part of a filtergraph.
 .S blackdetect       V->V       Detect video intervals that are (almost) black.
 .. blackdetect_vulkan V->V       Detect video intervals that are (almost) black.
 .. scdet             V->V       Detect video scene change
 .. scdet_vulkan      V->V       Detect video scene change
"""


def _fake_run(stdout: str, returncode: int = 0):
    def run(cmd, **kwargs):
        assert cmd[:2] == ["ffmpeg", "-hide_banner"]
        return SimpleNamespace(stdout=stdout, stderr="", returncode=returncode)

    return run


def test_returns_none_when_every_needed_filter_is_there(monkeypatch) -> None:
    monkeypatch.setattr(ffmpeg_module.subprocess, "run", _fake_run(_HEADER + _SCDET))

    assert ffmpeg_filter_problem() is None


def test_names_the_missing_filter(monkeypatch) -> None:
    monkeypatch.setattr(ffmpeg_module.subprocess, "run", _fake_run(_HEADER))

    problem = ffmpeg_filter_problem()

    assert problem is not None
    assert "scdet" in problem
    assert "4.4" in problem


def test_names_every_missing_filter(monkeypatch) -> None:
    only_unrelated = "Filters:\n ... acopy             A->A       Copy the input audio.\n"
    monkeypatch.setattr(ffmpeg_module.subprocess, "run", _fake_run(only_unrelated))

    problem = ffmpeg_filter_problem()

    assert problem is not None
    for name in REQUIRED_FILTERS:
        assert name in problem


def test_a_filter_name_inside_a_description_does_not_count(monkeypatch) -> None:
    """Only the name column counts, not a mention in another filter's text."""
    out = _HEADER + " ... other             V->V       Works like scdet but is not.\n"
    monkeypatch.setattr(ffmpeg_module.subprocess, "run", _fake_run(out))

    problem = ffmpeg_filter_problem()

    assert problem is not None
    assert "scdet" in problem


def test_ffmpeg_failing_is_reported(monkeypatch) -> None:
    monkeypatch.setattr(ffmpeg_module.subprocess, "run", _fake_run("", returncode=1))

    problem = ffmpeg_filter_problem()

    assert problem is not None
    assert "exit code 1" in problem


@pytest.mark.parametrize(
    "error",
    [FileNotFoundError("ffmpeg"), subprocess.TimeoutExpired(cmd="ffmpeg", timeout=15)],
)
def test_ffmpeg_not_runnable_is_reported(monkeypatch, error) -> None:
    def run(cmd, **kwargs):
        raise error

    monkeypatch.setattr(ffmpeg_module.subprocess, "run", run)

    problem = ffmpeg_filter_problem()

    assert problem is not None
    assert "Could not ask ffmpeg" in problem


def test_ffmpeg_9_two_character_flag_column_is_understood(monkeypatch) -> None:
    """FFmpeg 9 prints ' .S blackdetect' (2 flag characters), older versions
    ' T.C blackdetect' (3). Both must work."""
    monkeypatch.setattr(ffmpeg_module.subprocess, "run", _fake_run(_FFMPEG9))

    assert ffmpeg_filter_problem() is None


def test_a_filter_with_a_suffix_does_not_count(monkeypatch) -> None:
    """scdet_vulkan is a different filter; it must not stand in for scdet."""
    out = (
        "Filters:\n"
        " .S blackdetect       V->V       Detect video intervals that are (almost) black.\n"
        " .. scdet_vulkan      V->V       Detect video scene change\n"
    )
    monkeypatch.setattr(ffmpeg_module.subprocess, "run", _fake_run(out))

    problem = ffmpeg_filter_problem()

    assert problem is not None
    assert "scdet" in problem


def test_unrecognised_output_does_not_claim_filters_are_missing(monkeypatch) -> None:
    """If not one filter line can be read, the parser is the problem, not
    ffmpeg -- saying 'filters missing' would be a false accusation."""
    monkeypatch.setattr(
        ffmpeg_module.subprocess, "run", _fake_run("some completely different format\n")
    )

    assert ffmpeg_filter_problem() is None


def test_legend_lines_alone_are_not_mistaken_for_filters(monkeypatch) -> None:
    """The legend ('T.. = Timeline support') looks like a filter line; if it
    counted, an unreadable listing would be reported as 'filters missing'."""
    legend_only = "Filters:\n  T.. = Timeline support\n  .S. = Slice threading\n  ------\n"
    monkeypatch.setattr(ffmpeg_module.subprocess, "run", _fake_run(legend_only))

    assert ffmpeg_filter_problem() is None
