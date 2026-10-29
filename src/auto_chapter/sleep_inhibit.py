"""Keep the computer awake while auto-chapter works.

While detection or a write runs, the machine must not go to sleep because it
was idle (a long batch can run for hours with nobody touching the keyboard).
This takes a systemd "sleep:idle" inhibitor lock for exactly that time.

What this does NOT cover: closing the laptop lid. logind ignores sleep/idle
locks for the lid switch by default (LidSwitchIgnoreInhibited=yes), and KDE
treats it as intentional (a closed lid means "put it in the bag"). Nothing is
done about the lid on purpose: a laptop user knows what closing it does.

Everything here is best effort: no systemd, no logind, no `tail --pid`, or any
other failure just means "no lock" -- the work itself is never affected.
"""

import os
import shutil
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager

from auto_chapter.logging_setup import get_logger

log = get_logger(__name__)

# How long to wait to see whether systemd-inhibit really got its lock: it exits
# at once when logind/D-Bus is not there.
_STARTUP_CHECK_SECONDS = 0.3


def _start() -> "subprocess.Popen[bytes] | None":
    """Take the lock. Returns the process that holds it, or None."""
    if shutil.which("systemd-inhibit") is None:
        log.info("sleep_inhibit_unavailable", reason="systemd-inhibit not found")
        return None

    # The lock lives as long as the command runs. `tail --pid` ends by itself
    # when auto-chapter ends, whatever way it ends (even kill -9), so the lock
    # cannot be left behind. A new session keeps a Ctrl+C in the terminal from
    # killing it: the first Ctrl+C of a batch only means "finish this movie".
    cmd = [
        "systemd-inhibit",
        "--what=sleep:idle",
        "--who=auto-chapter",
        "--why=Detecting or writing chapter markers",
        "--mode=block",
        "tail",
        "--pid",
        str(os.getpid()),
        "-f",
        "/dev/null",
    ]
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as e:
        log.info("sleep_inhibit_unavailable", reason=str(e))
        return None

    try:
        returncode = proc.wait(timeout=_STARTUP_CHECK_SECONDS)
    except subprocess.TimeoutExpired:
        return proc  # still running: the lock is held
    log.info("sleep_inhibit_unavailable", reason=f"systemd-inhibit exited with {returncode}")
    return None


def _stop(proc: "subprocess.Popen[bytes]") -> None:
    """Let go of the lock. Never raises."""
    try:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.info("sleep_inhibit_release_problem", error=str(e))


@contextmanager
def keep_awake() -> Iterator[bool]:
    """Hold a sleep:idle lock for the duration of the `with` block.

    Yields True if the lock is held, False if it could not be taken (the block
    runs either way).
    """
    proc = _start()
    try:
        yield proc is not None
    finally:
        if proc is not None:
            _stop(proc)
