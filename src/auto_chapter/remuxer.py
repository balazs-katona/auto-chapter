# SPDX-FileCopyrightText: 2026 Balazs Katona
# SPDX-License-Identifier: GPL-3.0-or-later
"""mkvmerge wrapper: writes chapters and remuxes the container atomically.

Single-step approach:
    mkvmerge -o output.tmp.mkv --chapters chapters.xml --no-chapters input.mkv
This produces a fresh, fully-compliant Matroska container with exactly the
given chapters embedded -- equivalent to writing with mkvpropedit then
re-muxing, but in one pass.

``--no-chapters`` is essential. ``--chapters`` alone does NOT replace the
chapters already in the input file: mkvmerge keeps them and adds the new XML
as a separate chapter edition next to them, so every write would stack one
more edition on the file (our XML uses fresh random UIDs, so editions never
merge). ``--no-chapters`` is a per-input-file option and has to stand right
before the input file name.

After the temp file is verified, os.replace() atomically swaps it for the
original. If anything fails mid-write, the temp file is removed and the
original remains untouched.

A live progress bar shows the temp file growing in size compared to the
original, so the user can see remux is making progress.
"""

import contextlib
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from rich.progress import (
    BarColumn,
    Progress,
    TaskID,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)

from auto_chapter.batch_control import force_stopped, register_subprocess
from auto_chapter.chapter.xml import to_xml
from auto_chapter.console import console
from auto_chapter.logging_setup import get_logger
from auto_chapter.models import Chapter
from auto_chapter.tool_env import tool_env

log = get_logger(__name__)

# Suffixes of the two files a write creates next to the movie. Both are unique
# to this app, so the scan's stale-file cleanup (plex_client._cleanup_stale_tmp)
# can remove leftovers from a killed run without touching anyone else's file.
TEMP_OUTPUT_SUFFIX = ".autochapter.tmp"
CHAPTER_XML_SUFFIX = ".autochapter.xml"


STOPPED_ERROR = "stopped before the file was replaced"


@dataclass(frozen=True, slots=True)
class RemuxResult:
    """Outcome of a remux operation."""

    success: bool
    error: str | None = None


def _watch_tmp_file_growth(
    tmp_path: Path,
    expected_size: int,
    progress: Progress,
    task_id: TaskID,
    stop_event: threading.Event,
) -> None:
    """Update progress bar based on temp file size. Runs in a background thread."""
    while not stop_event.is_set():
        try:
            if tmp_path.exists():
                current = tmp_path.stat().st_size
                progress.update(task_id, completed=min(current, expected_size))
        except OSError:
            pass
        time.sleep(0.5)


def _build_mkvmerge_command(temp_output: Path, xml_path: Path, filepath: Path) -> list[str]:
    """Build the mkvmerge command line that swaps the chapters of ``filepath``.

    ``--chapters`` adds the new chapters from ``xml_path``; ``--no-chapters``
    (placed right before the input file, because it is a per-file option)
    drops every chapter edition the input file already has. Without it the
    old chapters stay in the output as extra editions.
    """
    return [
        "mkvmerge",
        "-o",
        str(temp_output),
        "--chapters",
        str(xml_path),
        "--no-chapters",
        str(filepath),
    ]


def _mkvmerge_problem(stdout: str, stderr: str) -> str:
    """What mkvmerge said went wrong.

    mkvmerge prints its errors to stdout ("Error: ..."), not stderr: reading
    only stderr lost the reason (the user was told "The source may be
    corrupt" even for a read-only folder).
    """
    lines = [line.strip() for line in f"{stdout}\n{stderr}".splitlines() if line.strip()]
    errors = [line for line in lines if line.startswith("Error:")]
    if errors:
        return " ".join(errors)
    return stderr.strip() or (lines[-1] if lines else "")


