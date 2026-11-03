# SPDX-FileCopyrightText: 2026 Balazs Katona
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the environment the external tools run in.

mkvmerge (and systemctl) translate their messages into the user's language
(Hungarian on the owner's machine), so every external tool runs with
LC_ALL=C.UTF-8: English messages, and still UTF-8 file names -- plain "C"
makes mkvmerge fail on a name like "Őrült nők.mkv".
"""

import ast
from pathlib import Path

import pytest

from auto_chapter.tool_env import tool_env

SRC = Path(__file__).resolve().parent.parent / "src" / "auto_chapter"


def _subprocess_calls() -> list[tuple[str, ast.Call]]:
    """Every subprocess.run(...) / subprocess.Popen(...) in the app's code."""
    calls = []
    for path in sorted(SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("run", "Popen")
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "subprocess"
            ):
                calls.append((f"{path.relative_to(SRC)}:{node.lineno}", node))
    return calls


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    return next((k.value for k in call.keywords if k.arg == name), None)


def test_tools_run_in_english_with_utf8_file_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANG", "hu_HU.UTF-8")
    monkeypatch.setenv("LANGUAGE", "hu")

    env = tool_env()

    assert env["LC_ALL"] == "C.UTF-8"  # beats LANG and LANGUAGE; never plain "C"
    assert env["LANG"] == "hu_HU.UTF-8"  # the rest of the environment is kept


def test_every_external_tool_gets_the_tool_env() -> None:
    calls = _subprocess_calls()

    assert calls  # the scan still finds them
    without = [
        where
        for where, call in calls
        if not (
            isinstance(env := _keyword(call, "env"), ast.Call)
            and isinstance(env.func, ast.Name)
            and env.func.id == "tool_env"
        )
    ]
    assert without == []


def test_text_output_never_fails_on_a_bad_byte() -> None:
    """A byte that is not UTF-8 (say, in a broken file's metadata) must not
    stop the work with a UnicodeDecodeError."""
    strict = [
        where
        for where, call in _subprocess_calls()
        if isinstance(text := _keyword(call, "text"), ast.Constant)
        and text.value is True
        and not (
            isinstance(errors := _keyword(call, "errors"), ast.Constant)
            and errors.value == "replace"
        )
    ]
    assert strict == []
