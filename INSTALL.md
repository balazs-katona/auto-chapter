# auto-chapter — Installation Guide

Automatic chapter marker generation for MKV movies on a Plex server.

## What This Tool Does

Finds MKV files on your Plex server without proper navigation chapters,
generates chapter markers by analyzing the video for scene transitions
(fade-to-black moments + scene changes), and writes them into the MKV files.

After processing, Plex Analyze is triggered so the new chapters appear in
all clients with thumbnails.

## Requirements

You need these tools installed on your system:

- **uv** (Python package manager)
- **ffmpeg** and **ffprobe** (video analysis; FFmpeg **4.4 or newer** -- the detection
  needs the `blackdetect` and `scdet` filters, and the installer checks for them)
- **mkvmerge** and **mkvextract** (from mkvtoolnix)
- **timeout** (part of GNU coreutils -- already on virtually every Linux system)

Arch Linux:
```
sudo pacman -S uv ffmpeg mkvtoolnix-cli
```

Debian / Ubuntu:
```
sudo apt install ffmpeg mkvtoolnix
curl -LsSf https://astral.sh/uv/install.sh | sh
```

You also need:
- A Plex Media Server you have admin access to
- The MKV files accessible from this machine (locally or via mounted network share)

## Installation

### 1. Extract the package

```
cd ~/projects   # or wherever you want it
unzip auto-chapter-vX.Y.Z.zip
cd auto-chapter-vX.Y.Z
```

### 2. Make the installer executable and run it

```
chmod +x install.sh
./install.sh
```

The installer will ask you for:

1. **Plex server address** -- e.g. `192.168.1.100:32400`
2. **Plex authentication token** -- see below how to find it
3. **Folder mapping** -- one Plex-side path and one local path per movie
   folder (see below)

You can skip folder mapping entirely and add folders later from
Settings inside the app -- the installer won't force you to configure
everything up front.

#### Why path mapping?

Plex and auto-chapter may see the same files at different paths.

**Example 1 -- Plex runs on the same machine:**
- Plex path: `/home/user/Movies/`
- Local path: `/home/user/Movies/`
- Same path; the installer offers to auto-map these for you.

**Example 2 -- Plex runs on a NAS, you mount it via SMB:**
- Plex (on NAS) sees: `/share/Multimedia/Movies/`
- This machine (via mount) sees: `/mnt/nas/Movies/`
- Different paths, mapping is essential.

auto-chapter uses the Plex API to find movies (which returns Plex-side paths)
but reads/writes files locally (which needs the local path). The mapping
translates between the two. The installer auto-discovers your Plex movie
folders and lets you map or skip each one. A skipped folder can be mapped later
in Settings → Folder mappings, which also tells you which Plex folders still
have no mapping.

#### How to find your Plex token

1. Open the Plex web app
2. Play any movie
3. Click the three dots on the movie's info screen -> 'Get Info'
4. At the bottom of the popup, click 'View XML'
5. In the URL of the XML page, find the value after `X-Plex-Token=`

Official guide:
https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/

### 3. After installation

The installer:
- Saves your configuration to `~/.config/auto-chapter/` (`config.yml` and
  `.env`), outside the app folder, so it survives an update
- Installs an `auto-chapter` command (a symlink in `~/.local/bin`, on PATH
  for every shell -- not a shell alias, so it works right away)
- Asks if you want to start it now

Whether you say yes or no, the command is ready immediately -- no need to
open a new terminal or reload your shell config (unless the installer
specifically tells you `~/.local/bin` isn't on your PATH yet, which it will
handle with its own instructions if so). Just type:

```
auto-chapter
```

That's the only command you need to remember.

## Usage

When you run `auto-chapter`, an interactive menu appears:

```
Main menu
  0) Quit
  1) Generate Auto Chapters
  2) Update Auto Chapters with missing Plex End Credits
  3) Find a movie by title
  4) Settings
  5) About
```

### Recommended first time

1. **Pick option 1 (Generate Auto Chapters)** -- shows a stats panel: how
   many movies total, how many already have chapters (ours or from another
   source), and how many need processing.
2. Choose **alphabetical order** or **manual pick** from the list.
3. When asked how many to process, you can start small (type a number) to
   see the result on a couple of movies before committing to the rest --
   Enter alone processes all of them.
4. Confirm when prompted. Progress is shown per movie; `Ctrl+C` once
   finishes the current movie then stops, twice stops immediately.

### Later, when new movies arrive

Same as above -- **Generate Auto Chapters** only shows movies that still
need it, so re-running periodically is the normal way to keep things
current. **Update Auto Chapters with missing Plex End Credits** catches
movies that were processed before Plex had detected their credits marker,
whenever you're ready to top those up too.

### Configuring things later

