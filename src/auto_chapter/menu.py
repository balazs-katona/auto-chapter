# SPDX-FileCopyrightText: 2026 Balazs Katona
# SPDX-License-Identifier: GPL-3.0-or-later
"""Interactive numbered menu for users who prefer not to type commands."""

import contextlib
import re
import textwrap
from collections.abc import Iterator
from functools import partial
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from rich.markup import escape
from rich.panel import Panel
from rich.progress import Progress, TextColumn
from rich.prompt import IntPrompt, Prompt
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

from auto_chapter.batch_control import (
    batch_interrupt,
    cancellable,
    force_stopped,
    stop_requested,
)
from auto_chapter.config import Config, PathMapping
from auto_chapter.console import console
from auto_chapter.detector.ffmpeg import get_duration
from auto_chapter.display import (
    chapter_summary,
    render_chapter_table,
)
from auto_chapter.errors import MovieReadError
from auto_chapter.errors import (
    friendly_plex_error as _friendly_plex_error,
)
from auto_chapter.errors import (
    retry_on_plex_timeout as _scan_with_retry,
)
from auto_chapter.errors import (
    short_error as _short_error,
)
from auto_chapter.logging_setup import get_logger
from auto_chapter.models import Movie, ProcessingResult
from auto_chapter.orchestrator import Orchestrator
from auto_chapter.path_mapping import PathMapper, check_dir_with_timeout
from auto_chapter.plex_client import ScanProgress, SkippedMatch, SkipReason, _searchable
from auto_chapter.shutdown import (
    SHUTDOWN_TIP_BATCH,
    SHUTDOWN_TIP_SINGLE,
    ShutdownToggle,
    execute_shutdown_with_countdown,
)
from auto_chapter.sleep_inhibit import keep_awake

# Line editing for input(): backspace/arrows work whatever the tty's erase key
# is. Importing readline is all it takes; it is not available on every platform,
# where plain input() still works.
with contextlib.suppress(ImportError):
    import readline  # noqa: F401

log = get_logger(__name__)

# Rich's own message for a non-number at a number prompt is technical; every
# number prompt in the app uses IntPrompt, so it is replaced once, here.
IntPrompt.validate_error_message = "[prompt.invalid]Please type a number."

# Standard cancel hint shown wherever the user can back out.
CANCEL_HINT = "[dim](type 0 to cancel)[/dim]"

# Ctrl+C tip for single-movie operations (one press cancels immediately).
CTRL_C_TIP_SINGLE = (
    "[dim]Press Ctrl+C to cancel. The original file is never damaged.[/dim]"
)

# Ctrl+C tip for multi-movie batches (graceful first press, force on second).
CTRL_C_TIP_BATCH = (
    "[dim]Press Ctrl+C to stop (twice for immediate stop, "
    "the original file is never damaged).[/dim]"
)


# --- Generic helpers ---

def _show_options(
    title: str,
    options: list[str],
    allow_cancel: bool = False,
    cancel_label: str = "Cancel",
) -> int:
    """Display a numbered options menu and return the chosen index (1-based).

    If allow_cancel is True, prepends a '0) {cancel_label}' option at the top.
    Returns 0 when the cancel option is chosen or when the user presses Ctrl+C / Ctrl+D.
    If allow_cancel is False, Ctrl+C propagates (so the caller can decide to quit).
    """
    console.print(f"\n[bold]{title}[/bold]")
    if allow_cancel:
        console.print(f"  [cyan]0[/cyan]) {cancel_label}")
    for i, opt in enumerate(options, start=1):
        console.print(f"  [cyan]{i}[/cyan]) {opt}")

    start = 0 if allow_cancel else 1
    try:
        return IntPrompt.ask(
            "Choose",
            choices=[str(i) for i in range(start, len(options) + 1)],
            show_choices=True,
        )
    except (KeyboardInterrupt, EOFError):
        if allow_cancel:
            console.print()  # newline so the "Cancelled" message starts cleanly
            return 0
        raise


def _ask_text(prompt: str, empty_hint: str | None = None) -> str:
    """Ask for free-text input. Returns empty string only on '0', Ctrl+C, or Ctrl+D.

    Empty Enter re-prompts with a hint (does not cancel). This makes cancellation
    a deliberate action rather than an accidental Enter.
    """
    hint = empty_hint or "Please type something, or 0 to cancel."
    while True:
        try:
            answer = Prompt.ask(
                f"{prompt}\n{CANCEL_HINT}",
                default="",
                show_default=False,
            ).strip()
        except (KeyboardInterrupt, EOFError):
            console.print()
            return ""
        if answer == "0":
            return ""
        if answer:
            return answer
        console.print(f"[yellow]{hint}[/yellow]")


# Indent and width for the explanation printed under a numbered option.
_NOTE_INDENT = " " * 5
_NOTE_WIDTH = 76


def _print_option_note(text: str) -> None:
    """Print an option's explanation, wrapped and indented under its number."""
    console.print(
        textwrap.fill(
            text,
            width=_NOTE_WIDTH,
            initial_indent=_NOTE_INDENT,
            subsequent_indent=_NOTE_INDENT,
        )
    )


def parse_selection(spec: str, max_n: int) -> list[int]:
    """Parse a selection like '1,5,7-10' into a sorted unique list of 1-based indices.

    Returns empty list if input is empty or fully invalid.
    Silently ignores out-of-range numbers; raises ValueError on bad syntax.
    """
    spec = spec.strip()
    if not spec:
        return []

    result: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo_s, hi_s = part.split("-", 1)
            lo = int(lo_s.strip())
            hi = int(hi_s.strip())
            if lo > hi:
                lo, hi = hi, lo
            for n in range(lo, hi + 1):
                if 1 <= n <= max_n:
                    result.add(n)
        else:
            n = int(part)
            if 1 <= n <= max_n:
                result.add(n)
    return sorted(result)


def _by_title(movies: list[Movie]) -> list[Movie]:
    """Alphabetical by title, accents ignored, the same on every system.

    With str.lower(), "Árvácska", "Ében" and "Őrült nők" came after "Zorro".
    Not locale-aware on purpose: the order must not depend on the machine.
    """
    return sorted(movies, key=lambda mv: _searchable(mv.display))


def _auto_chapters_status(movie: Movie) -> str:
    """Find by title on a movie with the app's own chapters: is it complete,
    or would Update offer it (no End Credits chapter, Plex End Credits found)?"""
    if not movie.has_credits_chapter and movie.has_plex_credits:
        return "already has Auto Chapters, End Credits chapter missing -- Plex End Credits found"
    return "already has Auto Chapters"


def _match_note(movie: Movie) -> str:
    """The note after a Find by title match, in the other menus' words
    (owner, 0.4.2: a movie Update would offer looked complete here)."""
    if movie.has_auto_chapters:
        return f"  [yellow]({_auto_chapters_status(movie)})[/yellow]"
    if not movie.has_plex_credits:
        return "  [dim](no Plex End Credits yet)[/dim]"
    return ""


@contextlib.contextmanager
def _scan_progress(label: str) -> Iterator[ScanProgress]:
    """The scan's line, kept up to date: "Scanning Plex... 300 / 1322 movies".

    The scan goes through Plex page by page (plex_client.SCAN_PAGE_SIZE movies
    a request) and reports after each page. Before 0.3.1 the line stood still
    for minutes and looked stuck.
    """
    console.print()
    with Progress(TextColumn("[dim]{task.description}[/dim]"), console=console) as progress:
        task = progress.add_task(label, total=None)

        def update(done: int, total: int) -> None:
            progress.update(task, description=f"{label} {done} / {total} movies")

        yield update


# --- Common workflows ---

def _process_one(movie: Movie, orch: Orchestrator) -> ProcessingResult | None:
    """Detect and write one movie. Returns None if skipped or aborted."""
    actual = get_duration(movie.local_path)
    if actual is not None:
        movie.duration = actual

    if force_stopped():
        return None

    result = orch.detect_movie(movie)
    if force_stopped():
        return None

    if not result.chapters:
        # Orchestrator already printed the specific reason
        return None

    console.print(
        f"  [bold]{chapter_summary(result.chapters)}[/bold] "
        f"in {result.elapsed_seconds:.0f}s"
    )
    render_chapter_table(console, movie, result.chapters)

    proc = orch.write_movie(result)
    if force_stopped():
        return None

    if proc.written:
        console.print(f"  [green]Written[/green] in {proc.elapsed_seconds:.0f}s.")
        if orch.plex.trigger_analyze(movie):
            console.print("  [dim]Plex Analyze queued.[/dim]")
    else:
        console.print(f"  [red]Write failed:[/red] {_short_error(proc.error or 'unknown error')}")
    return proc


