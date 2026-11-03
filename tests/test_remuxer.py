# SPDX-FileCopyrightText: 2026 Balazs Katona
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the mkvmerge wrapper: the command line that swaps the chapters.

What these guard against: ``mkvmerge --chapters new.xml input.mkv`` does NOT
replace the chapters already in ``input.mkv``. It keeps them and adds the new
XML as a separate chapter edition, so every write stacked one more edition on
the file (seen on a real movie: 9 editions after several runs).
``--no-chapters`` -- a per-input-file option -- drops the old ones.

mkvmerge itself is never started here; ``subprocess.Popen`` is replaced by a
stand-in that records the command line.
"""

from pathlib import Path
from typing import Any

import pytest

from auto_chapter import remuxer
from auto_chapter.models import Chapter, DetectionMethod
from auto_chapter.remuxer import _build_mkvmerge_command, write_chapters_and_remux

SOURCE = Path("/movies/Film.mkv")
TEMP = Path("/movies/Film.mkv.autochapter.tmp")
XML = Path("/movies/Film.mkv.autochapter.xml")


def test_command_drops_the_chapters_already_in_the_file() -> None:
    cmd = _build_mkvmerge_command(TEMP, XML, SOURCE)

    # A per-file option only applies to the input file that follows it, so it
    # has to stand directly before that file name.
    assert "--no-chapters" in cmd
    assert cmd[cmd.index(str(SOURCE)) - 1] == "--no-chapters"


def test_command_embeds_the_new_chapter_file() -> None:
    cmd = _build_mkvmerge_command(TEMP, XML, SOURCE)

    assert cmd[cmd.index("--chapters") + 1] == str(XML)


def test_command_writes_to_the_temp_file_and_reads_the_original() -> None:
    cmd = _build_mkvmerge_command(TEMP, XML, SOURCE)

    assert cmd[0] == "mkvmerge"
    assert cmd[cmd.index("-o") + 1] == str(TEMP)
    assert cmd.count(str(SOURCE)) == 1  # the original is the one and only input
    assert cmd[-1] == str(SOURCE)


class _FakeMkvmerge:
    """Stands in for the mkvmerge process: finishes at once with exit code 0."""

    returncode = 0

    def communicate(self) -> tuple[str, str]:
        return "", ""


def test_write_passes_the_new_chapters_and_the_no_chapters_flag_to_mkvmerge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    movie = tmp_path / "Film.mkv"
    movie.write_bytes(b"x" * 1000)
    commands: list[list[str]] = []
    chapter_xml: list[str] = []

    def fake_popen(cmd: list[str], **_kwargs: Any) -> _FakeMkvmerge:
        commands.append(cmd)
        chapter_xml.append(Path(cmd[cmd.index("--chapters") + 1]).read_text(encoding="utf-8"))
        # Like the real thing: the output file appears, about as big as the input.
        Path(cmd[cmd.index("-o") + 1]).write_bytes(b"y" * 1000)
        return _FakeMkvmerge()

    monkeypatch.setattr(remuxer.subprocess, "Popen", fake_popen)
    chapters = [
        Chapter(name="Auto Chapter 1", timestamp=0.0, method=DetectionMethod.START),
        Chapter(name="Credits", timestamp=5000.0, method=DetectionMethod.CREDITS),
    ]

    result = write_chapters_and_remux(movie, chapters, min_free_bytes=0)

    assert result.success, result.error
    assert len(commands) == 1
    assert commands[0][-2:] == ["--no-chapters", str(movie)]
    assert "<ChapterString>Auto Chapter 1</ChapterString>" in chapter_xml[0]
    assert "<ChapterString>Credits</ChapterString>" in chapter_xml[0]


def test_write_names_the_chapter_xml_after_the_movie_and_removes_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The chapter XML gets the app's own name next to the movie, so a copy left
    behind by a killed run can be recognised by the next scan's cleanup (a
    random tmpXXXX.xml could not be told apart from anyone else's file)."""
    movie = tmp_path / "Film.mkv"
    movie.write_bytes(b"x" * 1000)
    xml_paths: list[Path] = []

    def fake_popen(cmd: list[str], **_kwargs: Any) -> _FakeMkvmerge:
        xml_path = Path(cmd[cmd.index("--chapters") + 1])
        assert xml_path.exists()  # written before mkvmerge starts
        xml_paths.append(xml_path)
        Path(cmd[cmd.index("-o") + 1]).write_bytes(b"y" * 1000)
        return _FakeMkvmerge()

    monkeypatch.setattr(remuxer.subprocess, "Popen", fake_popen)
    chapters = [Chapter(name="Auto Chapter 1", timestamp=0.0, method=DetectionMethod.START)]

    result = write_chapters_and_remux(movie, chapters, min_free_bytes=0)

    assert result.success, result.error
    assert xml_paths == [tmp_path / "Film.mkv.autochapter.xml"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Film.mkv"]  # nothing left over


def test_a_failed_write_reports_what_mkvmerge_said(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """mkvmerge prints its errors to stdout, not stderr: reading only stderr
    lost the reason, and the user always got "The source may be corrupt"."""
    movie = tmp_path / "Film.mkv"
    movie.write_bytes(b"x" * 1000)

    class FailingMkvmerge:
        returncode = 2

        def communicate(self) -> tuple[str, str]:
            stdout = (
                "mkvmerge v103.0 ('The Darkness') 64-bit\n"
                "Error: The file '/movies/Film.mkv.autochapter.tmp' could not be "
                "opened for writing: open file error.\n"
            )
            return stdout, ""

    monkeypatch.setattr(remuxer.subprocess, "Popen", lambda *_a, **_k: FailingMkvmerge())
    chapters = [Chapter(name="Auto Chapter 1", timestamp=0.0, method=DetectionMethod.START)]

    result = write_chapters_and_remux(movie, chapters, min_free_bytes=0)

    assert not result.success
    assert result.error == (
        "mkvmerge failed: Error: The file '/movies/Film.mkv.autochapter.tmp' could not "
        "be opened for writing: open file error."
    )
