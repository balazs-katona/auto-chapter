"""Structured logging setup.

Structured events (log.info/warning/error) are diagnostic detail meant for
the log file, not the terminal. Every user-facing message in this app goes
through an explicit console.print() call elsewhere in the code, with its
own short, friendly wording -- structlog's own console rendering is
deliberately NOT wired up. Without this, a log.error(..., error=str(e))
call would dump the full raw exception string (connection pool internals,
escaped quotes, errno codes and all) straight onto the screen right next to
the friendly message, undoing the whole point of having one.
"""

import json
import logging
from pathlib import Path

import structlog
from structlog.types import EventDict, Processor, WrappedLogger

from auto_chapter.config import LoggingConfig


def _write_json_line(log_path: Path) -> Processor:
    """Structlog processor: appends each event as a JSON line to log_path.

    structlog's default logger_factory (PrintLoggerFactory) writes straight
    to stdout and never touches Python's stdlib `logging` module -- so a
    stdlib `logging.FileHandler` attached to the root logger silently never
    receives anything. This processor does the file write directly instead,
    as a plain step in the pipeline. Each write opens/writes/closes
    immediately (no buffering), so nothing is lost if the process is killed
    mid-run.
    """

    def processor(_logger: WrappedLogger, _name: str, event_dict: EventDict) -> EventDict:
        try:
            # timestamp/level/event lead the line so entries scan
            # chronologically at a glance -- otherwise a long "error" field
            # (a full exception string) can push the timestamp far to the
            # right or off past a wrapped line, easy to miss entirely.
            ordered = {
                "timestamp": event_dict.get("timestamp"),
                "level": event_dict.get("level"),
                "event": event_dict.get("event"),
                **{
                    k: v
                    for k, v in event_dict.items()
                    if k not in ("timestamp", "level", "event")
                },
            }
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(ordered, default=str) + "\n")
        except OSError:
            pass  # file logging must never crash the app
        return event_dict

    return processor


def _drop_event(_logger: WrappedLogger, _name: str, event_dict: EventDict) -> EventDict:
    """Terminal processor: discard the event instead of rendering it anywhere.

    Runs last, after the file-writing processor (if any) has already had its
    turn. structlog.DropEvent is the documented way to tell structlog "stop
    here, don't hand this to the underlying logger" -- without it, the
    default PrintLoggerFactory would print the raw event dict to stdout.
    """
    raise structlog.DropEvent


def setup_logging(config: LoggingConfig, log_path: Path | None = None) -> None:
    """Configure structlog to write JSON to file only -- never to the console.

    log_level gates what gets logged at all (WARNING by default). There is
    no console renderer regardless of level: this pipeline's only job is the
    optional file record: console.print() calls elsewhere handle everything
    the user actually sees.
    """
    level = getattr(logging, config.log_level.upper(), logging.INFO)

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    processors: list[Processor] = list(shared_processors)
    # log_path: the resolved file (Config.log_file_path()); without it, the
    # configured file_path is used as it is.
    if log_path is None and config.file_path:
        log_path = Path(config.file_path)
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        processors.append(_write_json_line(log_path))
    processors.append(_drop_event)

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Get a structlog logger."""
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
