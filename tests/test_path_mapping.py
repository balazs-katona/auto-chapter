"""Tests for path mapping."""

import time as time_module
from pathlib import Path

from auto_chapter.config import PathMapping
from auto_chapter.path_mapping import PathMapper, check_dir_with_timeout


def test_plex_to_local() -> None:
    mapper = PathMapper(
        [PathMapping(plex="/share/CACHEDEV1_DATA/Multimedia/", local="/mnt/nas/")]
    )
    result = mapper.plex_to_local(
        "/share/CACHEDEV1_DATA/Multimedia/media/Filmek/Matrix/Matrix.mkv"
    )
    assert result == Path("/mnt/nas/media/Filmek/Matrix/Matrix.mkv")


def test_local_to_plex() -> None:
    mapper = PathMapper(
        [PathMapping(plex="/share/CACHEDEV1_DATA/Multimedia/", local="/mnt/nas/")]
    )
    result = mapper.local_to_plex(Path("/mnt/nas/media/Filmek/Matrix/Matrix.mkv"))
    assert result == "/share/CACHEDEV1_DATA/Multimedia/media/Filmek/Matrix/Matrix.mkv"


def test_no_match_returns_none() -> None:
    mapper = PathMapper(
        [PathMapping(plex="/share/CACHEDEV1_DATA/Multimedia/", local="/mnt/nas/")]
    )
    assert mapper.plex_to_local("/some/other/path.mkv") is None
    assert mapper.local_to_plex("/some/other/path.mkv") is None


def test_multiple_mappings() -> None:
    mapper = PathMapper(
        [
            PathMapping(plex="/share/A/", local="/mnt/a/"),
            PathMapping(plex="/share/B/", local="/mnt/b/"),
        ]
    )
    assert mapper.plex_to_local("/share/A/file.mkv") == Path("/mnt/a/file.mkv")
    assert mapper.plex_to_local("/share/B/file.mkv") == Path("/mnt/b/file.mkv")


def test_disabled_mapping_is_ignored_by_plex_to_local() -> None:
    mapper = PathMapper(
        [PathMapping(plex="/share/A/", local="/mnt/a/", enabled=False)]
    )
    assert mapper.plex_to_local("/share/A/file.mkv") is None


def test_disabled_mapping_is_ignored_by_local_to_plex() -> None:
    mapper = PathMapper(
        [PathMapping(plex="/share/A/", local="/mnt/a/", enabled=False)]
    )
    assert mapper.local_to_plex(Path("/mnt/a/file.mkv")) is None


def test_has_any_mapping_for_matches_enabled_and_disabled() -> None:
    mapper = PathMapper(
        [
            PathMapping(plex="/share/A/", local="/mnt/a/", enabled=True),
            PathMapping(plex="/share/B/", local="/mnt/b/", enabled=False),
        ]
    )
    assert mapper.has_any_mapping_for("/share/A/file.mkv") is True
    assert mapper.has_any_mapping_for("/share/B/file.mkv") is True
    assert mapper.has_any_mapping_for("/share/C/file.mkv") is False


def test_has_enabled_mapping_for_only_matches_enabled() -> None:
    mapper = PathMapper(
        [
            PathMapping(plex="/share/A/", local="/mnt/a/", enabled=True),
            PathMapping(plex="/share/B/", local="/mnt/b/", enabled=False),
        ]
    )
    assert mapper.has_enabled_mapping_for("/share/A/file.mkv") is True
    assert mapper.has_enabled_mapping_for("/share/B/file.mkv") is False
    assert mapper.has_enabled_mapping_for("/share/C/file.mkv") is False


