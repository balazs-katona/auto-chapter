"""Rich helpers for CLI output formatting."""

from collections.abc import Sequence

from rich.console import Console
from rich.table import Table

from auto_chapter.models import Chapter, DetectionMethod, Movie


def fmt_time(seconds: float) -> str:
    """Format seconds as H:MM:SS or M:SS."""
    if seconds < 0:
        seconds = 0
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _method_label(method: DetectionMethod) -> str:
    """Render detection method in a user-friendly form."""
    return {
        DetectionMethod.START: "Opening",
        DetectionMethod.BLACK: "Fade-to-black",
        DetectionMethod.SCDET: "Visual cut",
        DetectionMethod.CREDITS: "End credits",
    }.get(method, method.value)


def render_chapter_table(console: Console, movie: Movie, chapters: Sequence[Chapter]) -> None:
    """Render a single movie's chapter list as a Rich table."""
    table = Table(
        title=f"{movie.display} -- {len(chapters)} generated chapters",
        show_lines=False,
    )
    table.add_column("Time", justify="right", style="cyan")
    table.add_column("Chapter name")
    table.add_column("How detected", style="magenta")
    # scdet's score: how much the picture changed at the cut, as a percentage
    # of the largest possible change -- not how sure the app is.
    table.add_column("Picture change", justify="right")

    for chapter in chapters:
        score_str = f"{chapter.score:.0f}%" if chapter.score is not None else ""
        table.add_row(
            fmt_time(chapter.timestamp),
            chapter.name,
            _method_label(chapter.method),
            score_str,
        )

    console.print(table)


def chapter_summary(chapters: Sequence[Chapter]) -> str:
    """"17 chapters detected (11 fade-to-black, 4 visual cuts, plus opening and
    end credits)" -- in the words the chapter table uses."""
    counts = chapter_method_counts(chapters)
    found = []
    if n := counts.get(DetectionMethod.BLACK, 0):
        found.append(f"{n} fade-to-black")
    if n := counts.get(DetectionMethod.SCDET, 0):
        found.append(f"{n} visual cut{'' if n == 1 else 's'}")
    fixed = []
    if counts.get(DetectionMethod.START):
        fixed.append("opening")
    if counts.get(DetectionMethod.CREDITS):
        fixed.append("end credits")
    if found and fixed:
        detail = f"{', '.join(found)}, plus {' and '.join(fixed)}"
    elif found:
        detail = ", ".join(found)
    else:
        detail = f"{' and '.join(fixed)} only"
    n = len(chapters)
    return f"{n} chapter{'' if n == 1 else 's'} detected ({detail})"


def chapter_method_counts(chapters: Sequence[Chapter]) -> dict[DetectionMethod, int]:
    """Count chapters by detection method."""
    counts: dict[DetectionMethod, int] = {}
    for ch in chapters:
        counts[ch.method] = counts.get(ch.method, 0) + 1
    return counts
