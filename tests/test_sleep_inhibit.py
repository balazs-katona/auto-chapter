"""Tests for sleep_inhibit: the idle-sleep lock held while work runs.

Everything is best effort: when the lock cannot be taken, the work still runs.
"""

import os
import shutil
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from auto_chapter import sleep_inhibit
from auto_chapter.sleep_inhibit import keep_awake


class _FakeProc:
    """Stand-in for the systemd-inhibit process."""

    def __init__(self, exits_at_startup: int | None = None, ignores_terminate: bool = False):
        self.exits_at_startup = exits_at_startup
        self.ignores_terminate = ignores_terminate
        self.terminated = False
        self.killed = False

    def wait(self, timeout: float | None = None) -> int:
        if self.killed or (self.terminated and not self.ignores_terminate):
            return 0
        if self.exits_at_startup is not None:
            return self.exits_at_startup
        raise subprocess.TimeoutExpired("systemd-inhibit", timeout or 0)

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True


@pytest.fixture
def fake_popen(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """systemd-inhibit "is installed"; Popen hands out _FakeProc objects."""
    state = SimpleNamespace(calls=[], proc=_FakeProc())
    monkeypatch.setattr(sleep_inhibit.shutil, "which", lambda _name: "/usr/bin/systemd-inhibit")

    def popen(cmd: list[str], **kwargs: object) -> _FakeProc:
        state.calls.append((cmd, kwargs))
        return state.proc

    monkeypatch.setattr(sleep_inhibit.subprocess, "Popen", popen)
    return state


# ---------------------------------------------------------------- keep_awake


def test_the_lock_is_for_sleep_and_idle_and_ends_with_auto_chapter(
    fake_popen: SimpleNamespace,
) -> None:
    with keep_awake() as held:
        assert held is True

    cmd, kwargs = fake_popen.calls[0]
    assert cmd[0] == "systemd-inhibit"
    assert "--what=sleep:idle" in cmd
    assert "--mode=block" in cmd
    # `tail --pid` ends by itself when auto-chapter ends, so the lock can never
    # be left behind -- not even after kill -9.
    assert cmd[cmd.index("tail") :][:3] == ["tail", "--pid", str(os.getpid())]
    # A Ctrl+C in the terminal must not release it (first Ctrl+C of a batch only
    # means "finish this movie").
    assert kwargs["start_new_session"] is True


def test_the_lock_is_released_when_the_block_ends(fake_popen: SimpleNamespace) -> None:
    with keep_awake():
        assert fake_popen.proc.terminated is False

    assert fake_popen.proc.terminated is True


def test_the_lock_is_released_even_when_the_block_raises(fake_popen: SimpleNamespace) -> None:
    with pytest.raises(RuntimeError), keep_awake():
        raise RuntimeError("detection blew up")

    assert fake_popen.proc.terminated is True


def test_a_process_that_will_not_stop_is_killed(fake_popen: SimpleNamespace) -> None:
    fake_popen.proc = _FakeProc(ignores_terminate=True)

    with keep_awake():
        pass

    assert fake_popen.proc.terminated is True
    assert fake_popen.proc.killed is True


def test_without_systemd_inhibit_the_work_still_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sleep_inhibit.shutil, "which", lambda _name: None)

    def must_not_run(*_a: object, **_k: object) -> None:
        raise AssertionError("tried to start systemd-inhibit although it is not installed")

    monkeypatch.setattr(sleep_inhibit.subprocess, "Popen", must_not_run)
    ran = False

    with keep_awake() as held:
        ran = True

    assert held is False
    assert ran is True


def test_a_start_failure_does_not_stop_the_work(
    fake_popen: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_a: object, **_k: object) -> None:
        raise OSError("Exec format error")

    monkeypatch.setattr(sleep_inhibit.subprocess, "Popen", broken)

    with keep_awake() as held:
        assert held is False


def test_a_lock_that_could_not_be_taken_is_reported_as_not_held(
    fake_popen: SimpleNamespace,
) -> None:
    """No logind / no D-Bus: systemd-inhibit exits at once."""
    fake_popen.proc = _FakeProc(exits_at_startup=1)

    with keep_awake() as held:
        assert held is False

    assert fake_popen.proc.terminated is False  # nothing to release


# A real process, not a stand-in: a fake systemd-inhibit that just runs the
# command it is given (what the real one does once it holds the lock). This
# checks the parts the stand-ins cannot: that `tail --pid` really runs, that it
# stays up during the block and is gone afterwards.
@pytest.mark.skipif(shutil.which("tail") is None, reason="needs tail")
def test_a_real_process_lives_during_the_block_and_is_gone_after(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = tmp_path / "systemd-inhibit"
    script.write_text(
        "#!/usr/bin/env bash\n"
        'while [[ "$1" == --* ]]; do shift; done\n'  # skip the lock's own options
        'exec "$@"\n'
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")

    started: list[subprocess.Popen[bytes]] = []
    real_popen = subprocess.Popen

    def recording_popen(*args: object, **kwargs: object) -> "subprocess.Popen[bytes]":
        proc = real_popen(*args, **kwargs)  # type: ignore[call-overload]
        started.append(proc)
        return proc

    monkeypatch.setattr(sleep_inhibit.subprocess, "Popen", recording_popen)

    with keep_awake() as held:
        assert held is True
        assert started[0].poll() is None  # still running during the block

    assert started[0].poll() is not None  # gone afterwards
