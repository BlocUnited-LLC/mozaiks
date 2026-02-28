from __future__ import annotations

# IMPORTANT: This module is a neutral usage-only collector (measurement + emission).
# It must NEVER contain enforcement logic (no pricing, gating, entitlements, balance checks, or billing decisions).

import os
import uuid
from datetime import UTC, datetime
from typing import Any, Dict, Optional

from mozaiksai.core.events.unified_event_dispatcher import get_event_dispatcher
from logs.logging_config import get_workflow_logger

logger = get_workflow_logger("token_manager")

USAGE_DELTA_EVENT_TYPE = "chat.usage_delta"
USAGE_SUMMARY_EVENT_TYPE = "chat.usage_summary"


def _usage_events_enabled() -> bool:
    value = os.getenv("USAGE_EVENTS_ENABLED", "true").strip().lower()
    return value not in {"0", "false", "off", "no", "disabled"}


class TokenManager:
    """Neutral token usage collector (measurement + emission only).

    This module MUST NOT implement pricing, entitlements, balance checks, or gating.
    It only emits factual, server-derived usage events that upstream control planes
    may consume for metering/billing decisions elsewhere.
    """

    @staticmethod
    async def ensure_can_start_chat(  # noqa: D401
        user_id: str,
        enterprise_id: str,
        workflow_name: str,
        persistence_manager: Any,
    ) -> Dict[str, Any]:
        """Legacy compatibility shim: runtime never gates chat start."""
        return {"allowed": True}

    @staticmethod
    async def emit_usage_delta(
        *,
        chat_id: str,
        app_id: str,
        user_id: str,
        workflow_name: str,
        agent_name: Optional[str] = None,
        model_name: Optional[str] = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: Optional[int] = None,
        cached: bool = False,
        duration_sec: float = 0.0,
        invocation_id: Optional[str] = None,
        event_ts: Optional[datetime] = None,
    ) -> None:
        # Advisory measurement only. Do not add enforcement or billing logic here.
        if not _usage_events_enabled():
            return

        if not chat_id or not app_id or not user_id or not workflow_name:
            logger.debug(
                "usage_delta_missing_context",
                extra={
                    "chat_id": chat_id,
                    "app_id": app_id,
                    "user_id": user_id,
                    "workflow_name": workflow_name,
                },
            )
            return

        prompt = max(0, int(prompt_tokens or 0))
        completion = max(0, int(completion_tokens or 0))
        total = max(0, int(total_tokens if total_tokens is not None else (prompt + completion)))

        payload: Dict[str, Any] = {
            "event_id": uuid.uuid4().hex[:12],
            "event_ts": (event_ts or datetime.now(UTC)).isoformat(),
            "chat_id": chat_id,
            "app_id": app_id,
            "user_id": user_id,
            "workflow_name": workflow_name,
            "agent_name": agent_name or None,
            "model_name": model_name or None,
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
            "cached": bool(cached),
            "duration_sec": float(duration_sec or 0.0),
            "invocation_id": invocation_id or None,
        }

        try:
            dispatcher = get_event_dispatcher()
            await dispatcher.emit(USAGE_DELTA_EVENT_TYPE, payload)
        except Exception as exc:  # pragma: no cover - best effort
            logger.debug("usage_delta_emit_failed", extra={"error": str(exc)})

    @staticmethod
    async def emit_usage_summary(
        *,
        chat_id: str,
        app_id: str,
        user_id: str,
        workflow_name: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: Optional[int] = None,
        event_ts: Optional[datetime] = None,
    ) -> None:
        # Advisory measurement only. Do not add enforcement or billing logic here.
        if not _usage_events_enabled():
            return

        prompt = max(0, int(prompt_tokens or 0))
        completion = max(0, int(completion_tokens or 0))
        total = max(0, int(total_tokens if total_tokens is not None else (prompt + completion)))

        payload: Dict[str, Any] = {
            "event_id": uuid.uuid4().hex[:12],
            "event_ts": (event_ts or datetime.now(UTC)).isoformat(),
            "chat_id": chat_id,
            "app_id": app_id,
            "user_id": user_id,
            "workflow_name": workflow_name,
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
        }

        try:
            dispatcher = get_event_dispatcher()
            await dispatcher.emit(USAGE_SUMMARY_EVENT_TYPE, payload)
        except Exception as exc:  # pragma: no cover - best effort
            logger.debug("usage_summary_emit_failed", extra={"error": str(exc)})
