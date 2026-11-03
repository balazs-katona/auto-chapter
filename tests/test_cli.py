# SPDX-FileCopyrightText: 2026 Balazs Katona
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for _check_required_tools: re-verified on every launch, not just
at install time, since a tool can vanish after installation (a system
update, a manual removal)."""

import pytest
import typer

from auto_chapter.cli import _REQUIRED_TOOLS, _check_required_tools


@pytest.fixture(autouse=True)
def _no_real_ffmpeg(monkeypatch) -> None:
    """The tests below are about which tools are on PATH; they must not depend on
    (or run) the ffmpeg of the machine they happen to run on."""
    monkeypatch.setattr("auto_chapter.cli.ffmpeg_filter_problem", lambda: None)
    monkeypatch.setattr("auto_chapter.cli.ffmpeg_decoder_problem", lambda: None)


def test_passes_silently_when_everything_is_on_path(monkeypatch) -> None:
    monkeypatch.setattr("auto_chapter.cli.shutil.which", lambda _tool: "/usr/bin/fake")

    _check_required_tools()  # must not raise


def test_exits_when_a_tool_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        "auto_chapter.cli.shutil.which",
        lambda tool: None if tool == "mkvmerge" else "/usr/bin/fake",
    )

    with pytest.raises(typer.Exit):
        _check_required_tools()


def test_reports_every_missing_tool_at_once(monkeypatch, capsys) -> None:
    """Fixing one, relaunching, and hitting the next missing tool one at a
    time would be a poor experience -- all gaps should show up together."""
    monkeypatch.setattr("auto_chapter.cli.shutil.which", lambda _tool: None)

    with pytest.raises(typer.Exit):
        _check_required_tools()

    output = capsys.readouterr().out
    for tool in _REQUIRED_TOOLS:
        assert tool in output


def test_exits_when_ffmpeg_lacks_a_needed_filter(monkeypatch, capsys) -> None:
    monkeypatch.setattr("auto_chapter.cli.shutil.which", lambda _tool: "/usr/bin/fake")
    monkeypatch.setattr(
        "auto_chapter.cli.ffmpeg_filter_problem", lambda: "This ffmpeg has no scdet filter."
    )

    with pytest.raises(typer.Exit):
        _check_required_tools()

    assert "no scdet filter" in capsys.readouterr().out


def test_reports_a_missing_tool_and_a_filter_problem_together(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "auto_chapter.cli.shutil.which",
        lambda tool: None if tool == "mkvmerge" else "/usr/bin/fake",
    )
    monkeypatch.setattr(
        "auto_chapter.cli.ffmpeg_filter_problem", lambda: "This ffmpeg has no scdet filter."
    )

    with pytest.raises(typer.Exit):
        _check_required_tools()

    output = capsys.readouterr().out
    assert "mkvmerge" in output
    assert "no scdet filter" in output


def test_filters_are_not_asked_when_ffmpeg_itself_is_missing(monkeypatch) -> None:
    """A missing ffmpeg is already reported; asking it for filters would only
    add a second, confusing error."""
    monkeypatch.setattr(
        "auto_chapter.cli.shutil.which",
        lambda tool: None if tool == "ffmpeg" else "/usr/bin/fake",
    )

    def must_not_run() -> str:
        raise AssertionError("ffmpeg was asked although it is not installed")

    monkeypatch.setattr("auto_chapter.cli.ffmpeg_filter_problem", must_not_run)

    with pytest.raises(typer.Exit):
        _check_required_tools()


# ---------------------------------------------------------------------------
# A broken config.yml: one clear sentence about what is wrong and where, and
# the one thing to do about it (0.2.31). Before, an empty file showed a raw
# Python error, a bad path showed pydantic's text with a link to its docs, and
# a missing token got a hint pointing at config.yml instead of .env.
# ---------------------------------------------------------------------------

from pathlib import Path  # noqa: E402

from auto_chapter.cli import _load_config  # noqa: E402


def _shown(capsys) -> str:
    """Printed text, with Rich's line wrapping undone."""
    return " ".join(capsys.readouterr().out.split())


def _settings(tmp_path: Path, config_text: str, token: str | None = "tok") -> Path:
    if token is not None:
        (tmp_path / ".env").write_text(f"PLEX_TOKEN={token}\n")
    path = tmp_path / "config.yml"
    path.write_text(config_text)
    return path


GOOD = "plex:\n  url: http://plex:32400\npaths:\n  - plex: /share/Filmek/\n    local: /mnt/nas/Filmek/\n"


@pytest.fixture(autouse=True)
def _no_token_from_the_environment(monkeypatch) -> None:
    monkeypatch.delenv("PLEX_TOKEN", raising=False)


def test_an_empty_config_yml_says_so_and_points_to_the_installer(tmp_path, capsys) -> None:
    path = _settings(tmp_path, "")

    with pytest.raises(typer.Exit):
        _load_config(path)

    shown = _shown(capsys)
    assert f"config.yml is empty: {path}" in shown
    assert "Run ./install.sh in the auto-chapter folder to set it up again." in shown
    assert "NoneType" not in shown


def test_a_bad_path_names_the_entry_without_pydantic_text(tmp_path, capsys) -> None:
    path = _settings(tmp_path, GOOD.replace("/mnt/nas/Filmek/", "mnt/x"))

    with pytest.raises(typer.Exit):
        _load_config(path)

    shown = _shown(capsys)
    assert f"There is a problem in config.yml ({path}):" in shown
    assert (
        "paths, entry 1, local: the path on this computer must start with '/' (or '~/'), "
        "got 'mnt/x'" in shown
    )
    assert "Fix this line in the file, then start auto-chapter again." in shown
    assert "pydantic" not in shown
    assert "Value error" not in shown


def test_a_yaml_syntax_error_uses_the_same_form(tmp_path, capsys) -> None:
    path = _settings(tmp_path, "plex:\n  url: http://plex:32400\npaths: [\n")

    with pytest.raises(typer.Exit):
        _load_config(path)

    shown = _shown(capsys)
    assert f"There is a problem in config.yml ({path}):" in shown
    assert "line 4" in shown  # YAML's own pointer to the place stays
    assert "Fix this line in the file, then start auto-chapter again." in shown


def test_a_missing_token_points_to_the_env_file(tmp_path, capsys) -> None:
    path = _settings(tmp_path, GOOD, token=None)

    with pytest.raises(typer.Exit):
        _load_config(path)

    shown = _shown(capsys)
    assert f"No Plex token found in {tmp_path / '.env'}." in shown
    assert (
        "Run ./install.sh in the auto-chapter folder, or put the line "
        "PLEX_TOKEN=<your token> into that file." in shown
    )
    assert "paths:" not in shown  # no hint that sends the user to config.yml


def test_an_ffmpeg_without_h264_hevc_decoders_is_a_warning_not_a_stop(
    monkeypatch, capsys
) -> None:
    """Fedora's ffmpeg-free: every H.264 / HEVC movie would fail at detection.
    Said at the start; movies in other formats still work, so the app goes on."""
    monkeypatch.setattr("auto_chapter.cli.shutil.which", lambda _tool: "/usr/bin/fake")
    monkeypatch.setattr(
        "auto_chapter.cli.ffmpeg_decoder_problem",
        lambda: "This ffmpeg can't decode H.264 / HEVC video, ...",
    )

    _check_required_tools()  # must not raise

    assert "can't decode H.264 / HEVC video" in capsys.readouterr().out
