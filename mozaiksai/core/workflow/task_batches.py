from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mozaiksai.core.adapters.ag2_task_batch_runner import (
    AG2TaskBatchRunner,
    AG2TaskBatchRunnerRequest,
)
from mozaiksai.core.ports.orchestration import RunStatus

from .generator_support.code_files import (
    extract_code_file_entries_from_payload,
    extract_code_file_map_from_payload,
    safe_relpath,
)
from .generator_support.page_plan_utils import (
    _page_from_plan,
    _page_stem_from_path,
    _page_stems,
)
from .paths import resolve_workflow_path


class TaskBatchSource(BaseModel):
    """Declarative source for the task list a workflow batch executes."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["context_variable", "structured_output"]
    path: str
    task_model: str

    @field_validator("path")
    @classmethod
    def _required_path(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("task batch source path must be non-empty")
        return text

    @field_validator("task_model")
    @classmethod
    def _required_task_model(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("task batch source task_model must be non-empty")
        return text

    def resolve_task_model(self, context_variables: dict[str, Any]) -> str:
        """Resolve ${variable_name} references in task_model from context.

        Allows dev packs to declare ``task_model: ${build_task_model}`` in
        task_batches.yaml without hardcoding a domain-specific model name in
        the harness YAML. The harness stores task items as plain dicts so the
        resolved name is currently advisory; it will be used for validation
        when typed task model resolution is introduced.
        """
        text = self.task_model
        if not text.startswith("${") or not text.endswith("}"):
            return text
        var_name = text[2:-1].strip()
        resolved = context_variables.get(var_name)
        if resolved and isinstance(resolved, str) and resolved.strip():
            return resolved.strip()  # type: ignore[no-any-return]
        return text


class TaskBatchWorker(BaseModel):
    """How a task item maps to an AG2 worker call."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["ag2_agent"] = "ag2_agent"
    agent_field: str = "initial_agent"
    prompt_field: str = "initial_message"
    context_fields: list[str] = Field(default_factory=list)

    @field_validator("agent_field", "prompt_field")
    @classmethod
    def _required_worker_field(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("worker field names must be non-empty")
        return text

    @field_validator("context_fields")
    @classmethod
    def _clean_context_fields(cls, value: list[str]) -> list[str]:
        fields: list[str] = []
        for item in value or []:
            text = str(item or "").strip()
            if text and text not in fields:
                fields.append(text)
        return fields


class TaskBatchExecution(BaseModel):
    """Dependency, concurrency, and failure policy for a task batch."""

    model_config = ConfigDict(extra="forbid")

    concurrency: int = Field(default=4, ge=1, le=32)
    dependency_field: str = "depends_on"
    failure_policy: Literal["fail_batch", "continue_with_available", "collect_errors"] = "fail_batch"
    retry_limit: int = Field(default=0, ge=0, le=5)
    timeout_seconds: int | None = Field(default=None, ge=1)

    @field_validator("dependency_field")
    @classmethod
    def _required_dependency_field(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("dependency_field must be non-empty")
        return text


class TaskBatchResult(BaseModel):
    """Where a completed batch writes normalized results."""

    model_config = ConfigDict(extra="forbid")

    context_key: str
    status_key: str
    merge_strategy: Literal["collect_task_outputs", "collect_outputs"] = "collect_task_outputs"
    require_owned_paths: bool = True

    @field_validator("context_key", "status_key")
    @classmethod
    def _required_result_key(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("result context keys must be non-empty")
        return text


class TaskBatchConveyor(BaseModel):
    """Minimal decomposition-driven task conveyor declaration.

    A conveyor keeps workflow YAML focused on the static contract: which agent
    decomposes and which agents may execute. The task list, dependencies, and
    task prompts come from the decomposition agent's structured output.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    decomposition_agent: str
    execution_agents: list[str] = Field(default_factory=list)
    concurrency: int = Field(default=4, ge=1, le=32)
    failure_policy: Literal["fail_batch", "continue_with_available", "collect_errors"] = "fail_batch"
    retry_limit: int = Field(default=0, ge=0, le=5)
    timeout_seconds: int | None = Field(default=None, ge=1)
    require_owned_paths: bool = False

    @field_validator("id", "decomposition_agent")
    @classmethod
    def _required_conveyor_text(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("conveyor id and decomposition_agent must be non-empty")
        return text

    @field_validator("execution_agents")
    @classmethod
    def _clean_execution_agents(cls, value: list[str]) -> list[str]:
        agents: list[str] = []
        for item in value or []:
            text = str(item or "").strip()
            if text and text not in agents:
                agents.append(text)
        if not agents:
            raise ValueError("conveyor execution_agents must contain at least one agent")
        return agents

    def to_batch_spec(self) -> TaskBatchSpec:
        return TaskBatchSpec(
            id=self.id,
            trigger_agent=self.decomposition_agent,
            source=TaskBatchSource(
                kind="structured_output",
                path="DecompositionPlan.tasks",
                task_model="DecomposedTask",
            ),
            worker=TaskBatchWorker(
                mode="ag2_agent",
                agent_field="execution_agent",
                prompt_field="task_prompt",
            ),
            execution=TaskBatchExecution(
                concurrency=self.concurrency,
                dependency_field="depends_on",
                failure_policy=self.failure_policy,
                retry_limit=self.retry_limit,
                timeout_seconds=self.timeout_seconds,
            ),
            result=TaskBatchResult(
                context_key=f"{self.id}_results",
                status_key=f"{self.id}_status",
                merge_strategy="collect_task_outputs",
                require_owned_paths=self.require_owned_paths,
            ),
            allowed_execution_agents=list(self.execution_agents),
        )


class TaskBatchSpec(BaseModel):
    """One workflow-local AG2 task batch declaration."""

    model_config = ConfigDict(extra="forbid")

    id: str
    trigger_agent: str
    source: TaskBatchSource
    worker: TaskBatchWorker = Field(default_factory=TaskBatchWorker)
    execution: TaskBatchExecution = Field(default_factory=TaskBatchExecution)
    result: TaskBatchResult
    allowed_execution_agents: list[str] = Field(default_factory=list, exclude=True)

    @field_validator("id", "trigger_agent")
    @classmethod
    def _required_spec_text(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("task batch id and trigger_agent must be non-empty")
        return text

    @field_validator("allowed_execution_agents")
    @classmethod
    def _clean_allowed_execution_agents(cls, value: list[str]) -> list[str]:
        agents: list[str] = []
        for item in value or []:
            text = str(item or "").strip()
            if text and text not in agents:
                agents.append(text)
        return agents


class TaskBatchesConfig(BaseModel):
    """Validated workflow-local task batch contract."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    batches: list[TaskBatchSpec] = Field(default_factory=list)
    conveyors: list[TaskBatchConveyor] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_unique_batches(self) -> TaskBatchesConfig:
        seen: set[str] = set()
        materialized_batches = [*self.batches, *[conveyor.to_batch_spec() for conveyor in self.conveyors]]
        for batch in materialized_batches:
            if batch.id in seen:
                raise ValueError(f"duplicate task batch id: {batch.id}")
            seen.add(batch.id)
        self.batches = materialized_batches
        return self


def parse_task_batches_config(payload: dict[str, Any]) -> TaskBatchesConfig:
    """Validate a raw task_batches.yaml payload."""

    if not isinstance(payload, dict):
        raise ValueError("task_batches.yaml must contain a mapping")
    return TaskBatchesConfig.model_validate(payload)


def get_task_batches_path(workflow_name: str, workflows_root: Path | None = None) -> Path:
    """Resolve the canonical task_batches.yaml path for a workflow."""

    workflow_path = resolve_workflow_path(workflow_name, root=workflows_root)
    if workflow_path is None:
        workflow_path = Path(workflows_root or ".") / str(workflow_name or "").strip()
    return workflow_path / "extended_orchestration" / "task_batches.yaml"


def load_task_batches_config(
    workflow_name: str,
    workflows_root: Path | None = None,
) -> TaskBatchesConfig | None:
    """Load and validate task_batches.yaml for a workflow when present."""

    path = get_task_batches_path(workflow_name, workflows_root)
    if not path.exists():
        return None
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return parse_task_batches_config(raw)


def workflow_has_task_batches(workflow_name: str, workflows_root: Path | None = None) -> bool:
    """Return true when a workflow declares at least one task batch."""

    config = load_task_batches_config(workflow_name, workflows_root)
    return bool(config and config.batches)


def resolve_path_value(payload: Any, path: str) -> Any:
    """Resolve a dot-separated path from dict/list/BaseModel payloads."""

    current = _to_plain_data(payload)
    for part in str(path or "").split("."):
        key = part.strip()
        if not key:
            continue
        if isinstance(current, Mapping):
            current = current.get(key)
            continue
        if isinstance(current, list):
            try:
                current = current[int(key)]
            except (ValueError, IndexError):
                return None
            continue
        return None
    return current


async def execute_task_batches_for_trigger(
    *,
    workflow_name: str,
    trigger_agent: str,
    batches_config: TaskBatchesConfig | None,
    agents: dict[str, Any],
    context_variables: dict[str, Any],
    structured_output: dict[str, Any] | None = None,
    chat_id: str | None = None,
    app_id: str | None = None,
    user_id: str | None = None,
    transport: Any | None = None,
    wf_logger: Any | None = None,
    fresh_agents_per_task: bool = True,
    agents_factory: Callable[..., Awaitable[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Execute workflow-local task batches triggered by an agent turn.

    Each task item runs through its own AG2 workflow channel. Normalized outputs
    are written back to context, then the parent AG2 Network channel continues
    from the next declared transition.
    """

    if not batches_config or not batches_config.batches:
        return {}

    matching_batches = [
        batch for batch in batches_config.batches if batch.trigger_agent == trigger_agent
    ]
    if not matching_batches:
        return {}

    results: dict[str, Any] = {}
    for batch in matching_batches:
        source_payload = (
            structured_output
            if batch.source.kind == "structured_output"
            else context_variables
        )
        if batch.source.kind == "structured_output" and not source_payload:
            if wf_logger:
                wf_logger.warning(
                    "[TASK_BATCH] %s source.kind=structured_output but structured_output is "
                    "empty or None — batch will produce no tasks. Verify the trigger agent "
                    "emits a structured output before this batch runs.",
                    batch.id,
                )
        resolved_task_model = batch.source.resolve_task_model(context_variables)
        raw_tasks = resolve_path_value(source_payload, batch.source.path)
        task_items = _normalize_task_items(raw_tasks)
        if not task_items:
            if wf_logger:
                wf_logger.debug(
                    "[TASK_BATCH] %s produced no task items from %s",
                    batch.id,
                    batch.source.path,
                )
            continue

        if wf_logger:
            wf_logger.debug(
                "[TASK_BATCH] Starting %s tasks=%d trigger=%s task_model=%s",
                batch.id,
                len(task_items),
                trigger_agent,
                resolved_task_model,
            )
        _validate_batch_owned_paths(batch, task_items)
        await _emit_task_batch_status(
            transport,
            chat_id,
            {
                "phase": "started",
                "batch_id": batch.id,
                "workflow_name": workflow_name,
                "trigger_agent": trigger_agent,
                "task_count": len(task_items),
            },
        )

        try:
            batch_output = await _execute_one_batch(
                workflow_name=workflow_name,
                batch=batch,
                task_items=task_items,
                agents=agents,
                context_variables=context_variables,
                chat_id=chat_id,
                app_id=app_id,
                user_id=user_id,
                wf_logger=wf_logger,
                fresh_agents_per_task=fresh_agents_per_task,
                agents_factory=agents_factory,
            )
        except Exception:
            context_variables[batch.result.status_key] = "failed"
            raise

        context_variables[batch.result.context_key] = batch_output["outputs"]
        context_variables[batch.result.status_key] = batch_output["status"]
        results[batch.id] = batch_output
        await _emit_task_batch_status(
            transport,
            chat_id,
            {
                "phase": batch_output["status"],
                "batch_id": batch.id,
                "workflow_name": workflow_name,
                "trigger_agent": trigger_agent,
                "task_count": len(task_items),
                "failure_count": len(batch_output.get("failed_tasks") or []),
            },
        )

    return results


async def _execute_one_batch(
    *,
    workflow_name: str,
    batch: TaskBatchSpec,
    task_items: list[dict[str, Any]],
    agents: dict[str, Any],
    context_variables: dict[str, Any],
    chat_id: str | None,
    app_id: str | None,
    user_id: str | None,
    wf_logger: Any | None,
    fresh_agents_per_task: bool,
    agents_factory: Callable[..., Awaitable[dict[str, Any]]] | None,
) -> dict[str, Any]:
    pending = {str(item["task_id"]): item for item in task_items}
    completed: dict[str, dict[str, Any]] = {}
    failed: dict[str, dict[str, Any]] = {}

    while pending:
        # A task is "resolved" when all its dependencies have settled (completed or failed).
        # Split resolved tasks into runnable (all deps completed) vs blocked (some dep failed).
        resolved: list[dict[str, Any]] = []
        blocked_by_failed: list[dict[str, Any]] = []
        for item in pending.values():
            deps = _task_dependencies(item, batch.execution.dependency_field)
            dep_strs = [str(d) for d in deps]
            if all(d in completed or d in failed for d in dep_strs):
                if any(d in failed for d in dep_strs):
                    blocked_by_failed.append(item)
                else:
                    resolved.append(item)

        # Drain tasks that can never run because a required dependency failed.
        for item in blocked_by_failed:
            task_id = str(item["task_id"])
            pending.pop(task_id, None)
            failed_dep = next(
                d for d in _task_dependencies(item, batch.execution.dependency_field)
                if str(d) in failed
            )
            failed[task_id] = {
                "task_id": task_id,
                "status": "failed",
                "error": f"dependency '{failed_dep}' failed",
                "worker_agent": str(item.get(batch.worker.agent_field) or ""),
            }

        ready = resolved
        if not ready:
            if pending:
                # Nothing resolved and pending is still non-empty → genuine cycle.
                unresolved = {
                    task_id: _task_dependencies(item, batch.execution.dependency_field)
                    for task_id, item in pending.items()
                }
                raise ValueError(
                    f"task batch {batch.id!r} has unresolved or cyclic dependencies: {unresolved}"
                )
            break

        semaphore = asyncio.Semaphore(batch.execution.concurrency)
        current_batch_outputs = _build_batch_outputs(
            batch=batch,
            completed=completed,
            failed=failed,
            task_count=len(task_items),
            status="running",
        )
        settled = await asyncio.gather(
            *[
                _run_one_task(
                    workflow_name=workflow_name,
                    batch=batch,
                    task=item,
                    all_task_items=task_items,
                    base_context=context_variables,
                    completed_task_outputs=completed,
                    current_batch_outputs=current_batch_outputs,
                    agents=agents,
                    chat_id=chat_id,
                    app_id=app_id,
                    user_id=user_id,
                    semaphore=semaphore,
                    fresh_agents_per_task=fresh_agents_per_task,
                    agents_factory=agents_factory,
                )
                for item in ready
            ],
            return_exceptions=True,
        )

        for task, outcome in zip(ready, settled, strict=False):
            task_id = str(task["task_id"])
            pending.pop(task_id, None)
            if isinstance(outcome, Exception):
                failed[task_id] = {
                    "task_id": task_id,
                    "status": "failed",
                    "error": str(outcome),
                    "worker_agent": str(task.get(batch.worker.agent_field) or ""),
                }
                if batch.execution.failure_policy == "fail_batch":
                    context_variables[batch.result.context_key] = _build_batch_outputs(
                        batch=batch,
                        completed=completed,
                        failed=failed,
                        task_count=len(task_items),
                        status="failed",
                    )
                    raise RuntimeError(
                        f"task batch {batch.id!r} failed at task {task_id!r}: {outcome}"
                    ) from outcome
                continue
            completed[task_id] = outcome  # type: ignore[assignment]

        if pending:
            context_variables[batch.result.context_key] = _build_batch_outputs(
                batch=batch,
                completed=completed,
                failed=failed,
                task_count=len(task_items),
                status="running",
            )
            context_variables[batch.result.status_key] = "running"

    status = "completed"
    if failed:
        status = (
            "partial"
            if batch.execution.failure_policy == "continue_with_available"
            else "completed_with_errors"
        )
    outputs = _build_batch_outputs(
        batch=batch,
        completed=completed,
        failed=failed,
        task_count=len(task_items),
        status=status,
    )
    return {
        "status": status,
        "outputs": outputs,
        "completed_tasks": list(completed),
        "failed_tasks": list(failed),
    }


async def _run_one_task(
    *,
    workflow_name: str,
    batch: TaskBatchSpec,
    task: dict[str, Any],
    all_task_items: list[dict[str, Any]],
    base_context: dict[str, Any],
    completed_task_outputs: dict[str, dict[str, Any]],
    current_batch_outputs: dict[str, Any],
    agents: dict[str, Any],
    chat_id: str | None,
    app_id: str | None,
    user_id: str | None,
    semaphore: asyncio.Semaphore,
    fresh_agents_per_task: bool,
    agents_factory: Callable[..., Awaitable[dict[str, Any]]] | None,
) -> dict[str, Any]:
    agent_name = str(task.get(batch.worker.agent_field) or "").strip()
    prompt = str(task.get(batch.worker.prompt_field) or "").strip()
    if not agent_name:
        raise ValueError(f"task {task.get('task_id')!r} is missing worker agent")
    if batch.allowed_execution_agents and agent_name not in set(batch.allowed_execution_agents):
        allowed = ", ".join(batch.allowed_execution_agents)
        raise ValueError(
            f"task {task.get('task_id')!r} references execution agent {agent_name!r} "
            f"outside allowed conveyor agents: {allowed}"
        )
    if not prompt:
        raise ValueError(f"task {task.get('task_id')!r} is missing worker prompt")

    task_context = _build_task_context(
        base_context=base_context,
        task=task,
        all_task_items=all_task_items,
        batch=batch,
        completed_task_outputs=completed_task_outputs,
        current_batch_outputs=current_batch_outputs,
        chat_id=chat_id,
        app_id=app_id,
        user_id=user_id,
    )
    scoped_prompt = _build_scoped_worker_prompt(prompt, task_context)

    if fresh_agents_per_task:
        if agents_factory is None:
            from .agents import create_agents

            async def _default_factory(wf_name: str, ctx: Any, seed: Any) -> dict[str, Any]:
                return await create_agents(wf_name, context_variables=ctx, cache_seed=seed)

            agents_factory = _default_factory
        # Call factory with positional args matching the convention in orchestration_patterns:
        # agents_factory(workflow_name, context, cache_seed)
        task_agents = await agents_factory(workflow_name, task_context, None)
        agent = task_agents.get(agent_name)
    else:
        agent = agents.get(agent_name)

    if agent is None:
        raise ValueError(f"task {task.get('task_id')!r} references unknown agent {agent_name!r}")

    async with semaphore:
        runner_result = None
        attempts = batch.execution.retry_limit + 1
        last_error: str | None = None
        for _attempt in range(attempts):
            runner_result = await AG2TaskBatchRunner().run(
                AG2TaskBatchRunnerRequest(
                    workflow_name=workflow_name,
                    batch_id=batch.id,
                    task_id=str(task["task_id"]),
                    chat_id=chat_id,
                    app_id=app_id,
                    agent_name=agent_name,
                    agent=agent,
                    prompt=scoped_prompt,
                    context_variables=task_context,
                    structured_registry=_structured_registry_for_agent(workflow_name, agent_name),
                    timeout_seconds=batch.execution.timeout_seconds,
                )
            )
            if runner_result.status is RunStatus.COMPLETED:
                break
            last_error = runner_result.error or runner_result.status.value
        if runner_result is None or runner_result.status is not RunStatus.COMPLETED:
            raise RuntimeError(
                f"AG2 task channel failed for task {task.get('task_id')!r}: {last_error or 'unknown error'}"
            )

    output = _normalize_agent_reply(runner_result.output)
    if not isinstance(output, dict):
        output = {"agent_message": str(output)}
    _reject_task_output_identity_drift(task, output)
    canonical_code_files = extract_code_file_entries_from_payload(output)
    if canonical_code_files:
        output["code_files"] = canonical_code_files
    if str(task.get("task_type") or "").strip() == "page_bundle":
        output["code_files"] = _normalize_owned_page_files_from_plan(
            output.get("code_files"),
            task=task,
            base_context=base_context,
        )
        output["_page_materialization_source"] = "app_build_plan.pages"
        output["_page_materialized_paths"] = [
            path
            for path in _normalize_owned_paths(task.get("owned_paths"))
            if _page_stem_from_path(path)
        ]
    _validate_task_output_ownership(batch, task, output)
    _stamp_task_output_identity(task, output)
    output.setdefault("_task_id", str(task["task_id"]))
    output.setdefault("_worker_agent", agent_name)
    output.setdefault("_owned_paths", list(task.get("owned_paths") or []))
    output.setdefault(
        "_ag2_task_channel",
        {
            "channel_id": runner_result.channel_id,
            "close_reason": runner_result.close_reason,
        },
    )
    return output


def _build_scoped_worker_prompt(prompt: str, task_context: dict[str, Any]) -> str:
    envelope = {
        "current_task_batch_id": task_context.get("current_task_batch_id"),
        "current_task_id": task_context.get("current_task_id"),
        "current_task": task_context.get("current_task"),
        "dependency_task_outputs": task_context.get("dependency_task_outputs") or {},
    }
    return (
        f"{prompt}\n\n"
        "[TASK BATCH CONTEXT]\n"
        "This JSON envelope is authoritative for the worker response. "
        "The response `task_id` MUST equal `current_task_id`; the response `kind` MUST equal `current_task.kind`.\n"
        f"{json.dumps(envelope, indent=2, sort_keys=True, default=str)}"
    )


def _reject_task_output_identity_drift(task: dict[str, Any], output: dict[str, Any]) -> None:
    task_id = str(task.get("task_id") or "").strip()
    output_task_id = str(output.get("task_id") or "").strip()
    if task_id and output_task_id and output_task_id != task_id:
        raise ValueError(
            f"task {task_id!r} emitted mismatched task_id {output_task_id!r}"
        )

    task_kind = str(task.get("kind") or "").strip()
    output_kind = str(output.get("kind") or "").strip()
    if task_kind and output_kind and output_kind != task_kind:
        raise ValueError(
            f"task {task_id!r} emitted kind {output_kind!r}; expected {task_kind!r}"
        )


def _stamp_task_output_identity(task: dict[str, Any], output: dict[str, Any]) -> None:
    task_id = str(task.get("task_id") or "").strip()
    if task_id and not str(output.get("task_id") or "").strip():
        output["task_id"] = task_id

    task_kind = str(task.get("kind") or "").strip()
    if task_kind and not str(output.get("kind") or "").strip():
        output["kind"] = task_kind


def _structured_registry_for_agent(workflow_name: str, agent_name: str) -> dict[str, Any]:
    try:
        from .outputs.structured import load_workflow_structured_outputs

        _, registry = load_workflow_structured_outputs(workflow_name)
    except Exception:
        return {}
    model_cls = registry.get(agent_name) if isinstance(registry, dict) else None
    return {agent_name: model_cls} if model_cls is not None else {}


def _planned_pages(base_context: dict[str, Any]) -> list[dict[str, Any]]:
    plan = base_context.get("app_build_plan")
    if isinstance(plan, dict) and isinstance(plan.get("pages"), list):
        return [dict(page) for page in plan["pages"] if isinstance(page, dict)]
    pages = base_context.get("pages")
    if isinstance(pages, list):
        return [dict(page) for page in pages if isinstance(page, dict)]
    return []


def _normalize_owned_page_files_from_plan(
    code_files: Any,
    *,
    task: dict[str, Any],
    base_context: dict[str, Any],
) -> list[dict[str, str]]:
    file_map: dict[str, str] = {}
    if isinstance(code_files, list):
        for entry in code_files:
            if not isinstance(entry, dict):
                continue
            filename = entry.get("filename") or entry.get("path")
            content = entry.get("content")
            safe = safe_relpath(str(filename or ""))
            if not safe or content is None:
                continue
            file_map[safe] = str(content)

    owned_page_paths = [
        path
        for path in _normalize_owned_paths(task.get("owned_paths"))
        if _page_stem_from_path(path)
    ]
    if not owned_page_paths:
        return [
            {"filename": filename, "content": content}
            for filename, content in file_map.items()
        ]

    planned_by_stem: dict[str, dict[str, Any]] = {}
    for page in _planned_pages(base_context):
        for stem in _page_stems(page):
            planned_by_stem.setdefault(stem, page)

    for path in owned_page_paths:
        stem = _page_stem_from_path(path)  # type: ignore[assignment]
        if not stem:
            continue
        planned = planned_by_stem.get(stem)
        if not planned:
            continue
        file_map[path] = yaml.safe_dump(
            _page_from_plan(planned, stem),
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        )
    return [
        {"filename": filename, "content": content}
        for filename, content in file_map.items()
    ]


def _normalize_owned_paths(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    paths: list[str] = []
    for item in value:
        safe = safe_relpath(str(item or ""))
        if safe and safe not in paths:
            paths.append(safe)
    return paths


def _validate_batch_owned_paths(batch: TaskBatchSpec, task_items: list[dict[str, Any]]) -> None:
    if not batch.result.require_owned_paths:
        return

    owner_by_path: dict[str, str] = {}
    duplicate_paths: dict[str, list[str]] = {}
    for task in task_items:
        task_id = str(task.get("task_id") or "").strip()
        owned_paths = _normalize_owned_paths(task.get("owned_paths"))
        if not owned_paths:
            raise ValueError(
                f"task batch {batch.id!r} requires owned_paths, but task {task_id!r} declares none"
            )
        for path in owned_paths:
            previous_owner = owner_by_path.get(path)
            if previous_owner and previous_owner != task_id:
                duplicate_paths.setdefault(path, [previous_owner]).append(task_id)
            else:
                owner_by_path[path] = task_id

    if duplicate_paths:
        details = {
            path: sorted(set(owners))
            for path, owners in sorted(duplicate_paths.items())
        }
        raise ValueError(
            f"task batch {batch.id!r} has owned_paths declared by multiple tasks: {details}"
        )


def _validate_task_output_ownership(
    batch: TaskBatchSpec,
    task: dict[str, Any],
    output: dict[str, Any],
) -> None:
    if not batch.result.require_owned_paths:
        return

    task_id = str(task.get("task_id") or "").strip()
    owned_paths = set(_normalize_owned_paths(task.get("owned_paths")))
    if not owned_paths:
        raise ValueError(
            f"task batch {batch.id!r} requires owned_paths, but task {task_id!r} declares none"
        )

    emitted_paths = set(extract_code_file_map_from_payload(output))
    if not emitted_paths:
        raise ValueError(
            f"task batch {batch.id!r} task {task_id!r} did not emit any code files"
        )

    optional_paths = _optional_task_output_paths(task)
    unexpected = sorted(emitted_paths.difference(owned_paths).difference(optional_paths))
    if unexpected:
        raise ValueError(
            f"task batch {batch.id!r} task {task_id!r} emitted files outside owned_paths: {unexpected}"
        )

    missing = sorted(owned_paths.difference(emitted_paths).difference(optional_paths))
    if missing:
        raise ValueError(
            f"task batch {batch.id!r} task {task_id!r} did not emit required owned_paths: {missing}"
        )


def _build_task_context(
    *,
    base_context: dict[str, Any],
    task: dict[str, Any],
    all_task_items: list[dict[str, Any]],
    batch: TaskBatchSpec,
    completed_task_outputs: dict[str, dict[str, Any]],
    current_batch_outputs: dict[str, Any],
    chat_id: str | None,
    app_id: str | None,
    user_id: str | None,
) -> dict[str, Any]:
    task_context = dict(base_context)
    task_context["task_run_mode"] = True
    task_context["current_task_batch_id"] = batch.id
    task_context["current_task_id"] = str(task["task_id"])
    task_context["current_task"] = dict(task)
    task_context["decomposition_plan"] = {
        "batch_id": batch.id,
        "tasks": [dict(item) for item in all_task_items],
    }
    task_context["current_build_task_id"] = str(task["task_id"])
    task_context["current_build_task_type"] = str(task.get("task_type") or "")
    task_context["current_build_task"] = dict(task)
    task_dependencies = _task_dependencies(task, batch.execution.dependency_field)
    task_context["completed_task_outputs"] = dict(completed_task_outputs)
    task_context["dependency_task_outputs"] = {
        dep: completed_task_outputs[dep]
        for dep in task_dependencies
        if dep in completed_task_outputs
    }
    task_context[batch.result.context_key] = dict(current_batch_outputs)
    task_context[batch.result.status_key] = str(
        (current_batch_outputs.get("_meta") or {}).get("status") or "running"
    )
    if chat_id:
        task_context["parent_chat_id"] = chat_id
    if app_id:
        task_context["app_id"] = app_id
    if user_id:
        task_context["user_id"] = user_id

    embedded_context = task.get("context_variables")
    if isinstance(embedded_context, Mapping):
        task_context.update(dict(embedded_context))

    for field_name in batch.worker.context_fields:
        if field_name in task:
            task_context[field_name] = task[field_name]
    return task_context


def _optional_task_output_paths(task: dict[str, Any]) -> set[str]:
    task_type = str(task.get("task_type") or "").strip()
    if task_type == "page_bundle":
        return {
            "brand/theme_config.json",
            "config/asset_manifest.json",
            "data/contract.json",
            "provenance.yaml",
            "config/shell.json",
            "ui/index.js",
            "ui/route_manifest.json",
        }
    if task_type != "module_contract":
        return set()
    module_id = str(task.get("capability_pack_id") or "").strip()
    if not module_id:
        return set()
    prefix = f"modules/{module_id}"
    return {
        f"{prefix}/contracts/notifications.yaml",
        f"{prefix}/contracts/policy_hooks.yaml",
        f"{prefix}/contracts/profile.yaml",
        f"{prefix}/contracts/relationships.yaml",
        f"{prefix}/contracts/reactions.yaml",
        f"{prefix}/runtime_extensions.yaml",
    }


def _build_batch_outputs(
    *,
    batch: TaskBatchSpec,
    completed: dict[str, dict[str, Any]],
    failed: dict[str, dict[str, Any]],
    task_count: int,
    status: str,
) -> dict[str, Any]:
    outputs: dict[str, Any] = dict(completed)
    if failed:
        outputs["_failed"] = dict(failed)
    outputs["_meta"] = {
        "batch_id": batch.id,
        "status": status,
        "task_count": task_count,
        "concurrency": batch.execution.concurrency,
        "completed_tasks": list(completed),
        "failed_tasks": list(failed),
        "result_context_key": batch.result.context_key,
    }
    return outputs


def _normalize_task_items(raw_tasks: Any) -> list[dict[str, Any]]:
    raw_tasks = _to_plain_data(raw_tasks)
    if isinstance(raw_tasks, Mapping):
        raw_iterable = list(raw_tasks.values())
    elif isinstance(raw_tasks, list):
        raw_iterable = raw_tasks
    else:
        raw_iterable = []

    items: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_iterable):
        item = _to_plain_data(raw)
        if not isinstance(item, Mapping):
            continue
        normalized = dict(item)
        task_id = str(
            normalized.get("task_id")
            or normalized.get("id")
            or normalized.get("name")
            or f"task_{index + 1}"
        ).strip()
        if not task_id:
            continue
        normalized["task_id"] = task_id
        items.append(normalized)
    return items


def _task_dependencies(task: dict[str, Any], dependency_field: str) -> list[str]:
    raw = task.get(dependency_field)
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw] if raw.strip() else []
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item or "").strip()]
    return []


def _normalize_agent_reply(reply: Any) -> Any:
    body = getattr(reply, "body", reply)
    return _to_plain_data(body)


def _to_plain_data(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return text
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return value
    return value


async def _emit_task_batch_status(
    transport: Any | None,
    chat_id: str | None,
    payload: dict[str, Any],
) -> None:
    if not transport or not chat_id:
        return
    batch_id = str(payload.get("batch_id") or "task_batch").strip() or "task_batch"
    phase = str(payload.get("phase") or "working").strip() or "working"
    status = "complete" if phase in {"completed", "complete", "success", "succeeded", "done"} else phase
    message = f"Task batch {batch_id} {phase}."
    try:
        await transport.send_tool_call_event(
            event_id=f"task-batch-{batch_id}",
            chat_id=chat_id,
            tool_name="SystemStatusCard",
            component_name="SystemStatusCard",
            display_type="inline",
            awaiting_response=False,
            agent_name="System",
            payload={
                **payload,
                "schema_version": "mozaiks.system_status.ui.v1",
                "workflow_name": payload.get("workflow_name"),
                "agent": "System",
                "status": status,
                "message": message,
                "display": "inline",
                "mode": "inline",
                "interaction_type": "ui_surface",
                "awaiting_response": False,
                "component_type": "SystemStatusCard",
            },
        )
    except Exception:
        return


__all__ = [
    "TaskBatchExecution",
    "TaskBatchConveyor",
    "TaskBatchResult",
    "TaskBatchSource",
    "TaskBatchSpec",
    "TaskBatchWorker",
    "TaskBatchesConfig",
    "get_task_batches_path",
    "load_task_batches_config",
    "parse_task_batches_config",
    "execute_task_batches_for_trigger",
    "resolve_path_value",
    "workflow_has_task_batches",
]
