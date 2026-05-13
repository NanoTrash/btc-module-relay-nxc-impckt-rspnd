"""Structured logging and JSONL output."""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any, TextIO

import structlog


def setup_logging(log_level: str = "INFO", jsonl_path: str = "sessions.jsonl") -> None:
    """Configure structlog console + JSONL file output."""
    level = getattr(logging, log_level.upper(), logging.INFO)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty()),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # JSONL file appender
    fh = Path(jsonl_path).open("a", encoding="utf-8")
    _jsonl_file = fh

    def _jsonl_sink(_event: str, **kwargs: Any) -> None:
        fh.write(json.dumps(kwargs, default=str, ensure_ascii=False) + "\n")
        fh.flush()

    structlog.contextvars.bind_contextvars(_jsonl_sink=_jsonl_sink)


def get_logger(name: str = "btc_relay") -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)


def jsonl_event(event: str, **kwargs: Any) -> None:
    """Write a raw JSONL event."""
    try:
        # Try to use the file handle directly to avoid circular issues
        path = kwargs.pop("_jsonl_path", "sessions.jsonl")
        with Path(path).open("a", encoding="utf-8") as fh:
            record = {"event": event, **kwargs}
            fh.write(json.dumps(record, default=str, ensure_ascii=False) + "\n")
    except Exception:
        pass
