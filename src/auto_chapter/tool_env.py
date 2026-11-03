# SPDX-FileCopyrightText: 2026 Balazs Katona
# SPDX-License-Identifier: GPL-3.0-or-later
"""The environment every external tool runs in.

Every subprocess the app starts (ffmpeg, ffprobe, mkvmerge, systemctl,
systemd-inhibit) gets `env=tool_env()`. mkvmerge and systemctl translate their
messages into the user's language (Hungarian on a hu_HU system), and the app
reads those messages and shows them next to its own English texts. ffmpeg is
not translated and always prints a decimal point, but it gets the same
environment, so no tool depends on the user's locale.

C.UTF-8, never plain "C": under "C" mkvmerge cannot handle a non-ASCII file
name ("Őrült nők.mkv" -> "no destination file name was given"). LC_ALL also
beats LANG and LANGUAGE.
"""

import os


def tool_env() -> dict[str, str]:
    """The user's environment, with English messages and UTF-8 for the tools."""
    return {**os.environ, "LC_ALL": "C.UTF-8"}
