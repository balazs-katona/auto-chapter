"""Parser for FFmpeg blackdetect filter output."""

import re
from dataclasses import dataclass

# Example line from blackdetect:
# [blackdetect @ 0x...] black_start:7.428 black_end:7.928 black_duration:0.500
_BLACK_END_RE = re.compile(r"black_end:([\d.]+)")


@dataclass(frozen=True, slots=True)
class BlackEvent:
    """A detected fade-to-black, with the timestamp where the new scene starts."""

    new_scene_time: float  # = black_end (when content resumes)


def parse_first_black(stderr: str, seek_offset: float) -> BlackEvent | None:
    """Return the first blackdetect event in stderr, or None.

    seek_offset is added to the timestamp because FFmpeg reports relative
    to the seek start when using -ss + trim.
    """
    match = _BLACK_END_RE.search(stderr)
    if not match:
        return None
    return BlackEvent(new_scene_time=seek_offset + float(match.group(1)))
