# SPDX-FileCopyrightText: 2026 Balazs Katona
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for setup_logging: verifies the log file actually receives content.

Regression test for a real bug: structlog's default logger_factory
(PrintLoggerFactory) writes straight to stdout and never touches Python's
stdlib `logging` module. Attaching a `logging.FileHandler` to the stdlib root
logger (the original implementation) silently received nothing -- the log
file existed but stayed empty forever, no matter what was logged.
"""

import json
from pathlib import Path

from auto_chapter.config import LoggingConfig
from auto_chapter.logging_setup import get_logger, setup_logging


def test_file_actually_receives_log_content(tmp_path: Path) -> None:
    log_path = tmp_path / "auto-chapter.log"
    config = LoggingConfig(log_level="INFO", file_path=str(log_path))

    setup_logging(config)
    log = get_logger("test")
    log.info("something_happened", detail="value123")

    assert log_path.exists()
    content = log_path.read_text()
    assert content.strip() != ""  # this is the crux of the regression: not empty
    assert "something_happened" in content
    assert "value123" in content


def test_file_content_is_valid_json_lines(tmp_path: Path) -> None:
    log_path = tmp_path / "auto-chapter.log"
    config = LoggingConfig(log_level="INFO", file_path=str(log_path))

    setup_logging(config)
    log = get_logger("test")
    log.info("first_event")
    log.warning("second_event")

    lines = [ln for ln in log_path.read_text().splitlines() if ln.strip()]
    assert len(lines) == 2
    for line in lines:
        parsed = json.loads(line)  # must not raise -- each line is valid JSON
        assert "event" in parsed


def test_no_file_created_when_file_path_empty(tmp_path: Path) -> None:
    config = LoggingConfig(log_level="INFO", file_path="")
    setup_logging(config)
    log = get_logger("test")
    log.info("no_file_should_appear")

    # Nothing under tmp_path should have been created as a side effect
    assert list(tmp_path.iterdir()) == []


def test_parent_directories_created_automatically(tmp_path: Path) -> None:
    nested_path = tmp_path / "nested" / "dirs" / "auto-chapter.log"
    config = LoggingConfig(log_level="INFO", file_path=str(nested_path))

    setup_logging(config)
    log = get_logger("test")
    log.info("event_in_nested_dir")

    assert nested_path.exists()
    assert "event_in_nested_dir" in nested_path.read_text()


def test_timestamp_level_event_lead_the_line(tmp_path: Path) -> None:
    """timestamp/level/event must come first in the JSON, even when other
    fields (like a long exception string) would otherwise push them to the
    end of the line -- easy to miss when scanning a log file by eye."""
    log_path = tmp_path / "auto-chapter.log"
    config = LoggingConfig(log_level="INFO", file_path=str(log_path))

    setup_logging(config)
    log = get_logger("test")
    log.error("scan_failed", error="a very long exception string goes here")

    line = log_path.read_text().strip()
    parsed = json.loads(line)
    keys = list(parsed.keys())
    assert keys[:3] == ["timestamp", "level", "event"]
