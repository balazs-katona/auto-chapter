"""Shutdown scheduler: 's' key toggle during batch + power off after success.

Listens to stdin for 's' keypresses while a batch runs. The user can toggle
the schedule on/off at any time. After the batch completes successfully, the
machine powers off (after a cancellable countdown).

If stdin is not a TTY (e.g. running headless), the toggle is unavailable;
batches behave normally with no shutdown handling.
"""

import contextlib
import select
import subprocess
import sys
import termios
import threading
import time
import tty
from collections.abc import Callable
from typing import Any

from auto_chapter.console import console
from auto_chapter.tool_env import tool_env

SHUTDOWN_TIP_BATCH = "[dim]Press 's' to schedule shutdown after the batch.[/dim]"
SHUTDOWN_TIP_SINGLE = "[dim]Press 's' to schedule shutdown once this movie is written.[/dim]"


class ShutdownToggle:
    """Background stdin listener for the 's' key.

    Run start() with a callback before the batch begins, stop() after.
    Check `scheduled` afterwards to decide whether to power off.

    The listener may be stopped and started again (the choice made so far in
    `scheduled` is kept). It must NOT run while a prompt waits for the user:
    it puts the terminal in cbreak mode (no echo, no line editing) and eats
    every key it does not know, so the prompt would never see the input.
    """

    def __init__(self) -> None:
        self.scheduled = False
        # A fresh Event per start(): a thread that outlived stop()'s join
        # timeout still holds its own (set) Event and ends by itself, instead
        # of being revived by a later start().
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._old_term: list[Any] | None = None

    def start(self, on_toggle: Callable[[bool], None]) -> bool:
        """Begin listening. Returns True if a TTY is available, False otherwise.

        When False, no listening happens; the caller should omit the 's' tip from
        the initial messages.
        """
        if self._thread is not None:
            return True  # already listening; keep the saved terminal mode
        if not sys.stdin.isatty():
            return False
        try:
            self._old_term = termios.tcgetattr(sys.stdin.fileno())
            tty.setcbreak(sys.stdin.fileno())
        except (termios.error, OSError):
            return False
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._poll, args=(on_toggle, self._stop), daemon=True
        )
        self._thread.start()
        return True

    def _poll(self, on_toggle: Callable[[bool], None], stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                ready, _, _ = select.select([sys.stdin], [], [], 0.3)
                if ready:
                    ch = sys.stdin.read(1)
                    if ch in ("s", "S"):
                        self.scheduled = not self.scheduled
                        with contextlib.suppress(Exception):
                            on_toggle(self.scheduled)
            except Exception:
                # Defensive: if stdin closes or terminal goes away, just stop quietly
                return

    def stop(self) -> None:
        """Stop listening and restore the terminal."""
        self._stop.set()
        try:
            if self._thread is not None:
                self._thread.join(timeout=1)
                self._thread = None
        finally:
            # Guaranteed even if join() itself gets interrupted -- an
            # unrestored terminal is a worse outcome than a leaked thread
            # reference, so this must not be skippable.
            if self._old_term is not None:
                with contextlib.suppress(termios.error, OSError):
                    termios.tcsetattr(
                        sys.stdin.fileno(), termios.TCSADRAIN, self._old_term
                    )
                self._old_term = None


def execute_shutdown_with_countdown(seconds: int = 30) -> None:
    """Show a countdown, then run `systemctl poweroff`. Ctrl+C cancels.

    If `systemctl poweroff` fails, prints a friendly message and returns; the
    batch's success is unaffected.
    """
    console.print(
        f"\n[bold yellow]Shutdown scheduled.[/bold yellow] "
        f"Powering off in {seconds} seconds."
    )
    console.print("[dim]Press Ctrl+C to cancel.[/dim]\n")

    try:
        for i in range(seconds, 0, -1):
            label = "second" if i == 1 else "seconds"
            # \r + end='' overwrites the same line each tick
            console.print(
                f"  Powering off in [bold]{i:>2}[/bold] {label}...",
                end="\r",
            )
            time.sleep(1)
        console.print()  # finish the last line
    except KeyboardInterrupt:
        console.print("\n[yellow]Shutdown cancelled.[/yellow]")
        return

    console.print("[dim]Running 'systemctl poweroff'...[/dim]")
    try:
        result = subprocess.run(
            ["systemctl", "poweroff"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            env=tool_env(),  # its error is shown as is: English, like the rest
        )
        if result.returncode != 0:
            msg = result.stderr.strip() or "unknown error"
            console.print(f"[red]Shutdown failed:[/red] {msg}")
            console.print(
                "[dim]The batch finished successfully; the machine is left running.[/dim]"
            )
    except FileNotFoundError:
        console.print("[red]Shutdown failed:[/red] 'systemctl' command not found.")
        console.print(
            "[dim]The batch finished successfully; the machine is left running.[/dim]"
        )
    except subprocess.TimeoutExpired:
        console.print("[red]Shutdown failed:[/red] command timed out.")
        console.print(
            "[dim]The batch finished successfully; the machine is left running.[/dim]"
        )
