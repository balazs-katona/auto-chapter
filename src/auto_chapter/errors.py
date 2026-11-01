"""Shared error-formatting and retry helpers for Plex connectivity.

Lives outside menu.py so plex_client.py can use the same friendly-message
and retry behavior for the *initial* connection attempt (see
PlexClient._connect) without menu.py and plex_client.py importing each
other -- menu.py already imports PlexClient, so the reverse import would be
circular.
"""

import time
from collections.abc import Callable

from requests.exceptions import ConnectionError as ReqConnectionError
from requests.exceptions import Timeout

from auto_chapter.console import console


def retry_on_plex_timeout[T](scan_fn: Callable[[], T], max_attempts: int = 3) -> T:
    """Run scan_fn; silently retry on Plex timeouts and transient network errors.

    Plex often needs a metadata warm-up when first hit after being idle, causing
    early requests to time out. This retries a few times with backoff before
    giving up.

    First failure: silent + short wait. Second failure: short message + longer wait.
    Third failure: raise so the caller shows a proper error.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            return scan_fn()
        except (Timeout, ReqConnectionError):
            if attempt == max_attempts:
                raise
            if attempt >= 2:
                # Also for an address that does not answer at all, so not "slow".
                console.print("[dim]No answer from Plex yet; retrying...[/dim]")
            time.sleep(attempt)  # 1s, then 2s
    raise AssertionError("unreachable")  # satisfies type checkers; loop always returns or raises


def friendly_plex_error(e: Exception) -> str:
    """Turn a raw plexapi/requests exception into a short user-facing sentence."""
    msg = str(e)
    lower = msg.lower()
    if "timed out" in lower or "timeout" in lower:
        return "Plex didn't respond in time. It may be busy indexing; try again in a moment."
    if "connection refused" in lower or "failed to establish" in lower:
        return "Couldn't connect to Plex. Is it running and reachable on the network?"
    if (
        "name or service not known" in lower
        or "failed to resolve" in lower
        or "nodename nor servname" in lower
        or "nameresolutionerror" in lower
    ):
        return "That address doesn't exist. Check the hostname or IP."
    if "no route to host" in lower or "network is unreachable" in lower:
        return "Can't reach that address on the network. Check your connection and the IP."
    if "unauthorized" in lower or "401" in lower:
        return "Plex rejected the token. You may need to update it in Settings."
    return short_error(e)


class MovieReadError(Exception):
    """ffmpeg could not read the movie file. Its message is already user-facing."""


def short_error(e: Exception | str) -> str:
    """Turn a raw exception or error string into a short user-facing sentence.

    Handles common filesystem/OS errors and falls back to a trimmed first line.
    """
    if isinstance(e, MovieReadError):
        return str(e)
    msg = str(e) if isinstance(e, Exception) else e
    lower = msg.lower()
    if "'mkvmerge'" in lower and "no such file" in lower:
        return "mkvmerge isn't installed or isn't on PATH. Check your mkvtoolnix installation."
    if "permission denied" in lower:
        return "Permission denied. Check that you have access to the file or folder."
    if "no such file" in lower or "file not found" in lower:
        return "File not found. It may have been moved, or the drive isn't mounted."
    if "no space left" in lower or "disk full" in lower or "insufficient disk space" in lower:
        return "Not enough disk space. Free up some room and try again."
    if "read-only" in lower:
        return "The location is read-only. Check the drive is mounted for writing."
    if "input/output error" in lower or "i/o error" in lower:
        return "File system error. Check that the drive is connected and healthy."
    if "mkvmerge failed" in lower:
        # mkvmerge's own reason (it runs in English, see tool_env), never a
        # guessed cause: the old "The source may be corrupt" was shown for
        # every failure, a read-only folder included.
        reason = msg.partition("mkvmerge failed:")[2].strip().removeprefix("Error:").strip()
        return f"mkvmerge couldn't write the file: {reason}" if reason else (
            "mkvmerge couldn't write the file."
        )
    if "output suspiciously small" in lower:
        return "The new file came out unexpectedly small. Skipping to protect the original."
    if "streamed" in lower and "movie" in lower:
        return "The movie is currently being streamed. Try again later."
    if "no chapters" in lower:
        return "No chapters were detected."
    # Fallback: first line, trimmed
    first_line = msg.splitlines()[0].strip() if msg else "unknown error"
    return first_line[:120] + ("..." if len(first_line) > 120 else "")
