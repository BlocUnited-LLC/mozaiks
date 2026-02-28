"""
Shared logging helpers for backend tool implementations.
"""

import logging
from pathlib import Path
from logging.handlers import RotatingFileHandler
from typing import Optional, Dict, Any

try:
    # Prefer project workflow logger if present
    from logs.logging_config import get_workflow_logger  # type: ignore
except Exception:  # pragma: no cover
    get_workflow_logger = None  # type: ignore

SENSITIVE_KEYS = {"api_key", "apikey", "authorization", "auth", "secret", "password", "token"}


def _redact_extras(extras: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    data = extras or {}
    out: Dict[str, Any] = {}
    for k, v in data.items():
        if any(s in k.lower() for s in SENSITIVE_KEYS):
            out[k] = "***"
        else:
            out[k] = v
    return out


class ToolLoggerAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):  # type: ignore[override]
        extra = kwargs.get("extra") or {}
        base = dict(self.extra) if isinstance(self.extra, dict) else {}
        other = dict(extra) if isinstance(extra, dict) else {}
        merged = {**base, **other}
        kwargs["extra"] = _redact_extras(merged)
        return msg, kwargs


_TOOLS_HANDLER_SETUP = False


def _ensure_tools_file_handler() -> None:
    """Attach a rotating file handler to the 'core.tools' logger writing to logs/logs/tools.log.
    Safe to call multiple times; applies once.
    """
    global _TOOLS_HANDLER_SETUP
    if _TOOLS_HANDLER_SETUP:
        return
    try:
        # Prefer the centralized LOGS_DIR from logging_config when available so
        # tools.log appears alongside the main mozaiks.log. Fall back to a
        # sane default adjacent to this module if LOGS_DIR is not importable.
        try:
            from logs.logging_config import LOGS_DIR  # type: ignore
            target_dir = Path(LOGS_DIR)
        except Exception:
            # Fallback: use a 'logs' sibling directory next to this file
            candidate = Path(__file__).resolve().parent / "logs"
            # If that doesn't exist, use this file's parent (logs/)
            target_dir = candidate if candidate.exists() else Path(__file__).resolve().parent

        target_dir.mkdir(parents=True, exist_ok=True)
        file_path = target_dir / "tools.log"

        handler = RotatingFileHandler(str(file_path), maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
        fmt = logging.Formatter(
            fmt="%(asctime)s %(levelname)s [%(name)s] %(message)s | extra=%(tool_name)s %(workflow_name)s %(chat_id)s %(ui_event_id)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(fmt)

        tools_root = logging.getLogger("core.tools")
        # Avoid adding duplicate similar handlers
        if not any(isinstance(h, RotatingFileHandler) and getattr(h, "baseFilename", None) == handler.baseFilename for h in tools_root.handlers):
            tools_root.addHandler(handler)
        # Keep propagate True so logs also reach the main mozaiks log
        tools_root.setLevel(logging.INFO)
        _TOOLS_HANDLER_SETUP = True
    except Exception:
        # Do not break logging on failures; main logger still works
        _TOOLS_HANDLER_SETUP = True


def get_tool_logger(
    *,
    tool_name: str,
    chat_id: Optional[str] = None,
    app_id: Optional[str] = None,
    workflow_name: Optional[str] = None,
    agent_message_id: Optional[str] = None,
    ui_event_id: Optional[str] = None,
    base_logger: Optional[logging.Logger] = None,
) -> ToolLoggerAdapter:
    # Ensure dedicated tools file logging is attached once
    _ensure_tools_file_handler()
    name = f"core.tools.{tool_name}"
    
    # Always use standard logger for tools, not ContextLogger
    # This ensures compatibility with ToolLoggerAdapter
    logger = base_logger if base_logger is not None else logging.getLogger(name)

    extra = {
        "logger_type": "tool",
        "tool_name": tool_name,
        "chat_id": chat_id,
        "app_id": app_id,
        "workflow_name": workflow_name,
        "agent_message_id": agent_message_id,
        "ui_event_id": ui_event_id,
    }
    return ToolLoggerAdapter(logger, extra)


def log_tool_event(
    logger: ToolLoggerAdapter,
    *,
    action: str,
    status: str = "info",
    message: Optional[str] = None,
    level: int = logging.INFO,
    **fields: Any,
) -> None:
    payload = {"event": "tool_event", "action": action, "status": status, **fields}
    logger.log(level, message or f"tool:{action} ({status})", extra=payload)
