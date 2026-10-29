"""Tests for _cleanup_stale_tmp: leftover .autochapter.tmp removal.

These files can only be left behind if a previous run was killed mid-write
(crash, power loss, SIGKILL) before its own try/finally cleanup ran. Since
mkvmerge always writes a full new container in one pass, the .tmp is never
a resumable partial file -- it's always safe to discard.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from requests.exceptions import ConnectionError as ReqConnectionError

from auto_chapter.config import PathMapping, PlexConfig
from auto_chapter.path_mapping import PathMapper
from auto_chapter.plex_client import PlexClient, PlexUnavailableError, _cleanup_stale_tmp


def test_removes_existing_stale_tmp(tmp_path: Path) -> None:
    movie = tmp_path / "Movie.mkv"
    movie.write_bytes(b"fake movie data")
    stale = tmp_path / "Movie.mkv.autochapter.tmp"
    stale.write_bytes(b"leftover partial write")

    _cleanup_stale_tmp(movie)

    assert not stale.exists()
    assert movie.exists()  # the real file must never be touched


def test_no_op_when_no_stale_tmp_exists(tmp_path: Path) -> None:
    movie = tmp_path / "Movie.mkv"
    movie.write_bytes(b"fake movie data")

    # Should not raise even though there's nothing to clean up
    _cleanup_stale_tmp(movie)

    assert movie.exists()


def test_only_removes_exact_autochapter_tmp_suffix(tmp_path: Path) -> None:
    """Guards the ownership guarantee: a generic .tmp or a different
    suffix must never be touched, only our own unique marker."""
    movie = tmp_path / "Movie.mkv"
    movie.write_bytes(b"fake movie data")
    unrelated_tmp = tmp_path / "Movie.mkv.tmp"
    unrelated_tmp.write_bytes(b"some other program's temp file")

    _cleanup_stale_tmp(movie)

    assert unrelated_tmp.exists()  # must not be deleted -- not our marker


def test_removes_existing_stale_chapter_xml(tmp_path: Path) -> None:
    """The chapter XML handed to mkvmerge is left behind too when a run is
    killed mid-write; it has its own app-specific name so it can be found."""
    movie = tmp_path / "Movie.mkv"
    movie.write_bytes(b"fake movie data")
    stale_xml = tmp_path / "Movie.mkv.autochapter.xml"
    stale_xml.write_text("<Chapters/>", encoding="utf-8")

    _cleanup_stale_tmp(movie)

    assert not stale_xml.exists()
    assert movie.exists()


def test_does_not_remove_other_xml_files(tmp_path: Path) -> None:
    """A generic tmpXXXX.xml (what older versions left behind, but also what
    any other program may create) or another program's XML is never ours."""
    movie = tmp_path / "Movie.mkv"
    movie.write_bytes(b"fake movie data")
    others = [tmp_path / "tmpabc123.xml", tmp_path / "Movie.xml", tmp_path / "Movie.mkv.xml"]
    for other in others:
        other.write_text("<x/>", encoding="utf-8")

    _cleanup_stale_tmp(movie)

    assert all(other.exists() for other in others)


def test_does_not_raise_on_permission_error(tmp_path: Path, monkeypatch) -> None:
    movie = tmp_path / "Movie.mkv"
    movie.write_bytes(b"fake movie data")
    stale = tmp_path / "Movie.mkv.autochapter.tmp"
    stale.write_bytes(b"leftover")

    def fake_unlink(self):
        raise OSError("Permission denied")

    monkeypatch.setattr(Path, "unlink", fake_unlink)

    # Should log a warning internally but never propagate the exception
    _cleanup_stale_tmp(movie)


# ---------------------------------------------------------------------------
# PlexClient: graceful connection failure handling
#
# The app must be able to start (into the main menu, so Settings > Plex
# connection is reachable to fix things) even when Plex is unreachable at
# launch -- previously, PlexClient.__init__ connected eagerly with no
# try/except at any call site, so an unreachable Plex crashed the whole app
# with a raw traceback before any menu ever appeared.
# ---------------------------------------------------------------------------


