"""AG2 runner for one deterministic Mozaiks task-batch work item."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, cast

from ag2.context import ConversationContext
from ag2.events.task_events import (
    TaskCancelled,
    TaskCompleted,
    TaskExpired,
    TaskFailed,
    TaskProgress,
    TaskStarted,
)
from ag2.network.policies import CHANNEL_STATE_DEP
from ag2.stream import MemoryStream
from ag2.task import Task, TaskSpec

from mozaiksai.core.ports.orchestration import RunStatus
from mozaiksai.core.workflow.outputs.runtime_validation import (
    reply_body_to_data,
    validate_agent_structured_output,
)

logger = logging.getLogger(__name__)


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
    task_id: str | None = None
    capability: str | None = None
    lifecycle_status: str | None = None
    lifecycle_events: list[dict[str, Any]] = field(default_factory=list)
    started_at: str | None = None
    completed_at: str | None = None
    channel_id: str | None = None
    close_reason: str | None = None
    wal: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


class AG2TaskBatchRunner:
    """Execute one task batch worker with authentic AG2 Task lifecycle evidence."""

    async def run(self, request: AG2TaskBatchRunnerRequest) -> AG2TaskBatchRunnerResult:
        stream = MemoryStream(persist_all=True)
        lifecycle_events: list[dict[str, Any]] = []

        async def _capture_lifecycle_event(event: Any) -> None:
            normalized = _normalize_task_lifecycle_event(event)
            if normalized is not None:
                lifecycle_events.append(normalized)

        sub_id = stream.subscribe(_capture_lifecycle_event, sync_to_thread=False)
        dependencies = {
            CHANNEL_STATE_DEP: SimpleNamespace(
                context_vars=dict(request.context_variables),
            )
        }
        context = ConversationContext(
            stream=stream,
            variables=dict(request.context_variables),
            dependencies=dict(dependencies),
        )
        capability = _task_capability(request)
        ag2_task = _create_task(request, context=context, capability=capability)

        try:
            async with ag2_task as active_task:
                try:
                    reply = await asyncio.wait_for(
                        request.agent.ask(
                            request.prompt,
                            stream=stream,
                            variables=dict(request.context_variables),
                            dependencies=dependencies,
                        ),
                        timeout=float(request.timeout_seconds or 120),
                    )
                except TimeoutError:
                    await active_task.expire()
                    return _result_from_task(
                        status=RunStatus.PAUSED,
                        task=active_task,
                        capability=capability,
                        lifecycle_events=lifecycle_events,
                        close_reason="expired",
                        error=(
                            "task worker did not complete within "
                            f"{request.timeout_seconds or 120} seconds"
                        ),
                    )
                except asyncio.CancelledError:
                    await active_task.cancel("cancelled")
                    current_task = asyncio.current_task()
                    if current_task is not None and current_task.cancelling():
                        raise
                    return _result_from_task(
                        status=RunStatus.PAUSED,
                        task=active_task,
                        capability=capability,
                        lifecycle_events=lifecycle_events,
                        close_reason="cancelled",
                        error="task worker was cancelled",
                    )
                except Exception as exc:
                    await active_task.fail(exc)
                    logger.error(
                        "AG2 task batch run failed batch=%s task=%s: %s",
                        request.batch_id,
                        request.task_id,
                        exc,
                        exc_info=True,
                    )
                    return _result_from_task(
                        status=RunStatus.FAILED,
                        task=active_task,
                        capability=capability,
                        lifecycle_events=lifecycle_events,
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
                        error = (
                            f"structured output validation failed for {request.agent_name}: "
                            f"{validation.error}"
                        )
                        await active_task.fail(error)
                        return _result_from_task(
                            status=RunStatus.FAILED,
                            task=active_task,
                            capability=capability,
                            lifecycle_events=lifecycle_events,
                            close_reason="structured_output_validation_failed",
                            error=error,
                        )
                    output = validation.structured_data
                else:
                    output = reply_body_to_data(reply)
                await active_task.complete(output)
                return _result_from_task(
                    status=RunStatus.COMPLETED,
                    task=active_task,
                    capability=capability,
                    lifecycle_events=lifecycle_events,
                    output=output,
                    close_reason="agent_ask_completed",
                )
        finally:
            stream.unsubscribe(sub_id)


def _task_capability(request: AG2TaskBatchRunnerRequest) -> str:
    return ".".join(
        [
            "mozaiks",
            "task_batch",
            str(request.workflow_name or "workflow").strip() or "workflow",
            str(request.batch_id or "batch").strip() or "batch",
            str(request.agent_name or "agent").strip() or "agent",
        ]
    )


def _create_task(
    request: AG2TaskBatchRunnerRequest,
    *,
    context: ConversationContext,
    capability: str,
) -> Task:
    title = f"{request.batch_id}:{request.task_id}"
    description = (
        "Mozaiks task-batch worker turn. Mozaiks preselected the worker, "
        "dependencies, prompt, context, timeout, retries, schema validation, "
        "output destination, and merge behavior."
    )
    payload = {
        "workflow_name": request.workflow_name,
        "batch_id": request.batch_id,
        "mozaiks_task_id": request.task_id,
        "agent_name": request.agent_name,
        "chat_id": request.chat_id,
        "app_id": request.app_id,
    }
    task_factory = getattr(request.agent, "task", None)
    if callable(task_factory):
        return cast(
            Task,
            task_factory(
                title,
                description=description,
                payload=payload,
                capability=capability,
                ttl_seconds=request.timeout_seconds,
                context=context,
            ),
        )
    return Task(
        owner_id=request.agent_name,
        spec=TaskSpec(
            title=title,
            description=description,
            payload=payload,
            capability=capability,
        ),
        context=context,
        ttl_seconds=request.timeout_seconds,
    )


def _result_from_task(
    *,
    status: RunStatus,
    task: Task,
    capability: str,
    lifecycle_events: list[dict[str, Any]],
    close_reason: str,
    output: Any = None,
    error: str | None = None,
) -> AG2TaskBatchRunnerResult:
    metadata = task.metadata
    return AG2TaskBatchRunnerResult(
        status=status,
        output=output,
        task_id=metadata.task_id,
        capability=capability,
        lifecycle_status=metadata.state.value,
        lifecycle_events=list(lifecycle_events),
        started_at=metadata.started_at,
        completed_at=metadata.completed_at,
        channel_id=None,
        close_reason=close_reason,
        error=error,
    )


def _normalize_task_lifecycle_event(event: Any) -> dict[str, Any] | None:
    if isinstance(event, TaskStarted):
        spec = event.spec
        return {
            "event": "TaskStarted",
            "task_id": event.task_id,
            "agent_name": event.agent_name,
            "objective": event.objective,
            "created_at": event.created_at,
            "capability": getattr(spec, "capability", None),
            "expires_at": event.expires_at,
            "payload": _json_safe(getattr(spec, "payload", {}) if spec is not None else {}),
        }
    if isinstance(event, TaskProgress):
        return {
            "event": "TaskProgress",
            "task_id": event.task_id,
            "agent_name": event.agent_name,
            "objective": event.objective,
            "created_at": event.created_at,
            "payload": _json_safe(event.payload),
        }
    if isinstance(event, TaskCompleted):
        return {
            "event": "TaskCompleted",
            "task_id": event.task_id,
            "agent_name": event.agent_name,
            "objective": event.objective,
            "created_at": event.created_at,
            "result": _json_safe(event.result),
            "task_stream_id": str(event.task_stream),
        }
    if isinstance(event, TaskFailed):
        return {
            "event": "TaskFailed",
            "task_id": event.task_id,
            "agent_name": event.agent_name,
            "objective": event.objective,
            "created_at": event.created_at,
            "error": str(event.error),
        }
    if isinstance(event, TaskExpired):
        return {
            "event": "TaskExpired",
            "task_id": event.task_id,
            "agent_name": event.agent_name,
            "objective": event.objective,
            "created_at": event.created_at,
        }
    if isinstance(event, TaskCancelled):
        return {
            "event": "TaskCancelled",
            "task_id": event.task_id,
            "agent_name": event.agent_name,
            "objective": event.objective,
            "created_at": event.created_at,
            "reason": event.reason,
        }
    return None


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _json_safe(model_dump(mode="json"))
    return str(value)


__all__ = [
    "AG2TaskBatchRunner",
    "AG2TaskBatchRunnerRequest",
    "AG2TaskBatchRunnerResult",
]
