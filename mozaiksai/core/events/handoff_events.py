from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from logs.logging_config import get_core_logger
from mozaiksai.core.events.unified_event_dispatcher import get_event_dispatcher

log = get_core_logger("handoff_events")

HANDOFF_EVENT_TYPE = "runtime.handoff"


def _run_async_fire_and_forget(coro: Any) -> None:
    """Ensure coroutine executes even from sync contexts."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        loop.create_task(coro)
        return

    # Fallback: run in a new loop (no active loop in this thread)
    new_loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(new_loop)
        new_loop.run_until_complete(coro)
    except Exception as exc:  # pragma: no cover - logging guard
        log.warning("⚠️ [HANDOFF_EVENTS] Failed to emit event: %s", exc)
    finally:
        asyncio.set_event_loop(None)
        new_loop.close()


def emit_handoff_event(event_kind: str, payload: Dict[str, Any]) -> None:
    """Emit a runtime handoff event through the unified dispatcher.

    Args:
        event_kind: Logical subtype (e.g. "context", "after_work").
        payload: Structured payload describing the transition.
    """
    dispatcher = get_event_dispatcher()
    event_payload = {"event_kind": event_kind, **payload}
    try:
        _run_async_fire_and_forget(dispatcher.emit(HANDOFF_EVENT_TYPE, event_payload))
    except Exception as exc:  # pragma: no cover - defensive logging only
        log.warning("⚠️ [HANDOFF_EVENTS] Unexpected failure scheduling event: %s", exc)


def sanitize_identifier(value: Any) -> Optional[str]:
    """Best-effort string suitable for logs and event consumers."""
    try:
        if isinstance(value, str):
            return value
        if hasattr(value, "name") and isinstance(value.name, str):
            return value.name
        if hasattr(value, "agent_name") and isinstance(value.agent_name, str):
            return value.agent_name
        if value is None:
            return None
        return str(value)
    except Exception:  # pragma: no cover
        return None


__all__ = ["emit_handoff_event", "sanitize_identifier", "HANDOFF_EVENT_TYPE"]
