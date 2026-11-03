# SPDX-FileCopyrightText: 2026 Balazs Katona
# SPDX-License-Identifier: GPL-3.0-or-later
"""Plex API client wrapper."""

import unicodedata
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from plexapi.server import PlexServer

from auto_chapter.config import PlexConfig
from auto_chapter.console import console
from auto_chapter.errors import friendly_plex_error, retry_on_plex_timeout
from auto_chapter.folder_match import FolderMatch, match_folder
from auto_chapter.logging_setup import get_logger
from auto_chapter.models import AUTO_CHAPTER_PREFIX, CREDITS_CHAPTER_NAME, Movie
from auto_chapter.path_mapping import PathMapper
from auto_chapter.remuxer import CHAPTER_XML_SUFFIX, TEMP_OUTPUT_SUFFIX

log = get_logger(__name__)

# How many movies' full metadata (chapters, markers) one request asks Plex for.
# Measured on the owner's server (1322 movies): 100 movies in ~1.4 s, while
# asking movie by movie took ~0.24 s each -- 5-6 minutes of steady load. Pages
# keep each request short, so Plex keeps serving its players in between.
SCAN_PAGE_SIZE = 100

# Called with (movies done, movies in scope) as a scan goes through the pages.
ScanProgress = Callable[[int, int], None]


class PlexUnavailableError(Exception):
    """Raised by PlexClient methods when there's no live connection.

    The message is already a complete, friendly sentence (produced by
    friendly_plex_error at connection time) -- callers that already catch
    generic Exception and run it through friendly_plex_error() / a trimmed
    fallback will display it correctly without any special-casing.
    """


def _movie_folders(sections: list[Any]) -> list[str]:
    """Folder paths of all movie sections, deduplicated, each with a final "/"."""
    seen: set[str] = set()
    folders: list[str] = []
    for section in sections:
        if section.type != "movie":
            continue
        for loc in section.locations or []:
            loc_s = loc if loc.endswith("/") else loc + "/"
            if loc_s not in seen:
                seen.add(loc_s)
                folders.append(loc_s)
    return folders


def _cleanup_stale_tmp(local_path: Path) -> None:
    """Remove a leftover .autochapter.tmp / .autochapter.xml next to local_path.

    This can only happen if a previous run was killed mid-write (crash, power
    loss, SIGKILL) before its own try/finally cleanup could run. The .tmp is
    never a resumable partial file -- mkvmerge writes a full new container in
    one pass -- and the .xml is just the chapter list handed to mkvmerge, so
    both are always safe to discard. Both suffixes are unique to this app, so
    we can be sure we're not touching anyone else's file. Called
    opportunistically while a scan already visits this movie's path, so no
    extra directory walk is needed.
    """
    for suffix in (TEMP_OUTPUT_SUFFIX, CHAPTER_XML_SUFFIX):
        stale = local_path.with_name(local_path.name + suffix)
        if stale.exists():
            try:
                stale.unlink()
                log.info("stale_tmp_removed", path=str(stale))
            except OSError as e:
                log.warning("stale_tmp_remove_failed", path=str(stale), error=str(e))


class SkipReason(StrEnum):
    """Why a movie that matched the title search cannot be processed."""

    OTHER_CHAPTERS = "other_chapters"      # chapters from another source, at/above the threshold
    NOT_MKV = "not_mkv"
    FOLDER_UNREACHABLE = "folder_unreachable"
    FILE_MISSING = "file_missing"
    TOO_SHORT = "too_short"


@dataclass(frozen=True, slots=True)
class SkippedMatch:
    """A movie that matched the title search but was left out, and why."""

    title: str
    year: int | None
    reason: SkipReason
    chapter_count: int = 0    # the movie's own count; only meaningful for OTHER_CHAPTERS

    @property
    def display(self) -> str:
        return f"{self.title} ({self.year})" if self.year else self.title


