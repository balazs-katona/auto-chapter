# SPDX-FileCopyrightText: 2026 Balazs Katona
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for menu helpers: selection parsing and the End Credits question."""

import dataclasses
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

from auto_chapter import menu
from auto_chapter.config import PathMapping
from auto_chapter.errors import MovieReadError
from auto_chapter.folder_match import FolderMatch
from auto_chapter.menu import _ask_credits_filter, parse_selection
from auto_chapter.models import (
    Chapter,
    DetectionMethod,
    DetectionResult,
    Movie,
    ProcessingResult,
)
from auto_chapter.plex_client import (
    CreditsScanStats,
    PlexUnavailableError,
    ScanStats,
    SkippedMatch,
    SkipReason,
)


@pytest.fixture(autouse=True)
def _no_real_inhibitor(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test may take a real sleep lock."""

    @contextmanager
    def no_lock() -> Iterator[bool]:
        yield False

    monkeypatch.setattr(menu, "keep_awake", no_lock)


def test_single_number() -> None:
    assert parse_selection("3", 10) == [3]


def test_comma_separated() -> None:
    assert parse_selection("1,5,7", 10) == [1, 5, 7]


def test_range() -> None:
    assert parse_selection("1-5", 10) == [1, 2, 3, 4, 5]


def test_mixed() -> None:
    assert parse_selection("1-3,7,10-12", 15) == [1, 2, 3, 7, 10, 11, 12]


def test_duplicates_removed() -> None:
    assert parse_selection("1,2,2,3-5,5", 10) == [1, 2, 3, 4, 5]


def test_reversed_range() -> None:
    assert parse_selection("5-1", 10) == [1, 2, 3, 4, 5]


def test_out_of_range_ignored() -> None:
    assert parse_selection("1,5,99,100", 10) == [1, 5]


def test_empty() -> None:
    assert parse_selection("", 10) == []
    assert parse_selection("   ", 10) == []


def test_whitespace_around() -> None:
    assert parse_selection(" 1 , 3 - 5 , 7 ", 10) == [1, 3, 4, 5, 7]


def test_invalid_raises() -> None:
    with pytest.raises(ValueError):
        parse_selection("abc", 10)
    with pytest.raises(ValueError):
        parse_selection("1,2,x", 10)


# --- _ask_credits_filter -----------------------------------------------------
#
# Design principle under test: End Credits movies are the priority, but the
# user may consciously process the others too (knowing it means doing the work
# twice). So the question is asked whenever ANY picked movie lacks Plex End
# Credits -- including when none of them has it.


def _movie(title: str, has_credits: bool, year: int | None = 2000) -> Movie:
    return Movie(
        title=title,
        year=year,
        duration=7200.0,
        local_path=Path(f"/media/{title}.mkv"),
        plex_path=f"/plex/{title}.mkv",
        chapter_count=0,
        has_plex_credits=has_credits,
    )


ENTER = object()  # scripted answer meaning "just press Enter" (accept the default)


