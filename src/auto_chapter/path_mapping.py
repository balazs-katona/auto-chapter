# SPDX-FileCopyrightText: 2026 Balazs Katona
# SPDX-License-Identifier: GPL-3.0-or-later
"""Path mapping between Plex paths and local filesystem paths."""

import threading
from dataclasses import dataclass
from pathlib import Path

from auto_chapter.config import PathMapping

# Seconds, per folder. A healthy but busy CIFS share (QNAP, while ffmpeg reads a
# movie from it) was measured answering a plain is_dir() in up to 7.7s; 2s
# reported such folders as "may be hung" and the scans skipped their movies.
DEFAULT_REACHABILITY_TIMEOUT = 10.0


@dataclass(frozen=True, slots=True)
class FolderCheck:
    """Result of checking one folder's reachability."""

    local: str
    reachable: bool | None
    """True/False if the check completed in time; None if it timed out
    (the folder may be on a hung mount -- see check_dir_with_timeout)."""


def check_dir_with_timeout(path: str, timeout: float = DEFAULT_REACHABILITY_TIMEOUT) -> bool | None:
    """Check whether `path` is a reachable directory, giving up after
    `timeout` seconds instead of blocking indefinitely.

    A network mount that's present but unresponsive (D-state) makes even a
    plain Path.is_dir() block forever -- no signal, not even SIGKILL, can
    interrupt that at the OS level (see the Ctrl+C/network-mount limitation
    documented in CLAUDE.md). Running the check in a background thread and
    only *joining* it with a timeout sidesteps this: the calling code
    always gets control back within `timeout` seconds. The thread itself
    may keep running, abandoned, until the hung I/O eventually resolves --
    harmless for a short-lived interactive CLI process (not a concern the
    way it would be for a long-running daemon accumulating leaked threads
    over days). Public (not PathMapper-internal) because menu.py's
    "Add folder mapping" flow also needs this for a freshly-typed path
    that isn't in any mapping yet.
    """
    result: list[bool] = []

    def check() -> None:
        result.append(Path(path).is_dir())

    thread = threading.Thread(target=check, daemon=True)
    thread.start()
    thread.join(timeout=timeout)
    if thread.is_alive():
        return None  # gave up waiting; thread may still be stuck
    return result[0] if result else None


class PathMapper:
    """Converts between Plex server paths and local filesystem paths."""

    def __init__(self, mappings: list[PathMapping]):
        self._mappings = mappings

    @property
    def mappings(self) -> list[PathMapping]:
        """All configured mappings (including disabled ones)."""
        return self._mappings

    def plex_to_local(self, plex_path: str) -> Path | None:
        """Translate a Plex path to a local filesystem Path.

        Disabled mappings are ignored as if they didn't exist.
        """
        for m in self._mappings:
            if not m.enabled:
                continue
            if plex_path.startswith(m.plex):
                return Path(plex_path.replace(m.plex, m.local, 1))
        return None

    def local_to_plex(self, local_path: Path | str) -> str | None:
        """Translate a local path back to Plex path."""
        s = str(local_path)
        for m in self._mappings:
            if not m.enabled:
                continue
            if s.startswith(m.local):
                return s.replace(m.local, m.plex, 1)
        return None

    def has_any_mapping_for(self, plex_path: str) -> bool:
        """True if any mapping (enabled or not) matches this Plex path."""
        return any(plex_path.startswith(m.plex) for m in self._mappings)

    def has_enabled_mapping_for(self, plex_path: str) -> bool:
        """True if an enabled mapping matches this Plex path."""
        return any(m.enabled and plex_path.startswith(m.plex) for m in self._mappings)

    def check_enabled_folders(
        self, timeout: float = DEFAULT_REACHABILITY_TIMEOUT
    ) -> list[FolderCheck]:
        """Check reachability of every enabled mapping's local folder.

        Each folder gets its own bounded `timeout` -- so even if every
        single enabled folder happens to be on a hung mount, the total
        wait is bounded (num_folders * timeout), never indefinite. Callers
        can always proceed and let the user act (e.g. disable or remove a
        bad mapping) instead of getting stuck with no way out. Disabled
        mappings are skipped entirely -- an inactive mapping's folder
        being down isn't actionable right now.
        """
        return [
            FolderCheck(local=m.local, reachable=check_dir_with_timeout(m.local, timeout))
            for m in self._mappings
            if m.enabled
        ]

    def unreachable_enabled_folders(
        self, timeout: float = DEFAULT_REACHABILITY_TIMEOUT
    ) -> list[str]:
        """Local paths of enabled mappings that are unreachable, or that
        couldn't be checked in time -- both are treated the same here
        ("don't count on this folder working right now"); use
        check_enabled_folders() directly if the distinction matters to the
        caller (e.g. showing *why* a folder looks unavailable).
        """
        return [
            c.local
            for c in self.check_enabled_folders(timeout)
            if c.reachable is not True
        ]

    def enabled_count(self) -> int:
        """Number of currently-enabled mappings."""
        return sum(1 for m in self._mappings if m.enabled)
