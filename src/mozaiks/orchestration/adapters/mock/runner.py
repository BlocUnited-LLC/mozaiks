"""Kernel AI runner port implementation backed by a mock adapter."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone

from mozaiks.contracts import DomainEvent, ResumeRequest, RunRequest
from mozaiks.contracts.ports import AI_RUNNER_PROTOCOL_VERSION


class MockAIWorkflowRunner:
    """Deterministic mock implementation of the kernel runner contract."""

    def __init__(self) -> None:
        self._cancelled: set[str] = set()
        self._seq_by_run: dict[str, int] = {}

    def capabilities(self) -> dict[str, object]:
        return {
            "supports_resume": True,
            "supports_checkpoints": True,
            "supports_tools": True,
            "protocol_version": AI_RUNNER_PROTOCOL_VERSION,
        }

    async def run(self, request: RunRequest) -> AsyncIterator[DomainEvent]:
        self._seq_by_run[request.run_id] = 0
        yield self._build_event(
            run_id=request.run_id,
            event_type="process.started",
            payload={
                "run_id": request.run_id,
                "workflow_name": request.workflow_name,
                "workflow_version": request.workflow_version,
                "metadata": request.metadata,
            },
        )

        if request.run_id in self._cancelled:
            yield self._build_event(
                run_id=request.run_id,
                event_type="process.cancelled",
                payload={"run_id": request.run_id, "reason": "cancelled"},
            )
            return

        message = str(
            request.input.get(
                "prompt",
                f"mock runner executed workflow '{request.workflow_name}'",
            )
        )
        yield self._build_event(
            run_id=request.run_id,
            event_type="evaluation.message_delta",
            payload={"run_id": request.run_id, "source": "mock", "delta": message},
        )
        yield self._build_event(
            run_id=request.run_id,
            event_type="process.completed",
            payload={
                "run_id": request.run_id,
                "result": {
                    "workflow_name": request.workflow_name,
                    "workflow_version": request.workflow_version,
                    "echo": request.input,
                },
            },
        )

    async def resume(self, request: ResumeRequest) -> AsyncIterator[DomainEvent]:
        self._seq_by_run[request.run_id] = max(0, int(request.last_seq))
        yield self._build_event(
            run_id=request.run_id,
            event_type="process.resumed",
            payload={
                "run_id": request.run_id,
                "checkpoint_key": request.checkpoint_key,
                "metadata": request.metadata,
            },
        )

        if request.run_id in self._cancelled:
            yield self._build_event(
                run_id=request.run_id,
                event_type="process.cancelled",
                payload={"run_id": request.run_id, "reason": "cancelled"},
            )
            return

        yield self._build_event(
            run_id=request.run_id,
            event_type="process.completed",
            payload={
                "run_id": request.run_id,
                "result": {
                    "resumed": True,
                    "checkpoint_key": request.checkpoint_key,
                },
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


__all__ = ["MockAIWorkflowRunner"]
