# SPDX-FileCopyrightText: 2026 Balazs Katona
# SPDX-License-Identifier: GPL-3.0-or-later
"""Credits timestamp detection.

Priority:
1. File chapters with credits-related names
2. Plex credits marker
3. None (caller falls back to end_margin)
"""

from pathlib import Path
from typing import Any

from auto_chapter.detector import ffmpeg
from auto_chapter.models import CreditsSource

# Chapter names that indicate the start of end credits.
# Compared case-insensitively after stripping.
CREDITS_KEYWORDS: frozenset[str] = frozenset({"credits", "end credits", "outro"})


def detect_from_file(filepath: Path) -> float | None:
    """Look for a credits-named chapter in the file. Returns timestamp or None."""
    for ch in ffmpeg.get_chapters(filepath):
        tags = ch.get("tags") or {}
        if not isinstance(tags, dict):
            continue
        title = str(tags.get("title", "")).strip().lower()
        if title in CREDITS_KEYWORDS:
            start = ch.get("start_time")
            if start is None:
                continue
            try:
                return float(start)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
    return None


def detect_from_plex(movie: Any) -> float | None:
    """Extract credits start time from a plexapi Movie's markers. None if absent."""
    markers = getattr(movie, "markers", None) or []
    for mk in markers:
        if getattr(mk, "type", None) == "credits":
            start_ms = getattr(mk, "start", 0) or 0
            return float(start_ms) / 1000.0
    return None


def resolve_credits(
    filepath: Path,
    plex_movie: Any | None,
) -> tuple[float | None, CreditsSource]:
    """Resolve credits timestamp using file -> Plex precedence.

    Returns:
        (timestamp_seconds_or_None, source_enum)
    """
    file_ts = detect_from_file(filepath)
    if file_ts is not None:
        return file_ts, CreditsSource.FILE

    if plex_movie is not None:
        plex_ts = detect_from_plex(plex_movie)
        if plex_ts is not None:
            return plex_ts, CreditsSource.PLEX

    return None, CreditsSource.NONE