def _make_client(monkeypatch, server_factory) -> "PlexClient":
    """Build a PlexClient with PlexServer replaced by server_factory.

    server_factory is called with no arguments (matching how PlexClient
    invokes it internally) and should either return a fake server object or
    raise, to simulate a successful or failed connection attempt.
    """
    import auto_chapter.plex_client as plex_client_module

    monkeypatch.setattr(
        plex_client_module, "PlexServer", lambda *a, **kw: server_factory()
    )
    monkeypatch.setattr(
        "auto_chapter.errors.time.sleep", lambda *_: None
    )  # skip real backoff delays in tests
    config = PlexConfig(url="http://fake:32400", token="faketoken")
    mapper = PathMapper([])
    return plex_client_module.PlexClient(config, mapper)


def test_successful_connection_sets_is_connected_true(monkeypatch) -> None:
    fake_server = SimpleNamespace(friendlyName="TESTSERVER")
    client = _make_client(monkeypatch, lambda: fake_server)

    assert client.is_connected is True
    assert client.server_name == "TESTSERVER"


def test_connection_succeeds_after_transient_retries(monkeypatch) -> None:
    """The common real-world case: Plex needs a moment to wake up, first
    couple of attempts time out, then it responds normally."""
    fake_server = SimpleNamespace(friendlyName="TESTSERVER")
    attempts = {"count": 0}

    def flaky_then_succeeds():
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise ReqConnectionError("Failed to establish a new connection")
        return fake_server

    client = _make_client(monkeypatch, flaky_then_succeeds)

    assert client.is_connected is True
    assert attempts["count"] == 3


def test_failed_connection_does_not_raise(monkeypatch) -> None:
    def always_fails():
        raise ReqConnectionError("Failed to establish a new connection")

    # Must not raise -- this is the core regression test for the crash bug
    client = _make_client(monkeypatch, always_fails)

    assert client.is_connected is False


def test_failed_connection_server_name_is_placeholder(monkeypatch) -> None:
    def always_fails():
        raise ReqConnectionError("Failed to establish a new connection")

    client = _make_client(monkeypatch, always_fails)

    assert client.server_name == "(not connected)"


def test_failed_connection_error_property_is_friendly(monkeypatch) -> None:
    """Used by the Settings > Plex connection > Reconnect flow to show why
    a retry failed, without reaching into a private attribute."""

    def always_fails():
        raise ReqConnectionError("Failed to establish a new connection")

    client = _make_client(monkeypatch, always_fails)

    assert client.connection_error is not None
    assert "Failed to establish" not in client.connection_error


def test_successful_connection_error_property_is_none(monkeypatch) -> None:
    fake_server = SimpleNamespace(friendlyName="TESTSERVER")
    client = _make_client(monkeypatch, lambda: fake_server)

    assert client.connection_error is None


def test_failed_connection_list_movie_folders_raises_friendly_error(monkeypatch) -> None:
    def always_fails():
        raise ReqConnectionError("Failed to establish a new connection")

    client = _make_client(monkeypatch, always_fails)

    with pytest.raises(PlexUnavailableError) as exc_info:
        client.list_movie_folders()
    # Message must be the already-friendly sentence, not a raw exception dump
    assert "Failed to establish" not in str(exc_info.value)
    assert len(str(exc_info.value)) > 0


def test_failed_connection_iter_movie_candidates_raises(monkeypatch) -> None:
    def always_fails():
        raise ReqConnectionError("Failed to establish a new connection")

    client = _make_client(monkeypatch, always_fails)

    with pytest.raises(PlexUnavailableError):
        client.iter_movie_candidates(chapter_threshold=4)


def test_failed_connection_iter_credits_update_candidates_raises(monkeypatch) -> None:
    def always_fails():
        raise ReqConnectionError("Failed to establish a new connection")

    client = _make_client(monkeypatch, always_fails)

    with pytest.raises(PlexUnavailableError):
        client.iter_credits_update_candidates()


# ---------------------------------------------------------------------------
# reconnect() / automatic retry via _require_server()
#
# is_connected is a static flag, not a live check -- something has to
# actually attempt a fresh connection for it to change. reconnect() is that
# one mechanism, used both explicitly (Settings > Reconnect) and
# automatically (_require_server(), right before any Plex-dependent action
# would otherwise fail on a possibly-stale disconnected state).
# ---------------------------------------------------------------------------


def test_reconnect_succeeds_and_updates_state(monkeypatch) -> None:
    fake_server = SimpleNamespace(friendlyName="TESTSERVER")
    client = _make_client(monkeypatch, lambda: fake_server)
    # Force into a disconnected state first, as if an earlier attempt failed
    client._server = None
    client._connection_error = "previous failure"

    result = client.reconnect()

    assert result is True
    assert client.is_connected is True
    assert client.connection_error is None  # cleared on success


