# SPDX-FileCopyrightText: 2026 Balazs Katona
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for folder_match: is the folder on this computer the folder Plex has?"""

import unicodedata
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from auto_chapter import folder_match
from auto_chapter.folder_match import FolderMatch, match_folder, names_in_dir, plex_entries


def _movie(path: str) -> SimpleNamespace:
    return SimpleNamespace(media=[SimpleNamespace(parts=[SimpleNamespace(file=path)])])


def _section(locations: list[str], paths: list[str], kind: str = "movie") -> SimpleNamespace:
    movies = [_movie(p) for p in paths]
    return SimpleNamespace(type=kind, locations=locations, all=lambda: movies)


def test_each_movie_counts_with_its_own_folder_or_its_file() -> None:
    section = _section(
        ["/plex/Filmek"],  # Plex may leave out the final "/"
        [
            "/plex/Filmek/Heat (1995)/Heat (1995).mkv",
            "/plex/Filmek/Alien (1979).mkv",  # right in the folder
            "/plex/Filmek/Gyűjtemény/Rocky (1976)/Rocky.mkv",  # deeper: its top folder
        ],
    )

    assert plex_entries([section], "/plex/Filmek/") == [
        "Heat (1995)",
        "Alien (1979).mkv",
        "Gyűjtemény",
    ]


def test_only_the_asked_folder_counts() -> None:
    """A section with two folders: only the movies of the asked one. A section
    without it, and a TV section, are not even listed."""

    def not_listed() -> list[Any]:
        raise AssertionError("listed a section without the folder")

    both = _section(
        ["/plex/Filmek/", "/plex/Filmek-Bence/"],
        ["/plex/Filmek/Heat/Heat.mkv", "/plex/Filmek-Bence/Up/Up.mkv"],
    )
    other = SimpleNamespace(type="movie", locations=["/plex/Mas/"], all=not_listed)
    shows = SimpleNamespace(type="show", locations=["/plex/Filmek/"], all=not_listed)

    assert plex_entries([both, other, shows], "/plex/Filmek/") == ["Heat"]


def test_a_movie_without_a_file_path_is_left_out() -> None:
    section = _section(["/plex/Filmek/"], ["/plex/Filmek/Heat/Heat.mkv"])
    broken = SimpleNamespace(media=[])
    movies = [broken, *section.all()]
    section.all = lambda: movies

    assert plex_entries([section], "/plex/Filmek/") == ["Heat"]


def test_names_in_dir_lists_the_folder(tmp_path: Path) -> None:
    (tmp_path / "Heat (1995)").mkdir()
    (tmp_path / "Alien.mkv").write_text("")

    assert names_in_dir(str(tmp_path)) == {"Heat (1995)", "Alien.mkv"}


def test_names_in_dir_is_none_for_a_folder_it_cannot_read(tmp_path: Path) -> None:
    assert names_in_dir(str(tmp_path / "missing")) is None


def test_names_in_dir_gives_up_on_a_folder_that_does_not_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def hangs(_path: str) -> list[str]:
        import time

        time.sleep(1)
        return []

    monkeypatch.setattr(folder_match.os, "listdir", hangs)

    assert names_in_dir("/mnt/hung", timeout=0.05) is None


def _server(*sections: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(library=SimpleNamespace(sections=lambda: list(sections)))


def test_the_right_folder_has_the_movies(tmp_path: Path) -> None:
    for name in ("Heat (1995)", "Alien (1979)"):
        (tmp_path / name).mkdir()
    server = _server(
        _section(
            ["/plex/Filmek/"],
            [
                "/plex/Filmek/Heat (1995)/Heat.mkv",
                "/plex/Filmek/Alien (1979)/Alien.mkv",
                "/plex/Filmek/Deleted (2001)/Deleted.mkv",  # still in Plex, gone here
            ],
        )
    )

    assert match_folder(server, "/plex/Filmek/", str(tmp_path) + "/") == FolderMatch(2, 3)


def test_another_folder_has_none_of_them(tmp_path: Path) -> None:
    (tmp_path / "Up (2009)").mkdir()  # the other library's movie
    server = _server(_section(["/plex/Filmek/"], ["/plex/Filmek/Heat (1995)/Heat.mkv"]))

    assert match_folder(server, "/plex/Filmek/", str(tmp_path) + "/") == FolderMatch(0, 1)


def test_accented_names_match_however_they_are_stored(tmp_path: Path) -> None:
    (tmp_path / unicodedata.normalize("NFD", "Árvácska (1976)")).mkdir()
    server = _server(_section(["/plex/Filmek/"], ["/plex/Filmek/Árvácska (1976)/Árvácska.mkv"]))

    assert match_folder(server, "/plex/Filmek/", str(tmp_path) + "/") == FolderMatch(1, 1)


def test_no_movies_in_plexs_folder_means_nothing_to_compare(tmp_path: Path) -> None:
    server = _server(_section(["/plex/Filmek/"], []))

    assert match_folder(server, "/plex/Filmek/", str(tmp_path) + "/") == FolderMatch(0, 0)


def test_a_folder_that_cannot_be_read_gives_no_answer(tmp_path: Path) -> None:
    server = _server(_section(["/plex/Filmek/"], ["/plex/Filmek/Heat/Heat.mkv"]))

    assert match_folder(server, "/plex/Filmek/", str(tmp_path / "missing") + "/") is None


# --- The installer's entry point -------------------------------------------


def test_main_prints_found_and_total_for_the_installer(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[tuple[str, str]] = []

    def fake_server(url: str, token: str, **_kw: object) -> str:
        seen.append((url, token))
        return "server"

    monkeypatch.setenv("PLEX_URL", "http://plex:32400")
    monkeypatch.setenv("PLEX_TOKEN", "abc")
    monkeypatch.setattr("plexapi.server.PlexServer", fake_server)
    monkeypatch.setattr(folder_match, "match_folder", lambda *_a: FolderMatch(650, 653))

    assert folder_match.main(["/plex/Filmek/", "/mnt/Filmek/"]) == 0
    assert capsys.readouterr().out == "650 653\n"
    assert seen == [("http://plex:32400", "abc")]


@pytest.mark.parametrize("problem", ["no answer", "Plex error"])
def test_main_prints_nothing_when_it_cannot_tell(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], problem: str
) -> None:
    def broken(*_a: object) -> None:
        raise ConnectionError("refused")

    monkeypatch.setenv("PLEX_URL", "http://plex:32400")
    monkeypatch.setenv("PLEX_TOKEN", "abc")
    monkeypatch.setattr("plexapi.server.PlexServer", lambda *_a, **_k: "server")
    monkeypatch.setattr(
        folder_match, "match_folder", (lambda *_a: None) if problem == "no answer" else broken
    )

    assert folder_match.main(["/plex/Filmek/", "/mnt/Filmek/"]) == 0
    assert capsys.readouterr().out == ""
