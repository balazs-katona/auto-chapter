# SPDX-FileCopyrightText: 2026 Balazs Katona
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for blackdetect parser."""

from auto_chapter.detector.blackdetect import parse_first_black


def test_first_black_detected() -> None:
    stderr = (
        "[blackdetect @ 0x55] black_start:5.123 black_end:7.428 black_duration:2.305\n"
        "[blackdetect @ 0x55] black_start:20.000 black_end:21.500 black_duration:1.500\n"
    )
    result = parse_first_black(stderr, seek_offset=0.0)
    assert result is not None
    assert result.new_scene_time == 7.428


def test_seek_offset_added() -> None:
    stderr = "[blackdetect @ 0x55] black_start:5.0 black_end:7.5 black_duration:2.5"
    result = parse_first_black(stderr, seek_offset=100.0)
    assert result is not None
    assert result.new_scene_time == 107.5


def test_no_black_found() -> None:
    assert parse_first_black("no blackdetect output here", seek_offset=0.0) is None


def test_decimal_only_match() -> None:
    """Regex should not accidentally include the next token (e.g. ' black_duration:')."""
    stderr = "[blackdetect @ 0x55] black_start:1.0 black_end:2.5 black_duration:1.5"
    result = parse_first_black(stderr, seek_offset=0.0)
    assert result is not None
    assert result.new_scene_time == 2.5
