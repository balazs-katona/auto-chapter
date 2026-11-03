# auto-chapter — Installation Guide

Automatic chapter marker generation for MKV movies on a Plex server.

## What This Tool Does

Finds MKV files on your Plex server without proper navigation chapters,
generates chapter markers by analyzing the video for scene transitions
(fade-to-black moments + scene changes), and writes them into the MKV files.

After processing, Plex Analyze is triggered so the new chapters appear in
all clients. Chapter thumbnails appear too if "Generate chapter thumbnails"
is turned on in your Plex server's settings.

## Requirements

These tools are needed:

- **uv** (Python package manager)
- **ffmpeg** and **ffprobe** (video analysis; FFmpeg **4.4 or newer** -- the detection
  needs the `blackdetect` and `scdet` filters, and the installer checks for them)
- **mkvmerge** and **mkvextract** (from mkvtoolnix)
- **timeout** (part of GNU coreutils -- already on virtually every Linux system)

**The installer finds what is missing and offers to install it for you** on
Debian / Ubuntu, Arch, Fedora and openSUSE, and on systems based on them
(Linux Mint, EndeavourOS, Manjaro, ...). It shows the commands, asks once,
and asks for your password (`sudo`) if you say yes. On other systems it
tells you what to install.

To install them yourself instead:

Arch Linux:
```
sudo pacman -S uv ffmpeg mkvtoolnix-cli
```

Debian / Ubuntu:
```
sudo apt install ffmpeg mkvtoolnix curl
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

0. **Only if a tool is missing:** "Install them now? (y/n)" -- see
   Requirements above
1. **Plex server address** -- e.g. `192.168.1.100:32400`
2. **Plex authentication token** -- see below how to find it
3. **Where Plex runs** -- on this computer, or on another machine such as a
   NAS. On another machine it then goes through Plex's movie folders one by
   one: use or skip each, and type where it is mounted on this computer
   (see "Why path mapping?" below). It then shows how many of the movies
   Plex has in that folder it found there -- if none, the path is probably
   wrong, and you can type another one

You can skip folder mapping entirely and add folders later from
Settings inside the app -- the installer won't force you to configure
everything up front.

#### Why path mapping?

Plex and auto-chapter may see the same files at different paths.

**Example 1 -- Plex runs on the same machine:**
- Plex path: `/home/user/Movies/`
- Local path: `/home/user/Movies/`
- Same path: answer "On this machine" and the installer maps every folder
  for you, without asking.

**Example 2 -- Plex runs on a NAS, you mount it via SMB:**
- Plex (on NAS) sees: `/share/Multimedia/Movies/`
- This machine (via mount) sees: `/mnt/nas/Movies/`
- Different paths, mapping is essential.

auto-chapter uses the Plex API to find movies (which returns Plex-side paths)
but reads/writes files locally (which needs the local path). The mapping
translates between the two. The installer auto-discovers your Plex movie
folders and lets you map or skip each one. A skipped folder can be mapped later
in Settings → Folder mappings, which also tells you which Plex folders still
have no mapping. Adding or changing a mapping there also shows how many of
Plex's movies are in the folder you typed.

#### How to find your Plex token

1. Open the Plex web app and go to any movie's info page
2. Click the three dots on the movie, then 'Get Info'
3. At the bottom of the popup, click 'View XML'
4. In the browser address bar, go to the very end of the URL: copy the
   value after `X-Plex-Token=` (about 20 letters and digits)

Official guide:
https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/

### 3. After installation

The installer:
- Saves your configuration to `~/.config/auto-chapter/` (`config.yml` and
  `.env`), outside the app folder, so it survives an update
- Installs an `auto-chapter` command (a symlink in `~/.local/bin`, on PATH
  for every shell -- not a shell alias)
- Asks if you want to start it now

Usually the command works right away. If your terminal doesn't know it yet,
the installer says what to do: open a new terminal, or add the one line it
shows to your shell config. Then just type:

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
2. Choose **1) for movies with Plex End Credits** (the ones Plex has already
   found the end credits for -- recommended), **2) for all movies**, or
   **3) manual pick** from the list.
3. When asked how many to process, you can start small (type a number) to
   see the result on a couple of movies before committing to the rest --
   Enter alone processes all of them.
4. Writing starts right after that choice -- there is no further question.
   Progress is shown per movie; `Ctrl+C` once finishes the current movie
   then stops, twice stops immediately. Press `s` to switch the computer off
   when the batch is done (`s` again cancels; a 30-second countdown that
   `Ctrl+C` stops comes first).

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
4. A hard link (one file under two names, for example in a download folder
   and in the Plex folder) is split: the new file replaces the old one at
   the Plex path, the other copy stays as it was. Until that other copy is
   deleted, the movie takes twice the space.

## Safety Features

- **You decide what gets written** -- Generate and Update process exactly
  the movies you chose (and how many); Find by title shows the chapters and
  asks before writing
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

**"No answer from Plex yet; retrying..."**
Usually Plex just needs a moment after being idle. auto-chapter tries
three times before it reports an error. If it keeps happening, check that
the Plex server is running and its address is right (Settings → Plex
connection).

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
