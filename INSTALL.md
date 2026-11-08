# auto-chapter — Installation

auto-chapter adds chapters to the MKV movies on your Plex server, so you can
jump through a movie on any Plex player.

## Contents

- [Before you start](#before-you-start)
- [Install](#install)
- [Finding your Plex token](#finding-your-plex-token)
- [Mounting a network share](#mounting-a-network-share)
- [First use](#first-use)
- [Updating to a new version](#updating-to-a-new-version)
- [Starting over or removing it](#starting-over-or-removing-it)
- [Problems](#problems)

## Before you start

You need:
- a Linux computer (for example Ubuntu, Linux Mint, Debian, Fedora, openSUSE,
  Arch, EndeavourOS, Manjaro). Tested on EndeavourOS and Ubuntu. Fedora and
  openSUSE should work but haven't been tested yet -- reports are welcome:
  https://github.com/balazs-katona/auto-chapter/issues
- a Plex Media Server that you manage
- your movie folders reachable from this computer: on its own disk, or on a
  network share mounted here, for example from a NAS (see
  [Mounting a network share](#mounting-a-network-share))

The installer installs the other programs it needs (ffmpeg, mkvtoolnix, uv).
It asks first, and asks for your password.

**auto-chapter rewrites your movie files** to add the chapters. Keep a backup
of anything you can't replace, and try it on a few movies first.

## Install

1. Download `auto-chapter-vX.Y.Z.zip` from the
   [Releases page](https://github.com/balazs-katona/auto-chapter/releases/latest)
   (not the "Source code" files).
2. Unpack the zip and go into its folder:
   ```
   unzip auto-chapter-vX.Y.Z.zip
   cd auto-chapter-vX.Y.Z
   ```
3. Start the installer:
   ```
   ./install.sh
   ```
4. Answer its questions:
   - **Install them now?** -- only if a program is missing. Type `y`.
   - **Plex server** -- its address, for example `192.168.1.100:32400`
   - **Plex token** -- see [Finding your Plex token](#finding-your-plex-token)
   - **Where is Plex running?** -- on this computer, or on another machine
     (for example a NAS). For another machine it goes through Plex's movie
     folders: choose Use or Skip, then type where that folder is on this
     computer (for example `/mnt/nas/Movies`). It shows how many of Plex's
     movies it found there. If it found none, the path is probably wrong.

   You can skip folders and add them later in the app:
   Settings → Folder mappings.

When it's done, start the app:
```
auto-chapter
```
If the installer says to open a new terminal first, do that.

## Finding your Plex token

1. Open the Plex web app and go to any movie's page
2. Click the three dots, then **Get Info**
3. At the bottom of the window, click **View XML**
4. In the browser's address bar, go to the very end and copy the text after
   `X-Plex-Token=` (about 20 letters and digits)

Plex's own guide:
https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/

## Mounting a network share

If your movies are on another machine (for example a NAS), they have to be
a normal folder on this computer. The address your file manager shows
(`smb://nas.local/Multimedia`) is not a folder, so mount the share first.
For an SMB (Windows/Samba) share, in a terminal:

1. Install the package `cifs-utils` (Ubuntu/Debian: `sudo apt install
   cifs-utils`, Fedora: `sudo dnf install cifs-utils`, Arch: `sudo pacman -S
   cifs-utils`, openSUSE: `sudo zypper install cifs-utils`)
2. Make a folder for it: `sudo mkdir -p /mnt/nas`
3. Save your NAS login in a file only you can read: create `~/.smbcredentials`
   with these two lines, then run `chmod 600 ~/.smbcredentials`
   ```
   username=YOUR-NAS-USER
   password=YOUR-NAS-PASSWORD
   ```
4. Open `/etc/fstab` (`sudo nano /etc/fstab`) and add this line at the end.
   Change the NAS name, the share name and `you` to your own (`1000` is the
   usual user number; `id -u` and `id -g` show yours):
   ```
   //nas.local/Multimedia  /mnt/nas  cifs  credentials=/home/you/.smbcredentials,uid=1000,gid=1000,iocharset=utf8,_netdev,nofail  0  0
   ```
5. Mount it: `sudo systemctl daemon-reload && sudo mount /mnt/nas`

Your movies are now under `/mnt/nas/`, for example `/mnt/nas/Movies/`. Type
that path when the installer or the app asks where the folder is on this
computer. The share is mounted again after every restart.

## First use

1. In the main menu choose **1) Generate Auto Chapters**. You see how many
   movies need chapters.
2. Choose **1) for movies with Plex End Credits** (recommended).
3. Type a small number first (for example `2`) to try it. Enter alone does
   all of them.
4. Check in Plex that the chapters are there, then do the rest.

While it works: `Ctrl+C` once stops after the current movie, twice stops at
once. `s` switches the computer off when the work is done.

Later, when new movies arrive, just run **Generate** again. **Update** adds
the End Credits chapter to movies that have Plex End Credits now but didn't
when they got their chapters.

Everything you set in the installer can be changed in the app's **Settings**.

## Updating to a new version

Download the new `auto-chapter-vX.Y.Z.zip` from the
[Releases page](https://github.com/balazs-katona/auto-chapter/releases/latest).
Your settings are kept outside the app folder, so just replace the folder:
```
rm -rf auto-chapter-vOLD
unzip auto-chapter-vX.Y.Z.zip
cd auto-chapter-vX.Y.Z
./install.sh
```
The installer finds your settings and asks nothing again.

## Starting over or removing it

To set everything up again from the beginning:
```
rm -rf ~/.config/auto-chapter
./install.sh
```
To remove auto-chapter completely, delete the app folder, the
`~/.config/auto-chapter` folder and the file `~/.local/bin/auto-chapter`.

## Problems

**It takes a long time** -- each movie is read from start to end. Over the
network expect 5-15 minutes for a 2-hour movie; a cable is faster than WiFi.

**"Not enough disk space"** -- while it works, the movie's drive needs room
for one more copy of the movie, plus 5 GB.

**"The movie is currently being streamed"** -- someone is watching it in Plex.
Try again when they've stopped.

**"Not connected to Plex"** -- check that Plex is running, and its address and
token in Settings → Plex connection.

**"No movie found matching ..."** -- check the spelling, or type a shorter
part of the title.

More details (installing the programs yourself, how files are written,
editing the settings file by hand): ADVANCED.md.