@dataclass(slots=True)
class ScanStats:
    """Summary of a Plex scan."""

    total_movies: int = 0
    with_auto_chapters: int = 0    # movies whose chapters are all Auto Chapter*/Credits
    with_other_chapters: int = 0   # movies at/above the chapter threshold from another source
    candidates: int = 0
    non_mkv: int = 0
    too_short: int = 0
    missing_files: int = 0
    # Filled only when a title_filter is given: the matches that were left out,
    # with the reason, so the caller can tell the user instead of saying "not found".
    skipped: list[SkippedMatch] = field(default_factory=list)
    # Plex paths of enabled mappings this scan did not use because Plex has no
    # movie folder with exactly that path (see PlexClient._scan_mapper).
    ignored_mappings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CreditsScanStats:
    """Summary of a Plex scan for End Credits update candidates."""

    total_movies: int = 0
    with_auto_chapters: int = 0
    candidates: int = 0
    missing_files: int = 0
    ignored_mappings: list[str] = field(default_factory=list)  # see ScanStats


class PlexClient:
    """Thin wrapper around plexapi for our needs.

    Connecting never raises -- see _connect(). Callers that need a live
    connection (scanning, folder discovery) get a clear PlexUnavailableError
    instead of an AttributeError on a None server, which flows correctly
    through the app's existing friendly-error handling at each call site.
    Display-only access (server_name) degrades to a placeholder instead of
    raising, since it's used unconditionally in the main menu panel.
    """

    def __init__(self, config: PlexConfig, mapper: PathMapper):
        self._mapper = mapper
        # Same object menu.py's Config.plex holds, not a copy -- a Settings
        # change that mutates config.plex.url/token in place is
        # automatically visible here too, without rebuilding this whole
        # instance. See reconnect().
        self._config = config
        self._server: PlexServer | None = None
        self._connection_error: str | None = None
        self._connect()

    def _connect(self) -> None:
        """(Re)attempt connecting using the current self._config.

        Same retry/backoff as any other Plex call in this app. Never
        raises: on failure, self._server stays None and
        self._connection_error holds a ready-to-display message, so the
        app can still start (into Settings > Plex connection, for example)
        instead of crashing before the menu ever appears. Called from
        __init__, and again from reconnect() any time a fresh attempt is
        needed.
        """
        console.print("[dim]Connecting to Plex...[/dim]")
        try:
            # 10s per HTTP request is enough for a healthy Plex; if it takes
            # longer we prefer to retry rather than sit waiting up to 30s
            # (plexapi default).
            self._server = retry_on_plex_timeout(
                lambda: PlexServer(  # type: ignore[no-untyped-call]
                    self._config.url, self._config.token, timeout=10
                )
            )
            self._connection_error = None  # clear any stale error on success
            log.info(
                "plex_connected", server=self._server.friendlyName, url=self._config.url
            )
        except Exception as e:
            self._server = None
            self._connection_error = friendly_plex_error(e)
            log.warning("plex_connect_failed", url=self._config.url, error=str(e))

    def reconnect(self) -> bool:
        """Explicitly (re)attempt connecting with the current config.

        Returns True if now connected. This is the one mechanism behind
        three different triggers, so all three stay in sync instead of
        drifting into subtly different code paths:
        - Settings > Plex connection > Reconnect (explicit, deliberate)
        - _require_server() (automatic, right before a Plex-dependent
          action would otherwise fail on a possibly-stale disconnected
          state)
        - _change_plex_url / _change_plex_token in menu.py, right after
          mutating config.plex in place -- replaces what used to be a
          full PlexClient rebuild with a simple reconnect using the
          (already-mutated, already shared) config.
        """
        self._connect()
        return self.is_connected

    @property
    def is_connected(self) -> bool:
        return self._server is not None

    @property
    def connection_error(self) -> str | None:
        """Friendly message from the last failed connection attempt, if any."""
        return self._connection_error

    def _require_server(self) -> PlexServer:
        """Return a live server, attempting one fresh reconnect first if
        not currently connected.

        Every server-touching method funnels through here, so this is the
        one place that needs to attempt reconnection -- a Plex-dependent
        action gets a real chance to recover from a since-resolved outage,
        rather than failing on a stale "disconnected" state that may no
        longer be true (e.g. the network came back, but nothing had
        triggered a fresh attempt yet). Never lets a raw
        Timeout/ConnectionError escape past this -- always converts to
        PlexUnavailableError with an already-friendly message -- so
        callers that separately retry on those specifically
        (_scan_with_retry, for transient mid-session hiccups) can't end up
        doing a redundant second round of retries stacked on top of this
        one.
        """
        if self._server is None:
            self.reconnect()
        if self._server is None:
            raise PlexUnavailableError(
                self._connection_error or "Plex is not connected."
            )
        return self._server

    @property
    def server_name(self) -> str:
        if self._server is None:
            return "(not connected)"
        return str(self._server.friendlyName)

    def list_movie_folders(self) -> list[str]:
        """Return all folder paths from all movie sections in Plex.

        Used by the Settings menu to find folders that aren't yet mapped.
        Deduplicates and appends a trailing slash for consistency.
        """
        return _movie_folders(self._require_server().library.sections())

    def folder_match(self, plex_folder: str, local_path: str) -> FolderMatch | None:
        """How many of the movies Plex has in `plex_folder` are in `local_path`
        on this computer (see folder_match.match_folder)."""
        return match_folder(self._require_server(), plex_folder, local_path)

    def _scan_mapper(self, sections: list[Any]) -> tuple[PathMapper, list[str]]:
        """The mapper a scan works with, and the mappings it had to leave out.

        A mapping is used only if its Plex path IS one of Plex's movie folders,
        exactly. Movies are matched by the start of their path, so a mapping
        like "/share/" (the parent of the real folder) would quietly pull in
        every other movie library under it, and the scans write into files.
        The app itself only ever stores paths taken from Plex's list; this
        guards against a hand-edited config.yml.

        If Plex lists no movie folders at all, nothing can be compared: the
        configured mapper is used unchanged (there are no movies to match
        anyway). Returns (mapper, plex paths of the enabled mappings left out).

        `sections` are the library sections the scan has just fetched anyway, so
        this costs no extra request.
        """
        folders = set(_movie_folders(sections))
        if not folders:
            return self._mapper, []
        usable = [m for m in self._mapper.mappings if m.plex in folders]
        ignored = [m.plex for m in self._mapper.mappings if m.enabled and m.plex not in folders]
        if not ignored:
            return self._mapper, []
        log.warning("mappings_ignored", plex_paths=ignored)
        return PathMapper(usable), ignored

    def _scan_setup(
        self, known_unreachable_folders: list[str] | None
    ) -> tuple[list[Any], PathMapper, list[str], list[str]]:
        """What both scans start from: Plex's library sections, the mapper the
        scan uses and the mappings it left out (_scan_mapper), and the
        unreachable folders.

        The unreachable folders are determined once, up front -- not per movie.
        A folder confirmed (or timed out) as unreachable means every movie under
        it is skipped immediately, no filesystem call at all, rather than
        re-exposing to the same hang hundreds of times over (once per movie in
        that folder). See PathMapper.unreachable_enabled_folders for why a single
        Path.exists() can itself hang indefinitely on a mount that's present but
        unresponsive. A caller that already has them (menu.py's pre-scan check)
        passes them in, so they are not checked twice.
        """
        server = self._require_server()
        sections = server.library.sections()
        mapper, ignored = self._scan_mapper(sections)
        unreachable = (
            known_unreachable_folders
            if known_unreachable_folders is not None
            else mapper.unreachable_enabled_folders()
        )
        return sections, mapper, ignored, unreachable

    def iter_movie_candidates(
        self,
        *,
        chapter_threshold: int,
        title_filter: str | None = None,
        regenerate: bool = False,
        min_duration_seconds: int = 0,
        known_unreachable_folders: list[str] | None = None,
        on_progress: ScanProgress | None = None,
    ) -> tuple[list[Movie], ScanStats]:
        """Scan all movie sections and return movies that need processing.

        Args:
            chapter_threshold: movies with fewer chapters than this are candidates.
            title_filter: optional substring filter on the Plex title or the
                original title, ignoring case and accents (see _searchable).
            regenerate: if True, also include movies with N>=threshold chapters
                where all chapters are "Auto Chapter*" or "Credits".
            min_duration_seconds: filter out movies shorter than this.
            known_unreachable_folders: pre-computed result of
                self._mapper.unreachable_enabled_folders(), if the caller
                already has it (e.g. menu.py's pre-scan
                _check_folder_reachability()) -- avoids re-running the same
                bounded-but-not-free, per-folder check a second time for
                the same scan. If None, computed internally here instead,
                so this method is still correct and safe on its own even
                if called from a context that skipped that pre-check.

        Returns:
            (movies, stats)
        """
        stats = ScanStats()
        movies: list[Movie] = []
        sections, mapper, stats.ignored_mappings, unreachable_folders = self._scan_setup(
            known_unreachable_folders
        )

        def skip(plex_movie: Any, reason: SkipReason, chapter_count: int = 0) -> None:
            # Only a title search reports skipped matches: in a full scan the
            # list would hold the whole library.
            if title_lc:
                stats.skipped.append(
                    SkippedMatch(
                        title=plex_movie.title,
                        year=plex_movie.year,
                        reason=reason,
                        chapter_count=chapter_count,
                    )
                )

        title_lc = _searchable(title_filter) if title_filter else None

        def matches_title(plex_movie: Any) -> bool:
            assert title_lc is not None
            return any(
                title_lc in _searchable(t)
                for t in (plex_movie.title, getattr(plex_movie, "originalTitle", None))
                if t
            )

        for plex_movie, plex_path in _movies_in_scope(
            self._require_server(),
            sections,
            mapper,
            # A title search needs the full metadata of the matching movies only.
            fetch_full=matches_title if title_lc else None,
            on_progress=on_progress,
        ):
            stats.total_movies += 1

            if title_lc and not matches_title(plex_movie):
                continue

            chapters = plex_movie.chapters or []
            chapter_count = len(chapters)
            is_auto = chapter_count > 0 and _all_auto_chapter_names(chapters)

            if chapter_count >= chapter_threshold:
                if is_auto:
                    stats.with_auto_chapters += 1
                else:
                    stats.with_other_chapters += 1
                if regenerate and is_auto:
                    pass  # accept for regeneration
                else:
                    if not is_auto:
                        skip(plex_movie, SkipReason.OTHER_CHAPTERS, chapter_count)
                    continue

            local_path, reason = _local_file(plex_path, mapper, unreachable_folders)
            if local_path is None:
                if reason is SkipReason.NOT_MKV:
                    stats.non_mkv += 1
                elif reason is not None:
                    stats.missing_files += 1
                if reason is not None:
                    skip(plex_movie, reason)
                continue

            duration_seconds = (plex_movie.duration or 0) / 1000.0

            if min_duration_seconds and duration_seconds < min_duration_seconds:
                stats.too_short += 1
                skip(plex_movie, SkipReason.TOO_SHORT)
                continue

            # Done only for candidates (small subset), keeps scan fast.
            has_plex_credits = _has_credits_marker(plex_movie)

            movies.append(
                Movie(
                    title=plex_movie.title,
                    year=plex_movie.year,
                    duration=duration_seconds,
                    local_path=local_path,
                    plex_path=plex_path,
                    chapter_count=chapter_count,
                    plex_movie_obj=plex_movie,
                    has_auto_chapters=is_auto,
                    has_plex_credits=has_plex_credits,
                    has_credits_chapter=is_auto and _ends_with_credits_chapter(chapters),
                )
            )
            stats.candidates += 1

        return movies, stats

    def iter_credits_update_candidates(
        self,
        *,
        min_duration_seconds: int = 0,
        known_unreachable_folders: list[str] | None = None,
        on_progress: ScanProgress | None = None,
    ) -> tuple[list[Movie], CreditsScanStats]:
        """Scan for movies that have Auto Chapters but are missing the End Credits chapter,
        and where Plex has since detected a credits marker.

        These movies got their Auto Chapters before Plex detected the credits start.
        Regenerating them will add the End Credits chapter at the right spot.

        known_unreachable_folders: see iter_movie_candidates -- same
        pre-computed-result-reuse reasoning.

        Returns:
            (movies, stats)
        """
        stats = CreditsScanStats()
        movies: list[Movie] = []
        sections, mapper, stats.ignored_mappings, unreachable_folders = self._scan_setup(
            known_unreachable_folders
        )

        for plex_movie, plex_path in _movies_in_scope(
            self._require_server(), sections, mapper, on_progress=on_progress
        ):
            stats.total_movies += 1

            chapters = plex_movie.chapters or []
            if not chapters or not _all_auto_chapter_names(chapters):
                continue
            stats.with_auto_chapters += 1

            if _ends_with_credits_chapter(chapters):
                continue  # already has End Credits chapter, nothing to update

            # Has Plex detected the credits since?
            if not _has_credits_marker(plex_movie):
                continue

            local_path, reason = _local_file(plex_path, mapper, unreachable_folders)
            if local_path is None:
                if reason in (SkipReason.FOLDER_UNREACHABLE, SkipReason.FILE_MISSING):
                    stats.missing_files += 1
                continue

            duration_seconds = (plex_movie.duration or 0) / 1000.0
            if min_duration_seconds and duration_seconds < min_duration_seconds:
                continue

            movies.append(
                Movie(
                    title=plex_movie.title,
                    year=plex_movie.year,
                    duration=duration_seconds,
                    local_path=local_path,
                    plex_path=plex_path,
                    chapter_count=len(chapters),
                    plex_movie_obj=plex_movie,
                    has_auto_chapters=True,
                    has_plex_credits=True,
                )
            )
            stats.candidates += 1

        return movies, stats

    def has_active_stream(self, movie: Movie) -> bool:
        """Check if any session is currently playing the given movie."""
        if self._server is None:
            return False  # no connection to ask -> same as a failed check: allow processing
        try:
            sessions = self._server.sessions()  # type: ignore[no-untyped-call]
        except Exception:
            return False  # if check fails, allow processing

        for session in sessions:
            try:
                rating_key = getattr(session, "ratingKey", None)
                target_key = getattr(movie.plex_movie_obj, "ratingKey", None)
                if rating_key is not None and rating_key == target_key:
                    return True
            except Exception:
                continue
        return False

    def trigger_analyze(self, movie: Movie) -> bool:
        """Trigger Plex analyze for a movie. Returns True on success."""
        if movie.plex_movie_obj is None:
            return False
        try:
            movie.plex_movie_obj.analyze()  # type: ignore[attr-defined]
            return True
        except Exception as e:
            log.warning("analyze_failed", movie=movie.display, error=str(e))
            return False