def test_reconnect_returns_false_when_still_unreachable(monkeypatch) -> None:
    def always_fails():
        raise ReqConnectionError("Failed to establish a new connection")

    client = _make_client(monkeypatch, always_fails)

    result = client.reconnect()

    assert result is False
    assert client.is_connected is False


def test_require_server_auto_retries_and_succeeds_if_now_reachable(monkeypatch) -> None:
    """The core scenario this exists for: Plex was down when the app
    started (all 3 attempts of the initial connect fail), but has come
    back by the time the user actually tries to use it -- a Plex-dependent
    action should recover automatically, not just fail on the stale
    startup-time state."""
    fake_server = SimpleNamespace(
        friendlyName="TESTSERVER",
        library=SimpleNamespace(sections=lambda: []),
    )
    calls = {"count": 0}

    def server_factory():
        calls["count"] += 1
        if calls["count"] <= 3:
            # Exhausts every attempt of the initial connect() at
            # construction time -- Plex is down at startup.
            raise ReqConnectionError("Failed to establish a new connection")
        # The network has come back by the time _require_server() retries.
        return fake_server

    client = _make_client(monkeypatch, server_factory)
    assert client.is_connected is False  # confirm startup connection failed

    folders = client.list_movie_folders()  # goes through _require_server()

    assert client.is_connected is True
    assert folders == []


def test_require_server_still_raises_if_reconnect_also_fails(monkeypatch) -> None:
    def always_fails():
        raise ReqConnectionError("Failed to establish a new connection")

    client = _make_client(monkeypatch, always_fails)

    with pytest.raises(PlexUnavailableError):
        client.list_movie_folders()


def test_reconnect_picks_up_mutated_config_in_place(monkeypatch) -> None:
    """config.plex is the same object menu.py's _change_plex_url/_change_
    plex_token mutate in place (config.plex.url = new_url) -- reconnect()
    must see that new value without needing the whole PlexClient rebuilt."""
    import auto_chapter.plex_client as plex_client_module

    seen_urls: list[str] = []

    def fake_plex_server(url, token, timeout):
        seen_urls.append(url)
        return SimpleNamespace(friendlyName="TESTSERVER")

    monkeypatch.setattr(plex_client_module, "PlexServer", fake_plex_server)
    monkeypatch.setattr("auto_chapter.errors.time.sleep", lambda *_: None)

    config = PlexConfig(url="http://original:32400", token="tok")
    client = plex_client_module.PlexClient(config, PathMapper([]))

    # Simulate a Settings > Change URL flow mutating the SAME config object
    config.url = "http://new-address:32400"
    client.reconnect()

    assert seen_urls == ["http://original:32400", "http://new-address:32400"]


# ---------------------------------------------------------------------------
# iter_movie_candidates: fast-skip for movies under a known-unreachable
# folder
#
# Regression test for a real gap: the folder-level reachability check
# (PathMapper.unreachable_enabled_folders) only protected the *decision* to
# proceed with a scan -- it didn't stop the scan from still calling
# Path.exists() individually for every movie under a folder already known
# to be down. With hundreds of movies in one folder, that meant hundreds of
# redundant chances to hang on the exact same bad mount. The fix: compute
# unreachable folders once at the top of the scan, then skip movies under
# them immediately, no filesystem call at all.
# ---------------------------------------------------------------------------


def _make_fake_movie(plex_path: str, title: str):
    return SimpleNamespace(
        title=title,
        year=2020,
        chapters=[],
        markers=[],
        duration=7_200_000,  # 2 hours, in ms (Plex's unit)
        media=[SimpleNamespace(parts=[SimpleNamespace(file=plex_path)])],
    )


