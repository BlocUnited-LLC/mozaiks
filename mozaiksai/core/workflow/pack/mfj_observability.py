from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Dict, Optional
from uuid import uuid4

from logs.logging_config import get_core_logger

logger = get_core_logger("mfj_observability")


@dataclass
class MFJObservationContext:
    trace_id: str
    trigger_id: str
    parent_chat_id: str
    workflow_name: str
    started_at: float = field(default_factory=perf_counter)
    child_started_at: Dict[str, float] = field(default_factory=dict)


class MFJObserver:
    """Structured logging + lightweight counters for MFJ cycles."""

    def __init__(self) -> None:
        self._counters: Dict[str, int] = {
            "mfj.fan_out.total": 0,
            "mfj.fan_in.total": 0,
            "mfj.timeout.total": 0,
            "mfj.partial_failure.total": 0,
            "mfj.contract_violation.total": 0,
        }

    def start_cycle(self, *, trigger_id: str, parent_chat_id: str, workflow_name: str) -> MFJObservationContext:
        return MFJObservationContext(
            trace_id=str(uuid4()),
            trigger_id=str(trigger_id),
            parent_chat_id=str(parent_chat_id),
            workflow_name=str(workflow_name),
        )

    def on_fan_out_started(self, ctx: MFJObservationContext, *, child_count: int, spawn_mode: str) -> None:
        self._inc("mfj.fan_out.total")
        self._log(
            level="info",
            event="fan_out_started",
            ctx=ctx,
            child_count=int(child_count),
            spawn_mode=str(spawn_mode),
        )

    def on_child_spawned(self, ctx: MFJObservationContext, *, child_chat_id: str, task_key: str) -> None:
        ctx.child_started_at[str(child_chat_id)] = perf_counter()
        self._log(
            level="debug",
            event="child_spawned",
            ctx=ctx,
            child_chat_id=str(child_chat_id),
            task_key=str(task_key),
        )

    def on_child_completed(self, ctx: MFJObservationContext, *, child_chat_id: str, success: bool) -> None:
        now = perf_counter()
        started = ctx.child_started_at.pop(str(child_chat_id), None)
        duration = max(0.0, now - started) if started is not None else None
        self._log(
            level="debug",
            event="child_completed",
            ctx=ctx,
            child_chat_id=str(child_chat_id),
            success=bool(success),
            child_duration_seconds=duration,
        )

    def on_fan_in_started(self, ctx: MFJObservationContext, *, child_count: int, reason: str) -> None:
        self._inc("mfj.fan_in.total")
        self._log(
            level="info",
            event="fan_in_started",
            ctx=ctx,
            child_count=int(child_count),
            reason=str(reason),
        )

    def on_fan_in_completed(self, ctx: MFJObservationContext, *, succeeded_count: int, failed_count: int, strategy: str) -> None:
        if failed_count > 0:
            self._inc("mfj.partial_failure.total")
        self._log(
            level="info",
            event="fan_in_completed",
            ctx=ctx,
            succeeded_count=int(succeeded_count),
            failed_count=int(failed_count),
            strategy=str(strategy),
        )

    def on_timeout(self, ctx: MFJObservationContext, *, timeout_seconds: int) -> None:
        self._inc("mfj.timeout.total")
        self._log(
            level="warning",
            event="timeout",
            ctx=ctx,
            timeout_seconds=int(timeout_seconds),
        )

    def on_contract_violation(self, *, parent_chat_id: str, trigger_id: str, missing: Any, contract: str) -> None:
        self._inc("mfj.contract_violation.total")
        logger.warning(
            "[MFJ] contract_violation parent=%s trigger=%s contract=%s missing=%s",
            parent_chat_id,
            trigger_id,
            contract,
            missing,
        )

    def on_cycle_completed(self, ctx: MFJObservationContext) -> None:
        elapsed = max(0.0, perf_counter() - ctx.started_at)
        self._log(
            level="info",
            event="cycle_completed",
            ctx=ctx,
            cycle_duration_seconds=elapsed,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    def snapshot_metrics(self) -> Dict[str, int]:
        return dict(self._counters)

    def _inc(self, key: str) -> None:
        self._counters[key] = int(self._counters.get(key, 0)) + 1

    def _log(self, *, level: str, event: str, ctx: MFJObservationContext, **fields: Any) -> None:
        fn = getattr(logger, str(level).lower(), logger.info)
        extra = {
            "event_source": "workflow_pack_coordinator",
            "event": event,
            "trigger_id": ctx.trigger_id,
            "parent_chat_id": ctx.parent_chat_id,
            "workflow_name": ctx.workflow_name,
            "mfj_trace_id": ctx.trace_id,
        }
        extra.update(fields)
        fn("[MFJ] %s", event, extra=extra)


_observer: Optional[MFJObserver] = None


def get_mfj_observer() -> MFJObserver:
    global _observer
    if _observer is None:
        _observer = MFJObserver()
    return _observer


__all__ = ["MFJObservationContext", "MFJObserver", "get_mfj_observer"]

