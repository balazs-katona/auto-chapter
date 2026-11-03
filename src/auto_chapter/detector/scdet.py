# SPDX-FileCopyrightText: 2026 Balazs Katona
# SPDX-License-Identifier: GPL-3.0-or-later
"""Parser for FFmpeg scdet filter output."""

import re
from dataclasses import dataclass

# scdet emits two adjacent metadata lines per detection:
#   [Parsed_scdet_2 @ 0x...] lavfi.scd.time: 16.241
#   [Parsed_scdet_2 @ 0x...] lavfi.scd.score: 38.726
_TIME_RE = re.compile(r"lavfi\.scd\.time:\s*([\d.]+)")
_SCORE_RE = re.compile(r"lavfi\.scd\.score:\s*([\d.]+)")


@dataclass(frozen=True, slots=True)
class SceneEvent:
    """A scdet-detected scene change with its score."""

    time: float
    score: float  # percentage 0-100


def parse_best_scene(stderr: str, seek_offset: float) -> SceneEvent | None:
    """Return the scene event with the highest score, or None.

    seek_offset is added to each time because FFmpeg reports relative
    to the seek start when using -ss + trim.
    """
    best: SceneEvent | None = None
    current_time: float | None = None
    current_score: float | None = None

    for line in stderr.split("\n"):
        time_match = _TIME_RE.search(line)
        if time_match:
            current_time = float(time_match.group(1))

        score_match = _SCORE_RE.search(line)
        if score_match:
            current_score = float(score_match.group(1))

        if current_time is not None and current_score is not None:
            if best is None or current_score > best.score:
                best = SceneEvent(time=seek_offset + current_time, score=current_score)
            current_time = None
            current_score = None

    return best