def test_scan_skips_exists_check_for_movies_under_unreachable_folder(
    monkeypatch, tmp_path: Path
) -> None:
    import auto_chapter.plex_client as plex_client_module

    reachable_dir = tmp_path / "reachable"
    reachable_dir.mkdir()
    (reachable_dir / "GoodMovie.mkv").write_bytes(b"fake")

    unreachable_local = str(tmp_path / "does-not-exist") + "/"

    mapper = PathMapper(
        [
            PathMapping(plex="/share/good/", local=str(reachable_dir) + "/"),
            PathMapping(plex="/share/bad/", local=unreachable_local),
        ]
    )

    good_movie = _make_fake_movie("/share/good/GoodMovie.mkv", "Good Movie")
    bad_movie = _make_fake_movie("/share/bad/BadMovie.mkv", "Bad Movie")
    fake_section = SimpleNamespace(
        type="movie",
        all=lambda: [good_movie, bad_movie],
        locations=["/share/good/", "/share/bad/"],
    )
    fake_server = SimpleNamespace(
        friendlyName="TEST", library=SimpleNamespace(sections=lambda: [fake_section])
    )

    monkeypatch.setattr(plex_client_module, "PlexServer", lambda *a, **kw: fake_server)
    monkeypatch.setattr("auto_chapter.errors.time.sleep", lambda *_: None)

    config = PlexConfig(url="http://fake:32400", token="tok")
    client = plex_client_module.PlexClient(config, mapper)

    # Track every path exists() is actually called on -- the bad movie's
    # path must never appear here, proving it was fast-skipped rather than
    # individually risking a hang.
    original_exists = Path.exists
    checked_paths: list[str] = []

    def tracked_exists(self):
        checked_paths.append(str(self))
        return original_exists(self)

    monkeypatch.setattr(Path, "exists", tracked_exists)

    _movies, stats = client.iter_movie_candidates(chapter_threshold=4)

    assert not any("BadMovie" in p for p in checked_paths)
    assert stats.missing_files == 1
    assert any("GoodMovie" in p for p in checked_paths)  # the healthy one WAS checked normally


def test_known_unreachable_folders_skips_redundant_recomputation(monkeypatch) -> None:
    """When the caller already computed unreachable folders (e.g. menu.py's
    pre-scan _check_folder_reachability), passing it in via
    known_unreachable_folders must be used as-is -- the scan should not
    redundantly re-run the same per-folder check a second time for the
    same scan (each run is bounded but not free -- up to N * timeout)."""
    import auto_chapter.plex_client as plex_client_module

    fake_server = SimpleNamespace(
        friendlyName="TEST", library=SimpleNamespace(sections=lambda: [])
    )
    monkeypatch.setattr(plex_client_module, "PlexServer", lambda *a, **kw: fake_server)
    monkeypatch.setattr("auto_chapter.errors.time.sleep", lambda *_: None)

    config = PlexConfig(url="http://fake:32400", token="tok")
    client = plex_client_module.PlexClient(config, PathMapper([]))

    def should_not_be_called(*_a, **_kw):
        raise AssertionError(
            "unreachable_enabled_folders() must not be called again when "
            "known_unreachable_folders was already provided"
        )

    monkeypatch.setattr(
        PathMapper, "unreachable_enabled_folders", should_not_be_called
    )

    # Must not raise -- proves the internal fallback computation was skipped
    client.iter_movie_candidates(
        chapter_threshold=4, known_unreachable_folders=["/mnt/already-known/"]
    )


def test_omitting_known_unreachable_folders_still_computes_it_internally(
    monkeypatch,
) -> None:
    """The parameter is optional -- a caller that doesn't have a
    pre-computed value (or calls this method from a context that skipped
    the menu-level pre-check) must still get correct, safe behavior."""
    import auto_chapter.plex_client as plex_client_module

    fake_server = SimpleNamespace(
        friendlyName="TEST", library=SimpleNamespace(sections=lambda: [])
    )
    monkeypatch.setattr(plex_client_module, "PlexServer", lambda *a, **kw: fake_server)
    monkeypatch.setattr("auto_chapter.errors.time.sleep", lambda *_: None)

    config = PlexConfig(url="http://fake:32400", token="tok")
    client = plex_client_module.PlexClient(config, PathMapper([]))

    calls = {"count": 0}
    original = PathMapper.unreachable_enabled_folders

    def counting_wrapper(self, *a, **kw):
        calls["count"] += 1
        return original(self, *a, **kw)

    monkeypatch.setattr(PathMapper, "unreachable_enabled_folders", counting_wrapper)

    client.iter_movie_candidates(chapter_threshold=4)  # no known_unreachable_folders

    assert calls["count"] == 1


# ---------------------------------------------------------------------------
# iter_movie_candidates: title search reports the matches it leaves out
#
# Find by title used to say "No movie found" for a film that Plex does know
# but the app skips (chapters from another source, not an MKV, ...). The scan
# now records every skipped match with its reason -- but only for a title
# search; a full scan would list the whole library.
# ---------------------------------------------------------------------------


