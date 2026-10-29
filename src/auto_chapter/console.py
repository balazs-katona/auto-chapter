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

console = Console()
