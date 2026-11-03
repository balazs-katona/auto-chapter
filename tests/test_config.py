# SPDX-FileCopyrightText: 2026 Balazs Katona
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for PathMapping: paths are checked and normalised when a mapping is built.

A hand-edited config.yml must not be able to produce a mapping that translates
movie paths wrongly (see PathMapping's docstring).
"""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from auto_chapter.config import Config, PathMapping, default_config_path
from auto_chapter.path_mapping import PathMapper


def test_correct_paths_are_left_alone() -> None:
    m = PathMapping(plex="/share/Filmek/", local="/mnt/nas/Filmek/")

    assert (m.plex, m.local, m.enabled) == ("/share/Filmek/", "/mnt/nas/Filmek/", True)


def test_a_missing_final_slash_is_added_on_both_sides() -> None:
    m = PathMapping(plex="/share/Filmek", local="/mnt/nas/Filmek")

    assert m.plex == "/share/Filmek/"
    assert m.local == "/mnt/nas/Filmek/"


def test_surrounding_whitespace_is_dropped() -> None:
    m = PathMapping(plex="  /share/Filmek/ ", local=" /mnt/nas/Filmek/  ")

    assert (m.plex, m.local) == ("/share/Filmek/", "/mnt/nas/Filmek/")


def test_home_folder_is_expanded_in_the_local_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", "/home/someone")

    m = PathMapping(plex="/share/Filmek/", local="~/Filmek")

    assert m.local == "/home/someone/Filmek/"


@pytest.mark.parametrize("bad", ["", "   ", "media/Filmek/", "./Filmek/", "Filmek"])
def test_a_local_path_that_is_not_absolute_is_refused(bad: str) -> None:
    with pytest.raises(ValidationError) as excinfo:
        PathMapping(plex="/share/Filmek/", local=bad)

    assert "must start with '/'" in str(excinfo.value)


@pytest.mark.parametrize("bad", ["", "   "])
def test_an_empty_plex_path_is_refused(bad: str) -> None:
    with pytest.raises(ValidationError) as excinfo:
        PathMapping(plex=bad, local="/mnt/nas/Filmek/")

    assert "the Plex path is empty" in str(excinfo.value)


def test_normalised_paths_translate_a_movie_path_correctly() -> None:
    """The two mistakes the normalisation exists for."""
    mapper = PathMapper([PathMapping(plex="/plex/Filmek", local="/mnt/nas/Filmek")])

    assert mapper.plex_to_local("/plex/Filmek/Heat (1995)/Heat.mkv") == Path(
        "/mnt/nas/Filmek/Heat (1995)/Heat.mkv"
    )
    # "/plex/Filmek" no longer matches a folder that merely starts the same way
    assert mapper.plex_to_local("/plex/Filmek2/X/X.mkv") is None


def _write_config(tmp_path: Path, plex_path: str, local_path: str) -> Path:
    path = tmp_path / "config.yml"
    path.write_text(
        yaml.safe_dump(
            {
                "plex": {"url": "http://plex:32400"},
                "plex_token": "token",
                "paths": [{"plex": plex_path, "local": local_path}],
            }
        )
    )
    return path


def test_loading_a_config_normalises_its_mappings(tmp_path: Path) -> None:
    config = Config.load(_write_config(tmp_path, "/share/Filmek", "/mnt/nas/Filmek"))

    assert [(m.plex, m.local) for m in config.paths] == [("/share/Filmek/", "/mnt/nas/Filmek/")]


def test_loading_a_config_with_a_relative_local_path_fails_and_names_the_entry(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match=r"paths, entry 1, local") as excinfo:
        Config.load(_write_config(tmp_path, "/share/Filmek/", "media/Filmek/"))

    assert "must start with '/'" in str(excinfo.value)


def test_old_chapter_name_settings_in_config_yml_are_ignored(tmp_path: Path) -> None:
    """Up to 0.2.26 config.yml had chapter.prefix and chapter.credits_label, but the
    app only ever recognised "Auto Chapter ..." / "Credits" as its own chapters:
    another name would have made it treat its own films as foreign. The names are
    fixed now; a config.yml that still has the old keys loads as before."""
    path = _write_config(tmp_path, "/share/Filmek/", "/mnt/nas/Filmek/")
    data = yaml.safe_load(path.read_text())
    data["chapter"] = {"prefix": "Fejezet", "credits_label": "Vége", "language": "hun"}
    path.write_text(yaml.safe_dump(data, allow_unicode=True))

    config = Config.load(path)

    assert config.chapter.language == "hun"
    assert not hasattr(config.chapter, "prefix")
    assert not hasattr(config.chapter, "credits_label")


# ---------------------------------------------------------------------------
# Where config.yml, .env and the log file live (0.2.29)
#
# They used to sit in the app's own folder, so deleting the folder and
# unpacking a new version (the owner's way of updating) took the settings and
# the token with it. Now they live in ~/.config/auto-chapter/ (or
# $XDG_CONFIG_HOME/auto-chapter/), and everything is read from and written back
# to the folder config.yml was loaded from -- never the current directory.
# ---------------------------------------------------------------------------


def test_settings_live_in_the_users_config_folder(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    assert default_config_path() == tmp_path / ".config" / "auto-chapter" / "config.yml"


def test_xdg_config_home_is_respected(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))

    assert default_config_path() == tmp_path / "cfg" / "auto-chapter" / "config.yml"


def test_a_relative_xdg_config_home_is_ignored(monkeypatch, tmp_path: Path) -> None:
    """The XDG spec says a relative value is invalid and must be ignored."""
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative/cfg")
    monkeypatch.setenv("HOME", str(tmp_path))

    assert default_config_path() == tmp_path / ".config" / "auto-chapter" / "config.yml"


def _write_settings(folder: Path, token: str, log_file: str = "") -> Path:
    """config.yml (without a token) and .env, as the installer writes them."""
    folder.mkdir(parents=True)
    path = folder / "config.yml"
    path.write_text(
        yaml.safe_dump(
            {
                "plex": {"url": "http://plex:32400"},
                "paths": [{"plex": "/share/Filmek/", "local": "/mnt/nas/Filmek/"}],
                "logging": {"log_level": "WARNING", "file_path": log_file},
            }
        )
    )
    (folder / ".env").write_text(f"PLEX_TOKEN={token}\n")
    return path


@pytest.fixture
def elsewhere(monkeypatch, tmp_path: Path) -> Path:
    """Run from another folder that has its own .env -- it must never be used."""
    folder = tmp_path / "elsewhere"
    folder.mkdir()
    (folder / ".env").write_text("PLEX_TOKEN=wrongtoken\n")
    monkeypatch.chdir(folder)
    monkeypatch.delenv("PLEX_TOKEN", raising=False)
    return folder


def test_the_token_is_read_from_the_env_file_next_to_config_yml(
    tmp_path: Path, elsewhere: Path
) -> None:
    path = _write_settings(tmp_path / "settings", "righttoken")

    config = Config.load(path)

    assert config.plex.token == "righttoken"


def test_a_relative_log_file_is_inside_the_settings_folder(
    tmp_path: Path, elsewhere: Path
) -> None:
    settings = tmp_path / "settings"
    config = Config.load(_write_settings(settings, "tok", log_file="logs/auto-chapter.log"))

    assert config.log_file_path() == settings / "logs" / "auto-chapter.log"


def test_settings_are_saved_where_they_were_loaded_from(
    monkeypatch, tmp_path: Path, elsewhere: Path
) -> None:
    from types import SimpleNamespace

    from auto_chapter import menu

    settings = tmp_path / "settings"
    config = Config.load(_write_settings(settings, "oldtoken"))

    config.detection.chapter_threshold = 7
    menu._save_config_to_disk(config)

    monkeypatch.setattr(menu, "_ask_text", lambda *_a, **_k: "newtoken")
    monkeypatch.setattr(menu, "_test_plex_connection", lambda *_a: (True, ""))
    menu._change_plex_token(config, SimpleNamespace(plex=SimpleNamespace(reconnect=lambda: True)))

    assert yaml.safe_load((settings / "config.yml").read_text())["detection"]["chapter_threshold"] == 7
    assert (settings / ".env").read_text() == "PLEX_TOKEN=newtoken\n"
    assert sorted(p.name for p in elsewhere.iterdir()) == [".env"]  # nothing written here
    assert (elsewhere / ".env").read_text() == "PLEX_TOKEN=wrongtoken\n"


# ---------------------------------------------------------------------------
# A Settings save keeps config.yml readable (0.2.30)
#
# Saving used to re-dump the whole file with PyYAML: every comment the
# installer wrote (what a setting means, "Prefer Settings over editing this by
# hand") was gone after the first change in Settings, and untouched values
# were reformatted (0.10 -> 0.1). Now only the changed values are written.
# ---------------------------------------------------------------------------


def _installed_config(folder: Path) -> Path:
    """config.yml as install.sh writes it: the example with a URL and one mapping."""
    example = (Path(__file__).parent.parent / "config.yml.example").read_text()
    example = example.replace("http://YOUR-PLEX-SERVER:32400", "http://plex:32400")
    example = example.replace("/path/as/plex/sees/it/", "/share/Filmek/")
    example = example.replace("/path/as/this/machine/sees/it/", "/mnt/nas/Filmek/")
    folder.mkdir(parents=True)
    (folder / ".env").write_text("PLEX_TOKEN=tok\n")
    path = folder / "config.yml"
    path.write_text(example)
    return path


def test_a_settings_save_keeps_the_comments_and_untouched_values(
    tmp_path: Path, elsewhere: Path
) -> None:
    from auto_chapter import menu

    path = _installed_config(tmp_path / "settings")
    before = path.read_text()
    config = Config.load(path)

    config.detection.chapter_threshold = 7
    menu._save_config_to_disk(config)

    after = path.read_text()
    comments = [ln for ln in before.splitlines() if ln.lstrip().startswith("#")]
    assert comments and all(c in after for c in comments)
    assert "  chapter_threshold: 7\n" in after
    assert "  black_pix_threshold: 0.10\n" in after  # untouched, written as it was
    assert "tok" not in after  # the token never goes into config.yml
    assert Config.load(path).detection.chapter_threshold == 7


def test_a_saved_mapping_change_loads_back_the_same(tmp_path: Path, elsewhere: Path) -> None:
    from auto_chapter import menu

    path = _installed_config(tmp_path / "settings")
    config = Config.load(path)

    config.paths[0].enabled = False
    config.paths.append(PathMapping(plex="/share/Filmek-2/", local="/mnt/nas/Filmek-2/"))
    menu._save_config_to_disk(config)

    reloaded = Config.load(path)
    assert [(m.plex, m.local, m.enabled) for m in reloaded.paths] == [
        ("/share/Filmek/", "/mnt/nas/Filmek/", False),
        ("/share/Filmek-2/", "/mnt/nas/Filmek-2/", True),
    ]
    text = path.read_text()
    assert "# Path mappings: how Plex sees files" in text
    # The blank line between the list and "detection:" stays after the list,
    # not inside the last mapping that got a key added.
    assert "    enabled: true\n\ndetection:" in text

    config = Config.load(path)
    del config.paths[0]
    menu._save_config_to_disk(config)

    assert "    enabled: true\n\ndetection:" in path.read_text()
    assert [m.plex for m in Config.load(path).paths] == ["/share/Filmek-2/"]
