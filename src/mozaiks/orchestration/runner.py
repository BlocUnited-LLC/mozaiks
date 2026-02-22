"""Kernel-port runner bridge for mozaiks-ai."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, AsyncIterator

from mozaiks.contracts import (
    AI_RUNNER_PROTOCOL_VERSION,
    DomainEvent,
    ResumeRequest as KernelResumeRequest,
    RunRequest as KernelRunRequest,
)
from mozaiks.contracts.ports import AIWorkflowRunnerPort

from .domain.dag import TaskDAG, TaskNode
from .domain.requests import ResumeRequest as EngineResumeRequest
from .domain.requests import RunRequest as EngineRunRequest
from .domain.runtime_context import RuntimeContext
from .domain.tool_spec import ToolSpec
from .scheduling.lifecycle import EngineRuntime, create_default_engine
from .scheduling.resume import deserialize_dag
from .tools.ui_flow import normalize_ui_submission


class KernelAIWorkflowRunner(AIWorkflowRunnerPort):
    """Concrete runner implementing the versioned kernel port."""

    def __init__(
        self,
        *,
        adapter: str = "mock",
        default_llm_config: dict[str, object] | None = None,
    ) -> None:
        self._adapter = adapter.strip().lower() or "mock"
        self._runtime: EngineRuntime = create_default_engine(
            adapter=self._adapter,
            default_llm_config=default_llm_config,
        )
        self._seq_by_run: dict[str, int] = {}

    def capabilities(self) -> dict[str, object]:
        return {
            "supports_resume": True,
            "supports_checkpoints": True,
            "supports_tools": True,
            "protocol_version": AI_RUNNER_PROTOCOL_VERSION,
            "adapters": ["mock", "ag2"],
        }

    async def cancel(self, run_id: str) -> None:
        await self._runtime.runner.cancel(run_id)

    async def run(self, request: KernelRunRequest) -> AsyncIterator[DomainEvent]:
        self._sync_request_tool_specs(request)
        plan = self._plan_from_request(request)
        internal = EngineRunRequest(
            run_id=request.run_id,
            dag=plan,
            initial_input=dict(request.input),
            metadata=dict(request.metadata),
            runtime_context=self._runtime_context_from_metadata(request.metadata),
        )

        self._seq_by_run[request.run_id] = 0
        async for event in self._runtime.runner.run(internal):
            yield self._to_domain_event(event=event, run_id=request.run_id)

    async def resume(
        self,
        request: KernelResumeRequest,
    ) -> AsyncIterator[DomainEvent]:
        checkpoint = await self._runtime.checkpoint_store.load(request.run_id)
        if checkpoint is not None and isinstance(checkpoint.dag_snapshot, dict):
            plan = deserialize_dag(checkpoint.dag_snapshot)
        else:
            plan = self._plan_from_resume_request(request)

        internal = EngineResumeRequest(
            run_id=request.run_id,
            dag=plan,
            runtime_context=self._runtime_context_from_metadata(request.metadata),
        )
        self._seq_by_run[request.run_id] = max(0, int(request.last_seq))
        async for event in self._runtime.runner.resume(internal):
            yield self._to_domain_event(event=event, run_id=request.run_id)

    def _plan_from_request(self, request: KernelRunRequest) -> TaskDAG:
        prompt_value = request.input.get("prompt")
        if prompt_value is None:
            prompt_value = str(request.input)
        return TaskDAG(
            nodes=[
                TaskNode(
                    task_id="task-1",
                    task_type=request.workflow_name,
                    prompt=str(prompt_value),
                    tools=self._request_tool_names(request),
                    metadata={
                        "workflow_version": request.workflow_version,
                        "app_id": request.app_id,
                        "user_id": request.user_id,
                        "chat_id": request.chat_id,
                    },
                )
            ],
            metadata=dict(request.metadata),
        )

    def _request_tool_names(self, request: KernelRunRequest) -> tuple[str, ...]:
        names: list[str] = []
        for raw in request.tool_specs:
            if not isinstance(raw, dict):
                continue
            name_value = raw.get("name")
            if name_value is None:
                continue
            name = str(name_value).strip()
            if not name:
                continue
            names.append(name)
        return tuple(names)

    def _plan_from_resume_request(self, request: KernelResumeRequest) -> TaskDAG:
        return TaskDAG(
            nodes=[
                TaskNode(
                    task_id="resume",
                    task_type=request.workflow_name,
                    prompt=f"resume:{request.checkpoint_key}",
                )
            ],
            metadata=dict(request.metadata),
        )

    def _sync_request_tool_specs(self, request: KernelRunRequest) -> None:
        for raw in request.tool_specs:
            if not isinstance(raw, dict):
                continue
            name_value = raw.get("name")
            if name_value is None:
                continue
            name = str(name_value).strip()
            if not name:
                continue
            if self._runtime.tool_registry.get_spec(name) is not None:
                continue

            spec = ToolSpec(
                name=name,
                description=str(raw.get("description") or f"Runtime tool {name}"),
                input_schema=(
                    dict(raw.get("input_schema", {}))
                    if isinstance(raw.get("input_schema"), dict)
                    else {}
                ),
                auto_invoke=bool(raw.get("auto_invoke", True)),
                metadata=(
                    dict(raw.get("metadata", {}))
                    if isinstance(raw.get("metadata"), dict)
                    else {}
                ),
            )
            self._runtime.tool_registry.register(spec, self._missing_tool_handler)

    @staticmethod
    def _missing_tool_handler(**_: object) -> object:
        raise NotImplementedError(
            "tool_not_implemented_in_process: provide runtime handler or sandbox context"
        )

    def _to_domain_event(self, *, event: Any, run_id: str) -> DomainEvent:
        event_type = getattr(event, "event_type", "process.event")
        payload = event.to_dict() if hasattr(event, "to_dict") else {"value": str(event)}
        occurred_at = getattr(event, "occurred_at", None)
        if not isinstance(occurred_at, datetime):
            occurred_at = datetime.now(timezone.utc)

        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}

        seq = self._seq_by_run.get(run_id, 0) + 1
        self._seq_by_run[run_id] = seq
        return DomainEvent(
            event_type=str(event_type),
            seq=seq,
            occurred_at=occurred_at,
            run_id=run_id,
            payload=payload,
            schema_version="1.0.0",
            metadata=metadata,
        )

    def _runtime_context_from_metadata(
        self,
        metadata: dict[str, Any] | None,
    ) -> RuntimeContext | None:
        if not isinstance(metadata, dict):
            return None

        raw_submissions = metadata.get("ui_tool_submissions")
        if raw_submissions is None:
            raw_submissions = metadata.get("ui_submissions")
        if not isinstance(raw_submissions, dict):
            return None

        normalized: dict[str, dict[str, Any]] = {}
        for raw_key, raw_value in raw_submissions.items():
            submission = normalize_ui_submission(raw_value)
            if submission is None:
                continue
            normalized[str(raw_key)] = submission

        if not normalized:
            return None
        return RuntimeContext(ui_tool_submissions=normalized)


def create_ai_workflow_runner(
    *,
    adapter: str = "mock",
    default_llm_config: dict[str, object] | None = None,
) -> AIWorkflowRunnerPort:
    """Create a kernel-contract runner backed by this package."""

    return KernelAIWorkflowRunner(
        adapter=adapter,
        default_llm_config=default_llm_config,
    )


def create_runner(
    *,
    adapter: str = "mock",
    default_llm_config: dict[str, object] | None = None,
) -> AIWorkflowRunnerPort:
    """Backward-compatible alias for runner factory."""

    return create_ai_workflow_runner(
        adapter=adapter,
        default_llm_config=default_llm_config,
    )
