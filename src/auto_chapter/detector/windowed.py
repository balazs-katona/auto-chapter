"""Windowed scene detection orchestrator.

For each 5-min window, runs blackdetect + scdet simultaneously.
Priority: blackdetect (fade-to-black) > best scdet score.
"""

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from auto_chapter.batch_control import force_stopped
from auto_chapter.config import DetectionConfig
from auto_chapter.detector import blackdetect, ffmpeg, scdet
from auto_chapter.errors import MovieReadError, short_error
from auto_chapter.logging_setup import get_logger
from auto_chapter.models import (
    AUTO_CHAPTER_PREFIX,
    CREDITS_CHAPTER_NAME,
    Chapter,
    DetectionMethod,
)

log = get_logger(__name__)

# The "[in#0 @ 0x55f7...] " in front of ffmpeg's error lines.
_FFMPEG_PREFIX = re.compile(r"^\[[^\]]*\]\s*", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class WindowMatch:
    """Best chapter point found in a single window."""

    method: DetectionMethod
    timestamp: float
    score: float | None = None


def detect_in_window(
    filepath: Path,
    seek_seconds: float,
    window_seconds: float,
    cfg: DetectionConfig,
) -> WindowMatch | None:
    """Find best chapter point in a single window. None if nothing found.

    Raises MovieReadError if ffmpeg could not read the window: its empty output
    would otherwise look like "no scene change here", and the movie would get a
    shorter chapter list written without anyone noticing.
    """
    result = ffmpeg.run_window_detection(
        filepath,
        seek_seconds=seek_seconds,
        window_seconds=window_seconds,
        black_duration=cfg.black_duration,
        black_pix_threshold=cfg.black_pix_threshold,
        black_pic_threshold=cfg.black_pic_threshold,
        scdet_threshold=cfg.scdet_threshold,
    )

    # A Ctrl+C terminates ffmpeg too (exit code != 0); that is a cancel, which
    # the caller already handles, not a broken file.
    if result.returncode != 0 and not force_stopped():
        log.warning(
            "ffmpeg_window_failed",
            filepath=str(filepath),
            start=seek_seconds,
            returncode=result.returncode,
            stderr_tail=result.stderr[-500:],
        )
        raise MovieReadError(
            "Couldn't read the movie file, nothing was written: "
            f"{short_error(_FFMPEG_PREFIX.sub('', result.stderr).strip() or 'ffmpeg failed')}"
        )

    black = blackdetect.parse_first_black(result.stderr, seek_offset=seek_seconds)
    if black is not None:
        return WindowMatch(method=DetectionMethod.BLACK, timestamp=black.new_scene_time)

    scene = scdet.parse_best_scene(result.stderr, seek_offset=seek_seconds)
    if scene is not None:
        return WindowMatch(
            method=DetectionMethod.SCDET,
            timestamp=scene.time,
            score=scene.score,
        )

    return None


def iter_chapter_points(
    filepath: Path,
    duration: float,
    effective_end: float,
    cfg: DetectionConfig,
    on_window_start: Callable[[float], None] | None = None,
) -> Iterator[WindowMatch]:
    """Walk through the film 5 min at a time, yielding chapter matches.

    Args:
        filepath: MKV file to scan.
        duration: total movie duration (seconds).
        effective_end: scan boundary (credits time or duration - end_margin).
        cfg: detection configuration.
        on_window_start: optional callback invoked with the cursor position
            (seconds) before each window's ffmpeg call. Use for progress display.

    Yields:
        WindowMatch per successful detection window.
    """
    min_gap = cfg.min_gap_seconds
    cursor = 0.0

    while cursor + min_gap < effective_end:
        # Bail out early if the user cancelled
        if force_stopped():
            return

        search_from = cursor + min_gap
        remaining = effective_end - search_from
        window = min(float(min_gap), remaining)

        if window < 30:
            # Too small to bother with
            break

        if on_window_start is not None:
            on_window_start(search_from)

        match = detect_in_window(filepath, search_from, window, cfg)

        if match is not None:
            cursor = match.timestamp
            yield match
        else:
            # Couldn't find anything; skip this window and continue
            log.debug(
                "no_match_in_window",
                filepath=str(filepath),
                start=search_from,
                window=window,
            )
            cursor = search_from


def build_chapter_list(
    matches: list[WindowMatch],
    *,
    credits_time: float | None,
) -> list[Chapter]:
    """Convert raw detection matches into a final Chapter list.

    Always includes Chapter 1 at 0:00. Appends Credits chapter if credits_time set.
    """
    chapters: list[Chapter] = [
        Chapter(name=f"{AUTO_CHAPTER_PREFIX} 1", timestamp=0.0, method=DetectionMethod.START)
    ]

    for i, match in enumerate(matches, start=2):
        chapters.append(
            Chapter(
                name=f"{AUTO_CHAPTER_PREFIX} {i}",
                timestamp=match.timestamp,
                method=match.method,
                score=match.score,
            )
        )

    if credits_time is not None:
        chapters.append(
            Chapter(
                name=CREDITS_CHAPTER_NAME,
                timestamp=credits_time,
                method=DetectionMethod.CREDITS,
            )
        )

    return chapters