def _scan_client(
    monkeypatch,
    tmp_path: Path,
    movies: list,
    mappings: list[PathMapping] | None = None,
    plex_folders: list[str] | None = None,
):
    """A client over one fake movie library whose folder in Plex is "/share/"."""
    import auto_chapter.plex_client as plex_client_module

    if mappings is None:
        mappings = [PathMapping(plex="/share/", local=str(tmp_path) + "/")]
    mapper = PathMapper(mappings)
    fake_section = SimpleNamespace(
        type="movie",
        all=lambda: movies,
        locations=["/share/"] if plex_folders is None else plex_folders,
    )
    fake_server = SimpleNamespace(
        friendlyName="TEST", library=SimpleNamespace(sections=lambda: [fake_section])
    )
    monkeypatch.setattr(plex_client_module, "PlexServer", lambda *a, **kw: fake_server)
    monkeypatch.setattr("auto_chapter.errors.time.sleep", lambda *_: None)
    config = PlexConfig(url="http://fake:32400", token="tok")
    return plex_client_module.PlexClient(config, mapper)


def _chapters(*names: str) -> list:
    return [SimpleNamespace(title=n) for n in names]


def _search(client: PlexClient, title: str = "Heat", **kwargs):
    return client.iter_movie_candidates(
        chapter_threshold=4,
        title_filter=title,
        regenerate=True,
        min_duration_seconds=kwargs.pop("min_duration_seconds", 0),
        **kwargs,
    )


def test_title_search_reports_a_match_with_chapters_from_another_source(
    monkeypatch, tmp_path: Path
) -> None:
    from auto_chapter.plex_client import SkipReason

    (tmp_path / "Heat.mkv").write_bytes(b"fake")
    movie = _make_fake_movie("/share/Heat.mkv", "Heat")
    movie.chapters = _chapters("Opening", "Heist", "Chase", "Diner", "Finale", "End")
    client = _scan_client(monkeypatch, tmp_path, [movie])

    movies, stats = _search(client)

    assert movies == []
    assert [(s.title, s.reason, s.chapter_count) for s in stats.skipped] == [
        ("Heat", SkipReason.OTHER_CHAPTERS, 6)
    ]


def test_title_search_reports_a_match_that_is_not_an_mkv(monkeypatch, tmp_path: Path) -> None:
    from auto_chapter.plex_client import SkipReason

    (tmp_path / "Heat.mp4").write_bytes(b"fake")
    client = _scan_client(monkeypatch, tmp_path, [_make_fake_movie("/share/Heat.mp4", "Heat")])

    movies, stats = _search(client)

    assert movies == []
    assert [s.reason for s in stats.skipped] == [SkipReason.NOT_MKV]


def test_title_search_reports_a_match_whose_file_is_missing(monkeypatch, tmp_path: Path) -> None:
    from auto_chapter.plex_client import SkipReason

    client = _scan_client(monkeypatch, tmp_path, [_make_fake_movie("/share/Heat.mkv", "Heat")])

    movies, stats = _search(client)

    assert movies == []
    assert [s.reason for s in stats.skipped] == [SkipReason.FILE_MISSING]


def test_title_search_reports_a_match_under_an_unreachable_folder(
    monkeypatch, tmp_path: Path
) -> None:
    from auto_chapter.plex_client import SkipReason

    client = _scan_client(monkeypatch, tmp_path, [_make_fake_movie("/share/Heat.mkv", "Heat")])

    movies, stats = _search(client, known_unreachable_folders=[str(tmp_path) + "/"])

    assert movies == []
    assert [s.reason for s in stats.skipped] == [SkipReason.FOLDER_UNREACHABLE]


def test_title_search_reports_a_match_that_is_too_short(monkeypatch, tmp_path: Path) -> None:
    from auto_chapter.plex_client import SkipReason

    (tmp_path / "Heat.mkv").write_bytes(b"fake")
    client = _scan_client(monkeypatch, tmp_path, [_make_fake_movie("/share/Heat.mkv", "Heat")])

    movies, stats = _search(client, min_duration_seconds=3 * 3600)  # the film runs 2 h

    assert movies == []
    assert [s.reason for s in stats.skipped] == [SkipReason.TOO_SHORT]