def _run_batch(movies: list[Movie], orch: Orchestrator) -> list[Movie]:
    """Run detect+write+analyze on each movie, one at a time, with Ctrl+C handling.

    The user can press 's' during the batch to schedule a shutdown for when the
    batch finishes successfully. Pressing 's' again cancels the schedule.

    Returns the list of movies that were successfully written.

    With one movie, Ctrl+C works as in Find by title: the first press stops
    at once. "Stop after the current movie" would mean finishing the only
    movie, so the first press did nothing useful (owner's 1.0.0 test).
    """
    single = len(movies) == 1
    shutdown_toggle = ShutdownToggle()

    def on_shutdown_toggle(scheduled: bool) -> None:
        if scheduled:
            console.print(
                "\n[bold yellow]Shutdown scheduled.[/bold yellow] "
                "The system will power off after this batch finishes. "
                "[dim]Press 's' to cancel.[/dim]"
            )
        else:
            console.print(
                "\n[bold]Shutdown cancelled.[/bold] "
                "[dim]The batch will continue without shutting down.[/dim]"
            )

    succeeded_movies: list[Movie] = []
    failed = 0
    stopped_early = False

    try:
        # start() and the tip prints are inside this try too: if Ctrl+C
        # lands in this narrow setup window (before batch_interrupt()'s own
        # handler is installed), Python's default KeyboardInterrupt would
        # otherwise skip the finally below entirely -- leaving the terminal
        # stuck in cbreak mode and the background 's'-listener thread
        # leaked for the rest of the session, silently stealing keystrokes
        # from whatever prompt runs next.
        can_toggle = shutdown_toggle.start(on_shutdown_toggle)

        console.print(f"\n{CTRL_C_TIP_SINGLE if single else CTRL_C_TIP_BATCH}")
        if can_toggle:
            console.print(SHUTDOWN_TIP_SINGLE if single else SHUTDOWN_TIP_BATCH)

        with keep_awake(), (cancellable() if single else batch_interrupt()):
            for i, mv in enumerate(movies, start=1):
                if stop_requested():
                    stopped_early = True
                    break

                console.print(f"\n[{i}/{len(movies)}] {mv.display}")

                try:
                    result = _process_one(mv, orch)
                except Exception as e:
                    log.error("movie_processing_failed", movie=mv.display, error=str(e))
                    console.print(f"  [red]{_short_error(e)}[/red]")
                    failed += 1
                    continue

                if force_stopped():
                    stopped_early = True
                    break

                if result is None:
                    continue
                if result.written:
                    succeeded_movies.append(mv)
                else:
                    failed += 1
    finally:
        shutdown_toggle.stop()

    # batch_interrupt() doesn't reset these flags on exit (only on the next
    # entry), so it's safe to read force_stopped() here to tell a graceful
    # single Ctrl+C apart from an urgent double Ctrl+C, even after the
    # `with` block above has already closed.
    was_force_stopped = force_stopped()

    succeeded = len(succeeded_movies)
    console.print()
    if stopped_early:
        console.print(
            f"[bold]Stopped by user.[/bold] {succeeded} of {len(movies)} processed.\n"
        )
    else:
        console.print(
            f"[bold]Done.[/bold] {succeeded} of {len(movies)} processed "
            f"successfully" + (f", {failed} failed" if failed else "") + ".\n"
        )

    # Shutdown fires whenever it's scheduled and at least one movie
    # succeeded -- a single Ctrl+C only skips the *remaining* movies, it
    # does not cancel an already-scheduled shutdown, since the person
    # might be stepping away and still wants the machine to power off once
    # what's already running wraps up, without waiting for movies they no
    # longer care about right now. A second Ctrl+C (force stop) is a much
    # more urgent "I need control back immediately" signal and DOES cancel
    # the schedule, same as an all-failed batch (nothing to "wrap up" for).
    if shutdown_toggle.scheduled:
        if was_force_stopped:
            console.print(
                "[dim]Scheduled shutdown cancelled -- the batch was stopped immediately.[/dim]\n"
            )
        elif succeeded == 0:
            console.print(
                "[dim]Scheduled shutdown cancelled -- no movies were "
                "processed successfully.[/dim]\n"
            )
        else:
            execute_shutdown_with_countdown()

    return succeeded_movies


def _detect_and_confirm_write(mv: Movie, orch: Orchestrator) -> list[Movie]:
    """Show a detection preview for one movie and ask before writing.

    Used only by Find a movie by title. That search can pick a movie that
    already has the app's own Auto Chapters (regenerate) or fewer chapters
    from another source than the chapter threshold, so the file may already
    hold chapters. The preview shows what would be written and lets the user
    back out before the file is touched. Movies whose chapter list from
    another source is assumed useful never get here: the title search leaves them out.

    Generate/Update never call this, even when the user picks just one
    movie from their already-filtered candidate list: picking 1 movie from
    a list that's guaranteed to need processing carries the same certainty
    as picking 5, so those flows go straight to _run_batch regardless of
    count -- an extra confirmation there would be pure friction with no
    matching safety benefit.

    Returns [mv] if written, [] otherwise.
    """
    actual = get_duration(mv.local_path)
    if actual is not None:
        mv.duration = actual

    shutdown_toggle = ShutdownToggle()

    def on_shutdown_toggle(scheduled: bool) -> None:
        if scheduled:
            console.print(
                "\n[bold yellow]Shutdown scheduled.[/bold yellow] "
                "The system will power off once this movie is written. "
                "[dim]Press 's' to cancel.[/dim]"
            )
        else:
            console.print(
                "\n[bold]Shutdown cancelled.[/bold] "
                "[dim]Continuing without shutting down.[/dim]"
            )

    written = False
    try:
        # start() and the tip prints are inside this try for the same
        # reason as in _run_batch: a Ctrl+C landing in this narrow setup
        # window (before cancellable()'s own handler is installed) must
        # still reach the finally below, or the terminal is left stuck
        # in cbreak mode with the listener thread leaked for the rest of
        # the session.
        can_toggle = shutdown_toggle.start(on_shutdown_toggle)

        console.print(f"\n[dim]Detecting chapters in {mv.display}...[/dim]")
        console.print(CTRL_C_TIP_SINGLE)
        if can_toggle:
            console.print(SHUTDOWN_TIP_SINGLE)

        with keep_awake(), cancellable():
            try:
                result = orch.detect_movie(mv)
            except Exception as e:
                log.error("detection_failed", movie=mv.display, error=str(e))
                if isinstance(e, MovieReadError):
                    console.print(f"\n[red]{escape(str(e))}[/red]")
                else:
                    console.print(f"\n[red]Detection failed:[/red] {_short_error(e)}")
                return []

        if force_stopped():
            console.print("\n[yellow]Cancelled.[/yellow]")
            return []

        if not result.chapters:
            # Orchestrator already printed the specific reason
            return []

        # Same summary line as in a batch
        console.print(
            f"\n[bold]{chapter_summary(result.chapters)}[/bold] "
            f"in {result.elapsed_seconds:.0f}s"
        )
        render_chapter_table(console, mv, result.chapters)

        # The 's' listener must be OFF while the prompt waits: in cbreak mode
        # it swallows the typed number (no echo, the prompt sees an empty
        # line and reports "Please enter a valid integer number").
        shutdown_toggle.stop()
        choice = _show_options(
            "What now?",
            ["Write these chapters to the file"],
            allow_cancel=True,
        )
        if choice == 0:
            return []

        console.print(f"\n{CTRL_C_TIP_SINGLE}")
        if can_toggle:
            # Back on for the write; a choice made earlier is kept.
            shutdown_toggle.start(on_shutdown_toggle)
            console.print(SHUTDOWN_TIP_SINGLE)

        with keep_awake(), cancellable():
            try:
                proc = orch.write_movie(result)
            except Exception as e:
                log.error("write_failed", movie=mv.display, error=str(e))
                console.print(f"\n[red]Write failed:[/red] {_short_error(e)}")
                return []

        if force_stopped():
            console.print("\n[yellow]Cancelled. The original file is unchanged.[/yellow]")
            return []
        if proc.written:
            written = True
            console.print(
                f"\n[green]Written[/green] in {proc.elapsed_seconds:.0f}s."
            )
            if orch.plex.trigger_analyze(mv):
                console.print("[dim]Plex Analyze queued.[/dim]")
            return [mv]
        else:
            console.print(
                f"\n[red]Write failed:[/red] {_short_error(proc.error or 'unknown error')}"
            )
            return []
    finally:
        shutdown_toggle.stop()
        # Same policy as the batch version: fires only if the movie was
        # actually written. Cancelling at the confirm prompt, a failed
        # detection/write, or a Ctrl+C before writing completed all
        # count as "nothing to power off for".
        if shutdown_toggle.scheduled:
            if written:
                execute_shutdown_with_countdown()
            else:
                console.print(
                    "[dim]Scheduled shutdown cancelled -- "
                    "the movie wasn't written.[/dim]\n"
                )


