# SPDX-FileCopyrightText: 2026 Balazs Katona
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for auto_chapter.errors: retry_on_plex_timeout, friendly_plex_error,
short_error.

These were extracted from menu.py into their own module so plex_client.py
could reuse them for the initial connection attempt without a circular
import (menu.py already imports PlexClient). No existing tests covered them
before the extraction -- this fills that gap.
"""

import pytest
from requests.exceptions import ConnectionError as ReqConnectionError
from requests.exceptions import Timeout

from auto_chapter.errors import friendly_plex_error, retry_on_plex_timeout, short_error

# ---------------------------------------------------------------------------
# retry_on_plex_timeout
# ---------------------------------------------------------------------------


def test_succeeds_on_first_try_no_retry_needed(monkeypatch) -> None:
    monkeypatch.setattr("auto_chapter.errors.time.sleep", lambda *_: None)
    calls = {"count": 0}

    def fn():
        calls["count"] += 1
        return "result"

    result = retry_on_plex_timeout(fn)

    assert result == "result"
    assert calls["count"] == 1


def test_succeeds_after_one_retry(monkeypatch) -> None:
    monkeypatch.setattr("auto_chapter.errors.time.sleep", lambda *_: None)
    calls = {"count": 0}

    def fn():
        calls["count"] += 1
        if calls["count"] < 2:
            raise Timeout("slow")
        return "result"

    result = retry_on_plex_timeout(fn)

    assert result == "result"
    assert calls["count"] == 2


def test_raises_after_exhausting_all_attempts(monkeypatch) -> None:
    monkeypatch.setattr("auto_chapter.errors.time.sleep", lambda *_: None)
    calls = {"count": 0}

    def always_times_out():
        calls["count"] += 1
        raise Timeout("still slow")

    with pytest.raises(Timeout):
        retry_on_plex_timeout(always_times_out, max_attempts=3)

    assert calls["count"] == 3


def test_connection_error_also_triggers_retry(monkeypatch) -> None:
    monkeypatch.setattr("auto_chapter.errors.time.sleep", lambda *_: None)
    calls = {"count": 0}

    def fn():
        calls["count"] += 1
        if calls["count"] < 2:
            raise ReqConnectionError("refused")
        return "ok"

    assert retry_on_plex_timeout(fn) == "ok"
    assert calls["count"] == 2


def test_non_retryable_exception_propagates_immediately(monkeypatch) -> None:
    """An exception that isn't Timeout/ConnectionError shouldn't be retried
    at all -- e.g. an auth failure should surface right away, not after
    wasting several seconds retrying something that will never succeed."""
    monkeypatch.setattr("auto_chapter.errors.time.sleep", lambda *_: None)
    calls = {"count": 0}

    def fn():
        calls["count"] += 1
        raise ValueError("not a network problem")

    with pytest.raises(ValueError):
        retry_on_plex_timeout(fn)

    assert calls["count"] == 1


# ---------------------------------------------------------------------------
# friendly_plex_error
# ---------------------------------------------------------------------------


def test_friendly_error_timeout() -> None:
    msg = friendly_plex_error(Timeout("Read timed out"))
    assert "didn't respond in time" in msg


def test_friendly_error_connection_refused() -> None:
    msg = friendly_plex_error(ReqConnectionError("Connection refused"))
    assert "Couldn't connect to Plex" in msg


def test_friendly_error_dns_failure() -> None:
    msg = friendly_plex_error(Exception("Name or service not known"))
    assert "doesn't exist" in msg


def test_friendly_error_unauthorized() -> None:
    msg = friendly_plex_error(Exception("401 Unauthorized"))
    assert "rejected the token" in msg


def test_friendly_error_unrecognized_falls_back_to_short_error() -> None:
    msg = friendly_plex_error(Exception("some completely novel failure"))
    assert msg == "some completely novel failure"


# ---------------------------------------------------------------------------
# short_error
# ---------------------------------------------------------------------------


def test_short_error_permission_denied() -> None:
    assert "Permission denied" in short_error(Exception("Permission denied: '/mnt/x'"))


def test_short_error_mkvmerge_missing() -> None:
    msg = short_error(
        FileNotFoundError("[Errno 2] No such file or directory: 'mkvmerge'")
    )
    assert "mkvmerge isn't installed" in msg


def test_short_error_disk_full() -> None:
    assert "disk space" in short_error(Exception("insufficient disk space"))


def test_short_error_accepts_plain_string() -> None:
    msg = short_error("output suspiciously small (100 vs 900)")
    assert "unexpectedly small" in msg


def test_short_error_fallback_truncates_long_messages() -> None:
    long_msg = "x" * 200
    result = short_error(Exception(long_msg))
    assert len(result) <= 123  # 120 chars + "..."
    assert result.endswith("...")


def test_the_retry_message_does_not_claim_plex_is_slow(monkeypatch, capsys) -> None:
    """It also shows when the address does not answer at all (wrong IP, Plex
    off), so "slow" would be a guess. "No answer yet" is true either way."""
    monkeypatch.setattr("auto_chapter.errors.time.sleep", lambda *_: None)

    def unreachable():
        raise ReqConnectionError("No route to host")

    with pytest.raises(ReqConnectionError):
        retry_on_plex_timeout(unreachable)

    out = capsys.readouterr().out
    assert "No answer from Plex yet; retrying..." in out
    assert "slow" not in out


def test_short_error_mkvmerge_failure_shows_mkvmerge_s_reason() -> None:
    """Never a guessed cause: the old text always said "The source may be
    corrupt", whatever mkvmerge had reported."""
    msg = short_error(
        "mkvmerge failed: Error: The file '/m/F.mkv.autochapter.tmp' could not be "
        "opened for writing: open file error."
    )

    assert msg == (
        "mkvmerge couldn't write the file: The file '/m/F.mkv.autochapter.tmp' could "
        "not be opened for writing: open file error."
    )


def test_short_error_mkvmerge_failure_without_a_message() -> None:
    assert short_error("mkvmerge failed: ") == "mkvmerge couldn't write the file."