def test_title_search_does_not_report_a_processable_match_or_other_titles(
    monkeypatch, tmp_path: Path
) -> None:
    (tmp_path / "Heat.mkv").write_bytes(b"fake")
    other = _make_fake_movie("/share/Other.mp4", "Other")  # not an MKV, but not a match
    client = _scan_client(
        monkeypatch, tmp_path, [_make_fake_movie("/share/Heat.mkv", "Heat"), other]
    )

    movies, stats = _search(client)

    assert [m.title for m in movies] == ["Heat"]
    assert stats.skipped == []


# The owner's Plex shows Hungarian titles (1114 of 1321 films have a different
# original title, 933 titles have accents). Plex Web finds "12 majom" for
# "Twelve Monkeys" and "4 hónap, 3 hét, 2 nap" for "honap"; the title search
# now does too. It stays a plain substring match: no typo tolerance.


def _hungarian_library(monkeypatch, tmp_path: Path) -> PlexClient:
    (tmp_path / "Monkeys.mkv").write_bytes(b"fake")
    (tmp_path / "Months.mkv").write_bytes(b"fake")
    (tmp_path / "Mad.mkv").write_bytes(b"fake")
    monkeys = _make_fake_movie("/share/Monkeys.mkv", "12 majom")
    monkeys.originalTitle = "Twelve Monkeys"
    months = _make_fake_movie("/share/Months.mkv", "4 hónap, 3 hét, 2 nap")
    months.originalTitle = "4 luni, 3 săptămâni și 2 zile"
    mad = _make_fake_movie("/share/Mad.mkv", "Őrült szív")  # no originalTitle at all
    return _scan_client(monkeypatch, tmp_path, [monkeys, months, mad])


@pytest.mark.parametrize(
    ("query", "found"),
    [
        ("Twelve Monkeys", "12 majom"),       # the original title
        ("twelve monk", "12 majom"),          # a part of it, any case
        ("honap", "4 hónap, 3 hét, 2 nap"),   # accents left out
        ("săptămâni", "4 hónap, 3 hét, 2 nap"),
        ("saptamani", "4 hónap, 3 hét, 2 nap"),
        ("orult", "Őrült szív"),              # ő, ű count as o, u
        ("ŐRÜLT", "Őrült szív"),
    ],
)
def test_title_search_finds_original_titles_and_ignores_accents(
    monkeypatch, tmp_path: Path, query: str, found: str
) -> None:
    movies, _stats = _search(_hungarian_library(monkeypatch, tmp_path), query)

    assert [m.title for m in movies] == [found]


def test_title_search_does_not_forgive_typos(monkeypatch, tmp_path: Path) -> None:
    movies, stats = _search(_hungarian_library(monkeypatch, tmp_path), "majon")

    assert movies == []
    assert stats.skipped == []


def test_title_search_still_offers_a_movie_with_its_own_auto_chapters(
    monkeypatch, tmp_path: Path
) -> None:
    """Regenerating the app's own chapters is the point of the title search."""
    (tmp_path / "Heat.mkv").write_bytes(b"fake")
    movie = _make_fake_movie("/share/Heat.mkv", "Heat")
    movie.chapters = _chapters(*[f"Auto Chapter {i}" for i in range(1, 8)])
    client = _scan_client(monkeypatch, tmp_path, [movie])

    movies, stats = _search(client)

    assert [m.title for m in movies] == ["Heat"]
    assert movies[0].has_auto_chapters is True
    assert stats.skipped == []


def test_a_full_scan_does_not_collect_skipped_matches(monkeypatch, tmp_path: Path) -> None:
    """Without a title filter the list would hold the whole library."""
    client = _scan_client(monkeypatch, tmp_path, [_make_fake_movie("/share/Heat.mkv", "Heat")])

    _movies, stats = client.iter_movie_candidates(chapter_threshold=4)

    assert stats.missing_files == 1
    assert stats.skipped == []


# ---------------------------------------------------------------------------
# Scans use a mapping only if its Plex path IS one of Plex's movie folders
#
# Movies are matched by the start of their path, so a hand-edited mapping like
# "/share/" (the parent of the real folder "/share/Filmek/") would quietly pull
# in every other movie library under it -- and the scans write into the files.
# ---------------------------------------------------------------------------