# --- Scan status submenu ---

def _ask_credits_filter(picked: list[Movie]) -> list[Movie] | None:
    """Ask how to handle picked movies that don't have Plex End Credits yet.

    Design principle: movies WITH a Plex-detected End Credits are the ones the
    user wants processed first. Processing a movie without it is still allowed,
    but only as a conscious decision, because it means doing the work twice
    (now without a Credits chapter, later again via the Update menu once Plex
    has detected the credits). So whenever ANY picked movie lacks them, ask:

    - all have End Credits   -> nothing to ask, process them all
    - a mix                  -> only the ones with End Credits, or all of them
    - none has End Credits   -> cancel, or process them anyway

    Asked for the manual pick only: the alphabetical modes choose up front
    ("for movies with Plex End Credits" / "for all movies", 0.4.0).

    Args:
        picked: the user's own selection from the list.

    Returns:
        The (possibly filtered) movie list to process, or None if the user cancelled.
    """
    with_credits = [mv for mv in picked if mv.has_plex_credits]
    without_credits = [mv for mv in picked if not mv.has_plex_credits]

    if not without_credits:
        return picked  # everything already has End Credits -- nothing to ask

    if not with_credits:
        return _ask_process_without_credits(picked)

    lead = "Some of the movies you selected don't have Plex End Credits yet."
    option_all = "Process all selected movies"
    option_only_text = (
        "The movies without Plex End Credits stay unprocessed. You can process\n"
        "     them next time, once Plex has detected their End Credits."
    )

    console.print(f"\n{lead}\n")
    console.print(
        f"  With Plex End Credits:                   {len(with_credits)}\n"
        f"  Without Plex End Credits:                {len(without_credits)}"
    )
    console.print("\n[bold]How would you like to handle them?[/bold]")
    console.print("  [dim]0)[/dim] Cancel")
    console.print("  [dim]1)[/dim] Process only the movies with Plex End Credits")
    console.print(f"     {option_only_text}")
    console.print(f"  [dim]2)[/dim] {option_all}")
    console.print(
        "     The movies without Plex End Credits will get Auto Chapters now, but no"
    )
    console.print(
        "     End Credits chapter. Once Plex detects their End Credits (usually within"
    )
    console.print(
        "     a few days), run the Update menu yourself to process them again, this"
    )
    console.print("     time with the End Credits chapter included.")

    try:
        choice = IntPrompt.ask("Choose", choices=["0", "1", "2"], show_choices=True)
    except (KeyboardInterrupt, EOFError):
        console.print()
        return None

    if choice == 0:
        return None
    if choice == 1:
        return with_credits
    return picked


def _ask_process_without_credits(picked: list[Movie]) -> list[Movie] | None:
    """None of the picked movies has Plex End Credits yet: process anyway, or wait?

    Returns `picked` if the user decides to go ahead, None if they cancel.
    The wording is singular for one movie, which is then named explicitly.
    """
    single = len(picked) == 1
    subj, obj, poss = ("It", "it", "its") if single else ("They", "them", "their")

    if single:
        lead = f"{escape(picked[0].display)} doesn't have Plex End Credits yet."
    else:
        lead = "None of the movies you selected have Plex End Credits yet."

    console.print(f"\n{lead}")
    console.print(f"\n[bold]How would you like to handle {obj}?[/bold]")
    console.print("  [dim]0)[/dim] Cancel")
    _print_option_note(
        f"Nothing is processed. You can process {obj} next time, once Plex has "
        f"detected {poss} End Credits."
    )
    console.print(f"  [dim]1)[/dim] Process {obj} anyway")
    _print_option_note(
        f"{subj} will get Auto Chapters now, but no End Credits chapter. Once Plex "
        f"detects {poss} End Credits (usually within a few days), run the Update menu "
        f"yourself to process {obj} again, this time with the End Credits chapter included."
    )

    try:
        choice = IntPrompt.ask("Choose", choices=["0", "1"], show_choices=True)
    except (KeyboardInterrupt, EOFError):
        console.print()
        return None

    return picked if choice == 1 else None


def _show_list_and_pick(candidates: list[Movie], orch: Orchestrator) -> None:
    """Show the numbered list, let the user pick movies to process.

    Mutates `candidates`: removes successfully processed movies so the parent
    menu's panel/list reflects reality without a re-scan.
    """
    console.print(f"\n[bold]Movies ({len(candidates)}):[/bold]")
    for i, mv in enumerate(candidates, start=1):
        marker = "" if mv.has_plex_credits else " [dim](no Plex End Credits yet)[/dim]"
        console.print(f"  [dim]{i:>4})[/dim] {mv.display}{marker}")

    while True:
        spec = _ask_text(
            "Select which movies to process. Examples: '3' for one, '1,5,7' for several,\n"
            "'1-5' for a range, '1-3,7,10-12' mixed.",
            empty_hint="Please type a selection (e.g. '3', '1,5,7', '1-5'), or 0 to cancel.",
        )
        if not spec:
            return

        try:
            indices = parse_selection(spec, len(candidates))
        except ValueError:
            console.print(
                "[yellow]That doesn't look right. Try something like '3', '1,5,7', or '1-5'.[/yellow]"
            )
            continue

        if not indices:
            console.print(
                f"[yellow]No movie numbers in that. Please pick between 1 and {len(candidates)}.[/yellow]"
            )
            continue

        break

    picked = [candidates[i - 1] for i in indices]
    console.print(f"\nSelected {len(picked)} movie(s):")
    for mv in picked:
        console.print(f"  - {mv.display}")

    filtered = _ask_credits_filter(picked)
    if filtered is None:
        return  # cancelled

    processed = _run_batch(filtered, orch)
    for mv in processed:
        if mv in candidates:
            candidates.remove(mv)


def _process_first_n(
    candidates: list[Movie], orch: Orchestrator, *, credits_only: bool = False
) -> None:
    """Process the first N movies from the list, in alphabetical order.

    credits_only: only the movies with End Credits detected by Plex -- chosen
    in Generate's own menu (0.4.0, owner: the choice was hidden in a question
    that came only after "alphabetical order"). The pool is narrowed BEFORE
    "how many?", so N counts from it: "5" means five movies with Plex End Credits.

    Mutates `candidates`: removes successfully processed movies.
    """
    if credits_only:
        pool = [mv for mv in candidates if mv.has_plex_credits]
        if not pool:
            console.print(
                "\n[yellow]None of the movies has Plex End Credits yet.[/yellow]"
            )
            return
    else:
        pool = list(candidates)
        if any(not mv.has_plex_credits for mv in pool):
            # Processing these means doing the work twice: say so once.
            console.print(
                "\n[dim]The movies without Plex End Credits get no End Credits chapter "
                "now. Once Plex has detected their End Credits, run the Update menu "
                "yourself to add it.[/dim]"
            )

    default = len(pool)
    console.print(
        f"\n[bold]How many movies should we process from the list?[/bold]\n"
        f"[dim]Movies are processed in alphabetical order, starting from the top of the list "
        f"(max {len(pool)}, or 0 to cancel).[/dim]"
    )
    try:
        n = IntPrompt.ask("Number", default=default)
    except (KeyboardInterrupt, EOFError):
        console.print()
        return

    if n <= 0:
        return
    if n > len(pool):
        n = len(pool)

    processed = _run_batch(pool[:n], orch)
    for mv in processed:
        if mv in candidates:
            candidates.remove(mv)


# An unreachable folder may also be a wrong path (a typo in the installer): say
# where it is fixed -- the owner found it only by chance (0.4.3).
_WRONG_PATH_HINT = (
    "[dim]If the path is wrong: Settings → Folder mappings → "
    "2) Change a mapping's local or mounted path.[/dim]"
)


def _print_missing_files(count: int, need: str) -> None:
    """Movies that need work but whose files weren't found where the mapping
    points. Said instead of "nothing to do": the 0.4.4 Ubuntu test (Plex's
    folder mapped to another folder) printed "Missing files: 10" and "All
    movies have Auto Chapters" together."""
    if count == 1:
        text = f"1 movie needs {need}, but its file wasn't found."
    else:
        text = f"{count} movies need {need}, but their files weren't found."
    console.print(f"\n[yellow]{text}[/yellow]")
    console.print(_WRONG_PATH_HINT)


