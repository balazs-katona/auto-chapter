# SPDX-FileCopyrightText: 2026 Balazs Katona
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pipeline orchestrator: scan -> detect -> write -> analyze."""

import time

from rich.progress import (
    BarColumn,
    Progress,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)

from auto_chapter.batch_control import force_stopped
from auto_chapter.chapter import credits
from auto_chapter.config import Config
from auto_chapter.console import console
from auto_chapter.detector.windowed import (
    WindowMatch,
    build_chapter_list,
    iter_chapter_points,
)
from auto_chapter.logging_setup import get_logger
from auto_chapter.models import DetectionResult, Movie, ProcessingResult
from auto_chapter.plex_client import PlexClient
from auto_chapter.remuxer import write_chapters_and_remux

log = get_logger(__name__)


def _format_time(seconds: float) -> str:
    """Format seconds as H:MM:SS."""
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


class Orchestrator:
    """Coordinates the full pipeline: detect, write, analyze."""

    def __init__(self, config: Config, plex: PlexClient):
        self.config = config
        self.plex = plex

    def detect_movie(self, movie: Movie) -> DetectionResult:
        """Run scene detection on a single movie. Returns DetectionResult.

        If the movie is shorter than min_duration_seconds, returns empty chapters.
        If the movie is currently being streamed, also returns empty chapters --
        no point spending time detecting if we cannot write anyway.
        """
        start = time.monotonic()

        if (
            self.config.processing.check_active_streams
            and self.plex.has_active_stream(movie)
        ):
            log.info("skip_active_stream", title=movie.display)
            console.print(
                "  [yellow]Skipped: this movie has an active or paused Plex session. "
                "Stop the stream first, then try again.[/yellow]"
            )
            return DetectionResult(movie=movie, chapters=[], elapsed_seconds=0.0)

        if movie.duration < self.config.detection.min_duration_seconds:
            log.info(
                "skip_short_movie",
                title=movie.display,
                duration_seconds=movie.duration,
            )
            mins = self.config.detection.min_duration_seconds // 60
            console.print(
                f"  [yellow]Skipped: movie is shorter than {mins} minutes.[/yellow]"
            )
            return DetectionResult(movie=movie, chapters=[], elapsed_seconds=0.0)

        # Resolve credits (file > Plex > none)
        credits_time, source = credits.resolve_credits(
            movie.local_path, movie.plex_movie_obj
        )
        movie.credits_time = credits_time
        movie.credits_source = source

        if credits_time is not None:
            effective_end = credits_time - self.config.detection.min_gap_seconds
        else:
            effective_end = movie.duration - self.config.detection.end_margin_seconds

        if effective_end <= self.config.detection.min_gap_seconds:
            log.warning("effective_end_too_short", title=movie.display, end=effective_end)
            console.print(
                "  [yellow]Skipped: not enough scannable content before the credits.[/yellow]"
            )
            return DetectionResult(movie=movie, chapters=[], elapsed_seconds=0.0)

        # Show progress bar while iterating windows. Matches the style of the
        # remuxer's "Writing chapters" bar so the UI feels unified.
        chapter_count = 0
        with Progress(
            TextColumn("    [progress.description]{task.description}"),
            BarColumn(bar_width=30),
            TaskProgressColumn(),
            TextColumn("[dim]{task.fields[pos_text]}[/dim]"),
            TimeElapsedColumn(),
            transient=True,
            console=console,
        ) as progress:
            task_id = progress.add_task(
                "Detecting chapters",
                total=movie.duration,
                pos_text=f"0:00:00 / {_format_time(movie.duration)}  0 chapters",
            )

            def on_window_start(cursor: float) -> None:
                progress.update(
                    task_id,
                    completed=cursor,
                    pos_text=(
                        f"{_format_time(cursor)} / {_format_time(movie.duration)}  "
                        f"{chapter_count} chapters"
                    ),
                )

            matches: list[WindowMatch] = []
            for match in iter_chapter_points(
                movie.local_path,
                duration=movie.duration,
                effective_end=effective_end,
                cfg=self.config.detection,
                on_window_start=on_window_start,
            ):
                matches.append(match)
                chapter_count = len(matches)
                # Jump bar to the match timestamp so the user sees the chapter land
                progress.update(
                    task_id,
                    completed=match.timestamp,
                    pos_text=(
                        f"{_format_time(match.timestamp)} / {_format_time(movie.duration)}  "
                        f"{chapter_count} chapters"
                    ),
                )

            # Final position update so the bar reaches the end cleanly
            progress.update(
                task_id,
                completed=effective_end,
                pos_text=(
                    f"{_format_time(effective_end)} / {_format_time(movie.duration)}  "
                    f"{chapter_count} chapters"
                ),
            )

        # If the user cancelled mid-detection, return without chapters --
        # we don't want to write a partial chapter list.
        if force_stopped():
            log.info("detection_cancelled", title=movie.display)
            return DetectionResult(movie=movie, chapters=[], elapsed_seconds=time.monotonic() - start)

        chapters = build_chapter_list(
            matches,
            credits_time=credits_time,
        )

        elapsed = time.monotonic() - start
        log.info(
            "detection_complete",
            title=movie.display,
            chapter_count=len(chapters),
            credits_source=source.value,
            elapsed_seconds=round(elapsed, 1),
        )
        return DetectionResult(movie=movie, chapters=chapters, elapsed_seconds=elapsed)

    def write_movie(self, detection: DetectionResult) -> ProcessingResult:
        """Write chapters to a movie via remux. Performs safety checks first."""
        movie = detection.movie

        if not detection.chapters:
            return ProcessingResult(
                movie=movie,
                chapters=[],
                written=False,
                error="no chapters to write",
            )

        # Active stream re-check just before writing (state may have changed
        # since detection -- e.g. someone started watching while we detected)
        if self.config.processing.check_active_streams and self.plex.has_active_stream(movie):
            return ProcessingResult(
                movie=movie,
                chapters=detection.chapters,
                written=False,
                error="movie is currently being streamed",
            )

        start = time.monotonic()
        result = write_chapters_and_remux(
            movie.local_path,
            detection.chapters,
            language=self.config.chapter.language,
        )
        elapsed = time.monotonic() - start

        if result.success:
            log.info(
                "chapters_written",
                title=movie.display,
                chapters=len(detection.chapters),
                elapsed_seconds=round(elapsed, 1),
            )
        else:
            log.error("write_failed", title=movie.display, error=result.error)

        return ProcessingResult(
            movie=movie,
            chapters=detection.chapters,
            written=result.success,
            error=result.error,
            elapsed_seconds=elapsed,
        )
