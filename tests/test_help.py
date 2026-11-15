# SPDX-FileCopyrightText: 2026 Balazs Katona
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Help menu (1.2.0, owner's idea) and its one-time notice."""

from pathlib import Path
from types import SimpleNamespace

import pytest

import auto_chapter.help as help_mod
import auto_chapter.menu as menu
from auto_chapter.config import DetectionConfig
from auto_chapter.models import AUTO_CHAPTER_PREFIX, CREDITS_CHAPTER_NAME


@pytest.fixture
def answers(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """What the user types at Help's own prompts, in order (0 = back)."""
    queue: list[int] = []

    def fake_ask(*_a: object, **_k: object) -> int:
        return queue.pop(0)

    monkeypatch.setattr(menu.IntPrompt, "ask", fake_ask)
    monkeypatch.setattr(help_mod.IntPrompt, "ask", fake_ask)
    return queue


def test_opening_help_hides_the_notice_for_good(tmp_path: Path, answers: list[int]) -> None:
    assert not help_mod.help_seen(tmp_path)
    answers.append(0)

    help_mod.help_flow(tmp_path, SimpleNamespace())

    assert help_mod.help_seen(tmp_path)


def test_without_a_settings_folder_there_is_no_notice() -> None:
    assert help_mod.help_seen(None)


@pytest.mark.parametrize(
    ("page", "expected"),
    [
        (1, "Most movie files come without chapters"),
        (2, "Type a small number first"),
        (3, "Plex End Credits"),
    ],
)
def test_every_help_page_opens_and_goes_back(
    tmp_path: Path, answers: list[int], capsys: pytest.CaptureFixture[str], page: int, expected: str
) -> None:
    answers.extend([page, 0, 0])

    help_mod.help_flow(tmp_path, SimpleNamespace())

    assert expected in " ".join(capsys.readouterr().out.split())


def test_the_main_menu_shows_the_notice_until_help_was_opened(
    tmp_path: Path, answers: list[int], capsys: pytest.CaptureFixture[str]
) -> None:
    plex = SimpleNamespace(is_connected=True, server_name="Test", connection_error=None)
    config = SimpleNamespace(paths=[SimpleNamespace(enabled=True)], path=tmp_path / "config.yml")
    answers.extend([5, 0, 0])  # Help, back, Quit

    menu.run_menu(SimpleNamespace(plex=plex), config)

    out = capsys.readouterr().out
    assert out.count("New here? Open 5) Help first") == 1  # before Help, not after
    assert "5) Help" in out and "6)" not in out


def test_the_help_text_matches_the_detection_settings() -> None:
    """The method is described with numbers: they must be the program's own."""
    d = DetectionConfig()
    assert d.min_gap_seconds == 5 * 60  # "From minute 5 on", "5-minute parts"
    assert d.end_margin_seconds == 15 * 60  # "15 minutes before the end"
    text = " ".join(help_mod.WHAT_IT_DOES.split())
    assert "From minute 5 on" in text
    assert "15 minutes before the end of the movie" in text


def test_the_words_use_the_real_chapter_names() -> None:
    words = dict(help_mod.WORDS)
    assert f'"{AUTO_CHAPTER_PREFIX} 1"' in words["Auto Chapters"]
    assert f'"{CREDITS_CHAPTER_NAME}"' in words["End Credits chapter"]
    assert "Plex Pass" in words["Plex End Credits"]


def test_about_is_helps_last_page(
    tmp_path: Path, answers: list[int], capsys: pytest.CaptureFixture[str]
) -> None:
    """1.2.1 (owner): About moved from the main menu into Help."""
    config = SimpleNamespace(log_file_path=lambda: tmp_path / "logs" / "auto-chapter.log")
    answers.extend([4, 0, 0])

    help_mod.help_flow(tmp_path, config)

    out = " ".join(capsys.readouterr().out.split())
    assert "4) About Version, log file, GitHub page and license" in out
    assert "https://github.com/balazs-katona/auto-chapter" in out
    assert "GPL-3.0-or-later" in out
