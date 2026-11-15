# SPDX-FileCopyrightText: 2026 Balazs Katona
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Help menu: what auto-chapter does, how to start, and the words it uses.

Owner's idea (1.2.0): one place a newcomer with only a Plex server can read,
instead of explanations scattered over the screens. Every sentence here must
match what the code does -- check it when the behaviour changes.
"""

import contextlib
from pathlib import Path

from rich.prompt import IntPrompt
from rich.table import Table

from auto_chapter.console import console

# The main menu shows a calm blue notice until Help was opened once; this
# file in the settings folder remembers that it was (owner chose "until opened
# once", not "every time": a notice seen every day stops being read).
HELP_SEEN_FILE = "help-seen"

WHAT_IT_DOES = """\
[bold]The problem[/bold]
Most movie files come without chapters, so in Plex all you can do is drag the
timeline. auto-chapter adds chapters to the MKV movies on your Plex server: on
any Plex player you can then jump from scene to scene, or straight to the end
credits.

[bold]How it finds the chapters[/bold]
The first chapter starts at 0:00. From minute 5 on, auto-chapter looks at the
movie in 5-minute parts and takes a fade to black, or, where there is none,
the strongest change of picture. From each chapter it found, it jumps 5 minutes
ahead. It stops 5 minutes before the Plex End Credits, or, without them, 15
minutes before the end of the movie. Where Plex has found the end credits,
they get a chapter of their own. The aim is chapters spread evenly through the
movie, about as many as a movie's own chapter list would have.

[bold]How it writes them[/bold]
The chapters go into the movie file itself, so other players (VLC, mpv, Kodi)
show them too. Nothing is re-encoded: picture and sound stay exactly the same.
A new copy of the movie is written next to it, checked, and only then replaces
the original; if anything goes wrong, the original stays as it was. Then Plex
is asked to read the movie again."""

HOW_TO_START = """\
[bold]The first time[/bold]
1. Choose 1) Generate Auto Chapters, then 1) for movies with Plex End Credits
   (recommended). Without Plex Pass, choose 2) for all movies.
2. Type a small number first, for example 2, to try it on a few movies.
3. When it is done, open one of those movies in Plex and look at its
   chapters. Plex may need a minute to read the file again.
4. If they look right, run Generate again for the rest.

While it works: Ctrl+C once stops after the current movie, twice stops at
once. Press s to switch the computer off when the work is done.

[bold]Later[/bold]
- New movies arrived: run Generate again. It picks only the movies that need
  chapters.
- Plex has found the End Credits of movies that already have Auto Chapters:
  run Update. It redoes their Auto Chapters, now with the End Credits chapter.
- One particular movie, or one to make again: Find a movie by title. It shows
  the chapters first and asks before writing.
- Movies are missing or skipped: check Settings → Folder mappings."""

WORDS: list[tuple[str, str]] = [
    ("Auto Chapters", 'The chapters auto-chapter makes: "Auto Chapter 1", "Auto Chapter 2", ...'),
    (
        "Chapters of their own",
        "Chapters a movie file already had, for example from the disc it was made from.",
    ),
    (
        "Chapter threshold",
        "From how many chapters of its own a movie is left alone. A movie with fewer "
        "gets Auto Chapters, which replace its own chapters. (Settings)",
    ),
    (
        "Plex End Credits",
        "The place where Plex found a movie's end credits. Plex finds them only with "
        'Plex Pass, with "Generate credits video markers" turned on '
        "(in Plex: Settings → Library).",
    ),
    (
        "End Credits chapter",
        'The last chapter, where the end credits start. It is called "Credits", '
        "as Plex calls it.",
    ),
    (
        "Update",
        "Redoes the Auto Chapters of movies that got them before Plex had found their "
        "End Credits, now with the End Credits chapter.",
    ),
    (
        "MKV",
        "A movie file type (.mkv). Chapters can be written into MKV files only; other "
        "files are skipped.",
    ),
    (
        "Minimum movie length",
        "Shorter videos, like trailers or extras, are skipped. (Settings)",
    ),
    (
        "Folder mapping",
        "Tells auto-chapter where a Plex movie folder is on this computer. Needed "
        "because Plex may run on another machine, where the same folder has another "
        "path. (Settings)",
    ),
    (
        "Mounted folder",
        "A folder of another machine, for example a NAS, that this computer shows as "
        "one of its own folders.",
    ),
    ("Plex URL", "Your Plex server's address, for example http://192.168.1.100:32400."),
    (
        "Plex token",
        "A code that lets auto-chapter talk to your Plex server. Keep it secret: never "
        "post it anywhere.",
    ),
]


def help_seen(settings_dir: Path | None) -> bool:
    """True once Help was opened (no settings folder known: don't nag)."""
    return settings_dir is None or (settings_dir / HELP_SEEN_FILE).exists()


def _remember_seen(settings_dir: Path | None) -> None:
    if settings_dir is None:
        return
    # If it can't be written, the notice simply shows again next time.
    with contextlib.suppress(OSError):
        (settings_dir / HELP_SEEN_FILE).touch()


def _words_table() -> Table:
    table = Table(show_header=False, box=None, padding=(0, 2, 1, 0))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column()
    for word, meaning in WORDS:
        table.add_row(word, meaning)
    return table


def _back() -> bool:
    """The "0) Back" line under a help page. False on Ctrl+C / Ctrl+D."""
    console.print("\n  [cyan]0[/cyan]) Back")
    try:
        IntPrompt.ask("Choose", choices=["0"], default=0, show_choices=True, show_default=False)
    except (KeyboardInterrupt, EOFError):
        console.print()
        return False
    return True


def help_flow(settings_dir: Path | None) -> None:
    """The Help menu. Opening it once hides the main menu's notice for good."""
    # Imported here: menu imports this module, and _show_options lives there.
    from auto_chapter.menu import _show_options

    _remember_seen(settings_dir)
    pages = [
        ("What auto-chapter does", WHAT_IT_DOES),
        ("How to start", HOW_TO_START),
        ("Words used in the app", None),
    ]
    while True:
        choice = _show_options(
            "Help",
            [title for title, _ in pages],
            allow_cancel=True,
            cancel_label="Back to main menu",
            notes=[
                "The problem it solves, and how",
                "The first time, and what to use later",
                "What the words on the screens mean",
            ],
        )
        if choice == 0:
            return
        title, text = pages[choice - 1]
        console.print(f"\n[bold]{title}[/bold]\n")
        console.print(_words_table() if text is None else text)
        if not _back():
            return
