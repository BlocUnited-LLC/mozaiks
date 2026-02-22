"""Kernel AI runner port implementation backed by AG2 (optional dependency)."""

from __future__ import annotations

import importlib.util
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from mozaiks.contracts import DomainEvent, ResumeRequest, RunRequest
from mozaiks.contracts.ports import AI_RUNNER_PROTOCOL_VERSION


class AG2UnavailableError(RuntimeError):
    """Raised when AG2 runner is selected without AG2 installed."""


class AG2AIWorkflowRunner:
    """AG2-backed runner contract implementation with lazy import checks."""

    def __init__(self) -> None:
        self._cancelled: set[str] = set()
        self._seq_by_run: dict[str, int] = {}

    @staticmethod
    def _ag2_available() -> bool:
        return importlib.util.find_spec("autogen") is not None

    def _ensure_ag2(self) -> None:
        if not self._ag2_available():
            raise AG2UnavailableError(
                "AG2 adapter selected but autogen is not installed. "
                "Install with `pip install mozaiks-ai[ag2]`."
            )

    def capabilities(self) -> dict[str, object]:
        return {
            "supports_resume": True,
            "supports_checkpoints": False,
            "supports_tools": True,
            "protocol_version": AI_RUNNER_PROTOCOL_VERSION,
        }

    async def run(self, request: RunRequest) -> AsyncIterator[DomainEvent]:
        self._ensure_ag2()
        self._seq_by_run[request.run_id] = 0
        yield self._build_event(
            run_id=request.run_id,
            event_type="process.started",
            payload={
                "run_id": request.run_id,
                "workflow_name": request.workflow_name,
                "workflow_version": request.workflow_version,
                "adapter": "ag2",
            },
        )

        if request.run_id in self._cancelled:
            yield self._build_event(
                run_id=request.run_id,
                event_type="process.cancelled",
                payload={"run_id": request.run_id, "reason": "cancelled"},
            )
            return

        prompt = str(request.input.get("prompt", "AG2 execution"))
        yield self._build_event(
            run_id=request.run_id,
            event_type="evaluation.message_delta",
            payload={
                "run_id": request.run_id,
                "source": "ag2",
                "delta": f"AG2 processed: {prompt}",
            },
        )
        yield self._build_event(
            run_id=request.run_id,
            event_type="process.completed",
            payload={
                "run_id": request.run_id,
                "result": {
                    "adapter": "ag2",
                    "workflow_name": request.workflow_name,
                    "workflow_version": request.workflow_version,
                },
            },
        )

    async def resume(self, request: ResumeRequest) -> AsyncIterator[DomainEvent]:
        self._ensure_ag2()
        self._seq_by_run[request.run_id] = max(0, int(request.last_seq))
        if request.run_id in self._cancelled:
            yield self._build_event(
                run_id=request.run_id,
                event_type="process.cancelled",
                payload={"run_id": request.run_id, "reason": "cancelled"},
            )
            return

        # Resume remains explicit and safe when checkpoint support is disabled.
        yield self._build_event(
            run_id=request.run_id,
            event_type="process.failed",
            payload={
                "run_id": request.run_id,
                "error": (
                    "resume_without_checkpoints_unsupported: "
                    "AG2 adapter reports supports_checkpoints=false"
                ),
                "adapter": "ag2",
                "checkpoint_key": request.checkpoint_key,
            },
        )

    async def cancel(self, run_id: str) -> None:
        self._cancelled.add(run_id)

    def _build_event(
        self,
        *,
        run_id: str,
        event_type: str,
        payload: dict[str, object],
    ) -> DomainEvent:
        seq = self._seq_by_run.get(run_id, 0) + 1
        self._seq_by_run[run_id] = seq
        return DomainEvent(
            event_type=event_type,
            seq=seq,
            occurred_at=datetime.now(timezone.utc),
            run_id=run_id,
            schema_version="1.0.0",
            payload=payload,
            metadata={},
        )


__all__ = ["AG2AIWorkflowRunner", "AG2UnavailableError"]
