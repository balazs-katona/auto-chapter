# auto-chapter — Details for advanced users

The everyday steps are in INSTALL.md. This file is for when you want to know
more, or do things by hand.

## Contents

- [Installing the programs yourself](#installing-the-programs-yourself)
- [How folder mapping works](#how-folder-mapping-works)
- [What happens to your files](#what-happens-to-your-files)
- [Safety features](#safety-features)
- [Editing the settings by hand](#editing-the-settings-by-hand)
- [Files it creates](#files-it-creates)

## Installing the programs yourself

The installer offers to install what is missing on Debian / Ubuntu, Arch,
Fedora and openSUSE, and on systems based on them. It shows the commands and
asks once. On other systems it tells you what to install. The programs:

- **uv** (Python package manager; it also brings Python if needed)
- **ffmpeg** and **ffprobe**, FFmpeg **4.4 or newer**: the detection needs
  the `blackdetect` and `scdet` filters (the installer and the app check them).
  It must also decode H.264 and HEVC video, which most movies use; the
  installer and the app warn if it can't. Fedora's own `ffmpeg-free` may not:
  the full ffmpeg is in RPM Fusion (on openSUSE: Packman).
- **mkvmerge** and **mkvextract** (from mkvtoolnix)
- **timeout** (GNU coreutils, already on practically every Linux system)

Arch Linux:
```
sudo pacman -S uv ffmpeg mkvtoolnix-cli
```

Debian / Ubuntu:
```
sudo apt install ffmpeg mkvtoolnix curl
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## How folder mapping works

auto-chapter asks Plex which movies there are, and Plex answers with its own
paths. It then reads and writes the files on this computer, where the same
folder may have a different path. A folder mapping connects the two:

- Plex runs on this computer: both paths are the same (for example
  `/home/user/Movies/`). Answer "On this machine" and every folder is mapped
  for you.
- Plex runs on another machine, for example a NAS: Plex sees
  `/share/Multimedia/Movies/`, this computer sees the mounted share as
  `/mnt/nas/Movies/`.

When you add or change a mapping, the installer and the app compare the
folder you typed with every movie Plex has in that folder, and show how many
are there. Settings → Folder mappings also lists Plex folders that have no
mapping yet, and marks a mapping whose Plex path Plex doesn't know.

An NFS share needs no login file; its line in `/etc/fstab`:
```
nas.local:/share/Multimedia  /mnt/nas  nfs  _netdev,nofail  0  0
```
The package is `nfs-utils` (Debian/Ubuntu: `nfs-common`, openSUSE:
`nfs-client`). In the SMB line of INSTALL.md, `uid`/`gid` make the files
yours so auto-chapter can write them, and `nofail` lets the computer start
when the NAS is off.

## What happens to your files

1. **Detection** reads the file and never changes it.
2. **Writing** makes a temporary copy with the chapters
   (`movie.mkv.autochapter.tmp`), then replaces the original with it in one
   step. If anything goes wrong (power cut, network drop, the program is
   killed), the original stays as it was; the half-written temporary file is
   removed by the next scan.
3. Video, audio and subtitles are copied bit for bit; only the file around
   them is rebuilt.
4. A hard link (one file under two names, for example in a download folder
   and in the Plex folder) is split: the Plex copy gets the chapters, the
   other copy stays as it was. Until that copy is deleted, the movie takes
   twice the space.

After writing, Plex is asked to analyze the movie, so the chapters show up
on every player. Chapter thumbnails appear too if "Generate chapter
thumbnails" is on in your Plex server's settings.

## Safety features

- Generate and Update process exactly the movies you chose; Find by title
  shows the chapters and asks before writing
- A movie that is being watched is skipped
- Needs free space for the movie's size + 5 GB before it starts
- The original is replaced only when the new file is complete and checked
- Leftovers of a killed run are recognised and removed by the next scan
- If Plex can't be reached, the app still starts and retries; Settings lets
  you check or fix the connection
- Folder checks have a time limit, so a network share that stopped
  answering can't lock up the app
- The required programs are checked at every start

## Editing the settings by hand

Usually not needed: Settings in the app covers folder mappings, minimum
movie length, chapter threshold and the Plex address and token. If you do
edit `config.yml` under `paths:`: the `plex:` path must be exactly a movie
folder as Plex lists it (Settings → Folder mappings shows which ones), and
the `local:` path must start with `/`. A missing `/` at the end is added for
you. A `plex:` path that isn't exactly one of Plex's folders (for example its
parent folder) is not used, and the app tells you so.

## Files it creates

In `~/.config/auto-chapter/` (or `$XDG_CONFIG_HOME/auto-chapter/`; readable
only by you):
- `config.yml` -- the settings
- `.env` -- the Plex token
- `logs/` -- the log file (one JSON line per event; only problems are logged)

In the app folder:
- `auto-chapter` -- the launcher (`~/.local/bin/auto-chapter` points to it)
- `.venv/` -- the Python environment (managed by uv)

If `~/.local/bin` isn't on your PATH yet, the installer adds one line to your
shell's settings file (`~/.bashrc`, `~/.zshrc` or fish's `config.fish`), with
a comment above it saying it came from the auto-chapter installer, so a new
terminal finds the `auto-chapter` command. When you remove auto-chapter, you
can delete those two lines too.