def _filmek_client(monkeypatch, tmp_path: Path, mapping_plex: str, **kwargs):
    (tmp_path / "Heat.mkv").write_bytes(b"fake")
    movie = _make_fake_movie("/share/Filmek/Heat.mkv", "Heat")
    mappings = [PathMapping(plex=mapping_plex, local=str(tmp_path) + "/")]
    return _scan_client(
        monkeypatch, tmp_path, [movie], mappings=mappings, plex_folders=["/share/Filmek/"], **kwargs
    )


def test_a_mapping_equal_to_a_plex_folder_is_used(monkeypatch, tmp_path: Path) -> None:
    client = _filmek_client(monkeypatch, tmp_path, "/share/Filmek/")

    movies, stats = client.iter_movie_candidates(chapter_threshold=4)

    assert [m.title for m in movies] == ["Heat"]
    assert stats.ignored_mappings == []


def test_a_parent_of_a_plex_folder_is_not_used_and_is_reported(
    monkeypatch, tmp_path: Path
) -> None:
    client = _filmek_client(monkeypatch, tmp_path, "/share/")

    movies, stats = client.iter_movie_candidates(chapter_threshold=4)

    assert movies == []
    assert stats.total_movies == 0  # the movie is out of scope, not "missing"
    assert stats.ignored_mappings == ["/share/"]


def test_a_sub_path_of_a_plex_folder_is_not_used_and_is_reported(
    monkeypatch, tmp_path: Path
) -> None:
    client = _filmek_client(monkeypatch, tmp_path, "/share/Filmek/Magyar/")

    movies, stats = client.iter_movie_candidates(chapter_threshold=4)

    assert movies == []
    assert stats.ignored_mappings == ["/share/Filmek/Magyar/"]


def test_a_sibling_name_that_only_starts_alike_is_not_used(monkeypatch, tmp_path: Path) -> None:
    """"/share/Film/" must not stand in for "/share/Filmek/"."""
    client = _filmek_client(monkeypatch, tmp_path, "/share/Film/")

    movies, stats = client.iter_movie_candidates(chapter_threshold=4)

    assert movies == []
    assert stats.ignored_mappings == ["/share/Film/"]


def test_a_good_mapping_keeps_working_next_to_a_bad_one(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "Heat.mkv").write_bytes(b"fake")
    movie = _make_fake_movie("/share/Filmek/Heat.mkv", "Heat")
    mappings = [
        PathMapping(plex="/share/", local="/nowhere/"),  # parent: not used
        PathMapping(plex="/share/Filmek/", local=str(tmp_path) + "/"),
    ]
    client = _scan_client(
        monkeypatch, tmp_path, [movie], mappings=mappings, plex_folders=["/share/Filmek/"]
    )

    movies, stats = client.iter_movie_candidates(chapter_threshold=4)

    assert [m.title for m in movies] == ["Heat"]
    assert movies[0].local_path == tmp_path / "Heat.mkv"  # translated by the good mapping
    assert stats.ignored_mappings == ["/share/"]


def test_a_disabled_bad_mapping_is_not_reported_as_ignored(monkeypatch, tmp_path: Path) -> None:
    """Disabled means the user switched it off: nothing was "ignored"."""
    (tmp_path / "Heat.mkv").write_bytes(b"fake")
    movie = _make_fake_movie("/share/Filmek/Heat.mkv", "Heat")
    mappings = [
        PathMapping(plex="/share/", local="/nowhere/", enabled=False),
        PathMapping(plex="/share/Filmek/", local=str(tmp_path) + "/"),
    ]
    client = _scan_client(
        monkeypatch, tmp_path, [movie], mappings=mappings, plex_folders=["/share/Filmek/"]
    )

    _movies, stats = client.iter_movie_candidates(chapter_threshold=4)

    assert stats.ignored_mappings == []


def test_when_plex_lists_no_folders_the_mappings_are_used_as_configured(
    monkeypatch, tmp_path: Path
) -> None:
    """Nothing to compare against, and no movies to match either: no false alarm."""
    client = _scan_client(monkeypatch, tmp_path, [], plex_folders=[])

    movies, stats = client.iter_movie_candidates(chapter_threshold=4)

    assert movies == []
    assert stats.ignored_mappings == []


def test_the_credits_scan_ignores_the_same_mappings(monkeypatch, tmp_path: Path) -> None:
    client = _filmek_client(monkeypatch, tmp_path, "/share/")

    movies, stats = client.iter_credits_update_candidates()

    assert movies == []
    assert stats.total_movies == 0
    assert stats.ignored_mappings == ["/share/"]
