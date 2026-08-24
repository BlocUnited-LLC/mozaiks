from __future__ import annotations

# IMPORTANT: This module is a neutral usage-only collector (measurement + emission).
# It must NEVER contain enforcement logic (no pricing, gating, entitlements, balance checks, or billing decisions).
import os
import uuid
from datetime import UTC, datetime
from typing import Any

from logs.logging_config import get_workflow_logger

logger = get_workflow_logger("token_manager")

USAGE_DELTA_EVENT_TYPE = "chat.usage_delta"
USAGE_SUMMARY_EVENT_TYPE = "chat.usage_summary"


def _usage_events_enabled() -> bool:
    value = os.getenv("USAGE_EVENTS_ENABLED", "true").strip().lower()
    return value not in {"0", "false", "off", "no", "disabled"}


# Emission accounting. Usage measurement is advisory and must never break a
# run, but a silently dropped event stream is invisible by design — these
# counters (plus a once-per-reason warning) make an all-drop run announce
# itself instead of looking identical to a healthy one.
_EMISSION_STATS: dict[str, int] = {
    "emitted": 0,
    "dropped_disabled": 0,
    "dropped_missing_context": 0,
    "failed": 0,
}
_WARNED_REASONS: set[str] = set()


def _count_emission(reason: str) -> None:
    _EMISSION_STATS[reason] = _EMISSION_STATS.get(reason, 0) + 1
    if reason != "emitted" and reason not in _WARNED_REASONS:
        _WARNED_REASONS.add(reason)
        logger.warning(
            "USAGE_EVENTS_DROPPED: first usage event dropped (reason=%s). "
            "Further drops for this reason are counted silently; inspect "
            "get_usage_emission_stats() for totals. For reason=dropped_disabled "
            "set USAGE_EVENTS_ENABLED=true to record; for "
            "reason=dropped_missing_context supply chat_id/app_id/user_id/"
            "workflow_name in context variables.",
            reason,
        )


def get_usage_emission_stats() -> dict[str, int]:
    """Return a snapshot of usage-event emission counters for this process."""

    return dict(_EMISSION_STATS)


class TokenManager:
    """Neutral token usage collector (measurement + emission only).

    This module MUST NOT implement pricing, entitlements, balance checks, or gating.
    It only emits factual, server-derived usage events that upstream control planes
    may consume for metering/billing decisions elsewhere.
    """

    @staticmethod
    async def emit_usage_delta(
        *,
        chat_id: str,
        app_id: str,
        user_id: str,
        workflow_name: str,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
        build_id: str | None = None,
        agent_name: str | None = None,
        model_name: str | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int | None = None,
        cached: bool = False,
        cached_tokens: int = 0,
        duration_sec: float = 0.0,
        invocation_id: str | None = None,
        event_ts: datetime | None = None,
    ) -> None:
        # Advisory measurement only. Do not add enforcement or billing logic here.
        if not _usage_events_enabled():
            _count_emission("dropped_disabled")
            return

        if not chat_id or not app_id or not user_id or not workflow_name:
            _count_emission("dropped_missing_context")
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
        cached_prompt = min(max(0, int(cached_tokens or 0)), prompt)

        payload: dict[str, Any] = {
            "event_id": uuid.uuid4().hex[:12],
            "event_ts": (event_ts or datetime.now(UTC)).isoformat(),
            "chat_id": chat_id,
            "app_id": app_id,
            "user_id": user_id,
            "tenant_id": tenant_id or None,
            "workspace_id": workspace_id or None,
            "workflow_name": workflow_name,
            "build_id": build_id or None,
            "agent_name": agent_name or None,
            "model_name": model_name or None,
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
            "cached": bool(cached or cached_prompt > 0),
            "cached_prompt_tokens": cached_prompt,
            "duration_sec": float(duration_sec or 0.0),
            "invocation_id": invocation_id or None,
        }

        try:
            from mozaiksai.core.events.unified_event_dispatcher import get_event_dispatcher

            dispatcher = get_event_dispatcher()
            await dispatcher.emit(USAGE_DELTA_EVENT_TYPE, payload)
            _count_emission("emitted")
        except Exception as exc:  # pragma: no cover - best effort
            _count_emission("failed")
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
        total_tokens: int | None = None,
        event_ts: datetime | None = None,
    ) -> None:
        # Advisory measurement only. Do not add enforcement or billing logic here.
        if not _usage_events_enabled():
            return

        prompt = max(0, int(prompt_tokens or 0))
        completion = max(0, int(completion_tokens or 0))
        total = max(0, int(total_tokens if total_tokens is not None else (prompt + completion)))

        payload: dict[str, Any] = {
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
            from mozaiksai.core.events.unified_event_dispatcher import get_event_dispatcher

            dispatcher = get_event_dispatcher()
            await dispatcher.emit(USAGE_SUMMARY_EVENT_TYPE, payload)
        except Exception as exc:  # pragma: no cover - best effort
            logger.debug("usage_summary_emit_failed", extra={"error": str(exc)})
