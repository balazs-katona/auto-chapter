"""Tests for scdet parser."""

from auto_chapter.detector.scdet import parse_best_scene


def test_best_score_wins() -> None:
    stderr = (
        "[Parsed_scdet_2 @ 0x55] lavfi.scd.time: 10.0\n"
        "[Parsed_scdet_2 @ 0x55] lavfi.scd.score: 20.5\n"
        "[Parsed_scdet_2 @ 0x55] lavfi.scd.time: 30.0\n"
        "[Parsed_scdet_2 @ 0x55] lavfi.scd.score: 65.0\n"
        "[Parsed_scdet_2 @ 0x55] lavfi.scd.time: 50.0\n"
        "[Parsed_scdet_2 @ 0x55] lavfi.scd.score: 40.0\n"
    )
    result = parse_best_scene(stderr, seek_offset=0.0)
    assert result is not None
    assert result.time == 30.0
    assert result.score == 65.0


def test_seek_offset_added() -> None:
    stderr = (
        "[Parsed_scdet_2 @ 0x55] lavfi.scd.time: 5.0\n"
        "[Parsed_scdet_2 @ 0x55] lavfi.scd.score: 50.0\n"
    )
    result = parse_best_scene(stderr, seek_offset=200.0)
    assert result is not None
    assert result.time == 205.0
    assert result.score == 50.0


def test_no_scenes_found() -> None:
    assert parse_best_scene("nothing here", seek_offset=0.0) is None


def test_single_scene() -> None:
    stderr = (
        "[Parsed_scdet_2 @ 0x55] lavfi.scd.time: 10.0\n"
        "[Parsed_scdet_2 @ 0x55] lavfi.scd.score: 15.0\n"
    )
    result = parse_best_scene(stderr, seek_offset=0.0)
    assert result is not None
    assert result.time == 10.0
    assert result.score == 15.0
