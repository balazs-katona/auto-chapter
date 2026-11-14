# Changelog

The changes in each version of auto-chapter. Version numbers follow
[Semantic Versioning](https://semver.org/).

## 1.1.0 — 2026-11-14

- Added: the main menu says in one line under each item what it does, so
  it is clear without reading the documentation
- Added: the tests run automatically on GitHub for every change

## 1.0.1 — 2026-11-08

Documentation only: the program is the same as in 1.0.0, so there is no
need to update.

- Added: screenshots in the README (chapters in the Plex player, the menu,
  the chapter list of a real movie) and the detection method in four steps
- Added: forms for bug reports, test reports and ideas, and CONTRIBUTING.md
- Changed: clearer wording -- auto-chapter runs on Linux, Plex on this
  computer or on another machine; the movie files on this computer's disk
  or on a mounted network share

## 1.0.0 — 2026-11-08

First public release.

- Adds chapters to the MKV movies on a Plex server: FFmpeg finds the fades to
  black and scene changes, mkvmerge writes the chapters into the file
- An End Credits chapter where Plex has found the end credits (Plex End
  Credits)
- A numbered menu: Generate, Update, Find a movie by title, Settings
- An installer that installs the missing programs (on EndeavourOS / Arch,
  Ubuntu / Debian, Fedora and openSUSE) and sets up the Plex connection and
  the movie folders
- Safe writing: the original file is replaced only when the new one is
  complete and checked; a movie that is being watched is left alone
- Tested on EndeavourOS and Ubuntu
