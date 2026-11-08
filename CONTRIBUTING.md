# Contributing to auto-chapter

Thanks for your interest! Bug reports, test reports from other Linux systems
and ideas are all welcome.

## Contents

- [Reporting a problem](#reporting-a-problem)
- [Testing on another Linux system](#testing-on-another-linux-system)
- [Ideas](#ideas)
- [Changing the code](#changing-the-code)

## Reporting a problem

Open a [new issue](https://github.com/balazs-katona/auto-chapter/issues/new/choose)
and choose **Bug report**. The form asks for:

- your auto-chapter version (shown at the top of the menu) and Linux system;
- what you did, what happened, and what you expected;
- what the app or the installer printed;
- if you can, the last lines of the log file, normally
  `~/.config/auto-chapter/logs/auto-chapter.log`.

**Never paste your Plex token** (it is in `~/.config/auto-chapter/.env`).
The log file and the terminal output can contain movie names, folder paths
and your Plex server's address: remove anything you'd rather keep private.

## Testing on another Linux system

auto-chapter has been tested on EndeavourOS and Ubuntu. The installer can
also install the programs it needs on Fedora and openSUSE (and systems based
on them), but these haven't been tested yet -- watch for the FFmpeg decoder
warning there: their default FFmpeg often can't decode H.264 and HEVC video
(see "Installing the programs yourself" in ADVANCED.md). If you try it on one of them, please open a
[new issue](https://github.com/balazs-katona/auto-chapter/issues/new/choose)
and choose **Test report** -- a short "it works" helps too.

## Ideas

Choose **Idea** on the [new issue](https://github.com/balazs-katona/auto-chapter/issues/new/choose)
page. auto-chapter aims to stay simple for people who don't live in a
terminal, so tell us what the idea would make easier for you.

## Changing the code

Please open an issue first, so we can agree on the change before you put
time into it.

Development setup (Python 3.13+, [uv](https://docs.astral.sh/uv/)):

```bash
uv sync --extra dev            # the test and lint tools (pytest, ruff, mypy)
uv run pytest                  # all tests must pass
uv run ruff check src tests    # lint
uv run mypy src                # type check (strict)
```

- One topic per pull request, with tests for what it changes.
- Format only the lines you changed: don't reformat whole files.
- New files start with the license header (with your name):
  ```python
  # SPDX-FileCopyrightText: 2026 Your Name
  # SPDX-License-Identifier: GPL-3.0-or-later
  ```
- Messages the user sees are plain, short and friendly: say what happened
  and what to do, never show a stack trace.

auto-chapter is licensed under the GNU General Public License v3.0 or later;
by contributing, you agree that your contribution is licensed under it too.