def _movies_in_scope(
    server: Any,
    sections: list[Any],
    mapper: PathMapper,
    *,
    fetch_full: Callable[[Any], bool] | None = None,
    on_progress: ScanProgress | None = None,
) -> Iterator[tuple[Any, str]]:
    """Every movie under a folder with an enabled mapping, with its Plex file path.

    The scope check comes before anything else, so a movie in a disabled or
    unmapped folder never reaches any stat -- both scans count from here.

    First Plex's plain listing of each section (title, file path; Plex pages it
    itself), then the full metadata -- chapters and markers -- of
    SCAN_PAGE_SIZE movies per request. The listing alone has no chapters or
    markers, and touching them made plexapi reload every movie on its own: one
    request per movie, ~5-6 minutes for 1322 movies. Auto-reload is switched
    off on every object handed out, so an empty field (no chapters, no
    original title) is taken as empty instead of starting a request.

    fetch_full: which movies need the full metadata (a title search needs only
    the matching ones); the others are yielded as listed.
    on_progress(done, in scope): before the first page and after each page.
    """
    in_scope: list[tuple[Any, str]] = []
    for section in sections:
        if section.type != "movie":
            continue
        for listed in section.all():
            listed._autoReload = False
            try:
                plex_path = listed.media[0].parts[0].file
            except (IndexError, AttributeError):
                continue  # can't determine scope -- skip silently
            if mapper.has_enabled_mapping_for(plex_path):
                in_scope.append((listed, plex_path))

    total = len(in_scope)
    if on_progress:
        on_progress(0, total)
    for start in range(0, total, SCAN_PAGE_SIZE):
        page = in_scope[start : start + SCAN_PAGE_SIZE]
        wanted = [m.ratingKey for m, _ in page if fetch_full is None or fetch_full(m)]
        full: dict[Any, Any] = {}
        if wanted:
            keys = ",".join(str(k) for k in wanted)
            for item in server.fetchItems(
                f"/library/metadata/{keys}?includeChapters=1&includeMarkers=1"
            ):
                item._autoReload = False
                full[item.ratingKey] = item
        for listed, plex_path in page:
            if listed.ratingKey in full:
                yield full[listed.ratingKey], plex_path
            elif listed.ratingKey not in wanted:
                yield listed, plex_path
            # else: gone from Plex since the listing -- left out
        if on_progress:
            on_progress(start + len(page), total)