@pytest.fixture
def prompt(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Replace menu.IntPrompt with a scripted stand-in.

    Set `prompt.answers` to what the "user" types, in the order the questions
    come: an int, ENTER (accept the default), or an exception instance to
    raise instead (e.g. KeyboardInterrupt). Every question that was asked is
    recorded: `prompt.asked` holds (text, choices, default) in order,
    `prompt.choices` just the `choices` lists of the multiple-choice ones, and
    `prompt.shown` whether each question shows its choices (Rich's default: yes).
    """
    state = SimpleNamespace(answers=[], asked=[], choices=[], shown=[])

    def fake_ask(
        text: str,
        *,
        choices: list[str] | None = None,
        default: int | None = None,
        **_kwargs: object,
    ) -> int | None:
        state.asked.append((text, choices, default))
        state.shown.append(_kwargs.get("show_choices", True))
        if choices is not None:
            state.choices.append(choices)
        answer = state.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return default if answer is ENTER else answer

    monkeypatch.setattr(menu, "IntPrompt", SimpleNamespace(ask=fake_ask))
    return state


def test_credits_filter_all_with_credits_asks_nothing(prompt: SimpleNamespace) -> None:
    picked = [_movie("A", True), _movie("B", True)]

    assert _ask_credits_filter(picked) == picked
    assert prompt.choices == []  # no question was asked


def test_credits_filter_none_with_credits_can_be_cancelled(prompt: SimpleNamespace) -> None:
    picked = [_movie("A", False), _movie("B", False)]
    prompt.answers = [0]

    assert _ask_credits_filter(picked) is None
    assert prompt.choices == [["0", "1"]]


def test_credits_filter_none_with_credits_can_proceed_anyway(prompt: SimpleNamespace) -> None:
    picked = [_movie("A", False), _movie("B", False)]
    prompt.answers = [1]

    assert _ask_credits_filter(picked) == picked
    assert prompt.choices == [["0", "1"]]


def test_credits_filter_mixed_only_with_credits(prompt: SimpleNamespace) -> None:
    credited = [_movie("A", True), _movie("B", True)]
    picked = [credited[0], _movie("C", False), credited[1]]
    prompt.answers = [1]

    assert _ask_credits_filter(picked) == credited
    assert prompt.choices == [["0", "1", "2"]]


def test_credits_filter_mixed_process_all(prompt: SimpleNamespace) -> None:
    picked = [_movie("A", True), _movie("C", False), _movie("B", True)]
    prompt.answers = [2]

    assert _ask_credits_filter(picked) == picked


def test_credits_filter_mixed_can_be_cancelled(prompt: SimpleNamespace) -> None:
    picked = [_movie("A", True), _movie("C", False)]
    prompt.answers = [0]

    assert _ask_credits_filter(picked) is None


@pytest.mark.parametrize("exc", [KeyboardInterrupt, EOFError])
@pytest.mark.parametrize(
    "flags",
    [(True, False), (False, False)],
    ids=["mixed", "none-with-credits"],
)
def test_credits_filter_interrupt_cancels(
    prompt: SimpleNamespace, flags: tuple[bool, bool], exc: type[BaseException]
) -> None:
    """Ctrl+C / Ctrl+D at either question backs out instead of crashing."""
    picked = [_movie(f"M{i}", has_credits) for i, has_credits in enumerate(flags)]
    prompt.answers = [exc()]

    assert _ask_credits_filter(picked) is None


def test_credits_filter_none_with_credits_wording(
    prompt: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    prompt.answers = [0]

    _ask_credits_filter([_movie("A", False), _movie("B", False)])

    out = capsys.readouterr().out
    assert "None of the movies you selected have Plex End Credits yet." in out
    assert "run the Update menu yourself" in " ".join(out.split())  # the note wraps
    assert "Process them anyway" in out


def test_credits_filter_mixed_option_wording(
    prompt: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    prompt.answers = [0]

    _ask_credits_filter([_movie("A", True), _movie("B", False)])

    assert "Process all selected movies" in capsys.readouterr().out


def test_credits_filter_single_movie_is_named_and_singular(
    prompt: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    """One movie reads naturally ("it", not "them") and is named."""
    prompt.answers = [0]

    _ask_credits_filter([_movie("Heat", False, year=1995)])

    out = capsys.readouterr().out
    assert "Heat (1995) doesn't have Plex End Credits yet." in out
    assert "Process it anyway" in out
    assert "process them" not in out
    assert "their" not in out


def test_credits_filter_title_in_brackets_is_printed_literally(
    prompt: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    """Rich would swallow an unescaped "[rec]" as a style tag."""
    prompt.answers = [0]

    _ask_credits_filter([_movie("[rec]", False, year=2007)])

    assert "[rec] (2007)" in capsys.readouterr().out


# --- _process_first_n (alphabetical mode) ------------------------------------
#
# Generate's own menu chooses "for movies with End Credits" or "for all
# movies" (0.4.0); the pool is narrowed before "how many?", so the number
# counts from it.


def _ten_movies() -> list[Movie]:
    """M01..M10 in alphabetical order; M02, M04 and M07 lack Plex End Credits."""
    lacking = {"M02", "M04", "M07"}
    return [_movie(f"M{i:02d}", f"M{i:02d}" not in lacking) for i in range(1, 11)]


@pytest.fixture
def batch(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Replace menu._run_batch: record what would be processed (by title) and
    report every movie as written, so `_process_first_n` drops it from the list."""
    state = SimpleNamespace(calls=[])

    def fake_run_batch(movies: list[Movie], _orch: object) -> list[Movie]:
        state.calls.append([mv.title for mv in movies])
        return list(movies)

    monkeypatch.setattr(menu, "_run_batch", fake_run_batch)
    return state


def test_first_n_with_end_credits_only_asks_just_how_many(
    prompt: SimpleNamespace, batch: SimpleNamespace
) -> None:
    """"5" means five movies WITH End Credits -- not 'the first five, minus
    the credit-less ones'."""
    prompt.answers = [5]

    menu._process_first_n(_ten_movies(), None, credits_only=True)

    assert [text for text, _, _ in prompt.asked] == ["Number"]
    assert batch.calls == [["M01", "M03", "M05", "M06", "M08"]]


def test_first_n_with_end_credits_only_defaults_to_all_of_them(
    prompt: SimpleNamespace, batch: SimpleNamespace
) -> None:
    prompt.answers = [ENTER]

    menu._process_first_n(_ten_movies(), None, credits_only=True)

    assert prompt.asked[0][2] == 7  # default of "Number" = the 7 with End Credits
    assert batch.calls == [["M01", "M03", "M05", "M06", "M08", "M09", "M10"]]


def test_first_n_with_end_credits_only_but_none_has_them(
    prompt: SimpleNamespace, batch: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    movies = [_movie(f"M{i:02d}", False) for i in range(1, 4)]

    menu._process_first_n(movies, None, credits_only=True)

    assert prompt.asked == []
    assert batch.calls == []
    out = " ".join(capsys.readouterr().out.split())
    assert "None of the 3 movies needing Auto Chapters has Plex End Credits yet." in out
    assert 'Choose "for all movies" to process them now' in out
    assert "wait until Plex has detected their End Credits." in out


def test_first_n_with_end_credits_only_one_movie_left_without_them(
    prompt: SimpleNamespace, batch: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    menu._process_first_n([_movie("M01", False)], None, credits_only=True)

    assert batch.calls == []
    out = " ".join(capsys.readouterr().out.split())
    assert "The 1 movie needing Auto Chapters has no Plex End Credits yet." in out
    assert "to process it now" in out
    assert "detected its End Credits." in out


def test_first_n_all_movies_counts_from_the_full_list_and_says_what_it_means(
    prompt: SimpleNamespace, batch: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    prompt.answers = [5]

    menu._process_first_n(_ten_movies(), None)

    assert [text for text, _, _ in prompt.asked] == ["Number"]
    assert prompt.asked[0][2] == 10
    assert batch.calls == [["M01", "M02", "M03", "M04", "M05"]]
    out = capsys.readouterr().out
    assert "The movies without Plex End Credits get no End Credits chapter now." in out
    assert "run the Update menu yourself to add it" in out


def test_first_n_all_movies_with_end_credits_says_nothing_extra(
    prompt: SimpleNamespace, batch: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    movies = [_movie(f"M{i:02d}", True) for i in range(1, 4)]
    prompt.answers = [ENTER]

    menu._process_first_n(movies, None)

    assert batch.calls == [["M01", "M02", "M03"]]
    assert "End Credits chapter now" not in capsys.readouterr().out


def test_first_n_zero_at_the_number_prompt_cancels(
    prompt: SimpleNamespace, batch: SimpleNamespace
) -> None:
    prompt.answers = [0]

    menu._process_first_n(_ten_movies(), None, credits_only=True)

    assert batch.calls == []


def test_first_n_number_above_the_list_is_clamped(
    prompt: SimpleNamespace, batch: SimpleNamespace
) -> None:
    prompt.answers = [99]

    menu._process_first_n(_ten_movies(), None, credits_only=True)

    assert batch.calls == [["M01", "M03", "M05", "M06", "M08", "M09", "M10"]]


@pytest.mark.parametrize("exc", [KeyboardInterrupt, EOFError])
def test_first_n_interrupt_at_the_number_prompt_cancels(
    prompt: SimpleNamespace, batch: SimpleNamespace, exc: type[BaseException]
) -> None:
    prompt.answers = [exc()]

    menu._process_first_n(_ten_movies(), None, credits_only=True)

    assert batch.calls == []


def test_first_n_removes_processed_movies_and_keeps_the_rest(
    prompt: SimpleNamespace, batch: SimpleNamespace
) -> None:
    movies = _ten_movies()
    prompt.answers = [5]

    menu._process_first_n(movies, None, credits_only=True)

    # The five processed movies are gone; the credit-less ones (and the two
    # credited ones beyond the five) stay in the list for later.
    assert [mv.title for mv in movies] == ["M02", "M04", "M07", "M09", "M10"]


@pytest.mark.parametrize(
    ("choice", "expected"),
    [
        (1, ["M01", "M03", "M05", "M06", "M08", "M09", "M10"]),
        (2, ["M01", "M02", "M03", "M04", "M05", "M06", "M07", "M08", "M09", "M10"]),
    ],
)
def test_generate_offers_end_credits_only_as_its_first_choice(
    monkeypatch: pytest.MonkeyPatch,
    prompt: SimpleNamespace,
    batch: SimpleNamespace,
    capsys: pytest.CaptureFixture[str],
    choice: int,
    expected: list[str],
) -> None:
    """The choice used to hide in a question that came only after "alphabetical
    order" (and only when the list was mixed)."""
    orch = _scan_flow_orch([])
    orch.plex.iter_movie_candidates = lambda **_k: (_ten_movies(), ScanStats())
    monkeypatch.setattr(menu, "_check_folder_reachability", lambda _cfg: (True, []))
    prompt.answers = [choice, ENTER, 0]  # the choice, "how many" = all, then back

    menu._generate_auto_chapters_flow(orch, orch.config)

    out = capsys.readouterr().out
    assert "1) Generate Auto Chapters for movies with Plex End Credits" in out
    assert "2) Generate Auto Chapters for all movies" in out
    assert "3) Show the list and pick movies manually" in out
    assert batch.calls == [expected]


# --- _detect_and_confirm_write: the 's' listener vs. the "What now?" prompt --
#
# The 's' key listener puts the terminal in cbreak mode (no echo) and eats
# every key it does not know. While the "What now?" prompt waits for a number
# the listener therefore has to be OFF, otherwise the typed answer never
# reaches the prompt ("Please enter a valid integer number"). It is switched
# on again for the write, and a shutdown the user scheduled earlier is kept.


class _RecordingToggle:
    """Stand-in for ShutdownToggle that remembers when it was listening."""

    instances: ClassVar[list["_RecordingToggle"]] = []
    initially_scheduled = False

    def __init__(self) -> None:
        self.scheduled = self.initially_scheduled
        self.listening = False
        self.events: list[str] = []
        _RecordingToggle.instances.append(self)

    def start(self, _on_toggle: object) -> bool:
        self.listening = True
        self.events.append("start")
        return True

    def stop(self) -> None:
        self.listening = False
        self.events.append("stop")


@pytest.fixture
def confirm_flow(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Everything around _detect_and_confirm_write replaced by stand-ins.

    `flow.answers` is what the user types at the prompt; `flow.at_prompt`
    records whether the listener was running when the prompt was shown,
    `flow.at_detect` / `flow.at_write` the same for detection and writing.
    """
    _RecordingToggle.instances = []
    state = SimpleNamespace(
        answers=[1],
        at_prompt=[],
        at_detect=[],
        at_write=[],
        awake=False,
        awake_at_detect=[],
        awake_at_prompt=[],
        awake_at_write=[],
        awake_entries=0,
        shutdowns=0,
        scheduled_before_write=False,
        written=False,
    )
    movie = _movie("Film", True)

    def toggle() -> _RecordingToggle:
        return _RecordingToggle.instances[0]

    state.toggle = toggle
    state.movie = movie

    def fake_ask(text: str, **_kwargs: object) -> int:
        state.at_prompt.append(toggle().listening)
        state.awake_at_prompt.append(state.awake)
        answer = state.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def detect_movie(mv: Movie) -> DetectionResult:
        state.at_detect.append(toggle().listening)
        state.awake_at_detect.append(state.awake)
        chapters = [
            Chapter(name="Auto Chapter 1", timestamp=0.0, method=DetectionMethod.START),
            Chapter(name="Credits", timestamp=6000.0, method=DetectionMethod.CREDITS),
        ]
        return DetectionResult(movie=mv, chapters=chapters, elapsed_seconds=1.0)

    def write_movie(detection: DetectionResult) -> ProcessingResult:
        state.at_write.append(toggle().listening)
        state.awake_at_write.append(state.awake)
        toggle().scheduled = state.scheduled_before_write or toggle().scheduled
        state.written = True
        return ProcessingResult(
            movie=detection.movie, chapters=detection.chapters, written=True
        )

    orch = SimpleNamespace(
        detect_movie=detect_movie,
        write_movie=write_movie,
        plex=SimpleNamespace(trigger_analyze=lambda _mv: True),
    )
    state.orch = orch

    def fake_shutdown() -> None:
        state.shutdowns += 1

    @contextmanager
    def fake_keep_awake() -> Iterator[bool]:
        state.awake = True
        state.awake_entries += 1
        try:
            yield True
        finally:
            state.awake = False

    monkeypatch.setattr(menu, "keep_awake", fake_keep_awake)
    monkeypatch.setattr(menu, "ShutdownToggle", _RecordingToggle)
    monkeypatch.setattr(menu, "IntPrompt", SimpleNamespace(ask=fake_ask))
    monkeypatch.setattr(menu, "get_duration", lambda _path: None)
    monkeypatch.setattr(menu, "render_chapter_table", lambda *_a, **_k: None)
    monkeypatch.setattr(menu, "execute_shutdown_with_countdown", fake_shutdown)
    return state


def test_confirm_prompt_is_asked_while_the_listener_is_off(
    confirm_flow: SimpleNamespace,
) -> None:
    result = menu._detect_and_confirm_write(confirm_flow.movie, confirm_flow.orch)

    assert result == [confirm_flow.movie]
    assert confirm_flow.at_detect == [True]  # 's' works during detection
    assert confirm_flow.at_prompt == [False]  # ... but not while the prompt waits
    assert confirm_flow.at_write == [True]  # ... and works again during the write
    assert confirm_flow.toggle().listening is False  # stopped at the end


def test_confirm_cancel_writes_nothing_and_leaves_the_listener_off(
    confirm_flow: SimpleNamespace,
) -> None:
    confirm_flow.answers = [0]

    result = menu._detect_and_confirm_write(confirm_flow.movie, confirm_flow.orch)

    assert result == []
    assert confirm_flow.written is False
    assert confirm_flow.at_write == []
    assert confirm_flow.toggle().events == ["start", "stop", "stop"]


def test_confirm_interrupt_at_the_prompt_cancels_cleanly(
    confirm_flow: SimpleNamespace,
) -> None:
    confirm_flow.answers = [KeyboardInterrupt()]

    result = menu._detect_and_confirm_write(confirm_flow.movie, confirm_flow.orch)

    assert result == []
    assert confirm_flow.written is False
    assert confirm_flow.toggle().listening is False


def test_confirm_scheduled_shutdown_still_fires_after_the_write(
    confirm_flow: SimpleNamespace,
) -> None:
    confirm_flow.scheduled_before_write = True

    menu._detect_and_confirm_write(confirm_flow.movie, confirm_flow.orch)

    assert confirm_flow.shutdowns == 1


def test_confirm_scheduled_shutdown_is_dropped_when_nothing_was_written(
    confirm_flow: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The user pressed 's' during detection, then cancelled at the prompt.
    monkeypatch.setattr(_RecordingToggle, "initially_scheduled", True)
    confirm_flow.answers = [0]

    menu._detect_and_confirm_write(confirm_flow.movie, confirm_flow.orch)

    assert confirm_flow.written is False
    assert confirm_flow.shutdowns == 0


# The computer must not go to sleep because it was idle while detection or a
# write runs -- but it may while the "What now?" prompt waits for the user
# (someone who walks away at that prompt must not keep the machine awake all
# night).


def test_confirm_machine_is_kept_awake_for_detection_and_write_but_not_at_the_prompt(
    confirm_flow: SimpleNamespace,
) -> None:
    menu._detect_and_confirm_write(confirm_flow.movie, confirm_flow.orch)

    assert confirm_flow.awake_at_detect == [True]
    assert confirm_flow.awake_at_prompt == [False]
    assert confirm_flow.awake_at_write == [True]
    assert confirm_flow.awake is False  # released at the end


def test_confirm_cancel_at_the_prompt_holds_the_lock_only_for_detection(
    confirm_flow: SimpleNamespace,
) -> None:
    confirm_flow.answers = [0]

    menu._detect_and_confirm_write(confirm_flow.movie, confirm_flow.orch)

    assert confirm_flow.awake_entries == 1
    assert confirm_flow.awake is False


def test_batch_holds_the_lock_while_the_movies_are_processed_and_releases_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    held: list[bool] = []
    state = SimpleNamespace(awake=False)

    @contextmanager
    def fake_keep_awake() -> Iterator[bool]:
        state.awake = True
        try:
            yield True
        finally:
            state.awake = False

    def fake_process_one(mv: Movie, _orch: object) -> ProcessingResult:
        held.append(state.awake)
        return ProcessingResult(movie=mv, chapters=[], written=True)

    _RecordingToggle.instances = []
    monkeypatch.setattr(menu, "keep_awake", fake_keep_awake)
    monkeypatch.setattr(menu, "ShutdownToggle", _RecordingToggle)
    monkeypatch.setattr(menu, "_process_one", fake_process_one)
    movies = [_movie("A", True), _movie("B", True)]

    done = menu._run_batch(movies, SimpleNamespace())

    assert done == movies
    assert held == [True, True]  # held during every movie
    assert state.awake is False  # released afterwards


# --- Find by title: matches that are left out --------------------------------
#
# A title Plex knows but the app skips used to end in "No movie found", which
# sent the user hunting for a typo. Now each skipped match is listed with its
# reason, and "No movie found" is kept for a title that matches nothing at all.


@pytest.fixture
def find_flow(monkeypatch: pytest.MonkeyPatch, prompt: SimpleNamespace) -> SimpleNamespace:
    """_find_by_title with the Plex search and the detection step replaced.

    Set `flow.found` (movies) and `flow.skipped` before calling. The search
    asks for one title ("Heat"), then the user cancels at the next title prompt.
    """
    state = SimpleNamespace(
        found=[],
        skipped=[],
        ignored=[],
        titles=["Heat", ""],
        processed=[],
        asked=[],
        writes=True,
    )

    def fake_scan(**_kwargs: object) -> tuple[list[Movie], ScanStats]:
        return list(state.found), ScanStats(
            skipped=list(state.skipped), ignored_mappings=list(state.ignored)
        )

    state.orch = SimpleNamespace(
        config=SimpleNamespace(
            paths=[SimpleNamespace(enabled=True)],
            detection=SimpleNamespace(chapter_threshold=4, min_duration_seconds=0),
        ),
        plex=SimpleNamespace(iter_movie_candidates=fake_scan),
    )
    monkeypatch.setattr(menu, "_check_folder_reachability", lambda _cfg: (True, []))

    def fake_ask_text(text: str, **_kwargs: object) -> str:
        state.asked.append(text)
        return state.titles.pop(0)

    monkeypatch.setattr(menu, "_ask_text", fake_ask_text)

    def fake_detect_and_confirm(mv: Movie, _orch: object) -> list[Movie]:
        state.processed.append(mv)
        return [mv] if state.writes else []

    monkeypatch.setattr(menu, "_detect_and_confirm_write", fake_detect_and_confirm)
    return state


def test_find_by_title_intro_does_not_name_a_chapter_number(find_flow: SimpleNamespace) -> None:
    """The cutoff is a setting; a fixed number here would be wrong as soon as
    the user changes it. The intro points to the setting instead."""
    find_flow.titles = [""]

    menu._find_by_title(find_flow.orch)

    intro = " ".join(find_flow.asked[0].split())
    assert "Settings > Chapter threshold" in intro
    assert not any(ch.isdigit() for ch in intro)


def test_find_by_title_nothing_matching_says_no_movie_found(
    find_flow: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    menu._find_by_title(find_flow.orch)

    out = capsys.readouterr().out
    assert "No movie found matching 'Heat'" in out
    assert "cannot be processed" not in out


def test_find_by_title_only_skipped_matches_are_listed_with_their_reason(
    find_flow: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    find_flow.skipped = [
        SkippedMatch("Heat", 1995, SkipReason.OTHER_CHAPTERS, chapter_count=12),
        SkippedMatch("Heat 2", None, SkipReason.NOT_MKV),
    ]

    menu._find_by_title(find_flow.orch)

    out = capsys.readouterr().out
    assert "Found 2 movies matching 'Heat', but none can be processed" in out
    assert "Heat (1995) -- already has 12 chapters from another source" in out
    assert "Heat 2 -- not an MKV file" in out
    assert "No movie found" not in out
    assert find_flow.processed == []


def test_find_by_title_chapter_note_points_to_the_setting_not_a_number(
    find_flow: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    find_flow.skipped = [SkippedMatch("Heat", 1995, SkipReason.OTHER_CHAPTERS, chapter_count=12)]

    menu._find_by_title(find_flow.orch)

    out = capsys.readouterr().out
    assert "Settings > Chapter threshold" in out
    assert "threshold is 4" not in out


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        (SkipReason.NOT_MKV, "not an MKV file"),
        (SkipReason.FOLDER_UNREACHABLE, "its folder is not reachable right now"),
        (SkipReason.FILE_MISSING, "the file was not found on this machine"),
        (SkipReason.TOO_SHORT, "shorter than the Minimum movie length in Settings"),
    ],
)
def test_find_by_title_each_skip_reason_has_its_own_wording(
    find_flow: SimpleNamespace,
    capsys: pytest.CaptureFixture[str],
    reason: SkipReason,
    expected: str,
) -> None:
    find_flow.skipped = [SkippedMatch("Heat", 1995, reason)]

    menu._find_by_title(find_flow.orch)

    out = capsys.readouterr().out
    assert f"Heat (1995) -- {expected}" in out
    assert "Settings > Chapter threshold" not in out  # only for the chapter reason


def test_find_by_title_skipped_matches_are_shown_next_to_a_processable_one(
    find_flow: SimpleNamespace,
    prompt: SimpleNamespace,
    capsys: pytest.CaptureFixture[str],
) -> None:
    find_flow.found = [_movie("Heat Wave", True, year=2001)]
    find_flow.skipped = [SkippedMatch("Heat", 1995, SkipReason.OTHER_CHAPTERS, chapter_count=12)]
    prompt.answers = [1]  # "Yes, this is the movie I meant"

    menu._find_by_title(find_flow.orch)

    out = capsys.readouterr().out
    assert "Also found, but cannot be processed" in out
    assert "Heat (1995) -- already has 12 chapters from another source" in out
    assert [mv.title for mv in find_flow.processed] == ["Heat Wave"]


def test_find_by_title_without_skipped_matches_prints_no_extra_block(
    find_flow: SimpleNamespace,
    prompt: SimpleNamespace,
    capsys: pytest.CaptureFixture[str],
) -> None:
    find_flow.found = [_movie("Heat", True, year=1995)]
    prompt.answers = [1]

    menu._find_by_title(find_flow.orch)

    assert "cannot be processed" not in capsys.readouterr().out


def test_find_by_title_title_with_brackets_is_printed_literally(
    find_flow: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    """Rich would swallow an unescaped "[rec]" as a style tag."""
    find_flow.skipped = [SkippedMatch("[rec]", 2007, SkipReason.NOT_MKV)]

    menu._find_by_title(find_flow.orch)

    assert "[rec] (2007) -- not an MKV file" in capsys.readouterr().out


# --- Find by title: where cancel leads --------------------------------------
#
# Find by title is a chain of steps. Cancel (0) at the title prompt leaves for
# the main menu; at any later step it goes one step back, to the title prompt.


def test_find_by_title_cancel_at_the_title_prompt_leaves_for_the_main_menu(
    find_flow: SimpleNamespace,
) -> None:
    find_flow.titles = [""]  # 0 / Ctrl+C at the title prompt

    menu._find_by_title(find_flow.orch)

    assert len(find_flow.asked) == 1


def test_find_by_title_cancel_at_the_pick_list_goes_back_to_the_title_prompt(
    find_flow: SimpleNamespace, prompt: SimpleNamespace
) -> None:
    find_flow.found = [_movie("Heat", True, year=1995), _movie("Heat Wave", True, year=2001)]
    prompt.answers = [0]  # Cancel at "Found 2 matches"

    menu._find_by_title(find_flow.orch)

    assert len(find_flow.asked) == 2  # asked again, then the user left with 0
    assert find_flow.processed == []


def test_find_by_title_cancel_at_the_single_match_confirmation_goes_back(
    find_flow: SimpleNamespace, prompt: SimpleNamespace
) -> None:
    find_flow.found = [_movie("Heat", True, year=1995)]
    prompt.answers = [0]  # Cancel at "Found 1 match"

    menu._find_by_title(find_flow.orch)

    assert len(find_flow.asked) == 2
    assert find_flow.processed == []


def test_find_by_title_cancel_at_the_regenerate_question_goes_back(
    find_flow: SimpleNamespace, prompt: SimpleNamespace
) -> None:
    mv = _movie("Heat", True, year=1995)
    mv.has_auto_chapters = True
    find_flow.found = [mv]
    prompt.answers = [0]  # Cancel at "already has Auto Chapters"

    menu._find_by_title(find_flow.orch)

    assert len(find_flow.asked) == 2
    assert find_flow.processed == []


def test_find_by_title_nothing_written_goes_back_to_the_title_prompt(
    find_flow: SimpleNamespace, prompt: SimpleNamespace
) -> None:
    """Cancelled at "What now?", a failed detection or a stop: no file was
    touched, so the user can try another title right away."""
    find_flow.found = [_movie("Heat", True, year=1995)]
    find_flow.writes = False
    prompt.answers = [1]

    menu._find_by_title(find_flow.orch)

    assert [mv.title for mv in find_flow.processed] == ["Heat"]
    assert len(find_flow.asked) == 2


def test_find_by_title_after_a_write_it_asks_for_the_next_title(
    find_flow: SimpleNamespace, prompt: SimpleNamespace
) -> None:
    """Like Generate and Update, which stay on their own screen after work:
    whoever searches by title often wants the next movie. It used to leave
    for the main menu."""
    find_flow.found = [_movie("Heat", True, year=1995)]
    prompt.answers = [1]

    menu._find_by_title(find_flow.orch)

    assert len(find_flow.asked) == 2  # the title prompt again ("" there leaves)
    assert [mv.title for mv in find_flow.processed] == ["Heat"]


# --- Settings > Folder mappings: seeing and changing the path -------------
#
# Plex may run on another machine, so a mapping pairs "the folder as Plex sees
# it" with "the same folder as THIS computer sees it". Changing the second
# used to mean removing the mapping and adding it again.


@pytest.fixture
def mapping_flow(monkeypatch: pytest.MonkeyPatch, prompt: SimpleNamespace) -> SimpleNamespace:
    """Folder mappings with the file system, config file and y/n question replaced.

    `flow.typed` is what the user types at the path question, `flow.yn` the
    answers to "Use it anyway?" / "Remove ...?", `flow.reachable` what the
    reachability check finds (True / False / None = timed out), `flow.matches`
    what comparing a reachable folder with Plex's movies finds, one per
    comparison (FolderMatch, None = couldn't tell, or an exception to raise;
    none left = None). `flow.compared` records (plex path, local path).
    """
    state = SimpleNamespace(
        config=SimpleNamespace(
            paths=[
                PathMapping(plex="/plex/Filmek/", local="/mnt/old/Filmek/", enabled=True),
                PathMapping(plex="/plex/Sorozat/", local="/mnt/old/Sorozat/", enabled=False),
            ]
        ),
        typed=[],
        yn=[],
        asked_text=[],
        saved=[],
        reachable=True,
        plex_folders=["/plex/Filmek/", "/plex/Sorozat/", "/plex/Uj/"],
        plex_calls=0,
        matches=[],
        compared=[],
    )

    def list_movie_folders() -> list[str]:
        state.plex_calls += 1
        return list(state.plex_folders)

    def fake_ask_text(text: str, **_kwargs: object) -> str:
        state.asked_text.append(text)
        return state.typed.pop(0)

    class FakeMapper:
        def __init__(self, paths: list[PathMapping]) -> None:
            self._paths = paths

        def check_enabled_folders(self) -> list[SimpleNamespace]:
            return [
                SimpleNamespace(local=m.local, reachable=True) for m in self._paths if m.enabled
            ]

    monkeypatch.setattr(menu, "_ask_text", fake_ask_text)
    monkeypatch.setattr(menu, "Prompt", SimpleNamespace(ask=lambda *_a, **_k: state.yn.pop(0)))
    monkeypatch.setattr(menu, "check_dir_with_timeout", lambda _path: state.reachable)
    monkeypatch.setattr(menu, "PathMapper", FakeMapper)
    monkeypatch.setattr(
        menu,
        "_save_config_to_disk",
        lambda cfg, *_a, **_k: state.saved.append([(m.plex, m.local, m.enabled) for m in cfg.paths]),
    )
    def folder_match(plex_folder: str, local_path: str) -> FolderMatch | None:
        state.compared.append((plex_folder, local_path))
        match = state.matches.pop(0) if state.matches else None
        if isinstance(match, Exception):
            raise match
        return match

    state.orch = SimpleNamespace(
        plex=SimpleNamespace(list_movie_folders=list_movie_folders, folder_match=folder_match)
    )
    return state


def test_mapping_list_shows_both_paths_and_the_four_actions(
    mapping_flow: SimpleNamespace,
    prompt: SimpleNamespace,
    capsys: pytest.CaptureFixture[str],
) -> None:
    prompt.answers = [0]

    menu._folder_mappings(mapping_flow.config, mapping_flow.orch)

    out = capsys.readouterr().out
    assert "/mnt/old/Filmek/  ->  /plex/Filmek/" in out
    assert "/mnt/old/Sorozat/  ->  /plex/Sorozat/" in out
    assert "1) Add folder mapping" in out
    assert "2) Change a mapping's local or mounted path" in out
    assert "3) Enable or disable a mapping" in out
    assert "4) Remove folder mapping" in out
    # The legend names the left side (this computer) first, as the lines do.
    assert out.index("on this computer (local or mounted)") < out.index("path in Plex")


def test_mapping_list_without_mappings_offers_only_add(
    mapping_flow: SimpleNamespace,
    prompt: SimpleNamespace,
    capsys: pytest.CaptureFixture[str],
) -> None:
    mapping_flow.config.paths = []
    prompt.answers = [0]

    menu._folder_mappings(mapping_flow.config, mapping_flow.orch)

    out = capsys.readouterr().out
    assert "1) Add folder mapping" in out
    assert "(no folders mapped yet)" in out
    assert "Change" not in out
    assert "Enable or disable" not in out
    assert "Remove" not in out


def test_change_through_the_list_uses_the_right_menu_number(
    mapping_flow: SimpleNamespace, prompt: SimpleNamespace
) -> None:
    """1 = Add, 2 = Change, 3 = Enable/disable, 4 = Remove -- whatever the mapping count."""
    prompt.answers = [2, 1, 0]  # Change -> first mapping -> back out of the list
    mapping_flow.typed = ["/mnt/new/Filmek"]

    menu._folder_mappings(mapping_flow.config, mapping_flow.orch)

    assert mapping_flow.config.paths[0].local == "/mnt/new/Filmek/"
    assert mapping_flow.config.paths[1].local == "/mnt/old/Sorozat/"


def test_remove_through_the_list_uses_the_right_menu_number(
    mapping_flow: SimpleNamespace, prompt: SimpleNamespace
) -> None:
    prompt.answers = [4, 1, 0]  # Remove -> first mapping -> back
    mapping_flow.yn = ["y"]

    menu._folder_mappings(mapping_flow.config, mapping_flow.orch)

    assert [m.plex for m in mapping_flow.config.paths] == ["/plex/Sorozat/"]


def test_change_keeps_the_active_or_disabled_state(
    mapping_flow: SimpleNamespace, prompt: SimpleNamespace
) -> None:
    prompt.answers = [2]  # the disabled one
    mapping_flow.typed = ["/mnt/new/Sorozat/"]

    menu._change_folder_mapping(mapping_flow.config, mapping_flow.orch)

    changed = mapping_flow.config.paths[1]
    assert changed.local == "/mnt/new/Sorozat/"
    assert changed.enabled is False
    assert mapping_flow.config.paths[0].local == "/mnt/old/Filmek/"  # the other one untouched


def test_change_saves_the_config_once_and_reports_old_and_new_path(
    mapping_flow: SimpleNamespace,
    prompt: SimpleNamespace,
    capsys: pytest.CaptureFixture[str],
) -> None:
    prompt.answers = [1]
    mapping_flow.typed = ["/mnt/new/Filmek/"]

    menu._change_folder_mapping(mapping_flow.config, mapping_flow.orch)

    assert len(mapping_flow.saved) == 1
    assert ("/plex/Filmek/", "/mnt/new/Filmek/", True) in mapping_flow.saved[0]
    out = capsys.readouterr().out
    assert "Mapping changed: /mnt/new/Filmek/ -> /plex/Filmek/" in out
    assert "was /mnt/old/Filmek/" in out


def test_change_expands_the_home_folder_and_adds_the_trailing_slash(
    mapping_flow: SimpleNamespace, prompt: SimpleNamespace
) -> None:
    prompt.answers = [1]
    mapping_flow.typed = ["~/Filmek"]

    menu._change_folder_mapping(mapping_flow.config, mapping_flow.orch)

    assert mapping_flow.config.paths[0].local == str(Path.home()) + "/Filmek/"


def test_change_rejects_a_relative_path_and_asks_again(
    mapping_flow: SimpleNamespace,
    prompt: SimpleNamespace,
    capsys: pytest.CaptureFixture[str],
) -> None:
    prompt.answers = [1]
    mapping_flow.typed = ["media/Filmek", "/mnt/new/Filmek/"]

    menu._change_folder_mapping(mapping_flow.config, mapping_flow.orch)

    assert "doesn't look like a folder path" in capsys.readouterr().out
    assert mapping_flow.config.paths[0].local == "/mnt/new/Filmek/"


def test_change_cancelled_at_the_pick_list_changes_nothing(
    mapping_flow: SimpleNamespace, prompt: SimpleNamespace
) -> None:
    prompt.answers = [0]

    menu._change_folder_mapping(mapping_flow.config, mapping_flow.orch)

    assert mapping_flow.config.paths[0].local == "/mnt/old/Filmek/"
    assert mapping_flow.saved == []


def test_change_cancelled_at_the_path_question_changes_nothing(
    mapping_flow: SimpleNamespace, prompt: SimpleNamespace
) -> None:
    prompt.answers = [1]
    mapping_flow.typed = [""]  # 0 / Ctrl+C at the path question

    menu._change_folder_mapping(mapping_flow.config, mapping_flow.orch)

    assert mapping_flow.config.paths[0].local == "/mnt/old/Filmek/"
    assert mapping_flow.saved == []


def test_change_to_the_same_path_says_so_and_saves_nothing(
    mapping_flow: SimpleNamespace,
    prompt: SimpleNamespace,
    capsys: pytest.CaptureFixture[str],
) -> None:
    prompt.answers = [1]
    mapping_flow.typed = ["/mnt/old/Filmek"]  # same as now, once the slash is added

    menu._change_folder_mapping(mapping_flow.config, mapping_flow.orch)

    assert "Nothing changed" in capsys.readouterr().out
    assert mapping_flow.saved == []


@pytest.mark.parametrize("reachable", [False, None])
def test_change_to_an_unreachable_path_is_kept_only_if_the_user_confirms(
    mapping_flow: SimpleNamespace, prompt: SimpleNamespace, reachable: bool | None
) -> None:
    """The NAS may simply not be mounted yet, so "yes" is allowed."""
    mapping_flow.reachable = reachable
    prompt.answers = [1]
    mapping_flow.typed = ["/mnt/not-mounted/Filmek"]
    mapping_flow.yn = ["n"]

    menu._change_folder_mapping(mapping_flow.config, mapping_flow.orch)

    assert mapping_flow.config.paths[0].local == "/mnt/old/Filmek/"

    prompt.answers = [1]
    mapping_flow.typed = ["/mnt/not-mounted/Filmek"]
    mapping_flow.yn = ["y"]

    menu._change_folder_mapping(mapping_flow.config, mapping_flow.orch)

    assert mapping_flow.config.paths[0].local == "/mnt/not-mounted/Filmek/"


def test_path_question_names_both_cases_and_never_says_remote(
    mapping_flow: SimpleNamespace, prompt: SimpleNamespace
) -> None:
    """Plex may run on another machine: the folder is on this computer's own
    disk ("local") or on a NAS that is mounted here ("mounted")."""
    prompt.answers = [1]
    mapping_flow.typed = ["/mnt/new/Filmek/"]

    menu._change_folder_mapping(mapping_flow.config, mapping_flow.orch)

    question = mapping_flow.asked_text[0]
    assert question == "Local or mounted path to this folder on this computer"
    assert "remote" not in question.lower()


def test_add_mapping_uses_the_same_path_question(
    mapping_flow: SimpleNamespace, prompt: SimpleNamespace
) -> None:
    prompt.answers = [1]  # the only unmapped Plex folder
    mapping_flow.typed = ["/mnt/new/Uj"]

    menu._add_folder_mapping(mapping_flow.config, mapping_flow.orch)

    assert mapping_flow.asked_text == ["Local or mounted path to this folder on this computer"]
    assert mapping_flow.config.paths[-1].plex == "/plex/Uj/"
    assert mapping_flow.config.paths[-1].local == "/mnt/new/Uj/"
    assert mapping_flow.config.paths[-1].enabled is True


# --- Settings > Folder mappings: fixed action numbers, numbered pick lists ---
#
# The mappings on the screen are not numbered; the action numbers do not move
# with the mapping count. Change / Enable-disable / Remove each open a pick
# list that shows both paths.


def test_mappings_on_the_screen_are_not_numbered(
    mapping_flow: SimpleNamespace,
    prompt: SimpleNamespace,
    capsys: pytest.CaptureFixture[str],
) -> None:
    out = _screen_output(mapping_flow, prompt, capsys)

    assert "1) /mnt/old/Filmek/" not in out
    assert "2) /mnt/old/Sorozat/" not in out
    assert "Pick a mapped folder's number" not in out


@pytest.mark.parametrize("count", [1, 2, 3])
def test_action_numbers_do_not_depend_on_the_mapping_count(
    mapping_flow: SimpleNamespace,
    prompt: SimpleNamespace,
    capsys: pytest.CaptureFixture[str],
    count: int,
) -> None:
    extra = [PathMapping(plex="/plex/X3/", local="/mnt/x3/", enabled=True)]
    mapping_flow.config.paths = (mapping_flow.config.paths + extra)[:count]

    out = _screen_output(mapping_flow, prompt, capsys)

    assert "1) Add folder mapping" in out
    assert "2) Change a mapping's local or mounted path" in out
    assert "3) Enable or disable a mapping" in out
    assert "4) Remove folder mapping" in out


def test_only_back_and_add_are_valid_without_mappings(
    mapping_flow: SimpleNamespace, prompt: SimpleNamespace
) -> None:
    mapping_flow.config.paths = []
    prompt.answers = [0]

    menu._folder_mappings(mapping_flow.config, mapping_flow.orch)

    assert prompt.choices == [["0", "1"]]


def test_all_four_actions_are_valid_with_mappings(
    mapping_flow: SimpleNamespace, prompt: SimpleNamespace
) -> None:
    prompt.answers = [0]

    menu._folder_mappings(mapping_flow.config, mapping_flow.orch)

    assert prompt.choices == [["0", "1", "2", "3", "4"]]


def test_toggle_switches_a_mapping_off_and_on_and_saves_each_time(
    mapping_flow: SimpleNamespace,
    prompt: SimpleNamespace,
    capsys: pytest.CaptureFixture[str],
) -> None:
    prompt.answers = [3, 1, 1, 0, 0]  # 3, switch 1 twice in the list, back, back

    menu._folder_mappings(mapping_flow.config, mapping_flow.orch)

    assert [s[0][2] for s in mapping_flow.saved] == [False, True]  # first mapping, each save
    assert mapping_flow.config.paths[0].enabled is True  # toggled twice
    out = capsys.readouterr().out
    assert "/mnt/old/Filmek/ -> /plex/Filmek/ disabled." in out
    assert "/mnt/old/Filmek/ -> /plex/Filmek/ enabled." in out


def test_toggle_enables_a_disabled_mapping(
    mapping_flow: SimpleNamespace, prompt: SimpleNamespace
) -> None:
    prompt.answers = [2, 0]  # the disabled one, then back

    menu._toggle_folder_mapping(mapping_flow.config)

    assert mapping_flow.config.paths[1].enabled is True
    assert mapping_flow.config.paths[0].enabled is True  # untouched
    assert len(mapping_flow.saved) == 1


def test_toggle_back_changes_and_saves_nothing(
    mapping_flow: SimpleNamespace, prompt: SimpleNamespace
) -> None:
    prompt.answers = [0]

    menu._toggle_folder_mapping(mapping_flow.config)

    assert [m.enabled for m in mapping_flow.config.paths] == [True, False]
    assert mapping_flow.saved == []


@pytest.mark.parametrize("action", ["change", "toggle", "remove"])
def test_pick_lists_show_both_paths_and_the_state(
    mapping_flow: SimpleNamespace,
    prompt: SimpleNamespace,
    capsys: pytest.CaptureFixture[str],
    action: str,
) -> None:
    prompt.answers = [0]
    fn = {
        "change": lambda cfg: menu._change_folder_mapping(cfg, mapping_flow.orch),
        "toggle": menu._toggle_folder_mapping,
        "remove": menu._remove_folder_mapping,
    }[action]

    fn(mapping_flow.config)

    out = capsys.readouterr().out
    assert "0) Back" in out
    assert "1) /mnt/old/Filmek/  ->  /plex/Filmek/  [active]" in out
    assert "2) /mnt/old/Sorozat/  ->  /plex/Sorozat/  [disabled]" in out


def test_remove_asks_with_both_paths_and_keeps_the_mapping_on_n(
    mapping_flow: SimpleNamespace, prompt: SimpleNamespace
) -> None:
    prompt.answers = [1]
    mapping_flow.yn = ["n"]

    menu._remove_folder_mapping(mapping_flow.config)

    assert len(mapping_flow.config.paths) == 2
    assert mapping_flow.saved == []


# --- Settings > Folder mappings: Plex folders that have no mapping yet ------
#
# A folder skipped (or never given a path) at setup is not in the config, so
# it is not in the list either. The screen tells the user that such folders
# exist, asking Plex once when it opens.


def test_unmapped_folder_is_reported_with_the_add_number(
    mapping_flow: SimpleNamespace,
    prompt: SimpleNamespace,
    capsys: pytest.CaptureFixture[str],
) -> None:
    prompt.answers = [0]

    menu._folder_mappings(mapping_flow.config, mapping_flow.orch)

    out = capsys.readouterr().out
    assert "1 Plex folder has no mapping yet." in out
    assert "Movies in a folder without a mapping are skipped." in out
    assert "Choose 1 to map it." in out  # Add is always number 1


def test_several_unmapped_folders_and_no_mapping_at_all(
    mapping_flow: SimpleNamespace,
    prompt: SimpleNamespace,
    capsys: pytest.CaptureFixture[str],
) -> None:
    mapping_flow.config.paths = []
    prompt.answers = [0]

    menu._folder_mappings(mapping_flow.config, mapping_flow.orch)

    out = capsys.readouterr().out
    assert "3 Plex folders have no mapping yet." in out
    assert "Choose 1 to map them." in out  # Add is always number 1


def test_no_line_when_every_plex_folder_is_mapped_even_a_disabled_one(
    mapping_flow: SimpleNamespace,
    prompt: SimpleNamespace,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """"Sorozat" is mapped but disabled: the list shows [disabled] for it, the
    unmapped line is only about folders with no mapping at all."""
    mapping_flow.plex_folders = ["/plex/Filmek/", "/plex/Sorozat/"]
    prompt.answers = [0]

    menu._folder_mappings(mapping_flow.config, mapping_flow.orch)

    out = capsys.readouterr().out
    assert "no mapping yet" not in out
    assert "disabled" in out


def test_when_plex_cannot_be_asked_the_user_is_told_and_the_list_still_shows(
    mapping_flow: SimpleNamespace,
    prompt: SimpleNamespace,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Silence would look like "everything is mapped"."""

    def broken() -> list[str]:
        raise RuntimeError("Plex is not answering")

    mapping_flow.orch.plex.list_movie_folders = broken
    prompt.answers = [0]

    menu._folder_mappings(mapping_flow.config, mapping_flow.orch)

    out = capsys.readouterr().out
    assert "Couldn't ask Plex for its folders" in out
    assert "Plex is not answering" in out
    assert "/mnt/old/Filmek/  ->  /plex/Filmek/" in out
    assert "no mapping yet" not in out


def test_plex_is_asked_once_when_the_screen_opens_not_on_every_render(
    mapping_flow: SimpleNamespace, prompt: SimpleNamespace
) -> None:
    prompt.answers = [3, 1, 0, 3, 1, 0, 0]  # toggle, back to the screen, toggle, back, back

    menu._folder_mappings(mapping_flow.config, mapping_flow.orch)

    assert mapping_flow.plex_calls == 1


def test_the_line_goes_away_once_the_folder_is_added(
    mapping_flow: SimpleNamespace,
    prompt: SimpleNamespace,
    capsys: pytest.CaptureFixture[str],
) -> None:
    prompt.answers = [1, 1, 0]  # Add -> the only unmapped folder -> back
    mapping_flow.typed = ["/mnt/new/Uj"]

    menu._folder_mappings(mapping_flow.config, mapping_flow.orch)

    out = capsys.readouterr().out
    assert out.count("no mapping yet") == 1  # shown on the first render only
    assert mapping_flow.plex_calls == 2  # opening the screen + the Add flow itself


def test_the_line_comes_back_when_a_mapping_is_removed(
    mapping_flow: SimpleNamespace,
    prompt: SimpleNamespace,
    capsys: pytest.CaptureFixture[str],
) -> None:
    prompt.answers = [4, 1, 0]  # Remove -> the first mapping -> back
    mapping_flow.yn = ["y"]

    menu._folder_mappings(mapping_flow.config, mapping_flow.orch)

    out = capsys.readouterr().out
    assert "1 Plex folder has no mapping yet" in out  # /plex/Uj/, before the removal
    assert "2 Plex folders have no mapping yet" in out  # + /plex/Filmek/, after it


# --- Settings > Folder mappings: a mapping Plex does not know ---------------
#
# The rule is exact, like the scans': a mapping whose Plex path is not one of
# Plex's movie folders gets "[not found in Plex]" because the scans do not use
# it. That is a renamed/removed Plex folder or a hand-edited path (a parent or
# sub path of the real folder included), and the real folder then counts as
# "no mapping yet".


def _screen_output(
    mapping_flow: SimpleNamespace, prompt: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> str:
    prompt.answers = [0]
    menu._folder_mappings(mapping_flow.config, mapping_flow.orch)
    return capsys.readouterr().out


def test_mapping_found_in_plex_gets_no_tag(
    mapping_flow: SimpleNamespace,
    prompt: SimpleNamespace,
    capsys: pytest.CaptureFixture[str],
) -> None:
    out = _screen_output(mapping_flow, prompt, capsys)

    assert "not found in Plex" not in out


def test_renamed_plex_folder_is_tagged_and_the_new_one_is_offered(
    mapping_flow: SimpleNamespace,
    prompt: SimpleNamespace,
    capsys: pytest.CaptureFixture[str],
) -> None:
    mapping_flow.config.paths[0].plex = "/plex/Movies/"  # Plex now calls it "Filmek"

    out = _screen_output(mapping_flow, prompt, capsys)

    assert "/mnt/old/Filmek/  ->  /plex/Movies/  [active]  [not found in Plex]" in out
    sorozat = next(line for line in out.splitlines() if "/plex/Sorozat/" in line)
    assert "not found in Plex" not in sorozat  # the other mapping is fine
    assert "this mapping is not used" in out
    assert "remove this mapping and add the new folder" in " ".join(out.split())
    assert "1 Plex folder has no mapping yet." not in out  # Filmek and Uj: two now
    assert "2 Plex folders have no mapping yet." in out


def test_a_disabled_mapping_is_tagged_too(
    mapping_flow: SimpleNamespace,
    prompt: SimpleNamespace,
    capsys: pytest.CaptureFixture[str],
) -> None:
    mapping_flow.config.paths[1].plex = "/plex/Series/"  # the disabled one

    out = _screen_output(mapping_flow, prompt, capsys)

    assert "/plex/Series/  [disabled]  [not found in Plex]" in out


def test_parent_mapping_is_flagged_and_its_folders_count_as_unmapped(
    mapping_flow: SimpleNamespace,
    prompt: SimpleNamespace,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The scans do not use a mapping that is not one of Plex's folders, so
    "/plex/" does not map "/plex/Filmek/" -- the screen says so."""
    mapping_flow.config.paths = [
        PathMapping(plex="/plex/", local="/mnt/old/", enabled=True),
    ]

    out = _screen_output(mapping_flow, prompt, capsys)

    assert "[not found in Plex]" in out
    assert "3 Plex folders have no mapping yet." in out


def test_sub_path_mapping_is_flagged_and_its_folder_counts_as_unmapped(
    mapping_flow: SimpleNamespace,
    prompt: SimpleNamespace,
    capsys: pytest.CaptureFixture[str],
) -> None:
    mapping_flow.config.paths = [
        PathMapping(plex="/plex/Filmek/Magyar/", local="/mnt/old/Magyar/", enabled=True),
    ]

    out = _screen_output(mapping_flow, prompt, capsys)

    assert "[not found in Plex]" in out
    assert "3 Plex folders have no mapping yet." in out


def test_a_mapping_written_without_the_final_slash_is_normalised_so_it_matches(
    mapping_flow: SimpleNamespace,
    prompt: SimpleNamespace,
    capsys: pytest.CaptureFixture[str],
) -> None:
    mapping_flow.config.paths = [
        PathMapping(plex="/plex/Filmek", local="/mnt/old/Filmek", enabled=True),
    ]
    mapping_flow.plex_folders = ["/plex/Filmek/"]

    out = _screen_output(mapping_flow, prompt, capsys)

    assert "/mnt/old/Filmek/  ->  /plex/Filmek/  [active]" in out
    assert "not found in Plex" not in out
    assert "no mapping yet" not in out


def test_no_tags_when_plex_cannot_be_asked(
    mapping_flow: SimpleNamespace,
    prompt: SimpleNamespace,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def broken() -> list[str]:
        raise RuntimeError("Plex is not answering")

    mapping_flow.orch.plex.list_movie_folders = broken
    mapping_flow.config.paths[0].plex = "/plex/Movies/"

    out = _screen_output(mapping_flow, prompt, capsys)

    assert "[not found in Plex]" not in out  # nothing to compare against
    assert "Couldn't ask Plex for its folders" in out


def test_no_tags_when_plex_reports_no_folders_at_all(
    mapping_flow: SimpleNamespace,
    prompt: SimpleNamespace,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An empty list says nothing about the mappings, so it must not mark them all."""
    mapping_flow.plex_folders = []

    out = _screen_output(mapping_flow, prompt, capsys)

    assert "[not found in Plex]" not in out
    assert "Plex reported no movie folders" in out
    assert "no mapping yet" not in out


def test_a_folder_new_in_plex_is_not_flagged_after_it_was_added(
    mapping_flow: SimpleNamespace,
    prompt: SimpleNamespace,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The screen asked Plex when it opened; a library created in Plex since
    then comes with the Add flow's own, fresh request -- the screen must use it."""
    calls: list[int] = []

    def list_movie_folders() -> list[str]:
        calls.append(1)
        if len(calls) == 1:  # when the screen opens
            return ["/plex/Filmek/", "/plex/Sorozat/"]
        return ["/plex/Filmek/", "/plex/Sorozat/", "/plex/Uj/"]  # later, in Add

    mapping_flow.orch.plex.list_movie_folders = list_movie_folders
    prompt.answers = [1, 1, 0]  # Add -> the new folder -> back
    mapping_flow.typed = ["/mnt/new/Uj"]

    menu._folder_mappings(mapping_flow.config, mapping_flow.orch)

    out = capsys.readouterr().out
    assert "[not found in Plex]" not in out
    assert len(calls) == 2


# --- Scans: mappings that were left out are reported -------------------------
#
# A scan uses a mapping only if its Plex path is exactly one of Plex's folders
# (PlexClient._scan_mapper). The movies under a mapping that was left out are
# simply absent from the scan, so every scan says which mapping was not used.


def test_ignored_mappings_are_named_with_the_reason_and_the_fix(
    capsys: pytest.CaptureFixture[str],
) -> None:
    menu._print_ignored_mappings(["/share/", "/share/Filmek/Magyar/"])

    out = capsys.readouterr().out
    assert "Not used: the mapping for /share/ -- Plex has no folder with exactly this path." in out
    assert "Not used: the mapping for /share/Filmek/Magyar/ --" in out
    assert "remove this mapping and add the new folder" in " ".join(out.split())


def test_nothing_is_printed_when_no_mapping_was_left_out(
    capsys: pytest.CaptureFixture[str],
) -> None:
    menu._print_ignored_mappings([])

    assert capsys.readouterr().out == ""


def test_ignored_mapping_with_brackets_is_printed_literally(
    capsys: pytest.CaptureFixture[str],
) -> None:
    menu._print_ignored_mappings(["/share/[rec]/"])

    assert "/share/[rec]/" in capsys.readouterr().out


def _scan_flow_orch(ignored: list[str]) -> SimpleNamespace:
    return SimpleNamespace(
        config=SimpleNamespace(
            paths=[SimpleNamespace(enabled=True)],
            detection=SimpleNamespace(chapter_threshold=4, min_duration_seconds=0),
        ),
        plex=SimpleNamespace(
            server_name="TEST",
            iter_movie_candidates=lambda **_k: ([], ScanStats(ignored_mappings=list(ignored))),
            iter_credits_update_candidates=lambda **_k: (
                [],
                CreditsScanStats(ignored_mappings=list(ignored)),
            ),
        ),
    )


@pytest.mark.parametrize("flow_name", ["_generate_auto_chapters_flow", "_update_end_credits_flow"])
def test_generate_and_update_name_the_mapping_that_was_not_used(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    flow_name: str,
) -> None:
    orch = _scan_flow_orch(["/share/"])
    monkeypatch.setattr(menu, "_check_folder_reachability", lambda _cfg: (True, []))

    getattr(menu, flow_name)(orch, orch.config)

    out = capsys.readouterr().out
    assert "Not used: the mapping for /share/" in out


@pytest.mark.parametrize("flow_name", ["_generate_auto_chapters_flow", "_update_end_credits_flow"])
def test_generate_and_update_say_nothing_extra_when_every_mapping_is_used(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    flow_name: str,
) -> None:
    orch = _scan_flow_orch([])
    monkeypatch.setattr(menu, "_check_folder_reachability", lambda _cfg: (True, []))

    getattr(menu, flow_name)(orch, orch.config)

    assert "Not used" not in capsys.readouterr().out


def test_find_by_title_names_the_mapping_that_was_not_used(
    find_flow: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    find_flow.ignored = ["/share/"]

    menu._find_by_title(find_flow.orch)

    out = capsys.readouterr().out
    assert "Not used: the mapping for /share/" in out
    assert "No movie found matching 'Heat'" in out  # the movie is out of scope


def test_find_by_title_says_nothing_extra_when_every_mapping_is_used(
    find_flow: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    menu._find_by_title(find_flow.orch)

    assert "Not used" not in capsys.readouterr().out


# --- Small usability fixes (0.2.32) ---------------------------------------
#
# Found by the full test on the real Plex; each one approved by the owner.


def test_find_by_title_shows_the_long_intro_only_the_first_time(
    find_flow: SimpleNamespace,
) -> None:
    find_flow.titles = ["Heat", ""]  # nothing found, then cancel

    menu._find_by_title(find_flow.orch)

    assert "Unlike Generate/Update" in find_flow.asked[0]
    assert find_flow.asked[1] == "Type a movie title"


def test_find_by_title_after_cancelling_the_match_list_asks_the_same_plain_question(
    find_flow: SimpleNamespace, prompt: SimpleNamespace
) -> None:
    """The owner saw "Type another title" after cancelling a list of three
    matches -- odd, since no other title had been asked for. One neutral
    question fits every way back to the title prompt."""
    find_flow.titles = ["Mátrix", ""]
    find_flow.found = [_movie("Mátrix", True, 1999), _movie("Mátrix: Újratöltve", True, 2003)]
    prompt.answers = [0]  # Cancel at "Found 2 matches"

    menu._find_by_title(find_flow.orch)

    assert find_flow.asked[1] == "Type a movie title"
    assert find_flow.processed == []


def test_a_number_prompt_asks_for_a_number_in_plain_words(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Rich's own "Please enter a valid integer number" is replaced app-wide."""
    import io

    from rich.prompt import IntPrompt as RichIntPrompt

    answer = RichIntPrompt.ask("Number", stream=io.StringIO("\nabc\n5\n"), console=menu.console)

    out = capsys.readouterr().out
    assert answer == 5
    assert "Please type a number." in out
    assert "valid integer" not in out


@pytest.mark.parametrize(
    "screen", ["folder mappings", "pick a mapping", "add a mapping", "plex connection", "about"]
)
def test_every_settings_prompt_shows_its_numbers(
    screen: str, mapping_flow: SimpleNamespace, prompt: SimpleNamespace
) -> None:
    """Like every other menu: "Choose [0/1/2/3/4]:", not a bare "Choose:"."""
    prompt.answers = [0]
    if screen == "folder mappings":
        menu._folder_mappings(mapping_flow.config, mapping_flow.orch)
    elif screen == "pick a mapping":
        menu._pick_mapping(mapping_flow.config, "Remove folder mapping")
    elif screen == "add a mapping":
        menu._add_folder_mapping(mapping_flow.config, mapping_flow.orch)
    elif screen == "plex connection":
        plex = SimpleNamespace(url="http://plex:32400", token="abcdefgh")
        menu._plex_connection(SimpleNamespace(plex=plex), SimpleNamespace())
    else:
        menu._about_flow(SimpleNamespace(log_file_path=lambda: None))

    assert prompt.shown == [True]


def test_about_goes_back_on_enter(prompt: SimpleNamespace) -> None:
    prompt.answers = [ENTER]

    menu._about_flow(SimpleNamespace(log_file_path=lambda: None))

    assert prompt.asked[0][2] == 0  # Enter takes the default: 0, back


def test_about_names_the_license(prompt: SimpleNamespace, capsys: pytest.CaptureFixture[str]) -> None:
    """The GPL asks a terminal program to say that it comes with no warranty."""
    prompt.answers = [ENTER]

    menu._about_flow(SimpleNamespace(log_file_path=lambda: None))

    assert "License:   GPL-3.0-or-later (no warranty, see LICENSE)" in capsys.readouterr().out


def test_main_menu_says_what_each_item_does(
    prompt: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    """A user who skipped the README can tell Generate from Update (owner, 2026-11-14)."""
    prompt.answers = [0]
    plex = SimpleNamespace(is_connected=True, server_name="Test", connection_error=None)

    menu.run_menu(SimpleNamespace(plex=plex), SimpleNamespace(paths=[SimpleNamespace(enabled=True)]))

    lines = [line.strip() for line in capsys.readouterr().out.splitlines()]
    generate = lines.index("1) Generate Auto Chapters")
    assert lines[generate + 1] == "Adds Auto Chapters to the movies that need them"
    about = lines.index("5) About")
    assert lines[about + 1] == "Version, log file, GitHub page and license"
    # 1.1.2 (owner): an empty line between the items, so each note
    # clearly belongs to the item above it.
    assert lines[lines.index("0) Quit") + 1] == ""
    assert lines[generate + 2] == ""
    assert lines[about - 1] == ""


def test_settings_says_what_each_item_does(
    prompt: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    prompt.answers = [0]

    menu._settings_flow(SimpleNamespace(), SimpleNamespace())

    lines = [line.strip() for line in capsys.readouterr().out.splitlines()]
    after = {lines[i - 1]: line for i, line in enumerate(lines) if i}
    assert after["1) Folder mappings"] == "Connects each Plex movie folder to its path on this computer"
    assert after["2) Minimum movie length"] == "Shorter movies are skipped"
    assert after["3) Chapter threshold"] == "How many chapters a movie needs to keep its own chapter list"
    assert after["4) Plex connection"] == "Plex URL and token, and reconnect"


def test_options_without_notes_keep_the_compact_layout(
    prompt: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    prompt.answers = [1]

    menu._show_options("Pick", ["One", "Two"], allow_cancel=True)

    lines = [line.strip() for line in capsys.readouterr().out.splitlines()]
    assert lines[lines.index("0) Cancel") + 1 :][:2] == ["1) One", "2) Two"]


def test_main_menu_says_why_plex_is_not_connected(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plex = SimpleNamespace(
        is_connected=False,
        server_name="(not connected)",
        connection_error="Couldn't connect to Plex. Is it running and reachable on the network?",
    )
    monkeypatch.setattr(menu, "_show_options", lambda *_a, **_k: 0)

    menu.run_menu(SimpleNamespace(plex=plex), SimpleNamespace(paths=[SimpleNamespace(enabled=True)]))

    shown = " ".join(capsys.readouterr().out.split())
    assert (
        "Not connected to Plex: Couldn't connect to Plex. "
        "Is it running and reachable on the network?" in shown
    )


def test_a_movie_that_cannot_be_read_is_reported_once(
    confirm_flow: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    def unreadable(_mv: Movie) -> DetectionResult:
        raise MovieReadError("Couldn't read the movie file, nothing was written: File not found.")

    confirm_flow.orch.detect_movie = unreadable

    assert menu._detect_and_confirm_write(confirm_flow.movie, confirm_flow.orch) == []

    shown = " ".join(capsys.readouterr().out.split())
    assert "Couldn't read the movie file, nothing was written: File not found." in shown
    assert "Detection failed" not in shown


def test_titles_with_accents_sort_with_their_letter() -> None:
    """str.lower() put "Árvácska", "Ében" and "Őrült nők" after "Zorro"."""
    titles = ["Zorro", "Árvácska", "Alien", "Őrült nők", "Oscar", "Ében"]

    ordered = menu._by_title([_movie(t, has_credits=True) for t in titles])

    assert [mv.title for mv in ordered] == [
        "Alien", "Árvácska", "Ében", "Őrült nők", "Oscar", "Zorro"
    ]


@pytest.mark.parametrize("flow_name", ["_generate_auto_chapters_flow", "_update_end_credits_flow"])
def test_the_scan_line_counts_the_movies(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    flow_name: str,
) -> None:
    """The scan used to show a still "Scanning Plex..." for minutes."""

    def paging_scan(**kwargs: Any) -> tuple[list[Movie], Any]:
        on_progress = kwargs["on_progress"]
        for done in (0, 100, 1322):
            on_progress(done, 1322)
        stats = ScanStats() if flow_name == "_generate_auto_chapters_flow" else CreditsScanStats()
        return [], stats

    orch = _scan_flow_orch([])
    orch.plex.iter_movie_candidates = paging_scan
    orch.plex.iter_credits_update_candidates = paging_scan
    monkeypatch.setattr(menu, "_check_folder_reachability", lambda _cfg: (True, []))

    getattr(menu, flow_name)(orch, orch.config)

    assert "1322 / 1322 movies" in capsys.readouterr().out


# --- Find by title: the same status notes as the other menus -------------------


def _find_movie(
    title: str, *, auto: bool = False, credits_chapter: bool = False, plex_credits: bool = True
) -> Movie:
    return dataclasses.replace(
        _movie(title, plex_credits),
        has_auto_chapters=auto,
        has_credits_chapter=credits_chapter,
    )


def test_find_by_title_list_says_what_each_match_needs(
    find_flow: SimpleNamespace, prompt: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    """A movie Update would offer (our chapters, no End Credits chapter, Plex
    End Credits found) looked the same as a complete one."""
    find_flow.found = [
        _find_movie("Heat A", auto=True, credits_chapter=True),
        _find_movie("Heat B", auto=True, credits_chapter=False),
        _find_movie("Heat C", plex_credits=False),
        _find_movie("Heat D"),
    ]
    prompt.answers = [0]  # cancel at the list

    menu._find_by_title(find_flow.orch)

    out = capsys.readouterr().out
    assert "1) Heat A (2000)  (already has Auto Chapters)\n" in out
    assert (
        "2) Heat B (2000) (already has Auto Chapters, End Credits chapter missing -- "
        "Plex End Credits found)" in " ".join(out.split())  # a long line may wrap
    )
    assert "3) Heat C (2000)  (no Plex End Credits yet)" in out
    assert "4) Heat D (2000)\n" in out


def test_find_by_title_single_match_says_the_credits_chapter_is_missing(
    find_flow: SimpleNamespace, prompt: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    find_flow.found = [_find_movie("Heat", auto=True, credits_chapter=False)]
    prompt.answers = [0]

    menu._find_by_title(find_flow.orch)

    out = " ".join(capsys.readouterr().out.split())  # a long line may wrap
    assert (
        "Heat (2000) already has Auto Chapters, End Credits chapter missing -- "
        "Plex End Credits found" in out
    )


def test_find_by_title_single_fresh_match_without_plex_end_credits(
    find_flow: SimpleNamespace, prompt: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    find_flow.found = [_find_movie("Heat", plex_credits=False)]
    prompt.answers = [0]

    menu._find_by_title(find_flow.orch)

    assert "Found 1 match: Heat (2000)  (no Plex End Credits yet)" in capsys.readouterr().out


# --- A wrong folder path: say where to fix it ------------------------------------
#
# The owner set a wrong path in the installer on purpose and found the fix in
# Settings only by chance: the message spoke only of mounting.

FIX_HINT = "If the path is wrong: Settings → Folder mappings → 2) Change a mapping's local or mounted path."


@pytest.mark.parametrize("all_unreachable", [True, False], ids=["none-reachable", "some"])
def test_an_unreachable_folder_says_where_to_fix_a_wrong_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], all_unreachable: bool
) -> None:
    mappings = [PathMapping(plex="/share/A/", local=str(tmp_path / "wrong") + "/")]
    if not all_unreachable:
        mappings.append(PathMapping(plex="/share/B/", local=str(tmp_path) + "/"))

    menu._check_folder_reachability(SimpleNamespace(paths=mappings))

    assert FIX_HINT in " ".join(capsys.readouterr().out.split())  # a long line may wrap


def test_toggle_stays_in_its_list_until_back(
    mapping_flow: SimpleNamespace, prompt: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    """A switch used to jump back to the mappings screen (and its folder
    check) every time; now it works like a switch: the list is shown again
    with the new state until 0."""
    prompt.answers = [1, 2, 0]

    menu._toggle_folder_mapping(mapping_flow.config)

    assert [m.enabled for m in mapping_flow.config.paths] == [False, True]
    assert len(mapping_flow.saved) == 2
    assert capsys.readouterr().out.count("Enable or disable a mapping") == 3


# --- Ctrl+C during a wait in Settings goes back one step ----------------------
#
# The folder check, asking Plex for its folders and testing a URL or token can
# take seconds; Ctrl+C there used to jump to the main menu.


def _raise_ctrl_c(*_args: object) -> None:
    raise KeyboardInterrupt


def test_ctrl_c_in_a_mapping_action_goes_back_to_the_mappings_screen(
    mapping_flow: SimpleNamespace,
    prompt: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(menu, "_add_folder_mapping", _raise_ctrl_c)
    prompt.answers = [1, 0]  # Add (interrupted), then Back

    menu._folder_mappings(mapping_flow.config, mapping_flow.orch)

    assert len(prompt.asked) == 2  # the mappings screen was shown again
    assert "Cancelled." in capsys.readouterr().out


def test_ctrl_c_in_a_settings_screen_goes_back_to_settings(
    prompt: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(menu, "_folder_mappings", _raise_ctrl_c)
    prompt.answers = [1, 0]  # Folder mappings (interrupted while checking), then Back

    menu._settings_flow(SimpleNamespace(), SimpleNamespace())

    assert len(prompt.asked) == 2  # Settings was shown again
    assert "Cancelled." in capsys.readouterr().out


def test_ctrl_c_while_testing_a_plex_address_goes_back_to_plex_connection(
    prompt: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(menu, "_change_plex_url", _raise_ctrl_c)
    prompt.answers = [1, 0]  # Change URL (interrupted while testing), then Back
    config = SimpleNamespace(plex=SimpleNamespace(url="http://plex:32400", token="abcdefgh"))

    menu._plex_connection(config, SimpleNamespace())

    assert len(prompt.asked) == 2  # the Plex connection screen was shown again
    assert "Cancelled." in capsys.readouterr().out


# --- Folder mappings: is the folder here the one Plex has? ------------------
#
# 0.4.4 Ubuntu test: Plex's "Filmek" folder mapped to the "Filmek-Bence" folder
# was accepted (it exists) and showed only as 10 missing files much later.


def test_adding_a_folder_shows_how_many_of_plexs_movies_are_there(
    mapping_flow: SimpleNamespace, prompt: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    prompt.answers = [1]  # the only unmapped Plex folder
    mapping_flow.typed = ["/mnt/new/Uj"]
    mapping_flow.matches = [FolderMatch(found=650, total=653)]

    menu._add_folder_mapping(mapping_flow.config, mapping_flow.orch)

    assert mapping_flow.compared == [("/plex/Uj/", "/mnt/new/Uj/")]
    assert "OK Found 650 of 653 Plex movies in this folder." in capsys.readouterr().out
    assert mapping_flow.config.paths[-1].local == "/mnt/new/Uj/"


def test_a_folder_without_any_of_plexs_movies_can_be_typed_again(
    mapping_flow: SimpleNamespace, prompt: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    prompt.answers = [1, 1]  # the unmapped folder; 1) Enter another path
    mapping_flow.typed = ["/mnt/wrong/", "/mnt/right/"]
    mapping_flow.matches = [FolderMatch(found=0, total=653), FolderMatch(found=653, total=653)]

    menu._add_folder_mapping(mapping_flow.config, mapping_flow.orch)

    out = " ".join(capsys.readouterr().out.split())
    assert "Found 0 of 653 Plex movies in this folder. Is the path right?" in out
    assert "1) Enter another path" in out
    assert "2) Use it anyway" in out
    assert mapping_flow.config.paths[-1].local == "/mnt/right/"
    assert len(mapping_flow.saved) == 1


def test_a_folder_without_any_of_plexs_movies_can_be_kept(
    mapping_flow: SimpleNamespace, prompt: SimpleNamespace
) -> None:
    prompt.answers = [1, 2]  # the unmapped folder; 2) Use it anyway
    mapping_flow.typed = ["/mnt/odd/"]
    mapping_flow.matches = [FolderMatch(found=0, total=653)]

    menu._add_folder_mapping(mapping_flow.config, mapping_flow.orch)

    assert mapping_flow.config.paths[-1].local == "/mnt/odd/"


def test_changing_a_path_compares_it_with_that_mappings_plex_folder(
    mapping_flow: SimpleNamespace, prompt: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    prompt.answers = [1, 1]  # first mapping; 1) Enter another path
    mapping_flow.typed = ["/mnt/wrong/", "/mnt/new/Filmek/"]
    mapping_flow.matches = [FolderMatch(found=0, total=10), FolderMatch(found=9, total=10)]

    menu._change_folder_mapping(mapping_flow.config, mapping_flow.orch)

    assert mapping_flow.compared == [
        ("/plex/Filmek/", "/mnt/wrong/"),
        ("/plex/Filmek/", "/mnt/new/Filmek/"),
    ]
    assert "Found 9 of 10 Plex movies" in capsys.readouterr().out
    assert mapping_flow.config.paths[0].local == "/mnt/new/Filmek/"


@pytest.mark.parametrize(
    "match",
    [None, FolderMatch(found=0, total=0), PlexUnavailableError("Plex is not answering.")],
    ids=["could not tell", "no movies in Plex's folder", "Plex not answering"],
)
def test_when_it_cant_compare_it_says_nothing_and_keeps_the_path(
    mapping_flow: SimpleNamespace,
    prompt: SimpleNamespace,
    capsys: pytest.CaptureFixture[str],
    match: object,
) -> None:
    prompt.answers = [1]
    mapping_flow.typed = ["/mnt/new/Uj/"]
    mapping_flow.matches = [match]

    menu._add_folder_mapping(mapping_flow.config, mapping_flow.orch)

    out = capsys.readouterr().out
    assert "Found" not in out.replace("Found 1 unmapped", "")
    assert mapping_flow.config.paths[-1].local == "/mnt/new/Uj/"


def test_an_unreachable_folder_is_not_compared(
    mapping_flow: SimpleNamespace, prompt: SimpleNamespace
) -> None:
    """Not there (or not answering): nothing to list; the existing question decides."""
    mapping_flow.reachable = False
    prompt.answers = [1]
    mapping_flow.typed = ["/mnt/not-mounted/Uj/"]
    mapping_flow.yn = ["y"]

    menu._add_folder_mapping(mapping_flow.config, mapping_flow.orch)

    assert mapping_flow.compared == []
    assert mapping_flow.config.paths[-1].local == "/mnt/not-mounted/Uj/"


# --- Movies that need work but whose files weren't found ---------------------
#
# 0.4.4 Ubuntu test: "Missing files: 10" and "All movies have Auto Chapters.
# Nothing to do here." on the same screen.


def _flow_with_stats(**counts: int) -> SimpleNamespace:
    return SimpleNamespace(
        config=SimpleNamespace(
            paths=[SimpleNamespace(enabled=True)],
            detection=SimpleNamespace(chapter_threshold=4, min_duration_seconds=0),
        ),
        plex=SimpleNamespace(
            server_name="TEST",
            iter_movie_candidates=lambda **_k: ([], ScanStats(**counts)),
            iter_credits_update_candidates=lambda **_k: ([], CreditsScanStats(**counts)),
        ),
    )


@pytest.mark.parametrize(
    ("flow_name", "need", "all_done"),
    [
        ("_generate_auto_chapters_flow", "Auto Chapters", "All movies have Auto Chapters"),
        ("_update_end_credits_flow", "an End Credits update", "No movies need an End Credits"),
    ],
)
def test_missing_files_are_said_instead_of_nothing_to_do(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    flow_name: str,
    need: str,
    all_done: str,
) -> None:
    orch = _flow_with_stats(total_movies=1322, missing_files=10)
    monkeypatch.setattr(menu, "_check_folder_reachability", lambda _cfg: (True, []))

    getattr(menu, flow_name)(orch, orch.config)

    out = " ".join(capsys.readouterr().out.split())
    assert f"10 movies need {need}, but their files weren't found." in out
    assert "Settings → Folder mappings → 2) Change a mapping's local or mounted path" in out
    assert all_done not in out


def test_one_missing_file_is_said_in_the_singular(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    orch = _flow_with_stats(total_movies=5, missing_files=1)
    monkeypatch.setattr(menu, "_check_folder_reachability", lambda _cfg: (True, []))

    menu._generate_auto_chapters_flow(orch, orch.config)

    assert "1 movie needs Auto Chapters, but its file wasn't found." in capsys.readouterr().out


@pytest.mark.parametrize(
    ("flow_name", "all_done"),
    [
        ("_generate_auto_chapters_flow", "All movies have Auto Chapters. Nothing to do here."),
        ("_update_end_credits_flow", "No movies need an End Credits update right now."),
    ],
)
def test_without_missing_files_nothing_to_do_stays(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    flow_name: str,
    all_done: str,
) -> None:
    orch = _flow_with_stats(total_movies=1322)
    monkeypatch.setattr(menu, "_check_folder_reachability", lambda _cfg: (True, []))

    getattr(menu, flow_name)(orch, orch.config)

    out = capsys.readouterr().out
    assert all_done in out
    assert "weren't found" not in out


# --- An address typed instead of a folder path --------------------------------


@pytest.mark.parametrize("typed", ["smb://nas.local/media/Filmek", "NFS://nas/Filmek"])
def test_a_network_address_says_the_share_has_to_be_mounted(
    mapping_flow: SimpleNamespace,
    prompt: SimpleNamespace,
    capsys: pytest.CaptureFixture[str],
    typed: str,
) -> None:
    prompt.answers = [1]
    mapping_flow.typed = [typed, "/mnt/nas/Filmek"]

    menu._change_folder_mapping(mapping_flow.config, mapping_flow.orch)

    out = " ".join(capsys.readouterr().out.split())
    scheme = typed.split(":")[0]
    assert f"That's a network address ({scheme}://...), not a folder path." in out
    assert "mounted as a folder on this computer first" in out
    assert 'INSTALL.md, "Mounting a network share"' in out
    assert "doesn't look like a folder path" not in out
    assert mapping_flow.config.paths[0].local == "/mnt/nas/Filmek/"  # asked again


def test_a_local_folders_address_is_used_as_its_path(
    mapping_flow: SimpleNamespace, prompt: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    prompt.answers = [1]
    mapping_flow.typed = ["file:///home/user/%C3%81rv%C3%A1cska%20filmek"]

    menu._change_folder_mapping(mapping_flow.config, mapping_flow.orch)

    assert mapping_flow.config.paths[0].local == "/home/user/Árvácska filmek/"
    assert "network address" not in capsys.readouterr().out


# --- Ctrl+C in Generate / Update with one movie --------------------------------
#
# Owner's 1.0.0 test: with one movie picked, the first Ctrl+C said "Will stop
# cleanly after the current movie" -- that is, after the only movie: nothing.


def _ctrl_c_handlers(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    used: list[str] = []

    def recorder(name: str):  # type: ignore[no-untyped-def]
        @contextmanager
        def handler() -> Iterator[None]:
            used.append(name)
            yield

        return handler

    _RecordingToggle.instances = []
    monkeypatch.setattr(menu, "cancellable", recorder("at once"))
    monkeypatch.setattr(menu, "batch_interrupt", recorder("after this movie"))
    monkeypatch.setattr(menu, "ShutdownToggle", _RecordingToggle)
    monkeypatch.setattr(
        menu,
        "_process_one",
        lambda mv, _orch: ProcessingResult(movie=mv, chapters=[], written=True),
    )
    return used


def test_one_movie_stops_at_the_first_ctrl_c(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    used = _ctrl_c_handlers(monkeypatch)

    menu._run_batch([_movie("Gladiátor", True)], SimpleNamespace())

    assert used == ["at once"]
    out = capsys.readouterr().out
    assert "Press Ctrl+C to cancel. The original file is never damaged." in out
    assert "twice" not in out


def test_several_movies_keep_the_two_step_ctrl_c(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    used = _ctrl_c_handlers(monkeypatch)

    menu._run_batch([_movie("A", True), _movie("B", True)], SimpleNamespace())

    assert used == ["after this movie"]
    assert "twice for immediate stop" in capsys.readouterr().out


class _TogglingToggle(_RecordingToggle):
    """Presses 's' twice (schedule, cancel) and once more (schedule) as soon
    as it starts listening, so every message of the callback is printed."""

    def start(self, on_toggle):  # type: ignore[no-untyped-def]
        super().start(on_toggle)
        for scheduled in (True, False, True):
            self.scheduled = scheduled
            on_toggle(scheduled)
        return True


@pytest.mark.parametrize("count", [1, 2])
def test_shutdown_messages_say_movie_or_batch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], count: int
) -> None:
    """Owner's 1.0.0 test: with one movie the 's' messages still spoke of a batch."""
    _ctrl_c_handlers(monkeypatch)
    monkeypatch.setattr(menu, "ShutdownToggle", _TogglingToggle)
    monkeypatch.setattr(menu, "_process_one", lambda _mv, _orch: None)  # nothing written
    movies = [_movie(t, True) for t in ("A", "B")[:count]]

    menu._run_batch(movies, SimpleNamespace())

    out = " ".join(capsys.readouterr().out.split())
    if count == 1:
        assert "The system will power off once this movie is written." in out
        assert "Shutdown cancelled. Continuing without shutting down." in out
        assert "Scheduled shutdown cancelled -- the movie wasn't written." in out
        assert "batch" not in out
    else:
        assert "The system will power off after this batch finishes." in out
        assert "The batch will continue without shutting down." in out
        assert "no movies were processed successfully" in out
