# SPDX-FileCopyrightText: 2026 Balazs Katona
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for shutdown: ShutdownToggle behavior and countdown cancellation.

These verify the parts that are hard to check manually:
- ShutdownToggle doesn't touch the terminal when stdin isn't a TTY (headless case)
- Terminal state is restored on stop() even if the callback raised
- Ctrl+C during the countdown cancels the shutdown (does NOT run systemctl)
- systemctl failure is handled gracefully (no crash)
"""

import subprocess
import sys
import time
from unittest.mock import MagicMock, patch

from auto_chapter.shutdown import ShutdownToggle, execute_shutdown_with_countdown

# ---------------------------------------------------------------------------
# ShutdownToggle: TTY detection and terminal state restoration
# ---------------------------------------------------------------------------


def test_start_returns_false_when_stdin_is_not_a_tty():
    """Headless run (piped stdin) must gracefully return False and skip listening."""
    toggle = ShutdownToggle()
    with patch("sys.stdin.isatty", return_value=False):
        result = toggle.start(lambda scheduled: None)
    assert result is False
    assert toggle._thread is None
    assert toggle._old_term is None


def test_start_returns_false_when_termios_fails():
    """Some terminals don't support tcgetattr; should not crash the batch."""
    toggle = ShutdownToggle()
    with patch("sys.stdin.isatty", return_value=True), \
         patch("termios.tcgetattr", side_effect=OSError("nope")):
        result = toggle.start(lambda scheduled: None)
    assert result is False


def test_stop_is_idempotent_when_never_started():
    """Calling stop() without a prior start() must not crash."""
    toggle = ShutdownToggle()
    toggle.stop()  # should not raise
    toggle.stop()  # calling twice must also be safe


def test_stop_restores_terminal_state():
    """Guarantee terminal isn't left in cbreak mode after the batch."""
    toggle = ShutdownToggle()
    fake_old_term = ["fake", "state"]

    with patch("sys.stdin.isatty", return_value=True), \
         patch("sys.stdin.fileno", return_value=0), \
         patch("termios.tcgetattr", return_value=fake_old_term), \
         patch("tty.setcbreak"), \
         patch("termios.tcsetattr") as mock_setattr:
        assert toggle.start(lambda scheduled: None) is True
        toggle.stop()

    mock_setattr.assert_called_once()
    # Second arg is the mode we're restoring
    assert mock_setattr.call_args[0][2] == fake_old_term
    assert toggle._old_term is None


def test_stop_restores_terminal_even_when_thread_hangs():
    """If the poll thread is slow to exit, we still restore terminal state."""
    toggle = ShutdownToggle()
    fake_old_term = ["state"]

    with patch("sys.stdin.isatty", return_value=True), \
         patch("sys.stdin.fileno", return_value=0), \
         patch("termios.tcgetattr", return_value=fake_old_term), \
         patch("tty.setcbreak"), \
         patch("termios.tcsetattr") as mock_setattr, \
         patch("select.select", side_effect=lambda *a, **k: ([], [], [])):
        assert toggle.start(lambda scheduled: None) is True
        toggle.stop()

    mock_setattr.assert_called_once()


def test_toggle_can_be_stopped_and_started_again():
    """The listener is paused around a prompt and resumed: both runs must work."""
    toggle = ShutdownToggle()
    calls: list[bool] = []
    reads = list("s")

    def fake_read(_n):
        if reads:
            return reads.pop(0)
        time.sleep(0.02)
        return ""

    with patch("sys.stdin.isatty", return_value=True), \
         patch("sys.stdin.fileno", return_value=0), \
         patch("termios.tcgetattr", return_value=["old"]), \
         patch("tty.setcbreak") as mock_cbreak, \
         patch("termios.tcsetattr") as mock_setattr, \
         patch("select.select", side_effect=lambda *a, **k: ([sys.stdin], [], [])), \
         patch("sys.stdin.read", side_effect=fake_read):
        assert toggle.start(calls.append) is True
        time.sleep(0.2)
        toggle.stop()
        assert toggle._thread is None
        assert mock_setattr.call_count == 1  # terminal restored while paused

        reads.append("s")
        assert toggle.start(calls.append) is True
        assert toggle._thread is not None and toggle._thread.is_alive()
        time.sleep(0.2)
        toggle.stop()

    assert calls == [True, False]  # the choice made earlier carried over
    assert mock_cbreak.call_count == 2
    assert mock_setattr.call_count == 2


def test_start_twice_does_not_spawn_a_second_listener():
    toggle = ShutdownToggle()
    with patch("sys.stdin.isatty", return_value=True), \
         patch("sys.stdin.fileno", return_value=0), \
         patch("termios.tcgetattr", return_value=["old"]), \
         patch("tty.setcbreak") as mock_cbreak, \
         patch("termios.tcsetattr"), \
         patch("select.select", side_effect=lambda *a, **k: ([], [], [])):
        assert toggle.start(lambda scheduled: None) is True
        first = toggle._thread
        assert toggle.start(lambda scheduled: None) is True
        assert toggle._thread is first
        toggle.stop()

    assert mock_cbreak.call_count == 1