def write_chapters_and_remux(
    filepath: Path,
    chapters: Iterable[Chapter],
    *,
    language: str = "und",
    min_free_bytes: int = 5 * 1024 * 1024 * 1024,  # 5 GB safety margin
) -> RemuxResult:
    """Write chapters to file via full mkvmerge remux. Atomic.

    Args:
        filepath: target MKV file.
        chapters: chapters to embed. Every chapter edition already in the
            file is dropped, so afterwards the file holds exactly this list.
        language: Matroska language code (e.g. "und", "eng").
        min_free_bytes: refuse to start if less than this much free disk space.

    Returns:
        RemuxResult with success flag and optional error.
    """
    if not filepath.exists():
        return RemuxResult(success=False, error=f"file not found: {filepath}")

    file_size = filepath.stat().st_size
    free = shutil.disk_usage(filepath.parent).free
    if free < file_size + min_free_bytes:
        return RemuxResult(
            success=False,
            error=f"insufficient disk space (need {file_size + min_free_bytes}, have {free})",
        )

    xml_content = to_xml(chapters, language=language)
    temp_output = filepath.with_name(filepath.name + TEMP_OUTPUT_SUFFIX)
    # A fixed, app-specific name (not tempfile's random tmpXXXX.xml), so a copy
    # left behind by a killed run can be recognised and removed by the next scan.
    xml_path = filepath.with_name(filepath.name + CHAPTER_XML_SUFFIX)
    xml_path.write_text(xml_content, encoding="utf-8")

    try:
        cmd = _build_mkvmerge_command(temp_output, xml_path, filepath)

        # Set up progress bar
        stop_event = threading.Event()
        watcher_thread: threading.Thread | None = None

        with Progress(
            TextColumn("    [progress.description]{task.description}"),
            BarColumn(bar_width=30),
            TaskProgressColumn(),
            TextColumn("[dim]{task.fields[size_text]}[/dim]"),
            TimeElapsedColumn(),
            transient=True,
            console=console,
        ) as progress:
            size_gb = file_size / (1024 ** 3)
            task_id = progress.add_task(
                "Writing chapters",
                total=file_size,
                size_text=f"0.0 / {size_gb:.1f} GB",
            )

            # Custom updater that also keeps size_text fresh
            def watcher() -> None:
                while not stop_event.is_set():
                    try:
                        if temp_output.exists():
                            current = temp_output.stat().st_size
                            current_gb = current / (1024 ** 3)
                            progress.update(
                                task_id,
                                completed=min(current, file_size),
                                size_text=f"{current_gb:.1f} / {size_gb:.1f} GB",
                            )
                    except OSError:
                        pass
                    time.sleep(0.5)

            watcher_thread = threading.Thread(target=watcher, daemon=True)
            watcher_thread.start()

            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    start_new_session=True,  # isolate from terminal Ctrl+C
                    env=tool_env(),
                )
                register_subprocess(proc)
                try:
                    stdout, stderr = proc.communicate()
                finally:
                    register_subprocess(None)
            finally:
                stop_event.set()
                if watcher_thread is not None:
                    watcher_thread.join(timeout=2)

            # Final update: 100%
            progress.update(task_id, completed=file_size)

        # A stop (second Ctrl+C) terminates mkvmerge: its file is incomplete,
        # however big it already is. Never let it replace the original.
        if force_stopped():
            return RemuxResult(success=False, error=STOPPED_ERROR)

        # mkvmerge: 0 = ok, 1 = warnings (still ok), 2 = error. Killed by a
        # signal = negative: until 1.1.1 only ">= 2" was checked, so a killed
        # mkvmerge's partial file (over 90 % of the size) would have replaced
        # the movie.
        if proc.returncode not in (0, 1):
            if proc.returncode < 0:
                return RemuxResult(
                    success=False,
                    error=f"mkvmerge was stopped (signal {-proc.returncode}).",
                )
            return RemuxResult(
                success=False,
                error=f"mkvmerge failed: {_mkvmerge_problem(stdout, stderr)[:300]}",
            )

        if not temp_output.exists():
            return RemuxResult(
                success=False,
                error="mkvmerge completed but output file missing",
            )

        # Sanity: temp file should be roughly comparable in size to original
        temp_size = temp_output.stat().st_size
        if temp_size < file_size * 0.9:
            return RemuxResult(
                success=False,
                error=f"output suspiciously small ({temp_size} vs {file_size})",
            )

        # Last look before the original is touched: a stop requested while the
        # checks above ran still keeps it.
        if force_stopped():
            return RemuxResult(success=False, error=STOPPED_ERROR)

        # Atomic replace
        os.replace(temp_output, filepath)
        return RemuxResult(success=True)

    except Exception as e:
        return RemuxResult(success=False, error=f"{type(e).__name__}: {e}")
    finally:
        # Cleanup
        if xml_path.exists():
            xml_path.unlink()
        if temp_output.exists():
            with contextlib.suppress(OSError):
                temp_output.unlink()
