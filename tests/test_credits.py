"""Tests for credits detection."""

from auto_chapter.chapter.credits import CREDITS_KEYWORDS


def test_keywords_lowercase_and_stripped() -> None:
    """All keywords must be lowercase + no extra whitespace."""
    for keyword in CREDITS_KEYWORDS:
        assert keyword == keyword.lower().strip()


def test_expected_keywords_present() -> None:
    assert "credits" in CREDITS_KEYWORDS
    assert "end credits" in CREDITS_KEYWORDS
    assert "outro" in CREDITS_KEYWORDS


# ---------------------------------------------------------------------------
# Where the end credits time comes from (0.2.34): a credits-named chapter in
# the file first, then Plex's credits marker, else none. Until now only the
# keyword list was tested. ffprobe is replaced; no file is read.
# ---------------------------------------------------------------------------

from pathlib import Path  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402

from auto_chapter.chapter import credits  # noqa: E402
from auto_chapter.models import CreditsSource  # noqa: E402

FILM = Path("/m/Film.mkv")


def _file_chapters(monkeypatch: pytest.MonkeyPatch, *chapters: tuple[str, str]) -> None:
    """(title, start time) pairs, the way ffprobe -show_chapters reports them."""
    monkeypatch.setattr(
        credits.ffmpeg,
        "get_chapters",
        lambda _path: [{"start_time": t, "tags": {"title": title}} for title, t in chapters],
    )


def _plex_movie(*markers: tuple[str, int]) -> SimpleNamespace:
    """(type, start in ms) pairs, like plexapi's markers."""
    return SimpleNamespace(markers=[SimpleNamespace(type=t, start=ms) for t, ms in markers])


@pytest.mark.parametrize("title", ["Credits", "END CREDITS", "  outro  "])
def test_a_credits_chapter_in_the_file_is_used_first(monkeypatch, title: str) -> None:
    _file_chapters(monkeypatch, ("Opening", "0.000000"), (title, "6612.500000"))

    assert credits.resolve_credits(FILM, _plex_movie(("credits", 6000_000))) == (
        6612.5,
        CreditsSource.FILE,
    )


def test_without_one_plexs_credits_marker_is_used_in_seconds(monkeypatch) -> None:
    _file_chapters(monkeypatch, ("Opening", "0.000000"), ("Act 2", "1800.000000"))

    assert credits.resolve_credits(FILM, _plex_movie(("intro", 30_000), ("credits", 6600_500))) == (
        6600.5,
        CreditsSource.PLEX,
    )


def test_neither_gives_none(monkeypatch) -> None:
    _file_chapters(monkeypatch)

    assert credits.resolve_credits(FILM, _plex_movie(("intro", 30_000))) == (
        None,
        CreditsSource.NONE,
    )
    assert credits.resolve_credits(FILM, None) == (None, CreditsSource.NONE)


def test_a_chapter_name_that_only_contains_credits_does_not_count(monkeypatch) -> None:
    """Exact names only: "Opening credits" is not the end of the film."""
    _file_chapters(monkeypatch, ("Opening credits", "60.000000"))

    assert credits.detect_from_file(FILM) is None