def _check_folder_reachability(config: Config) -> tuple[bool, list[str]]:
    """Check enabled mapped folders are reachable before scanning.

    Returns (should_proceed, unreachable_folders). Callers should pass
    unreachable_folders straight into the scan call
    (known_unreachable_folders=...) -- the scan would otherwise redundantly
    run the exact same check again itself, doubling the wait for no
    benefit (menu.py already has the answer; the scan's own fallback
    computation exists only for callers that *didn't* do this pre-check).

    Cheap for the small number of mapped folders this app typically has;
    only called at natural I/O points (right before a scan), not on every
    menu loop -- see PathMapper.unreachable_enabled_folders for why.
    """
    mapper = PathMapper(config.paths)
    unreachable = mapper.unreachable_enabled_folders()
    if not unreachable:
        return True, []

    if len(unreachable) == mapper.enabled_count():
        console.print(
            "\n[red]None of the mapped folders are reachable right now:[/red]"
        )
        for path in unreachable:
            console.print(f"  {path}")
        console.print(
            "[dim]Check that the drive/share is mounted, then try again.[/dim]"
        )
        console.print(_WRONG_PATH_HINT)
        return False, unreachable

    console.print("\n[yellow]Some mapped folders aren't reachable right now:[/yellow]")
    for path in unreachable:
        console.print(f"  {path}")
    console.print(
        "[dim]Check that the drive/share is mounted. "
        "Movies there will show as missing.[/dim]"
    )
    console.print(_WRONG_PATH_HINT)
    return True, unreachable


def _generate_auto_chapters_flow(orch: Orchestrator, config: Config) -> None:
    """Scan status + generate Auto Chapters for movies that need them."""
    if not any(m.enabled for m in config.paths):
        # The main menu banner already shows this reminder right above and
        # right below the choice the user just made -- printing it a third
        # time here would just be visual noise.
        return
    should_proceed, known_unreachable = _check_folder_reachability(config)
    if not should_proceed:
        return
    try:
        with _scan_progress("Scanning Plex...") as on_progress:
            candidates, stats = _scan_with_retry(
                lambda: orch.plex.iter_movie_candidates(
                    chapter_threshold=config.detection.chapter_threshold,
                    min_duration_seconds=config.detection.min_duration_seconds,
                    known_unreachable_folders=known_unreachable,
                    on_progress=on_progress,
                )
            )
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelled.[/yellow]")
        return
    except Exception as e:
        log.error("scan_failed", error=str(e))
        console.print(f"\n[red]Couldn't scan Plex:[/red] {_friendly_plex_error(e)}")
        return

    _print_ignored_mappings(stats.ignored_mappings)

    candidates = _by_title(candidates)

    # The panel is rebuilt each iteration so it stays accurate as movies
    # are processed (and removed from the candidates list) within this session.
    initial_remaining = len(candidates)
    initial_auto = stats.with_auto_chapters

    while True:
        processed_so_far = initial_remaining - len(candidates)
        auto_done = initial_auto + processed_so_far

        with_credits = sum(1 for mv in candidates if mv.has_plex_credits)
        without_credits = len(candidates) - with_credits

        panel = Panel(
            f"  Total movies:                                     {stats.total_movies}\n"
            f"  Movies with chapters from another source:         {stats.with_other_chapters}\n"
            f"  Movies with Auto Chapters:                        {auto_done}\n"
            f"\n"
            f"  [bold]Movies needing Auto Chapters:                     {len(candidates)}[/bold]\n"
            f"    With Plex End Credits:                          {with_credits}\n"
            f"    Without Plex End Credits:                       {without_credits}\n"
            f"\n"
            f"  Too short (skipped):                              {stats.too_short}\n"
            f"  Non-MKV files (skipped):                          {stats.non_mkv}\n"
            f"  Missing files:                                    {stats.missing_files}",
            title=f"Plex server: {orch.plex.server_name}",
            border_style="cyan",
        )

        console.print(panel)

        if not candidates:
            if stats.total_movies == 0:
                console.print(
                    "\n[yellow]No movies found.[/yellow] "
                    "Add a folder mapping in Settings."
                )
            elif stats.missing_files:
                _print_missing_files(stats.missing_files, "Auto Chapters")
            else:
                console.print("\n[green]All movies have Auto Chapters. Nothing to do here.[/green]")
            return

        choice = _show_options(
            "What would you like to do?",
            [
                "Generate Auto Chapters for movies with Plex End Credits",
                "Generate Auto Chapters for all movies",
                "Show the list and pick movies manually",
            ],
            allow_cancel=True,
        )

        if choice == 0:
            return  # back to main menu
        if choice == 1:
            _process_first_n(candidates, orch, credits_only=True)
        elif choice == 2:
            _process_first_n(candidates, orch)
        elif choice == 3:
            _show_list_and_pick(candidates, orch)
        # Loop back: panel rebuilds with updated counts (no re-scan needed)


def _update_end_credits_flow(orch: Orchestrator, config: Config) -> None:
    """Find movies with Auto Chapters but no End Credits chapter
    (where Plex has since detected the credits start), and regenerate them."""
    if not any(m.enabled for m in config.paths):
        # See _generate_auto_chapters_flow: the main menu banner already
        # covers this, no need to repeat it here.
        return
    should_proceed, known_unreachable = _check_folder_reachability(config)
    if not should_proceed:
        return
    try:
        with _scan_progress("Scanning Plex for movies needing an End Credits chapter...") as on_progress:
            candidates, stats = _scan_with_retry(
                lambda: orch.plex.iter_credits_update_candidates(
                    min_duration_seconds=config.detection.min_duration_seconds,
                    known_unreachable_folders=known_unreachable,
                    on_progress=on_progress,
                )
            )
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelled.[/yellow]")
        return
    except Exception as e:
        log.error("credits_scan_failed", error=str(e))
        console.print(f"\n[red]Couldn't scan Plex:[/red] {_friendly_plex_error(e)}")
        return

    _print_ignored_mappings(stats.ignored_mappings)

    candidates = _by_title(candidates)

    initial_remaining = len(candidates)

    while True:
        processed_so_far = initial_remaining - len(candidates)
        remaining = len(candidates)

        panel = Panel(
            f"  Total movies:                       {stats.total_movies}\n"
            f"  Movies with Auto Chapters:          {stats.with_auto_chapters}\n"
            f"\n"
            f"  [bold]Movies needing End Credits update:  {remaining}[/bold]",
            title=f"Plex server: {orch.plex.server_name}",
            border_style="cyan",
        )
        console.print(panel)

        console.print(
            "\n[dim]Plex has since detected the End Credits start for these movies.\n"
            "Regenerating updates the file with an End Credits chapter at the right spot\n"
            "(and refreshes the existing Auto Chapters).[/dim]"
        )

        if not candidates:
            if stats.total_movies == 0:
                console.print(
                    "\n[yellow]No movies found.[/yellow] "
                    "Add a folder mapping in Settings."
                )
            else:
                if processed_so_far > 0:
                    console.print(
                        "\n[green]Done. All movies have been updated with the End Credits chapter.[/green]"
                    )
                if stats.missing_files:
                    _print_missing_files(stats.missing_files, "an End Credits update")
                elif processed_so_far == 0:
                    console.print(
                        "\n[green]No movies need an End Credits update right now.[/green]"
                    )
            return

        choice = _show_options(
            "What would you like to do?",
            [
                "Update Auto Chapters in alphabetical order",
                "Show the list and pick movies manually",
            ],
            allow_cancel=True,
        )

        if choice == 0:
            return
        if choice == 1:
            _process_first_n(candidates, orch)
        elif choice == 2:
            _show_list_and_pick(candidates, orch)


def _print_ignored_mappings(ignored: list[str]) -> None:
    """Tell the user which enabled mappings a scan did not use, and why.

    A mapping is used only if Plex has a movie folder with exactly its Plex path
    (PlexClient._scan_mapper). The movies under an ignored mapping are simply
    absent from the scan, so without this line they would look "missing".
    """
    for plex_path in ignored:
        console.print(
            f"\n[yellow]Not used:[/yellow] the mapping for {escape(plex_path)} -- "
            f"Plex has no folder with exactly this path."
        )
    if ignored:
        console.print(
            "[dim]If the folder was renamed or moved in Plex, remove this mapping and add "
            "the new folder\nin Settings > Folder mappings.[/dim]"
        )


# --- Find by title ---