# ---------------------------------------------------------------------------
# ShutdownToggle: the 's' key toggles state; other keys are ignored
# ---------------------------------------------------------------------------


def test_toggle_flips_scheduled_on_s_key():
    """Simulate 's' key input; verify state flips and callback fires."""
    toggle = ShutdownToggle()
    callback_calls: list[bool] = []
    reads_queue = list("sxs")  # 's' -> toggle, 'x' -> ignored, 's' -> toggle back

    def fake_read(_n):
        if reads_queue:
            return reads_queue.pop(0)
        time.sleep(0.05)  # let the loop exit gracefully
        return ""

    with patch("sys.stdin.isatty", return_value=True), \
         patch("sys.stdin.fileno", return_value=0), \
         patch("termios.tcgetattr", return_value=[]), \
         patch("tty.setcbreak"), \
         patch("termios.tcsetattr"), \
         patch("sys.stdin.read", side_effect=fake_read), \
         patch("select.select", side_effect=lambda *a, **k: ([sys.stdin], [], [])):
        assert toggle.start(lambda scheduled: callback_calls.append(scheduled)) is True
        # Give the polling thread a moment to consume all the fake input
        time.sleep(0.2)
        toggle.stop()

    # 's' flipped once (True), 'x' ignored, 's' flipped again (False)
    assert callback_calls == [True, False]
    assert toggle.scheduled is False


def test_callback_exception_does_not_kill_the_toggle_thread():
    """A user-facing print in the callback might raise; toggle must keep working."""
    toggle = ShutdownToggle()
    reads_queue = list("ss")
    call_count = [0]

    def bad_callback(_scheduled):
        call_count[0] += 1
        raise RuntimeError("callback blew up")

    def fake_read(_n):
        if reads_queue:
            return reads_queue.pop(0)
        time.sleep(0.05)
        return ""

    with patch("sys.stdin.isatty", return_value=True), \
         patch("sys.stdin.fileno", return_value=0), \
         patch("termios.tcgetattr", return_value=[]), \
         patch("tty.setcbreak"), \
         patch("termios.tcsetattr"), \
         patch("sys.stdin.read", side_effect=fake_read), \
         patch("select.select", side_effect=lambda *a, **k: ([sys.stdin], [], [])):
        assert toggle.start(bad_callback) is True
        time.sleep(0.2)
        toggle.stop()

    # Both toggles fired, even though each raised
    assert call_count[0] == 2


# ---------------------------------------------------------------------------
# execute_shutdown_with_countdown: cancellation and error handling
# ---------------------------------------------------------------------------


def test_countdown_cancelled_by_keyboard_interrupt_does_not_call_systemctl():
    """Ctrl+C during countdown must cancel; systemctl poweroff never runs."""
    call_count = [0]

    def fake_sleep(_seconds):
        call_count[0] += 1
        raise KeyboardInterrupt()  # simulate Ctrl+C after the first tick

    with patch("time.sleep", side_effect=fake_sleep), \
         patch("subprocess.run") as mock_run:
        execute_shutdown_with_countdown(seconds=3)

    assert mock_run.called is False
    assert call_count[0] == 1  # only the first sleep was reached


def test_countdown_runs_systemctl_after_finishing():
    """When the countdown completes without interruption, poweroff is invoked."""
    result = MagicMock(returncode=0, stderr="")

    with patch("time.sleep"), patch("subprocess.run", return_value=result) as mock_run:
        execute_shutdown_with_countdown(seconds=2)

    mock_run.assert_called_once()
    assert mock_run.call_args[0][0] == ["systemctl", "poweroff"]


def test_countdown_handles_systemctl_missing():
    """Some minimal systems don't have systemctl; must not crash."""
    with patch("time.sleep"), \
         patch("subprocess.run", side_effect=FileNotFoundError):
        # Just verify no exception escapes
        execute_shutdown_with_countdown(seconds=1)


def test_countdown_handles_systemctl_nonzero_exit():
    """Permission denied or similar: friendly message, no crash."""
    result = MagicMock(returncode=1, stderr="Access denied")

    with patch("time.sleep"), patch("subprocess.run", return_value=result):
        execute_shutdown_with_countdown(seconds=1)


def test_countdown_handles_systemctl_timeout():
    """subprocess.run might time out; must not propagate."""
    with patch("time.sleep"), \
         patch("subprocess.run", side_effect=subprocess.TimeoutExpired(["systemctl"], 10)):
        execute_shutdown_with_countdown(seconds=1)
