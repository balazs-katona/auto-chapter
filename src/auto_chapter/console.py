# SPDX-FileCopyrightText: 2026 Balazs Katona
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared Rich Console instance.

Every module that prints to the terminal, or runs a Progress/Live display,
must use THIS console -- not create its own. Rich's Live rendering (used by
Progress) tracks cursor position and on-screen content per Console instance.
Mixing multiple separate Console objects (or Rich's own implicit global
default alongside our explicit instances) makes a Live display lose track
of what's actually on screen: the visible symptom is a brand new progress
bar line appearing every time an unrelated console.print() call interleaves
with an active Progress context, instead of the bar updating in place.
"""

from rich.console import Console
from rich.theme import Theme

# "note": the quiet lines -- hints, help lines under menu items, details.
# A fixed grey instead of Rich's "dim": many terminals don't show dim text at
# all (the owner's showed the help lines as bright as the item names, 1.2.1),
# while a 256-colour grey looks the same nearly everywhere. Write [note]...
# [/note] in markup, never [dim].
NOTE_STYLE = "grey62"

console = Console(theme=Theme({"note": NOTE_STYLE}))
