"""Tests for batch_control: signal handlers, subprocess registration, force stop.

These tests directly exercise the signal-handling code paths that are hard to
manually verify (Ctrl+C during running operations, subprocess termination, etc).
"""

import os
import signal
import subprocess
import time

import pytest

from auto_chapter.batch_control import (
    _state,
    batch_interrupt,
    cancellable,
    force_stopped,
    register_subprocess,
    stop_requested,
)


@pytest.fixture(autouse=True)
def reset_state():
    """Guarantee each test starts with a fresh batch state."""
    _state.stop_requested = False
    _state.force_stop = False
    _state.set_proc(None)
    yield
    _state.stop_requested = False
    _state.force_stop = False
    _state.set_proc(None)


def _send_sigint_and_wait(delay: float = 0.05) -> None:
    """Deliver SIGINT to this process and wait for the handler to run.

    Python delivers signals to the main thread at bytecode boundaries. A small
    sleep is enough to let the handler execute.
    """
    os.kill(os.getpid(), signal.SIGINT)
    time.sleep(delay)


# ---------------------------------------------------------------------------
# batch_interrupt: first Ctrl+C = graceful, second = force
# ---------------------------------------------------------------------------


def test_batch_interrupt_first_ctrl_c_sets_stop_requested():
    with batch_interrupt():
        assert not stop_requested()
        assert not force_stopped()
        _send_sigint_and_wait()
        assert stop_requested()
        assert not force_stopped()


def test_batch_interrupt_second_ctrl_c_sets_force_stop():
    with batch_interrupt():
        _send_sigint_and_wait()
        _send_sigint_and_wait()
        assert stop_requested()
        assert force_stopped()


def test_batch_interrupt_third_ctrl_c_is_idempotent():
    """A third Ctrl+C after force_stop already set should not crash."""
    with batch_interrupt():
        _send_sigint_and_wait()
        _send_sigint_and_wait()
        _send_sigint_and_wait()  # extra press
        assert force_stopped()


def test_batch_interrupt_resets_flags_on_entry():
    """Entering a new batch context should clear any leftover flags."""
    _state.stop_requested = True
    _state.force_stop = True
    with batch_interrupt():
        assert not stop_requested()
        assert not force_stopped()


def test_batch_interrupt_restores_original_signal_handler():
    """After the context, the original SIGINT handler must be back in place."""
    original = signal.getsignal(signal.SIGINT)
    with batch_interrupt():
        # Our custom handler is active inside
        current = signal.getsignal(signal.SIGINT)
        assert current is not original
    # After exit, original is restored
    restored = signal.getsignal(signal.SIGINT)
    assert restored is original


# ---------------------------------------------------------------------------
# cancellable: any Ctrl+C = immediate force stop
# ---------------------------------------------------------------------------


def test_cancellable_single_ctrl_c_sets_force_stop():
    with cancellable():
        assert not force_stopped()
        _send_sigint_and_wait()
        assert force_stopped()


def test_cancellable_resets_flags_on_entry():
    _state.force_stop = True
    with cancellable():
        assert not force_stopped()


def test_cancellable_restores_original_signal_handler():
    original = signal.getsignal(signal.SIGINT)
    with cancellable():
        current = signal.getsignal(signal.SIGINT)
        assert current is not original
    restored = signal.getsignal(signal.SIGINT)
    assert restored is original


# ---------------------------------------------------------------------------
# Subprocess registration + force termination
# ---------------------------------------------------------------------------


def test_register_none_does_not_crash():
    """Callers can pass None to clear the current subprocess. No crash."""
    register_subprocess(None)
    # State's internal proc is None. Terminating it should be a no-op.
    _state.terminate_current()  # should not raise


def test_terminate_current_after_process_already_exited():
    """If the registered subprocess already exited, terminate is a no-op."""
    proc = subprocess.Popen(["true"])
    proc.wait()
    register_subprocess(proc)
    _state.terminate_current()  # must not raise even though proc is dead


def test_force_stop_terminates_running_subprocess():
    """The core end-to-end test: register a long-running subprocess, trigger a
    double Ctrl+C, verify the process is terminated."""
    # start_new_session so SIGINT from the terminal doesn't also hit the child
    proc = subprocess.Popen(["sleep", "10"], start_new_session=True)
    try:
        with batch_interrupt():
            register_subprocess(proc)
            _send_sigint_and_wait()  # graceful stop, proc still running
            assert proc.poll() is None
            _send_sigint_and_wait()  # force stop -> terminate
            # Wait for termination
            for _ in range(20):
                if proc.poll() is not None:
                    break
                time.sleep(0.05)
        assert proc.poll() is not None, "subprocess should have been terminated"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=1)


def test_cancellable_force_stop_terminates_running_subprocess():
    """Same as above but for cancellable (single Ctrl+C = force)."""
    proc = subprocess.Popen(["sleep", "10"], start_new_session=True)
    try:
        with cancellable():
            register_subprocess(proc)
            _send_sigint_and_wait()  # single press -> force stop
            for _ in range(20):
                if proc.poll() is not None:
                    break
                time.sleep(0.05)
        assert proc.poll() is not None, "subprocess should have been terminated"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=1)


def test_context_exit_clears_registered_subprocess():
    """After the with-block exits, the registered subprocess reference is gone
    so a later terminate call doesn't act on a stale process."""
    proc = subprocess.Popen(["sleep", "0.1"])
    with batch_interrupt():
        register_subprocess(proc)
    proc.wait(timeout=1)
    # After context exit, internal proc ref is cleared
    assert _state._current_proc is None