_SKIP_REASON_TEXT = {
    SkipReason.NOT_MKV: "not an MKV file (only MKV files can be written)",
    SkipReason.FOLDER_UNREACHABLE: "its folder is not reachable right now",
    SkipReason.FILE_MISSING: "the file was not found on this machine",
    SkipReason.TOO_SHORT: "shorter than the Minimum movie length in Settings",
}


def _skip_reason_text(skipped: SkippedMatch) -> str:
    if skipped.reason is SkipReason.OTHER_CHAPTERS:
        return f"already has {skipped.chapter_count} chapters from another source"
    return _SKIP_REASON_TEXT[skipped.reason]


def _print_skipped_matches(skipped: list[SkippedMatch]) -> None:
    """List title-search matches that were left out, each with its reason."""
    for sm in skipped:
        console.print(f"  - {escape(sm.display)} -- {_skip_reason_text(sm)}")
    if any(sm.reason is SkipReason.OTHER_CHAPTERS for sm in skipped):
        console.print(
            "[dim]A chapter list from another source that is probably useful is left alone.\n"
            "Settings > Chapter threshold sets how many chapters it takes to assume that.[/dim]"
        )

def _find_by_title(orch: Orchestrator) -> None:
    if not any(m.enabled for m in orch.config.paths):
        # See _generate_auto_chapters_flow: the main menu banner already
        # covers this, no need to repeat it here.
        return
    should_proceed, known_unreachable = _check_folder_reachability(orch.config)
    if not should_proceed:
        return
    first_search = True
    while True:
        title = _ask_text(
            "Type a movie title"
            if not first_search
            else "Type a movie title to search for. The title shown in Plex or the original\n"
            "title works, accents are optional, and a part of the title is enough.\n"
            "\n"
            "[dim]Unlike Generate/Update, this also finds movies that already have Auto\n"
            "Chapters, so you can regenerate them on demand. A movie whose chapter list\n"
            "from another source is probably useful is left alone. Settings > Chapter\n"
            "threshold sets how many chapters it takes to assume that.[/dim]",
            empty_hint="Please type a movie title to search for, or 0 to cancel.",
        )
        if not title:
            return  # back to main menu
        first_search = False

        console.print(f"[dim]Searching for '{escape(title)}'...[/dim]")
        try:
            # partial() binds the current title right now (a lambda would look
            # it up later, which is what lint rule B023 warns about)
            movies, stats = _scan_with_retry(
                partial(
                    orch.plex.iter_movie_candidates,
                    chapter_threshold=orch.config.detection.chapter_threshold,
                    title_filter=title,
                    regenerate=True,
                    min_duration_seconds=orch.config.detection.min_duration_seconds,
                    known_unreachable_folders=known_unreachable,
                )
            )
        except KeyboardInterrupt:
            console.print("\n[yellow]Cancelled.[/yellow]")
            return
        except Exception as e:
            log.error("search_failed", error=str(e))
            console.print(f"\n[red]Search failed:[/red] {_friendly_plex_error(e)}")
            continue  # let the user try a different title

        _print_ignored_mappings(stats.ignored_mappings)

        if not movies:
            if stats.skipped:
                console.print(
                    f"[yellow]Found {len(stats.skipped)} "
                    f"{'movie' if len(stats.skipped) == 1 else 'movies'} matching "
                    f"'{escape(title)}', but none can be processed "
                    f"(this list is for information only):[/yellow]"
                )
                _print_skipped_matches(stats.skipped)
            else:
                console.print(
                    f"[yellow]No movie found matching '{escape(title)}'.[/yellow]\n"
                    f"[dim]Tip: check the spelling, or type a shorter part of the title.[/dim]"
                )
            continue  # back to title prompt -- nothing to process, try another title

        if stats.skipped:
            console.print(
                "\n[yellow]Also found, but cannot be processed "
                "(not selectable):[/yellow]"
            )
            _print_skipped_matches(stats.skipped)

        if len(movies) > 1:
            labels = [f"{escape(mv.display)}{_match_note(mv)}" for mv in movies]
            choice = _show_options(
                f"Found {len(movies)} matches",
                labels,
                allow_cancel=True,
            )
            if choice == 0:
                continue  # cancel -- back to the title prompt
            movie = movies[choice - 1]
        else:
            movie = movies[0]

        # If the picked movie already has Auto Chapters, ask explicitly for regeneration
        if movie.has_auto_chapters:
            choice = _show_options(
                f"{escape(movie.display)} {_auto_chapters_status(movie)}",
                ["Regenerate them (replaces the existing Auto Chapters)"],
                allow_cancel=True,
            )
            if choice == 0:
                continue  # cancel -- back to the title prompt
        elif len(movies) == 1:
            # Single fresh match -- one quick confirmation that this is the right movie
            choice = _show_options(
                f"Found 1 match: {escape(movie.display)}{_match_note(movie)}",
                ["Yes, this is the movie I meant"],
                allow_cancel=True,
            )
            if choice == 0:
                continue  # cancel -- back to the title prompt

        _detect_and_confirm_write(movie, orch)
        # Written or not (cancelled at "What now?", detection failed, stopped):
        # back to the title prompt for the next movie, where 0 leaves for the
        # main menu -- like Generate and Update, which stay on their own screen
        # after work. (Until 0.4.4 a write left for the main menu.)


# --- Settings ---

def _save_config_to_disk(config: Config, path: Path | None = None) -> None:
    """Write the current config back to YAML. The token stays in .env, not here.

    Written back to the config.yml it was loaded from (config.path), never to the
    current directory. Only values that changed are rewritten: the file's
    comments (what each setting means) and the formatting of everything else
    stay as they were.
    """
    path = path or config.path
    data = config.model_dump(exclude={"plex_token"})
    if "plex" in data and "token" in data["plex"]:
        data["plex"].pop("token", None)

    rt = YAML()  # round-trip mode: keeps comments, quotes and key order
    rt.preserve_quotes = True
    rt.width = 4096  # never re-wrap long paths
    rt.indent(mapping=2, sequence=4, offset=2)  # "  - plex: ...", like install.sh
    doc = rt.load(path.read_text(encoding="utf-8")) if path.exists() else None
    if not isinstance(doc, dict):
        doc = CommentedMap()
    _update_changed(doc, data)

    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        rt.dump(doc, f)
    tmp.replace(path)


def _pop_trailing_comment(seq: list[Any]) -> Any:
    """Detach the comment ruamel stored after the last key of the last item."""
    if seq and isinstance(seq[-1], CommentedMap) and seq[-1]:
        return seq[-1].ca.items.pop(list(seq[-1])[-1], None)
    return None


def _update_changed(doc: dict[str, Any], data: dict[str, Any]) -> None:
    """Copy `data` into `doc`, touching only the values that differ."""
    for key, value in data.items():
        if isinstance(value, dict) and isinstance(doc.get(key), dict):
            _update_changed(doc[key], value)
        elif isinstance(value, list) and isinstance(doc.get(key), list):
            # Item by item (the folder mappings): replacing the whole list
            # would drop the blank line and comments around it.
            seq = doc[key]
            # ruamel keeps the blank line/comment after the list on the last
            # key of the last item; take it off and put it back at the new end,
            # or it lands inside a mapping that gets a key added.
            trailing = _pop_trailing_comment(seq)
            for i, item in enumerate(value):
                if i >= len(seq):
                    seq.append(CommentedMap(item) if isinstance(item, dict) else item)
                elif isinstance(item, dict) and isinstance(seq[i], dict):
                    _update_changed(seq[i], item)
                elif seq[i] != item:
                    seq[i] = item
            del seq[len(value):]
            if trailing is not None and seq and isinstance(seq[-1], CommentedMap) and seq[-1]:
                seq[-1].ca.items[list(seq[-1])[-1]] = trailing
        elif key not in doc or doc[key] != value:
            doc[key] = value


def _save_env_token(token: str, path: Path) -> None:
    """Update PLEX_TOKEN in .env (the one next to config.yml), preserving any other keys."""
    lines: list[str] = []
    if path.exists():
        with open(path) as f:
            lines = [ln for ln in f if not ln.strip().startswith("PLEX_TOKEN=")]
    lines.append(f"PLEX_TOKEN={token}\n")
    with open(path, "w") as f:
        f.writelines(lines)
    with contextlib.suppress(OSError):
        path.chmod(0o600)


def _mask_token(token: str) -> str:
    if not token:
        return "(not set)"
    if len(token) <= 4:
        return "*" * len(token)
    return "*" * (len(token) - 4) + token[-4:]