Everything from the installer (folder mappings, minimum movie length,
chapter-count threshold, Plex URL/token) can also be changed from
**Settings** inside the app -- no need to re-run the installer or hand-edit
`config.yml` for routine changes.

## What Happens to My Files

1. **Detection** reads the file but never modifies it.
2. **Writing** creates a temporary copy (`movie.mkv.autochapter.tmp`) with
   chapters embedded, then atomically replaces the original. If anything
   goes wrong (power loss, network drop, a kill signal), the original stays
   intact -- only the half-written temp file is left behind, and the next
   scan cleans it up automatically.
3. The new file contains identical video, audio, and subtitle streams
   (bit-for-bit). Only the container structure is rebuilt.
4. Hard links are preserved (a file shared between e.g. Radarr and Plex stays
   shared after processing).

## Safety Features

- **Confirmation required before any write** -- how many movies to process,
  or an explicit preview for a single movie, depending on the flow
- **Active stream check**: a movie currently being watched is skipped
- **Disk space check**: requires file size + 5 GB free before starting
- **Atomic write**: temp file -> replace, interruption-safe
- **Stale temp file cleanup**: if a previous run was killed mid-write, the
  leftover is identified and removed automatically on the next scan
- **Doesn't crash if Plex is unreachable**: starts normally, retries
  automatically when you try to use it, and Settings always gives you a
  way to check or fix the connection
- **Doesn't get stuck on a bad network share**: folder checks are time-
  bounded, so even a mount that's stopped responding can't lock you out of
  fixing it
- **Required tools re-checked on every launch**: catches a missing
  ffmpeg/mkvmerge/etc. right away with a clear message, not partway
  through processing

## Troubleshooting

**The installer keeps asking for the Plex token**
Just paste it again -- the prompt loops until it gets something that looks
like a valid token (roughly 20 alphanumeric characters). Make sure you
copied only the token itself, not the surrounding URL.

**"No movie found matching ..."**
The search looks for a part of the title shown in Plex or of the original
title, with or without accents. It does not forgive typos: check the
spelling, or type a shorter part of the title.

**Detection takes a long time**
Detection runs FFmpeg over the file. Over the network, expect about 5-15
minutes for a 2-hour movie (measured: about 10 minutes on a home LAN to a
NAS); WiFi is slower. Use ethernet, or process movies during off-hours.

**"Not enough disk space"**
The destination drive needs enough room for an extra full-size copy of the
movie during remux. Free up space and retry.

**"The movie is currently being streamed"**
Someone is watching this movie via Plex. Wait for them to stop, then retry.

**Plex is slow to respond right after being idle**
This is normal -- Plex sometimes needs a moment to warm up metadata after
being idle. auto-chapter retries automatically a couple of times before
reporting an error.

## Updating to a new version

Your settings and token are in `~/.config/auto-chapter/`, not in the app
folder, so you can simply replace the app folder:

```
cd ~/projects
rm -rf auto-chapter-vOLD          # the folder of the version you had
unzip auto-chapter-vX.Y.Z.zip
cd auto-chapter-vX.Y.Z
./install.sh
```

`./install.sh` finds your existing settings and asks nothing again; it only
rebuilds the Python environment and the `auto-chapter` command. Until you run
it, the `auto-chapter` command does not work (it points into the folder you
just replaced).

Coming from version 0.2.28 or older (settings still in the app folder)? Then
unpack the new version *over* the old folder this one time, without deleting
it, and run `./install.sh`: it moves `config.yml` and `.env` to
`~/.config/auto-chapter/` (copy, check, then remove the old ones).

## Reconfiguring

For routine changes (folder mappings, Plex URL/token, minimum movie length,
chapter threshold), use **Settings** inside the app -- no restart or
reinstall needed.

Running `./install.sh` again keeps your existing settings. To start over from
scratch, delete the settings folder first, then run the installer:

```
rm -rf ~/.config/auto-chapter
./install.sh
```

If you do edit the folder paths under `paths:` by hand: the `plex:` path must be
exactly a movie folder as Plex lists it (Settings > Folder mappings shows which
ones are), and the `local:` path must start with `/`. A missing `/` at the end
is added for you. A `plex:` path that is not exactly one of Plex's folders (for
example its parent folder) is not used, and the app tells you so.

## Files Created

In `~/.config/auto-chapter/` (or `$XDG_CONFIG_HOME/auto-chapter/`; the folder is
readable only by you):
- `config.yml` -- your configuration
- `.env` -- your Plex token (chmod 600, never committed)
- `logs/` -- structured log file (JSON, one line per event; optional)

In the app folder:
- `auto-chapter` -- the launcher script (symlinked from `~/.local/bin`)
- `.venv/` -- Python virtual environment (managed by uv)