def _local_file(
    plex_path: str, mapper: PathMapper, unreachable_folders: list[str]
) -> tuple[Path | None, SkipReason | None]:
    """Where this computer reaches the movie file, or why it can't be used.

    Returns (local path, None), or (None, NOT_MKV / FOLDER_UNREACHABLE /
    FILE_MISSING). (None, None) only for a path no mapping translates, which
    can't happen after _movies_in_scope (logged). A movie under a folder already
    known to be unreachable gets no filesystem call at all (no exists(), which
    could hang on the same bad mount). A leftover temp file from a killed write
    is removed while the folder is visited anyway.
    """
    if not plex_path.lower().endswith(".mkv"):
        return None, SkipReason.NOT_MKV
    local_path = mapper.plex_to_local(plex_path)
    if local_path is None:
        log.warning("unexpected_unmapped_path", plex_path=plex_path)
        return None, None
    if any(str(local_path).startswith(f) for f in unreachable_folders):
        return None, SkipReason.FOLDER_UNREACHABLE
    if not local_path.exists():
        log.warning("missing_file", local_path=str(local_path))
        return None, SkipReason.FILE_MISSING
    _cleanup_stale_tmp(local_path)
    return local_path, None


def _ends_with_credits_chapter(chapters: list[Any]) -> bool:
    """True if the last chapter is the app's End Credits chapter -- the Update
    scan's rule, also shown by Find by title."""
    if not chapters:
        return False
    last = chapters[-1]
    name = getattr(last, "title", None) or getattr(last, "tag", None) or ""
    return bool(name.strip() == CREDITS_CHAPTER_NAME)


def _has_credits_marker(plex_movie: Any) -> bool:
    """True if Plex has detected where the end credits start."""
    try:
        return any(
            str(getattr(marker, "type", "")).lower() == "credits"
            for marker in (plex_movie.markers or [])
        )
    except Exception:
        return False


def _searchable(text: str) -> str:
    """Lower case without accents, for the title search: "Őrült" -> "orult".

    Plex Web finds "4 hónap" for "honap" too, and users type without accents.
    """
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _all_auto_chapter_names(chapters: list[Any]) -> bool:
    """True if the chapter list was generated by auto-chapter.

    Requires at least one chapter named 'Auto Chapter ...'; the rest must be
    either 'Auto Chapter ...' or 'Credits'. A list containing only 'Credits'
    entries does not count.
    """
    has_auto = False
    for ch in chapters:
        name = (getattr(ch, "title", None) or getattr(ch, "tag", None) or "").strip()
        if name.startswith(AUTO_CHAPTER_PREFIX):
            has_auto = True
        elif name != CREDITS_CHAPTER_NAME:
            return False
    return has_auto
