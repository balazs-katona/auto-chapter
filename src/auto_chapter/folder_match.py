# SPDX-FileCopyrightText: 2026 Balazs Katona
# SPDX-License-Identifier: GPL-3.0-or-later
"""Does a folder on this computer hold the movies Plex has in one of its folders?

Asked when a folder mapping is added or changed, in the app and in the
installer. A wrong path -- another library's folder, or a typo that happens to
exist -- would otherwise show only as missing files later, and only for movies
that still need chapters (0.4.4 Ubuntu test). Not a sample: every movie Plex
has in the folder is looked up, against one listing of the folder here.
"""

import contextlib
import os
import sys
import threading
import unicodedata
from dataclasses import dataclass
from typing import Any

from auto_chapter.path_mapping import DEFAULT_REACHABILITY_TIMEOUT


@dataclass(frozen=True, slots=True)
class FolderMatch:
    """How many of the movies Plex has in a folder are in the folder here."""

    found: int
    total: int


def _nfc(name: str) -> str:
    # The same accented name can be stored composed or decomposed.
    return unicodedata.normalize("NFC", name)


def plex_entries(sections: list[Any], plex_folder: str) -> list[str]:
    """For each movie Plex has under `plex_folder`, the name right inside it:
    the movie's own folder, or its file if it sits directly in `plex_folder`.

    `plex_folder` ends with "/", as in Plex's folder list and in the mappings.
    Plex's plain section listing has the file paths, so this is one listing
    per section, not a request per movie.
    """
    names: list[str] = []
    for section in sections:
        if section.type != "movie":
            continue
        locations = {loc if loc.endswith("/") else loc + "/" for loc in section.locations or []}
        if plex_folder not in locations:
            continue
        for listed in section.all():
            listed._autoReload = False
            try:
                path = listed.media[0].parts[0].file
            except (IndexError, AttributeError):
                continue
            if path.startswith(plex_folder):
                name = path[len(plex_folder) :].split("/", 1)[0]
                if name:
                    names.append(_nfc(name))
    return names


def names_in_dir(path: str, timeout: float = DEFAULT_REACHABILITY_TIMEOUT) -> set[str] | None:
    """The names in folder `path`, or None if it couldn't be read in time.

    In a thread, like check_dir_with_timeout: a mount that's present but not
    responding can block a plain listing forever.
    """
    result: list[set[str]] = []

    def read() -> None:
        with contextlib.suppress(OSError):
            result.append({_nfc(n) for n in os.listdir(path)})

    thread = threading.Thread(target=read, daemon=True)
    thread.start()
    thread.join(timeout=timeout)
    return result[0] if result else None


def match_folder(server: Any, plex_folder: str, local_path: str) -> FolderMatch | None:
    """How many of the movies Plex has in `plex_folder` are in `local_path`.

    total is 0 when Plex has no movies there (nothing to compare). None if
    `local_path` couldn't be read in time.
    """
    entries = plex_entries(server.library.sections(), plex_folder)
    if not entries:
        return FolderMatch(found=0, total=0)
    here = names_in_dir(local_path)
    if here is None:
        return None
    return FolderMatch(found=sum(1 for e in entries if e in here), total=len(entries))


def main(argv: list[str]) -> int:
    """For the installer: `python -m auto_chapter.folder_match PLEX_FOLDER LOCAL_PATH`
    with the Plex address and token in PLEX_URL and PLEX_TOKEN.

    Prints "FOUND TOTAL", or nothing if it can't tell -- the installer then
    says nothing either, as the app does.
    """
    from plexapi.server import PlexServer

    try:
        server = PlexServer(os.environ["PLEX_URL"], os.environ["PLEX_TOKEN"], timeout=10)  # type: ignore[no-untyped-call]
        match = match_folder(server, argv[0], argv[1])
    except Exception:
        return 0
    if match is not None:
        print(match.found, match.total)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
