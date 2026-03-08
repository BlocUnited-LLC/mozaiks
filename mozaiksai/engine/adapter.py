"""AG2 orchestration adapter – implements ``OrchestrationPort``.

This module provides the concrete adapter that bridges the existing
``run_workflow_orchestration`` function (keyword-arg-heavy, returns ``Any``)
to the canonical ``OrchestrationPort`` protocol (typed ``RunRequest`` /
``ResumeRequest``, yields ``DomainEvent`` stream).

Usage
-----
>>> from mozaiksai.engine.adapter import get_ag2_orchestration_adapter
>>> adapter = get_ag2_orchestration_adapter()
>>> isinstance(adapter, OrchestrationPort)
True
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from mozaiksai.contracts.events import DomainEvent, EVENT_SCHEMA_VERSION
from mozaiksai.contracts.runner import RunRequest, ResumeRequest
from mozaiksai.engine.capabilities import get_ag2_capability_report
from mozaiksai.ports.orchestration import OrchestrationPort


class AG2OrchestrationAdapter:
    """Adapter from ``run_workflow_orchestration`` to ``OrchestrationPort``.

    The adapter unpacks ``RunRequest`` / ``ResumeRequest`` fields into the
    kwargs expected by :func:`run_workflow_orchestration`, runs it, and wraps
    the raw result into a ``DomainEvent`` async iterator.

    The adapter is intentionally *thin*: it does **not** re-implement business
    logic.  When the underlying engine evolves to native async streaming,
    the ``yield`` loop will simply forward real events.
    """

    # ------------------------------------------------------------------
    # OrchestrationPort.run
    # ------------------------------------------------------------------
    async def run(self, request: RunRequest) -> AsyncIterator[DomainEvent]:
        from mozaiksai.engine.orchestration import (
            run_workflow_orchestration,
        )

        result = await run_workflow_orchestration(
            workflow_name=request.workflow_name,
            app_id=request.app_id or "",
            chat_id=request.chat_id or request.run_id,
            user_id=request.user_id,
            initial_message=request.context.get("initial_message"),
            **request.metadata,
        )

        yield self._wrap_result(
            event_type="workflow.run_completed",
            run_id=request.run_id,
            result=result,
        )

    # ------------------------------------------------------------------
    # OrchestrationPort.resume
    # ------------------------------------------------------------------
    async def resume(self, request: ResumeRequest) -> AsyncIterator[DomainEvent]:
        from mozaiksai.engine.orchestration import (
            run_workflow_orchestration,
        )

        result = await run_workflow_orchestration(
            workflow_name=request.workflow_name,
            app_id=request.app_id or "",
            chat_id=request.chat_id or request.run_id,
            user_id=request.user_id,
            last_seen_sequence=request.last_seq,
            **request.metadata,
        )

        yield self._wrap_result(
            event_type="workflow.resume_completed",
            run_id=request.run_id,
            result=result,
        )

    # ------------------------------------------------------------------
    # OrchestrationPort.cancel
    # ------------------------------------------------------------------
    async def cancel(self, run_id: str) -> None:
        # Current AG2 has no first-class cancel.  This is a no-op placeholder
        # that will be wired when native cancel support becomes available.
        pass

    # ------------------------------------------------------------------
    # OrchestrationPort.capabilities
    # ------------------------------------------------------------------
    def capabilities(self) -> dict[str, Any]:
        report = get_ag2_capability_report()
        return {
            "engine": "ag2",
            "version": report.get("version", "unknown"),
            "streaming": True,
            "streaming_transport": "iostream_bridge",
            "a2a": True,
            "cancel": False,
            "resume": True,
            "run_iter": bool(report.get("agent_run_iter")),
            "custom_events": bool(report.get("custom_events")),
            "groupchat_iter_sync": bool(report.get("groupchat_iter_sync")),
            "groupchat_iter_async": bool(report.get("groupchat_iter_async")),
            "opentelemetry": bool(report.get("opentelemetry", {}).get("enabled")),
            "protocols": ["OrchestrationPort/1.0.0", "A2A/draft"],
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _wrap_result(
        *,
        event_type: str,
        run_id: str,
        result: Any,
        seq: int = 0,
    ) -> DomainEvent:
        """Package a raw orchestration result into a ``DomainEvent``."""
        event_data: dict[str, Any]
        if isinstance(result, dict):
            event_data = result
        else:
            event_data = {"result": result}

        return DomainEvent(
            event_type=event_type,
            seq=seq,
            occurred_at=datetime.now(timezone.utc),
            run_id=run_id,
            schema_version=EVENT_SCHEMA_VERSION,
            data=event_data,
        )


# Singleton accessor --------------------------------------------------------

_AG2_ADAPTER: AG2OrchestrationAdapter | None = None


def get_ag2_orchestration_adapter() -> AG2OrchestrationAdapter:
    """Return singleton AG2 orchestration adapter."""
    global _AG2_ADAPTER
    if _AG2_ADAPTER is None:
        _AG2_ADAPTER = AG2OrchestrationAdapter()
    return _AG2_ADAPTER


__all__ = ["AG2OrchestrationAdapter", "get_ag2_orchestration_adapter"]
