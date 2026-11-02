# auto-chapter

**v0.4.2**

Automatic chapter marker generation for MKV movies on a Plex server.

Detects scene transitions using FFmpeg (blackdetect + scdet) and writes
chapter markers into the MKV files via mkvmerge. The whole file is rewritten
in one pass: that is the most reliable way to make the new files work well
on any device.

## Quick Start

```bash
chmod +x install.sh
./install.sh
```

The installer checks required tools and offers to install the missing ones,
asks for your Plex URL and token,
walks you through mapping your movie folders, and sets everything up. Zero
folders mapped is fine too -- add them later from the app's Settings menu.
When done, type `auto-chapter` from any directory to start.

See **INSTALL.md** for the full installation guide and menu walkthrough.

## Requirements

- Linux with systemd. On Debian/Ubuntu, Arch, Fedora and openSUSE (and
  systems based on them) the installer can install the tools below for you.
- [uv](https://docs.astral.sh/uv/) -- it also provides Python 3.13+ if your
  system has none
- ffmpeg, ffprobe (FFmpeg 4.4 or newer: the detection needs the `blackdetect` and `scdet` filters)
- mkvmerge, mkvextract (from mkvtoolnix)
- A Plex Media Server with an auth token
- The movie files reachable from this computer: on its own disk, or a NAS
  share mounted here

## Your files

auto-chapter **rewrites your MKV files** to add the chapters. Video, audio and
subtitles are copied unchanged (no re-encoding); only the file around them is
rebuilt. The original is replaced only after the new file is complete and
checked, so an interruption never leaves a half-written movie. It does not
touch a movie that is being watched, and needs free space for one extra copy
of the movie plus 5 GB while it works.

If a movie is a hard link (one file under two names, for example in a
download folder and in the Plex folder), the link is split: the Plex copy
gets the chapters, the other copy stays as it was. Until that other copy is
deleted, the movie takes twice the space.

Still, it writes into your movie collection: **keep a backup of anything you
can't replace**, and try it on a few movies first. Details: "What Happens to
My Files" in INSTALL.md.

## What it does

Once running, `auto-chapter` gives you a numbered menu:

```
Main menu
  0) Quit
  1) Generate Auto Chapters
  2) Update Auto Chapters with missing Plex End Credits
  3) Find a movie by title
  4) Settings
  5) About
```

- **Generate** scans your mapped folders, shows how many movies need chapters,
  and processes them in alphabetical order -- **only the movies with Plex
  End Credits** (end credits Plex has already detected; the first choice),
  or all of them -- or a manual pick from the list. You choose how many;
  Enter means all of them. Writing starts right after that choice, with no
  extra question. Movies without Plex End Credits get no End Credits chapter
  now; once Plex has found their end credits, run the **Update** menu
  yourself to add it. If a manual pick includes such movies, you're asked
  how to handle them.
- **Update** catches movies that got their chapters before Plex had detected
  their Plex End Credits, and regenerates just those with the End Credits
  chapter added.
- **Find a movie by title** lets you (re)process one specific movie by name,
  including one that already has Auto Chapters. You see the chapters first
  and decide whether to write them. If a movie matches but can't be
  processed (for example it probably already has a useful chapter list from
  another source), you are told why.
- **Settings** covers folder mappings (add/change/remove/enable/disable),
  minimum movie length, the chapter threshold (from how many chapters on a
  movie is assumed to have a useful chapter list already), and your Plex
  connection.

While movies are being processed, `Ctrl+C` once finishes the current movie
and then stops, twice stops at once. Press `s` to switch the computer off
when the work is done (`s` again cancels).

## Features

- Interactive menu, no command-line flags to remember
- Standard-compliant Matroska chapters (RFC 9559)
- Atomic write (temp file + `os.replace()`) -- the original file is never
  touched until the new one is fully written and checked; safe to
  interrupt at any point
- Active stream protection (won't modify a file currently being watched)
- Live progress bar during detection and during the mkvmerge remux
- Plex Analyze auto-triggered after writing
- You decide what gets written: Generate and Update process exactly the
  movies you chose; Find by title shows the chapters and asks first
- Friendly error messages -- network issues, missing tools, disk space,
  permissions, and so on are translated into a short, specific sentence,
  never a raw stack trace (the details go to the log file)
- Resilient to a temporarily unreachable Plex server or network share --
  keeps running, retries automatically where it can, and always leaves you
  a way to fix it (never gets stuck with no way out)
- Required external tools (ffmpeg, mkvmerge, etc.) are re-checked on every
  launch, not just at install time
- Runs fine with zero folders mapped; tells you where to add one instead of
  crashing
- Keeps the computer from going to sleep because it was idle while it works
  (systemd; skipped where it is not available)
- Optional power-off when a batch is done (`s` key)

## Development

```bash
uv sync --extra dev            # adds the test and lint tools (pytest, ruff, mypy)
uv run pytest                  # run tests
uv run ruff check src tests    # lint
uv run mypy src                # type check
```
