# SPDX-FileCopyrightText: 2026 Balazs Katona
# SPDX-License-Identifier: GPL-3.0-or-later
"""Coordinated Ctrl+C handling for batch operations.

Design: the signal handler only sets flags; it never raises. The main code
checks the flags at safe points (between subprocess calls, between movies)
and exits cleanly.

- First Ctrl+C: sets stop_requested. The current movie finishes processing,
  then the batch loop breaks.
- Second Ctrl+C: sets force_stop and sends SIGTERM to the currently running
  subprocess (FFmpeg or mkvmerge). The subprocess exits, the main code checks
  force_stop after the subprocess returns and propagates an early exit.
"""

import signal
import subprocess
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

from auto_chapter.console import console


class _BatchState:
    def __init__(self) -> None:
        self.stop_requested = False
        self.force_stop = False
        self._current_proc: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()

    def set_proc(self, proc: subprocess.Popen[str] | None) -> None:
        with self._lock:
            self._current_proc = proc

    def terminate_current(self) -> None:
        with self._lock:
            proc = self._current_proc
        if proc is None or proc.poll() is not None:
            return
        with suppress(Exception):
            proc.terminate()
        threading.Thread(target=_say_when_slow, args=(proc,), daemon=True).start()


# A terminated mkvmerge first sends what it has written to the disk; on a
# network share that took about a minute (owner, 2026-11-14), and the screen
# showed nothing. Say so, only when it really takes a while.
SLOW_EXIT_SECONDS = 2.0


def _say_when_slow(proc: "subprocess.Popen[str]", delay: float = SLOW_EXIT_SECONDS) -> None:
    """After a terminate: tell the user we are waiting, if the tool is not gone soon."""
    waited = 0.0
    while waited < delay:
        if proc.poll() is not None:
            return
        time.sleep(0.1)
        waited += 0.1
    if proc.poll() is None:
        args = proc.args if isinstance(proc.args, list) else [str(proc.args)]
        tool = Path(str(args[0])).name if args else "the tool"
        console.print(
            f"[dim]Waiting for {tool} to close the movie file -- on a network "
            "share this can take a minute.[/dim]"
        )


_state = _BatchState()


def register_subprocess(proc: subprocess.Popen[str] | None) -> None:
    """Called by subprocess wrappers to track the running child."""
    _state.set_proc(proc)


def stop_requested() -> bool:
    """True if a graceful stop or force stop has been requested."""
    return _state.stop_requested or _state.force_stop


def force_stopped() -> bool:
    """True if a forced (second Ctrl+C) stop has been requested."""
    return _state.force_stop


@contextmanager
def batch_interrupt() -> Iterator[None]:
    """Install Ctrl+C handler for multi-movie batches: first = graceful, second = force.

    First Ctrl+C signals the batch to stop after the current movie finishes
    cleanly. Second Ctrl+C terminates the currently-running subprocess and
    stops immediately.
    """
    _state.stop_requested = False
    _state.force_stop = False
    original = signal.getsignal(signal.SIGINT)

    def handler(_signum: int, _frame: object) -> None:
        # Signal handlers only set flags here. They do NOT raise exceptions.
        # Uses the shared console (not a bare print()) so this message
        # correctly interleaves with an active Progress/Live display instead
        # of corrupting its in-place rendering -- Rich's Live coordination
        # only works for output that goes through the same Console object.
        if not _state.stop_requested:
            _state.stop_requested = True
            console.print(
                "\n\n[bold yellow]Stop requested.[/bold yellow] "
                "Will stop cleanly after the current movie.\n"
                "[dim]Press Ctrl+C again to stop immediately.[/dim]\n"
            )
        else:
            _state.force_stop = True
            _state.terminate_current()
            console.print(
                "\n[bold red]Stopping immediately.[/bold red] "
                "The original file is left intact.\n"
            )

    signal.signal(signal.SIGINT, handler)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, original)
        _state.set_proc(None)


@contextmanager
def cancellable() -> Iterator[None]:
    """Install Ctrl+C handler for single-movie operations: any press cancels immediately.

    Use this when only one movie is being processed -- the user pressing Ctrl+C
    obviously wants to cancel that one operation, so no graceful-first nuance.
    """
    _state.stop_requested = False
    _state.force_stop = False
    original = signal.getsignal(signal.SIGINT)
    already_fired = [False]

    def handler(_signum: int, _frame: object) -> None:
        if already_fired[0]:
            # Second Ctrl+C: restore default so user can really kill us
            signal.signal(signal.SIGINT, original)
            return
        already_fired[0] = True
        _state.force_stop = True
        _state.terminate_current()
        console.print("\n[bold red]Cancelling.[/bold red] The original file is not modified.\n")

    signal.signal(signal.SIGINT, handler)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, original)
        _state.set_proc(None)
