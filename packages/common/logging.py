"""Structured logging for MetaScale.

Emits single-line JSON logs by default (great for the future observability
member) plus a ``log_event`` helper that records the standard sharding fields:

    request_id, operation, shard_id, routing_strategy, key,
    query_type, latency_ms, status

A contextvar carries the current ``request_id`` so it does not have to be
threaded through every function signature.
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)

_RESERVED = {"message", "level", "logger"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        rid = request_id_var.get()
        if rid:
            payload["request_id"] = rid
        for key, value in getattr(record, "__fields__", {}).items():
            if key not in _RESERVED:
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, separators=(",", ":"))


def configure_logging(level: str = "INFO") -> None:
    """Configure root logging once, replacing pre-existing handlers."""
    root = logging.getLogger()
    root.setLevel(level.upper())
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    # Quiet noisy libraries
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("pgserver").setLevel(logging.WARNING)


class StructuredLogger:
    """Thin wrapper around stdlib logger that attaches structured fields."""

    def __init__(self, name: str) -> None:
        self._logger = logging.getLogger(name)

    def _log(self, level: int, message: str, **fields: Any) -> None:
        # Perform %-formatting safety: message is static
        record = self._logger.makeRecord(
            self._logger.name,
            level,
            "(structured)",
            0,
            message,
            (),
            None,
        )
        record.__fields__ = fields  # type: ignore[attr-defined]
        self._logger.handle(record)

    def info(self, message: str, **fields: Any) -> None:
        self._log(logging.INFO, message, **fields)

    def warning(self, message: str, **fields: Any) -> None:
        self._log(logging.WARNING, message, **fields)

    def error(self, message: str, **fields: Any) -> None:
        self._log(logging.ERROR, message, **fields)

    def debug(self, message: str, **fields: Any) -> None:
        self._log(logging.DEBUG, message, **fields)


def get_logger(name: str) -> StructuredLogger:
    return StructuredLogger(name)


@contextmanager
def request_context(request_id: str | None) -> Iterator[None]:
    token = request_id_var.set(request_id)
    try:
        yield
    finally:
        request_id_var.reset(token)


@contextmanager
def timed(logger: StructuredLogger, operation: str, **fields: Any) -> Iterator[dict[str, Any]]:
    """Context manager that measures elapsed time and logs on exit."""
    start = time.perf_counter()
    info: dict[str, Any] = {"status": "success"}
    try:
        yield info
    except Exception:
        info["status"] = "error"
        raise
    finally:
        info["latency_ms"] = round((time.perf_counter() - start) * 1000.0, 3)
        merged = {**fields, **info}
        logger.info(operation, operation=operation, **merged)