def test_enabled_mapping_used_when_mixed_with_disabled() -> None:
    """Regression test: when both an enabled and a disabled mapping exist,
    only the enabled one should ever be used for translation, and toggling
    one mapping must not affect scanning of the other."""
    mapper = PathMapper(
        [
            PathMapping(plex="/share/A/", local="/mnt/a/", enabled=True),
            PathMapping(plex="/share/B/", local="/mnt/b/", enabled=False),
        ]
    )
    assert mapper.plex_to_local("/share/A/movie.mkv") == Path("/mnt/a/movie.mkv")
    assert mapper.plex_to_local("/share/B/movie.mkv") is None


# ---------------------------------------------------------------------------
# unreachable_enabled_folders / enabled_count
#
# Lets menu.py warn about a down network mount (Plex reachable, but the
# actual movie folder isn't) BEFORE running a full scan, rather than the
# user seeing a misleadingly "healthy" stats panel -- with_auto_chapters/
# with_other_chapters come from Plex's own metadata, not a live file read,
# so they don't reflect a mount being down at all.
# ---------------------------------------------------------------------------


def test_unreachable_enabled_folders_empty_when_all_exist(tmp_path: Path) -> None:
    folder = tmp_path / "movies"
    folder.mkdir()
    mapper = PathMapper([PathMapping(plex="/share/A/", local=str(folder))])

    assert mapper.unreachable_enabled_folders() == []


