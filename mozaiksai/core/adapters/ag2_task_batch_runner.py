"""AG2 runner for one deterministic Mozaiks task-batch work item."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

logger = logging.getLogger(__name__)

from autogen.beta.network.policies import CHANNEL_STATE_DEP

from mozaiksai.core.ports.orchestration import RunStatus
from mozaiksai.core.workflow.outputs.runtime_validation import (
    reply_body_to_data,
    validate_agent_structured_output,
)


@dataclass(slots=True)
class AG2TaskBatchRunnerRequest:
    workflow_name: str
    batch_id: str
    task_id: str
    chat_id: str | None
    app_id: str | None
    agent_name: str
    agent: Any
    prompt: str
    context_variables: dict[str, Any] = field(default_factory=dict)
    structured_registry: dict[str, Any] = field(default_factory=dict)
    timeout_seconds: int | None = None


@dataclass(slots=True)
class AG2TaskBatchRunnerResult:
    status: RunStatus
    output: Any = None
    channel_id: str | None = None
    close_reason: str | None = None
    wal: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


class AG2TaskBatchRunner:
    """Execute one task batch worker through AG2's Agent runtime primitive."""

    async def run(self, request: AG2TaskBatchRunnerRequest) -> AG2TaskBatchRunnerResult:
        try:
            reply = await asyncio.wait_for(
                request.agent.ask(
                    request.prompt,
                    variables=dict(request.context_variables),
                    dependencies={
                        CHANNEL_STATE_DEP: SimpleNamespace(
                            context_vars=dict(request.context_variables),
                        )
                    },
                ),
                timeout=float(request.timeout_seconds or 120),
            )
        except TimeoutError:
            return AG2TaskBatchRunnerResult(
                status=RunStatus.PAUSED,
                channel_id=f"{request.batch_id}:{request.task_id}",
                close_reason="timeout",
                error=f"task worker did not complete within {request.timeout_seconds or 120} seconds",
            )
        except Exception as exc:
            logger.error(
                "AG2 task batch run failed batch=%s task=%s: %s",
                request.batch_id,
                request.task_id,
                exc,
                exc_info=True,
            )
            return AG2TaskBatchRunnerResult(
                status=RunStatus.FAILED,
                channel_id=f"{request.batch_id}:{request.task_id}",
                close_reason="worker_failed",
                error="internal_error",
            )

        validation = validate_agent_structured_output(
            agent_name=request.agent_name,
            reply=reply,
            structured_registry=request.structured_registry,
        )
        if validation is not None:
            if not validation.validation_passed or validation.structured_data is None:
                return AG2TaskBatchRunnerResult(
                    status=RunStatus.FAILED,
                    channel_id=f"{request.batch_id}:{request.task_id}",
                    close_reason="structured_output_validation_failed",
                    error=(
                        f"structured output validation failed for {request.agent_name}: "
                        f"{validation.error}"
                    ),
                )
            output = validation.structured_data
        else:
            output = reply_body_to_data(reply)
        return AG2TaskBatchRunnerResult(
            status=RunStatus.COMPLETED,
            output=output,
            channel_id=f"{request.batch_id}:{request.task_id}",
            close_reason="agent_ask_completed",
        )


__all__ = [
    "AG2TaskBatchRunner",
    "AG2TaskBatchRunnerRequest",
    "AG2TaskBatchRunnerResult",
]
