"""Data models for movies, chapters, and processing results."""

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

# The names of the chapters the app writes. Fixed on purpose: the scans tell
# the app's own chapters from other sources by exactly these names, so a
# different name would make the app treat its own films as foreign.
AUTO_CHAPTER_PREFIX = "Auto Chapter"
CREDITS_CHAPTER_NAME = "Credits"


class DetectionMethod(StrEnum):
    """How a chapter point was identified."""

    START = "start"
    BLACK = "black"
    SCDET = "scdet"
    CREDITS = "credits"


class CreditsSource(StrEnum):
    """Where credits timestamp came from."""

    FILE = "file"
    PLEX = "plex"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class Chapter:
    """A single chapter marker."""

    name: str
    timestamp: float  # seconds from start
    method: DetectionMethod
    score: float | None = None  # scdet score percentage (if applicable)


@dataclass(slots=True)
class Movie:
    """Movie under consideration for chapter generation."""

    title: str
    year: int | None
    duration: float  # seconds
    local_path: Path
    plex_path: str
    chapter_count: int
    credits_time: float | None = None
    credits_source: CreditsSource = CreditsSource.NONE
    plex_movie_obj: object | None = None  # opaque plexapi.video.Movie
    has_auto_chapters: bool = False  # True if already processed by auto-chapter
    has_plex_credits: bool = False  # True if Plex has detected the End Credits start

    @property
    def display(self) -> str:
        if self.year:
            return f"{self.title} ({self.year})"
        return self.title


@dataclass(slots=True)
class DetectionResult:
    """Result of running detection on a movie."""

    movie: Movie
    chapters: list[Chapter] = field(default_factory=list)
    elapsed_seconds: float = 0.0


@dataclass(slots=True)
class ProcessingResult:
    """Result of writing chapters to a movie."""

    movie: Movie
    chapters: list[Chapter]
    written: bool = False
    error: str | None = None
    elapsed_seconds: float = 0.0