def _test_plex_connection(url: str, token: str) -> tuple[bool, str]:
    """Try to connect. Returns (success, error_msg)."""
    try:
        from plexapi.exceptions import Unauthorized
        from plexapi.server import PlexServer

        server = PlexServer(url, token, timeout=5)  # type: ignore[no-untyped-call]
        _ = server.friendlyName  # force a real request
        return True, ""
    except Unauthorized:
        return False, "the token was rejected by this server"
    except Exception as e:
        return False, _friendly_plex_error(e)


def _unmapped_plex_folders(all_folders: list[str], config: Config) -> list[str]:
    """Plex movie folders that have no mapping yet (disabled ones count as mapped).

    Exact match, like the scans: a mapping is used only when its Plex path is
    one of Plex's folders, so a parent or sub path leaves the real folder unmapped.
    """
    already_mapped = {m.plex for m in config.paths}
    return [f for f in all_folders if f not in already_mapped]


def _mappings_unknown_to_plex(config: Config, plex_folders: list[str]) -> set[str]:
    """Plex paths of mappings that match none of Plex's movie folders exactly.

    The scans do not use such a mapping (PlexClient._scan_mapper), so this is
    the same rule: a folder renamed or removed in Plex, or a path edited by
    hand. With no folders from Plex there is nothing to compare against.
    """
    if not plex_folders:
        return set()
    known = set(plex_folders)
    return {m.plex for m in config.paths if m.plex not in known}


def _plex_folders_or_problem(orch: Orchestrator) -> tuple[list[str] | None, str]:
    """Ask Plex for its movie folders. Returns (folders, "") or (None, reason)."""
    try:
        return orch.plex.list_movie_folders(), ""
    except Exception as e:
        return None, _friendly_plex_error(e)


# Menu numbers on the Folder mappings screen. Fixed on purpose: they do not
# depend on how many mappings there are (the mappings themselves are not
# numbered here -- each action opens its own numbered pick list).
_MAP_ADD, _MAP_CHANGE, _MAP_TOGGLE, _MAP_REMOVE = 1, 2, 3, 4


def _mapping_line(m: PathMapping) -> str:
    """"path on this computer  ->  path in Plex  [active|disabled]"."""
    state = "[green]active[/green]" if m.enabled else "[yellow]disabled[/yellow]"
    return f"{escape(m.local)}  ->  {escape(m.plex)}  \\[{state}]"


def _pick_mapping(config: Config, title: str) -> PathMapping | None:
    """Numbered list of the mappings. Returns the chosen one, None for Back / Ctrl+C."""
    console.print(f"\n[bold]{title}[/bold]")
    console.print("  [cyan]0[/cyan]) Back")
    for i, m in enumerate(config.paths, start=1):
        console.print(f"  [cyan]{i}[/cyan]) {_mapping_line(m)}")
    try:
        choice = IntPrompt.ask(
            "Choose",
            choices=[str(i) for i in range(0, len(config.paths) + 1)],
            show_choices=True,
        )
    except (KeyboardInterrupt, EOFError):
        console.print()
        return None
    return None if choice == 0 else config.paths[choice - 1]


def _folder_mappings(config: Config, orch: Orchestrator) -> None:
    """The mappings with their state, and Add / Change / Enable-disable / Remove."""
    # Plex is asked once, when the screen opens. Later renders (after an
    # Add, Change, Enable/disable or Remove) compare this list with the current
    # config, so the "no mapping yet" line stays right without another request.
    console.print("\n[dim]Asking Plex for its folders...[/dim]")
    plex_folders, plex_problem = _plex_folders_or_problem(orch)

    while True:
        # Checked fresh each time this list renders -- bounded per folder
        # (see PathMapper.check_enabled_folders), so even if every enabled
        # folder happens to be on a hung mount, this screen still renders
        # within a predictable time and the user can always reach Remove/
        # disable -- never gets stuck with no way to fix a bad mapping.
        console.print("\n[dim]Checking folder status...[/dim]")
        status_by_local = {
            c.local: c.reachable
            for c in PathMapper(config.paths).check_enabled_folders()
        }
        unknown_to_plex = (
            _mappings_unknown_to_plex(config, plex_folders) if plex_folders is not None else set()
        )

        console.print("\n[bold]Folder mappings[/bold]")
        if not config.paths:
            console.print("  [dim](no folders mapped yet)[/dim]")
        for m in config.paths:
            reachable = status_by_local.get(m.local) if m.enabled else True
            if reachable is True:
                status_tag = ""
            elif reachable is False:
                status_tag = "  [red]\\[unreachable now][/red]"
            else:  # None -- timed out, possibly a hung mount
                status_tag = "  [red]\\[couldn't check -- may be hung][/red]"
            plex_tag = (
                "  [red]\\[not found in Plex][/red]" if m.plex in unknown_to_plex else ""
            )
            console.print(f"  {_mapping_line(m)}{status_tag}{plex_tag}")

        console.print()
        console.print("  [cyan]0[/cyan]) Back")
        console.print(f"  [cyan]{_MAP_ADD}[/cyan]) Add folder mapping")
        if config.paths:
            console.print(
                f"  [cyan]{_MAP_CHANGE}[/cyan]) Change a mapping's local or mounted path"
            )
            console.print(f"  [cyan]{_MAP_TOGGLE}[/cyan]) Enable or disable a mapping")
            console.print(f"  [cyan]{_MAP_REMOVE}[/cyan]) Remove folder mapping")
            console.print(
                "[dim]Left: the folder's path on this computer (local or mounted). Right:\n"
                "the same folder's path in Plex.[/dim]"
            )

        if unknown_to_plex:
            console.print(
                "[dim]\\[not found in Plex]: Plex does not list a folder with exactly this\n"
                "path, so this mapping is not used. If the folder was renamed or moved in\n"
                "Plex, remove this mapping and add the new folder (it is listed under Add).[/dim]"
            )

        if plex_folders is None:
            # Say so: silence would look like "everything is fine".
            console.print(
                f"[dim]Couldn't ask Plex for its folders, so folders without a mapping and\n"
                f"mappings Plex does not know can't be shown: {escape(plex_problem)}[/dim]"
            )
        elif not plex_folders:
            console.print(
                "[dim]Plex reported no movie folders, so the mappings can't be compared "
                "with it.[/dim]"
            )
        else:
            unmapped = _unmapped_plex_folders(plex_folders, config)
            if unmapped:
                many = len(unmapped) > 1
                console.print(
                    f"[yellow]{len(unmapped)} Plex folder{'s have' if many else ' has'} "
                    f"no mapping yet.[/yellow]\n"
                    f"[dim]Movies in a folder without a mapping are skipped. "
                    f"Choose {_MAP_ADD} to map {'them' if many else 'it'}.[/dim]"
                )

        max_choice = _MAP_REMOVE if config.paths else _MAP_ADD
        try:
            choice = IntPrompt.ask(
                "Choose",
                choices=[str(i) for i in range(0, max_choice + 1)],
                show_choices=True,
            )
        except (KeyboardInterrupt, EOFError):
            console.print()
            return

        if choice == 0:
            return
        try:
            if choice == _MAP_ADD:
                fresh = _add_folder_mapping(config, orch)
                if fresh is not None:
                    # Add just asked Plex; use that list so a folder new in Plex
                    # is not flagged "not found" merely because it is newer than
                    # the copy taken when the screen opened.
                    plex_folders, plex_problem = fresh, ""
            elif choice == _MAP_CHANGE:
                _change_folder_mapping(config, orch)
            elif choice == _MAP_TOGGLE:
                _toggle_folder_mapping(config)
            elif choice == _MAP_REMOVE:
                _remove_folder_mapping(config)
        except (KeyboardInterrupt, EOFError):
            _cancelled()  # Ctrl+C while asking Plex or checking a path: one step back


def _toggle_folder_mapping(config: Config) -> None:
    """Switch mappings between active and disabled, each saved at once.

    Works like a switch: after each change the list is shown again with the
    new state, until 0 (Back). Until 0.4.4 every switch jumped back to the
    mappings screen and its folder check.
    """
    while True:
        m = _pick_mapping(config, "Enable or disable a mapping")
        if m is None:
            return
        m.enabled = not m.enabled
        _save_config_to_disk(config)
        state = "enabled" if m.enabled else "disabled"
        console.print(f"[green]OK[/green] {escape(m.local)} -> {escape(m.plex)} {state}.")


def _remove_folder_mapping(config: Config) -> None:
    """Let the user pick a folder mapping to remove from the config."""
    m = _pick_mapping(config, "Remove folder mapping")
    if m is None:
        return

    try:
        confirm = Prompt.ask(
            f"Remove {escape(m.local)} -> {escape(m.plex)}? (y/n)",
            choices=["y", "n"],
            case_sensitive=False,
        )
    except (KeyboardInterrupt, EOFError):
        console.print()
        return
    if confirm.lower() != "y":
        return

    config.paths.remove(m)
    _save_config_to_disk(config)
    console.print(f"[green]OK[/green] {escape(m.local)} -> {escape(m.plex)} removed.")


