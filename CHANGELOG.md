# Changelog

The changes in each version of auto-chapter. Version numbers follow
[Semantic Versioning](https://semver.org/).

## 1.0.0 — release date

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
