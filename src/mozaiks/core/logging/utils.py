"""Minimal logging helpers with optional structured output."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any


class JsonFormatter(logging.Formatter):
    """Serialize log records as JSON lines."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _RESERVED_LOG_RECORD_FIELDS
        }
        if extras:
            payload["context"] = extras
        return json.dumps(payload, ensure_ascii=True)


class ContextLoggerAdapter(logging.LoggerAdapter):
    """Logger adapter that merges static context into each log record."""

    def process(self, msg: object, kwargs: dict[str, Any]) -> tuple[object, dict[str, Any]]:
        existing = kwargs.get("extra")
        merged = dict(self.extra)
        if isinstance(existing, dict):
            merged.update(existing)
        kwargs["extra"] = merged
        return msg, kwargs


def configure_logging(level: str = "INFO", *, json_output: bool = False) -> None:
    """Configure root logging if no handlers have been configured yet."""

    root = logging.getLogger()
    if root.handlers:
        root.setLevel(getattr(logging, level.upper(), logging.INFO))
        return

    handler = logging.StreamHandler()
    if json_output:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))


def get_logger(name: str, **context: object) -> ContextLoggerAdapter:
    """Return a context-aware logger adapter."""

    return ContextLoggerAdapter(logging.getLogger(name), context)


_RESERVED_LOG_RECORD_FIELDS = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
    "message",
}

__all__ = ["ContextLoggerAdapter", "configure_logging", "get_logger"]