def _ask_path_on_this_computer(orch: Orchestrator, plex_path: str) -> str | None:
    """Ask where THIS computer reaches Plex folder `plex_path` (Plex may run elsewhere).

    Returns the path with a trailing "/", or None if the user cancelled or
    declined to use a path that is not reachable right now. An unreachable
    path is allowed after a question: the NAS may simply not be mounted yet.
    A reachable one is compared with the movies Plex has in the folder
    (_movies_are_here); if none of them are there, the user may type another.
    """
    while True:
        path = _ask_one_path()
        if path is None:
            return None
        reachable = check_dir_with_timeout(path)
        if reachable is True:
            if _movies_are_here(orch, plex_path, path):
                return path
            continue
        if reachable is False:
            console.print(f"[yellow]{escape(path)} does not exist on this computer.[/yellow]")
            console.print(
                "[dim]This is fine if the folder will be mounted later "
                "(e.g. NAS not connected yet).[/dim]"
            )
        else:  # None -- timed out, possibly an unresponsive mount
            console.print(f"[yellow]Couldn't verify {escape(path)} in time.[/yellow]")
            console.print(
                "[dim]It may be on a mount that's present but not responding.[/dim]"
            )
        try:
            keep = Prompt.ask(
                "Use it anyway?",
                choices=["y", "n"],
                case_sensitive=False,
            )
        except (KeyboardInterrupt, EOFError):
            console.print()
            return None
        return path if keep.lower() == "y" else None


def _movies_are_here(orch: Orchestrator, plex_path: str, path: str) -> bool:
    """Compare a reachable folder with the movies Plex has in `plex_path`.

    Shows how many of them are here. Every movie is looked up, in one listing
    of the folder (folder_match). False when none are and the user wants to
    type another path. If it can't tell (Plex not answering, folder not read
    in time, no movies in Plex's folder) it says nothing and keeps the path.
    """
    console.print("[dim]Comparing with the movies Plex has in this folder...[/dim]")
    try:
        match = orch.plex.folder_match(plex_path, path)
    except Exception as e:
        log.warning("folder_match_failed", error=str(e))
        return True
    if match is None or match.total == 0:
        return True
    found = f"Found {match.found} of {match.total} Plex movies in this folder."
    if match.found:
        console.print(f"[green]OK[/green] {found}")
        return True
    console.print(f"[yellow]{found} Is the path right?[/yellow]")
    console.print("  [cyan]1[/cyan]) Enter another path")
    console.print("  [cyan]2[/cyan]) Use it anyway")
    choice = IntPrompt.ask("Choose", choices=["1", "2"], show_choices=True)
    return choice == 2


# "smb://...", "nfs://...", "file://...": what a file manager shows in its
# address bar, copied instead of a folder path (0.4.4 Ubuntu test).
_ADDRESS = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*)://")


def _network_address_text(scheme: str) -> str:
    return (
        f"[yellow]That's a network address ({escape(scheme)}://...), not a folder path.[/yellow]\n"
        "auto-chapter reads and writes the movie files directly, so the share has to be\n"
        "mounted as a folder on this computer first. How to do that: INSTALL.md,\n"
        '"Mounting a network share".'
    )


def _ask_one_path() -> str | None:
    """The folder path the user types (~ expanded, final "/"), or None for 0."""
    while True:
        path = _ask_text(
            "Local or mounted path to this folder on this computer",
            empty_hint="Please type the folder path, or 0 to cancel.",
        )
        if not path:
            return None
        address = _ADDRESS.match(path)
        if address and address.group(1).lower() == "file":
            # A local folder's address: the same folder, so just use its path.
            path = unquote(urlsplit(path).path)
        elif address:
            console.print(_network_address_text(address.group(1)))
            continue
        # Expand ~ to user home
        path = str(Path(path).expanduser())
        # Absolute path check
        if not path.startswith("/"):
            console.print(
                "[yellow]That doesn't look like a folder path. It should start with '/' or '~/'.[/yellow]"
            )
            continue
        return path if path.endswith("/") else path + "/"


def _change_folder_mapping(config: Config, orch: Orchestrator) -> None:
    """Change where this computer reaches an already mapped Plex folder.

    Keeps the mapping's active/disabled state. Saves the new path at once.
    """
    m = _pick_mapping(config, "Change a mapping's local or mounted path")
    if m is None:
        return

    console.print(f"\n[bold]Plex path:[/bold] {escape(m.plex)}")
    console.print(f"[bold]Now on this computer:[/bold] {escape(m.local)}")

    new_path = _ask_path_on_this_computer(orch, m.plex)
    if new_path is None:
        return
    if new_path == m.local:
        console.print("[dim]That is the path it already has. Nothing changed.[/dim]")
        return

    old_path = m.local
    m.local = new_path
    _save_config_to_disk(config)
    console.print(
        f"[green]OK[/green] Mapping changed: {escape(new_path)} -> {escape(m.plex)} "
        f"[dim](was {escape(old_path)})[/dim]"
    )


def _add_folder_mapping(config: Config, orch: Orchestrator) -> list[str] | None:
    """Show Plex folders that aren't mapped yet, let the user pick one.

    Returns the folder list it just got from Plex (None if Plex could not be
    asked), so the Folder mappings screen can refresh its own copy.
    """
    try:
        all_folders = orch.plex.list_movie_folders()
    except Exception as e:
        console.print(f"\n[red]Couldn't get folders from Plex:[/red] {_friendly_plex_error(e)}")
        return None

    unmapped = _unmapped_plex_folders(all_folders, config)

    if not unmapped:
        console.print(
            "\n[dim]No new folders in Plex. All movie folders are already mapped.[/dim]"
        )
        return all_folders

    console.print(f"\n[bold]Found {len(unmapped)} unmapped folder(s) in Plex:[/bold]")
    console.print("  [cyan]0[/cyan]) Cancel")
    for i, f in enumerate(unmapped, start=1):
        console.print(f"  [cyan]{i}[/cyan]) {f}")

    try:
        choice = IntPrompt.ask(
            "Which to add?",
            choices=[str(i) for i in range(0, len(unmapped) + 1)],
            show_choices=True,
        )
    except (KeyboardInterrupt, EOFError):
        console.print()
        return all_folders

    if choice == 0:
        return all_folders

    plex_path = unmapped[choice - 1]
    console.print(f"\n[bold]Plex path:[/bold] {escape(plex_path)}")

    local_path = _ask_path_on_this_computer(orch, plex_path)
    if local_path is None:
        return all_folders

    config.paths.append(PathMapping(plex=plex_path, local=local_path))
    _save_config_to_disk(config)
    console.print(
        f"[green]OK[/green] Mapping added: {escape(local_path)} -> {escape(plex_path)}"
    )
    return all_folders


def _min_movie_length(config: Config) -> None:
    """Change min_duration_seconds (stored in seconds, displayed in minutes)."""
    current_min = config.detection.min_duration_seconds // 60
    console.print("\n[bold]Minimum movie length[/bold]")
    console.print("Movies shorter than this are skipped during scanning.")
    console.print(f"Current: [cyan]{current_min}[/cyan] minutes")

    while True:
        try:
            new_val = IntPrompt.ask("\nNew value in minutes (or 0 to cancel)")
        except (KeyboardInterrupt, EOFError):
            console.print()
            return
        if new_val == 0:
            return
        if new_val < 1:
            console.print("[yellow]Must be at least 1 minute.[/yellow]")
            continue
        break

    config.detection.min_duration_seconds = new_val * 60
    _save_config_to_disk(config)
    console.print(f"[green]OK[/green] Saved. New minimum: {new_val} minutes.")


def _chapter_threshold(config: Config) -> None:
    """Change detection.chapter_threshold — the cutoff for skipping already-chaptered movies."""
    current = config.detection.chapter_threshold
    console.print("\n[bold]Chapter threshold[/bold]")
    console.print("Movies with this many chapters or more are assumed to already have a useful")
    console.print("chapter list and are skipped. Fewer chapters are assumed not to be useful.")
    console.print(f"Current: [cyan]{current}[/cyan]")

    while True:
        try:
            new_val = IntPrompt.ask("\nNew value (1-99, or 0 to cancel)")
        except (KeyboardInterrupt, EOFError):
            console.print()
            return
        if new_val == 0:
            return
        if new_val < 1 or new_val > 99:
            console.print("[yellow]Must be between 1 and 99.[/yellow]")
            continue
        break

    config.detection.chapter_threshold = new_val
    _save_config_to_disk(config)
    console.print(f"[green]OK[/green] Saved. New threshold: {new_val}.")


