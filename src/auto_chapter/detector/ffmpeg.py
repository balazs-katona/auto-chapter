# SPDX-FileCopyrightText: 2026 Balazs Katona
# SPDX-License-Identifier: GPL-3.0-or-later
"""Low-level FFmpeg/ffprobe subprocess wrappers."""

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from auto_chapter.batch_control import register_subprocess
from auto_chapter.tool_env import tool_env


@dataclass(frozen=True, slots=True)
class FFmpegResult:
    """Output from an FFmpeg invocation."""

    stdout: str
    stderr: str
    returncode: int


# The detection runs these two ffmpeg filters. scdet is the newer one: it is in
# FFmpeg 4.4 and later, but not in 4.2. A build can also lack a filter if it was
# compiled without it, so the filter list is checked, not the version number.
REQUIRED_FILTERS = ("blackdetect", "scdet")

# One line of `ffmpeg -filters`. The flag column is 3 characters in older FFmpeg
# (" T.C blackdetect  V->V  ...") and 2 in FFmpeg 9 (" .S blackdetect  V->V  ...").
_FILTER_LINE = re.compile(r"^\s*[TSC.]{2,3}\s+(\w\S*)\s")


def ffmpeg_filter_problem() -> str | None:
    """Ask ffmpeg for its filter list. Returns None if every filter we need is
    there, otherwise a ready-to-show sentence saying what is wrong."""
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            env=tool_env(),
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"Could not ask ffmpeg for its filter list ({type(e).__name__}: {e})."
    if proc.returncode != 0:
        return f"Could not ask ffmpeg for its filter list (exit code {proc.returncode})."

    available = {
        m.group(1) for line in proc.stdout.splitlines() if (m := _FILTER_LINE.match(line))
    }
    if not available:
        # Not one filter line was recognised: the output looks different from
        # what this parser knows. That says nothing about the filters, so do not
        # claim they are missing -- the app would be blocked by our own mistake.
        return None
    missing = [name for name in REQUIRED_FILTERS if name not in available]
    if missing:
        return (
            f"This ffmpeg has no {' / '.join(missing)} filter. auto-chapter needs "
            f"{' and '.join(REQUIRED_FILTERS)}; FFmpeg 4.4 or newer has both. "
            "Update ffmpeg (or install a build that includes them)."
        )
    return None


# Most movies are H.264 or HEVC. Some builds can't decode them -- Fedora's
# ffmpeg-free is the known case (the full ffmpeg comes from RPM Fusion; on
# openSUSE from Packman). Every detection would then fail.
REQUIRED_DECODERS = {"h264": "H.264", "hevc": "HEVC"}

# One line of `ffmpeg -codecs`: flags ("D" first = decoding supported), name,
# description, then "(decoders: ...)" when a decoder has another name.
# " DEV.LS h264   H.264 / AVC ... (decoders: h264 h264_qsv h264_cuvid) (encoders: ...)"
_CODEC_LINE = re.compile(r"^\s*([D.])[E.][VASDT.]\S*\s+(\S+)\s*(.*)$")
_DECODER_LIST = re.compile(r"\(decoders: ([^)]*)\)")
# Decoders that need particular graphics hardware; a codec with only these is
# no use on most computers.
_HARDWARE_DECODER = re.compile(r"_(cuvid|qsv|v4l2m2m|amf|mediacodec|rkmpp|mmal|oh)$")


def ffmpeg_decoder_problem() -> str | None:
    """Ask ffmpeg whether it can decode H.264 and HEVC video. Returns None if
    it can (or if its answer can't be read), otherwise a ready-to-show
    sentence. A warning, not a stop: movies in other formats still work."""
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-codecs"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            env=tool_env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None

    usable: dict[str, bool] = {}
    for line in proc.stdout.splitlines():
        m = _CODEC_LINE.match(line)
        if not m:
            continue
        flag, name, rest = m.groups()
        listed = _DECODER_LIST.search(rest)
        decoders = listed.group(1).split() if listed else [name]
        usable[name] = flag == "D" and any(not _HARDWARE_DECODER.search(d) for d in decoders)
    if not usable:
        # Not one codec line recognised: the output looks different from what
        # this parser knows -- don't claim anything is missing.
        return None
    missing = [label for name, label in REQUIRED_DECODERS.items() if not usable.get(name)]
    if not missing:
        return None
    return (
        f"This ffmpeg can't decode {' / '.join(missing)} video, so movies in that "
        "format can't get chapters. Install an ffmpeg that can (on Fedora it comes "
        "from RPM Fusion, on openSUSE from Packman)."
    )


def run_ffmpeg(args: list[str], timeout: float | None = None) -> FFmpegResult:
    """Run ffmpeg with given args. Returns captured output.

    The child is isolated from the terminal's process group (start_new_session)
    so a Ctrl+C during batch processing doesn't kill it directly -- that would
    bypass the graceful "finish the current movie" behavior on the first press.
    Instead, batch_control's signal handler explicitly terminates the
    registered subprocess only when it decides to (second Ctrl+C in a batch,
    or any Ctrl+C for a single-movie operation).
    """
    cmd = ["ffmpeg", "-hide_banner", *args]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=True,
        env=tool_env(),
    )
    register_subprocess(proc)
    try:
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            raise
    finally:
        register_subprocess(None)

    return FFmpegResult(
        stdout=stdout,
        stderr=stderr,
        returncode=proc.returncode,
    )


def get_duration(filepath: Path) -> float | None:
    """Get duration in seconds via ffprobe. None if unreadable."""
    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(filepath),
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env=tool_env(),
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def get_chapters(filepath: Path) -> list[dict[str, object]]:
    """Get embedded chapters as a list of dicts. Empty if none."""
    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-show_chapters",
        "-print_format",
        "json",
        str(filepath),
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env=tool_env(),
    )

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []

    chapters = data.get("chapters", [])
    return chapters if isinstance(chapters, list) else []


def run_window_detection(
    filepath: Path,
    seek_seconds: float,
    window_seconds: float,
    *,
    black_duration: float,
    black_pix_threshold: float,
    black_pic_threshold: float,
    scdet_threshold: int,
) -> FFmpegResult:
    """Run blackdetect + scdet on a single time window.

    Returns raw FFmpeg result; parsing happens in blackdetect/scdet modules.
    """
    vf = (
        f"trim=duration={window_seconds:.2f},setpts=PTS-STARTPTS,"
        f"blackdetect=d={black_duration}:"
        f"pix_th={black_pix_threshold}:pic_th={black_pic_threshold},"
        f"scdet=s=1:t={scdet_threshold}"
    )
    args = [
        "-ss",
        f"{seek_seconds:.2f}",
        "-i",
        str(filepath),
        "-vf",
        vf,
        "-an",
        "-f",
        "null",
        "-",
    ]
    return run_ffmpeg(args)
