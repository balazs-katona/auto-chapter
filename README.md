# auto-chapter

**v0.2.39**

Automatic chapter marker generation for MKV movies on a Plex server.

Detects scene transitions using FFmpeg (blackdetect + scdet) and writes
chapter markers into MKV files via mkvmerge -- single-pass write + remux
for full TV client compatibility (Samsung Tizen, LG WebOS, and others).

## Quick Start

```bash
chmod +x install.sh
./install.sh
```

The installer checks required tools, asks for your Plex URL and token,
walks you through mapping your movie folders, and sets everything up. Zero
folders mapped is fine too -- add them later from the app's Settings menu.
When done, type `auto-chapter` from any directory to start.

See **INSTALL.md** for the full installation guide and menu walkthrough.

## Requirements

- Python 3.13+ (managed by uv)
- ffmpeg, ffprobe (FFmpeg 4.4 or newer: the detection needs the `blackdetect` and `scdet` filters)
- mkvmerge, mkvextract (from mkvtoolnix)
- A Plex Media Server with an auth token

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
  and processes them: alphabetically (you choose how many; Enter means all of
  them) or a manual pick from the list. Writing starts right after that
  choice, with no extra question. Movies whose End Credits Plex has already
  detected are the priority: if your selection includes movies without them,
  you're asked how to handle those -- leave them for later, or process them
  anyway (they then need an extra **Update** pass once Plex has found their
  credits).
- **Update** catches movies that got their chapters before Plex had detected
  the End Credits marker, and regenerates just those with the credits
  chapter added.
- **Find a movie by title** lets you (re)process one specific movie by name,
  including one that already has Auto Chapters. If a movie matches but can't
  be processed (for example it probably already has a useful chapter list from another
  source), you are told why.
- **Settings** covers folder mappings (add/change/remove/enable/disable), minimum
  movie length, the chapter-count threshold that decides what's "already
  done", and your Plex connection.

## Features

- Interactive menu, no command-line flags to remember
- Standard-compliant Matroska chapters (RFC 9559)
- Atomic write (temp file + `os.replace()`) -- the original file is never
  touched until the new one is fully written and verified; safe to
  interrupt at any point
- Active stream protection (won't modify a file currently being watched)
- Live progress bar during detection and during the mkvmerge remux
- Plex Analyze auto-triggered after writing
- Confirmation required before any write -- batch size, single-movie
  preview, or an explicit list pick, depending on how you got there
- Friendly error messages -- network issues, missing tools, disk space,
  permissions, and so on are translated into a short, specific sentence,
  never a raw stack trace
- Resilient to a temporarily unreachable Plex server or network share --
  keeps running, retries automatically where it can, and always leaves you
  a way to fix it (never gets stuck with no way out)
- Required external tools (ffmpeg, mkvmerge, etc.) are re-checked on every
  launch, not just at install time
- Runs fine with zero folders mapped; tells you where to add one instead of
  crashing
- Keeps the computer from going to sleep because it was idle while it works
  (systemd; skipped where it is not available)

## Development

```bash
uv run pytest                  # run tests
uv run ruff check src/         # lint
uv run mypy src/                # type check
```