def _plex_connection(config: Config, orch: Orchestrator) -> None:
    """Show and edit Plex URL / token."""
    while True:
        masked = _mask_token(config.plex.token)
        console.print("\n[bold]Plex connection[/bold]")
        console.print("  [cyan]0[/cyan]) Back")
        console.print(f"  [cyan]1[/cyan]) Change Plex URL   [dim]({config.plex.url})[/dim]")
        console.print(f"  [cyan]2[/cyan]) Change Plex token [dim]({masked})[/dim]")
        console.print("  [cyan]3[/cyan]) Reconnect (retry with current URL/token)")

        try:
            choice = IntPrompt.ask(
                "Choose", choices=["0", "1", "2", "3"], show_choices=True
            )
        except (KeyboardInterrupt, EOFError):
            console.print()
            return

        if choice == 0:
            return
        try:
            _plex_connection_action(choice, config, orch)
        except (KeyboardInterrupt, EOFError):
            _cancelled()  # Ctrl+C while a URL / token is tested: one step back


def _plex_connection_action(choice: int, config: Config, orch: Orchestrator) -> None:
    """One choice of the Plex connection screen."""
    if choice == 1:
        _change_plex_url(config, orch)
    elif choice == 2:
        _change_plex_token(config, orch)
    elif choice == 3:
        # reconnect() is also what backs "test connection" in effect --
        # unlike the old standalone Test option (a throwaway probe
        # using its own fresh PlexServer, never touching orch.plex),
        # this actually updates orch.plex's live connection state, so
        # there's no reason to keep both: checking AND fixing in one
        # action is strictly more useful than checking alone. Prints
        # its own "Connecting to Plex..." -- no need to print anything
        # before calling it.
        if orch.plex.reconnect():
            console.print(
                f"[green]OK[/green] Connected to {orch.plex.server_name}."
            )
        else:
            console.print(
                f"[red]Still not connected:[/red] "
                f"{orch.plex.connection_error or 'unknown error'}"
            )


def _change_plex_url(config: Config, orch: Orchestrator) -> None:
    new_url = _ask_text(
        "Enter new Plex URL",
        empty_hint="Please type a URL, or 0 to cancel.",
    )
    if not new_url:
        return

    # Auto-prepend http:// if the user forgot the scheme
    if not new_url.startswith(("http://", "https://")):
        new_url = f"http://{new_url}"

    console.print("\n[dim]Testing new URL...[/dim]")
    ok, err = _test_plex_connection(new_url, config.plex.token)
    if not ok:
        console.print(f"[red]Connection failed:[/red] {err}")
        console.print("[dim]URL not saved. The previous URL remains active.[/dim]")
        return

    config.plex.url = new_url
    _save_config_to_disk(config)

    # config.plex is the same object orch.plex already holds a reference
    # to, so it already sees the new URL -- reconnect() just needs to
    # (re)establish the actual connection, no rebuild needed.
    orch.plex.reconnect()
    console.print("[green]OK[/green] Connection succeeded. New URL saved.")


def _change_plex_token(config: Config, orch: Orchestrator) -> None:
    new_token = _ask_text(
        "Enter new Plex token",
        empty_hint="Please type a token, or 0 to cancel.",
    )
    if not new_token:
        return

    console.print("\n[dim]Testing new token...[/dim]")
    ok, err = _test_plex_connection(config.plex.url, new_token)
    if not ok:
        console.print(f"[red]Connection failed:[/red] {err}")
        console.print("[dim]Token not saved. The previous token remains active.[/dim]")
        return

    config.plex.token = new_token
    _save_env_token(new_token, config.env_path)

    # Same reasoning as _change_plex_url: config.plex is shared, just
    # reconnect using it, no need to rebuild orch.plex from scratch.
    orch.plex.reconnect()
    console.print("[green]OK[/green] Connection succeeded. New token saved.")


def _cancelled() -> None:
    """Ctrl+C during a wait in Settings: one step back, said once (0.4.4)."""
    console.print("\n[yellow]Cancelled.[/yellow]")


def _settings_flow(orch: Orchestrator, config: Config) -> None:
    """Top-level Settings menu."""
    while True:
        choice = _show_options(
            "Settings",
            [
                "Folder mappings",
                "Minimum movie length",
                "Chapter threshold",
                "Plex connection",
            ],
            allow_cancel=True,
            cancel_label="Back to main menu",
        )
        if choice == 0:
            return
        try:
            if choice == 1:
                _folder_mappings(config, orch)
            elif choice == 2:
                _min_movie_length(config)
            elif choice == 3:
                _chapter_threshold(config)
            elif choice == 4:
                _plex_connection(config, orch)
        except (KeyboardInterrupt, EOFError):
            # Ctrl+C while a screen asks Plex or checks the folders (that can
            # take seconds on a hung NAS): back to Settings, not the main menu.
            _cancelled()


def _about_flow(config: Config) -> None:
    """Show version, log location, and project info."""
    from auto_chapter import __version__

    log_path = config.log_file_path()

    console.print(
        Panel(
            f"[bold]auto-chapter[/bold] v{__version__}\n"
            f"[dim]Automatic chapter marker generation for MKV movies "
            f"on a Plex server.[/dim]\n"
            f"\n"
            f"[bold]Log file:[/bold]  {log_path if log_path else '(file logging disabled)'}\n"
            f"[bold]GitHub:[/bold]    https://github.com/balazs-katona/auto-chapter\n"
            f"[bold]License:[/bold]   GPL-3.0-or-later (no warranty, see LICENSE)\n"
            f"\n"
            f"[dim]Found a bug or have a feature request? Open an issue there.[/dim]",
            border_style="cyan",
        )
    )
    console.print("\n  [cyan]0[/cyan]) Back to main menu")
    try:
        IntPrompt.ask("Choose", choices=["0"], default=0, show_choices=True, show_default=False)
    except (KeyboardInterrupt, EOFError):
        console.print()


# --- Top-level loop ---


def run_menu(orch: Orchestrator, config: Config) -> None:
    """Top-level interactive menu loop."""
    from auto_chapter import __version__

    # The disconnected-state warning lives solely in the per-loop-iteration
    # banner below (same pattern as "No folders mapped") -- it fires on the
    # very first iteration too, so putting a second copy of the same message
    # here would just duplicate it back to back.
    if orch.plex.is_connected:
        status_line = f"\n[dim]Connected to: {orch.plex.server_name}[/dim]"
    else:
        status_line = ""

    console.print(
        Panel(
            f"[bold]auto-chapter[/bold] [dim]v{__version__}[/dim]{status_line}",
            border_style="green",
        )
    )

    options = [
        "Generate Auto Chapters",
        "Update Auto Chapters with missing Plex End Credits",
        "Find a movie by title",
        "Settings",
        "About",
    ]

    while True:
        if not orch.plex.is_connected:
            reason = orch.plex.connection_error
            console.print(
                f"\n[yellow]Not connected to Plex{': ' if reason else '.'}[/yellow]"
                f"{escape(reason) if reason else ''}\n"
                "[dim]Generate/Update/Find will retry automatically -- or use "
                "Settings → Plex connection to check now or update the address/token.[/dim]"
            )
        if not any(m.enabled for m in config.paths):
            console.print(
                "\n[yellow]No folders mapped.[/yellow] "
                "[dim]Add one in Settings → Folder mappings.[/dim]"
            )
        try:
            choice = _show_options(
                "Main menu", options, allow_cancel=True, cancel_label="Quit"
            )
        except (KeyboardInterrupt, EOFError):
            # Safety net; _show_options with allow_cancel returns 0 instead
            console.print("\n[dim]Goodbye.[/dim]")
            return

        if choice == 0:
            console.print("\n[dim]Goodbye.[/dim]")
            return

        try:
            if choice == 1:
                _generate_auto_chapters_flow(orch, config)
            elif choice == 2:
                _update_end_credits_flow(orch, config)
            elif choice == 3:
                _find_by_title(orch)
            elif choice == 4:
                _settings_flow(orch, config)
            elif choice == 5:
                _about_flow(config)
        except (KeyboardInterrupt, EOFError):
            # User pressed Ctrl+C / Ctrl+D somewhere -- treat as cancel back to main
            console.print("\n[yellow]Cancelled.[/yellow]")
        except Exception as e:
            # Last-resort safety net: any unexpected error returns to main menu
            log.error("menu_error", error=str(e))
            console.print("\n[red]Something went wrong. Please try again.[/red]")
