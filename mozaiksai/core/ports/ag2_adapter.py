"""AG2 orchestration adapter – implements ``OrchestrationPort``.

This module provides the concrete adapter that bridges the existing
``run_workflow_orchestration`` function (keyword-arg-heavy, returns ``Any``)
to the canonical ``OrchestrationPort`` protocol (typed ``RunRequest`` /
``ResumeRequest``, yields ``DomainEvent`` stream).

Usage
-----
>>> from mozaiksai.core.ports.ag2_adapter import get_ag2_orchestration_adapter
>>> adapter = get_ag2_orchestration_adapter()
>>> isinstance(adapter, OrchestrationPort)
True
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from mozaiksai.core.contracts.events import DomainEvent, EVENT_SCHEMA_VERSION
from mozaiksai.core.contracts.runner import RunRequest, ResumeRequest
from mozaiksai.core.ports.orchestration import OrchestrationPort


class AG2OrchestrationAdapter:
    """Adapter from ``run_workflow_orchestration`` to ``OrchestrationPort``.

    The adapter unpacks ``RunRequest`` / ``ResumeRequest`` fields into the
    kwargs expected by :func:`run_workflow_orchestration`, runs it, and wraps
    the raw result into a ``DomainEvent`` async iterator.

    The adapter is intentionally *thin*: it does **not** re-implement business
    logic.  When the underlying engine evolves to native async streaming,
    the ``yield`` loop will simply forward real events.
    """

    AG2_ENGINE_VERSION = "ag2-0.x"

    # ------------------------------------------------------------------
    # OrchestrationPort.run
    # ------------------------------------------------------------------
    async def run(self, request: RunRequest) -> AsyncIterator[DomainEvent]:
        from mozaiksai.core.workflow.orchestration_patterns import (
            run_workflow_orchestration,
        )

        result = await run_workflow_orchestration(
            workflow_name=request.workflow_name,
            app_id=request.app_id or "",
            chat_id=request.chat_id or request.run_id,
            user_id=request.user_id,
            initial_message=request.payload.get("initial_message"),
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
        from mozaiksai.core.workflow.orchestration_patterns import (
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
        return {
            "engine": "ag2",
            "version": self.AG2_ENGINE_VERSION,
            "streaming": False,  # will flip to True when native async streaming is adopted
            "cancel": False,
            "resume": True,
            "protocols": ["OrchestrationPort/1.0.0"],
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
        payload: dict[str, Any]
        if isinstance(result, dict):
            payload = result
        else:
            payload = {"result": result}

        return DomainEvent(
            event_type=event_type,
            seq=seq,
            occurred_at=datetime.now(timezone.utc),
            run_id=run_id,
            schema_version=EVENT_SCHEMA_VERSION,
            payload=payload,
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
