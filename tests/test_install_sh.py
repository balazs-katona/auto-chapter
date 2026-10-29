"""Tests for the questions of install.sh: with no input at all (started with a
double click, from a script, or Ctrl+D), every question used to print its
"Please type ..." line forever instead of stopping."""

import re
import subprocess
from pathlib import Path

INSTALL_SH = Path(__file__).resolve().parent.parent / "install.sh"
STOPPED = "No input received -- installation stopped."


def _run_ask(stdin: str) -> subprocess.CompletedProcess[str]:
    """Run install.sh's own ask() (and the warn() it uses) on one question."""
    text = INSTALL_SH.read_text(encoding="utf-8")
    warn = re.search(r"^warn\(\).*$", text, re.MULTILINE)
    ask = re.search(r"^ask\(\) \{\n.*?^\}$", text, re.MULTILINE | re.DOTALL)
    assert warn and ask, "install.sh has no ask() / warn() function"
    script = f'{warn.group(0)}\n{ask.group(0)}\nask "Question: " answer\necho "answer=[$answer]"\n'
    return subprocess.run(
        ["bash", "-c", script], input=stdin, capture_output=True, text=True, timeout=5, check=False
    )


def test_no_input_stops_instead_of_asking_forever() -> None:
    done = _run_ask("")

    assert done.returncode == 1
    assert STOPPED in done.stdout
    assert "answer=" not in done.stdout  # nothing after the question ran


def test_a_normal_answer_is_read() -> None:
    done = _run_ask("  192.168.1.10:32400\n")

    assert done.returncode == 0
    assert "answer=[192.168.1.10:32400]" in done.stdout  # read itself drops the spaces
    assert STOPPED not in done.stdout


def test_a_last_answer_without_a_final_newline_still_counts() -> None:
    done = _run_ask("n")

    assert done.returncode == 0
    assert "answer=[n]" in done.stdout


def test_every_question_goes_through_ask() -> None:
    """A new question with a bare `read` would bring the endless loop back.
    The only other `read`s: the one inside ask() and "Start it now?", which
    treats no input as 'n' (the installation is already complete there)."""
    allowed = ('read -e -rp "$1" "$2"', 'read -e -rp "Start it now? (y/n): " start_now')
    reads = [
        line.strip()
        for line in INSTALL_SH.read_text(encoding="utf-8").splitlines()
        if re.search(r"(^|\s|!)read\s", line) and not line.lstrip().startswith("#")
    ]

    assert reads  # the pattern still finds them
    assert [r for r in reads if not any(a in r for a in allowed)] == []


def _code_lines() -> list[str]:
    return [
        line.strip()
        for line in INSTALL_SH.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_users_get_no_dev_tools() -> None:
    """pytest, ruff and mypy are for development only: the installer syncs
    the app's own dependencies and does not run the test suite."""
    syncs = [line for line in _code_lines() if "uv sync" in line]

    assert syncs  # the dependency step is still there
    assert [line for line in syncs if "dev" in line] == []
    assert [line for line in _code_lines() if "pytest" in line] == []


def test_quit_installer_says_that_nothing_was_saved() -> None:
    """"3) Quit installer" used to end without a word."""
    quits = [line for line in _code_lines() if line.startswith("3)")]

    assert len(quits) == 2  # after a failed connection and after a bad token
    for line in quits:
        assert "Installation stopped -- nothing was saved." in line, line