def test_unreachable_enabled_folders_reports_missing_directory(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    mapper = PathMapper([PathMapping(plex="/share/A/", local=str(missing))])

    assert mapper.unreachable_enabled_folders() == [str(missing) + "/"]


def test_unreachable_enabled_folders_ignores_disabled_mappings(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    mapper = PathMapper(
        [PathMapping(plex="/share/A/", local=str(missing), enabled=False)]
    )

    # A disabled mapping's folder being unreachable isn't the user's
    # problem right now -- they intentionally turned it off.
    assert mapper.unreachable_enabled_folders() == []


def test_unreachable_enabled_folders_mixed_reachable_and_not(tmp_path: Path) -> None:
    reachable = tmp_path / "reachable"
    reachable.mkdir()
    missing = tmp_path / "missing"
    mapper = PathMapper(
        [
            PathMapping(plex="/share/A/", local=str(reachable)),
            PathMapping(plex="/share/B/", local=str(missing)),
        ]
    )

    assert mapper.unreachable_enabled_folders() == [str(missing) + "/"]


def test_enabled_count() -> None:
    mapper = PathMapper(
        [
            PathMapping(plex="/share/A/", local="/mnt/a/", enabled=True),
            PathMapping(plex="/share/B/", local="/mnt/b/", enabled=True),
            PathMapping(plex="/share/C/", local="/mnt/c/", enabled=False),
        ]
    )

    assert mapper.enabled_count() == 2


# ---------------------------------------------------------------------------
# check_dir_with_timeout: the D-state safety net
#
# A network mount that's present but unresponsive makes even a plain
# Path.is_dir() block forever -- no signal, not even SIGKILL, can interrupt
# that at the OS level. These tests simulate that hang directly (mocking
# Path.is_dir to sleep) to prove the timeout actually bounds the wait,
# rather than just testing the fast/healthy path over and over.
# ---------------------------------------------------------------------------


def test_check_dir_with_timeout_fast_path_reachable(tmp_path: Path) -> None:
    folder = tmp_path / "movies"
    folder.mkdir()

    assert check_dir_with_timeout(str(folder), timeout=2.0) is True


def test_check_dir_with_timeout_fast_path_missing(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"

    assert check_dir_with_timeout(str(missing), timeout=2.0) is False


def test_check_dir_with_timeout_returns_none_when_check_hangs(monkeypatch) -> None:
    """The core regression test: simulates a hung (D-state) mount where
    the underlying is_dir() call never returns within the timeout. The
    function must still hand control back to the caller promptly, not
    block for as long as the hang actually lasts."""

    def hanging_is_dir(self):
        time_module.sleep(2.0)  # much longer than the timeout below
        return True

    monkeypatch.setattr(Path, "is_dir", hanging_is_dir)

    start = time_module.monotonic()
    result = check_dir_with_timeout("/some/path", timeout=0.2)
    elapsed = time_module.monotonic() - start

    assert result is None
    assert elapsed < 1.0  # returned well before the simulated 2s hang finished


def test_default_timeout_waits_out_a_busy_but_healthy_mount(monkeypatch) -> None:
    """Measured on the owner's QNAP over CIFS: while the share is busy (an
    ffmpeg reading a movie), a plain is_dir() on a healthy folder took 1.5-7.7s.
    The old 2s default reported such a folder as "may be hung" and the scans
    then skipped every movie in it. A 2.5s answer must count as reachable."""

    def slow_is_dir(self):
        time_module.sleep(2.5)
        return True

    monkeypatch.setattr(Path, "is_dir", slow_is_dir)

    assert check_dir_with_timeout("/mnt/busy-nas/Filmek/") is True


def test_unreachable_enabled_folders_treats_timeout_same_as_unreachable(
    monkeypatch,
) -> None:
    """For the simple pre-scan check, "couldn't verify in time" and
    "confirmed gone" both mean the same practical thing: don't count on
    this folder working right now."""

    def hanging_is_dir(self):
        time_module.sleep(2.0)

    monkeypatch.setattr(Path, "is_dir", hanging_is_dir)
    mapper = PathMapper([PathMapping(plex="/share/A/", local="/mnt/hung/")])

    assert mapper.unreachable_enabled_folders(timeout=0.2) == ["/mnt/hung/"]


def test_check_enabled_folders_distinguishes_missing_from_timed_out(
    tmp_path: Path, monkeypatch
) -> None:
    """Unlike the simple unreachable_enabled_folders(), the detailed
    check_enabled_folders() must let a caller (the Folder mappings screen)
    tell "confirmed gone" apart from "possibly hung" -- these suggest
    different next steps for the user (remove a genuinely gone mapping vs.
    wait out a mount that might come back)."""
    missing = tmp_path / "does-not-exist"
    real_is_dir = Path.is_dir

    def selectively_hanging_is_dir(self):
        # pathlib normalizes away a trailing slash when stringified --
        # Path("/mnt/hung/") stringifies to "/mnt/hung", not "/mnt/hung/".
        if str(self) == "/mnt/hung":
            time_module.sleep(2.0)
            return True
        return real_is_dir(self)

    monkeypatch.setattr(Path, "is_dir", selectively_hanging_is_dir)
    mapper = PathMapper(
        [
            PathMapping(plex="/share/A/", local=str(missing)),
            PathMapping(plex="/share/B/", local="/mnt/hung/"),
        ]
    )

    results = {c.local: c.reachable for c in mapper.check_enabled_folders(timeout=0.2)}

    assert results[str(missing) + "/"] is False
    assert results["/mnt/hung/"] is None


def test_worst_case_wait_is_bounded_not_indefinite(monkeypatch) -> None:
    """If every single enabled folder happens to be hung, the total wait
    must still be bounded (num_folders * timeout), so the Folder mappings
    screen always renders and the user can always reach Remove/disable --
    never gets stuck with no way to fix a bad mapping."""

    def hanging_is_dir(self):
        time_module.sleep(5.0)  # far longer than 3 folders * 0.2s timeout

    monkeypatch.setattr(Path, "is_dir", hanging_is_dir)
    mapper = PathMapper(
        [
            PathMapping(plex="/share/A/", local="/mnt/a/"),
            PathMapping(plex="/share/B/", local="/mnt/b/"),
            PathMapping(plex="/share/C/", local="/mnt/c/"),
        ]
    )

    start = time_module.monotonic()
    results = mapper.check_enabled_folders(timeout=0.2)
    elapsed = time_module.monotonic() - start

    assert all(c.reachable is None for c in results)
    assert elapsed < 2.0  # bounded (~3 * 0.2s), nowhere near the 5s hang
