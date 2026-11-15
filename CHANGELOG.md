# Changelog

The changes in each version of auto-chapter. Version numbers follow
[Semantic Versioning](https://semver.org/).

## 1.2.1 — 2026-11-15

- Changed: About moved into the Help menu (Help → About), so the main menu
  holds the work, and Help everything about the program.
- Docs: the README calls the detection "auto-chapter's advanced method".

## 1.2.0 — 2026-11-15

- Added: a Help menu -- what auto-chapter does, how to start, and the words
  it uses. Until you open it once, the main menu shows a short notice
  pointing to it.
- Added: before you choose how many or which movies to process, the app
  says what processing does: it writes into the movie files, takes a few
  minutes per movie, and needs free space for one more copy of the movie.
- Fixed: a short movie with only a few Auto Chapters came back as "needing
  Auto Chapters" at every Generate. Now a movie with Auto Chapters is done,
  however few they are (Find a movie by title can still redo it).
- Fixed: the installer refused Plex tokens that contain - or _.
- Fixed: running the installer again in the same terminal said "uv is
  missing" and offered to install uv once more.
- Changed: plainer words in many places -- "chapters of their own", the
  minimum length shown in the table, the recommended choice marked in
  Generate, Plex Pass mentioned where Plex End Credits are needed, correct
  singular and plural counts.
- Changed: Find sums up a long list of movies it can't process; when adding
  or changing a folder, a path that is not used is asked again, and a folder
  already mapped to another Plex folder is questioned.
- Docs: the End Credits chapter needs Plex Pass, with Plex's "Generate
  credits video markers" turned on.

## 1.1.2 — 2026-11-14

- Changed: the menus are easier to read -- the item names are bold, with
  an empty line between the items
- Added: Settings and the Generate choice say in one line under each item
  what it does
- Changed: when none of the movies needing Auto Chapters has Plex End
  Credits yet, Generate says how many movies wait and what you can do
- Added: when you stop the work and a tool takes long to close the movie
  file (on a network share this can take a minute), the program says what
  it is waiting for

## 1.1.1 — 2026-11-14

Please update: this fixes a safety problem.

- Fixed: pressing Ctrl+C twice while a movie was being written could, near
  the end of the writing, replace the movie with an incomplete file. Now a
  stop during writing always keeps the original file.

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
