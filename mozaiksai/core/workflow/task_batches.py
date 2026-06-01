from __future__ import annotations

from pathlib import Path, PurePosixPath
import asyncio
import json
import re
from collections.abc import Mapping
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .generator_support.code_files import (
    extract_code_file_entries_from_payload,
    extract_code_file_map_from_payload,
    safe_relpath,
)
from .paths import resolve_workflow_path


class TaskBatchSource(BaseModel):
    """Declarative source for the task list a workflow batch executes."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["context_variable", "structured_output"]
    path: str
    task_model: str

    @field_validator("path", "task_model")
    @classmethod
    def _required_text(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("task batch source fields must be non-empty")
        return text


class TaskBatchWorker(BaseModel):
    """How a task item maps to an AG2 worker call."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["ag2_agent"] = "ag2_agent"
    agent_field: str = "initial_agent"
    prompt_field: str = "initial_message"
    context_fields: List[str] = Field(default_factory=list)

    @field_validator("agent_field", "prompt_field")
    @classmethod
    def _required_worker_field(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("worker field names must be non-empty")
        return text

    @field_validator("context_fields")
    @classmethod
    def _clean_context_fields(cls, value: List[str]) -> List[str]:
        fields: List[str] = []
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
    timeout_seconds: Optional[int] = Field(default=None, ge=1)

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


class TaskBatchSpec(BaseModel):
    """One workflow-local AG2 task batch declaration."""

    model_config = ConfigDict(extra="forbid")

    id: str
    trigger_agent: str
    source: TaskBatchSource
    worker: TaskBatchWorker = Field(default_factory=TaskBatchWorker)
    execution: TaskBatchExecution = Field(default_factory=TaskBatchExecution)
    result: TaskBatchResult

    @field_validator("id", "trigger_agent")
    @classmethod
    def _required_spec_text(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("task batch id and trigger_agent must be non-empty")
        return text


class TaskBatchesConfig(BaseModel):
    """Validated workflow-local task batch contract."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    batches: List[TaskBatchSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_unique_batches(self) -> "TaskBatchesConfig":
        seen: set[str] = set()
        for batch in self.batches:
            if batch.id in seen:
                raise ValueError(f"duplicate task batch id: {batch.id}")
            seen.add(batch.id)
        return self


def parse_task_batches_config(payload: Dict[str, Any]) -> TaskBatchesConfig:
    """Validate a raw task_batches.yaml payload."""

    if not isinstance(payload, dict):
        raise ValueError("task_batches.yaml must contain a mapping")
    return TaskBatchesConfig.model_validate(payload)


def get_task_batches_path(workflow_name: str, workflows_root: Optional[Path] = None) -> Path:
    """Resolve the canonical task_batches.yaml path for a workflow."""

    workflow_path = resolve_workflow_path(workflow_name, root=workflows_root)
    if workflow_path is None:
        workflow_path = Path(workflows_root or ".") / str(workflow_name or "").strip()
    return workflow_path / "extended_orchestration" / "task_batches.yaml"


def load_task_batches_config(
    workflow_name: str,
    workflows_root: Optional[Path] = None,
) -> Optional[TaskBatchesConfig]:
    """Load and validate task_batches.yaml for a workflow when present."""

    path = get_task_batches_path(workflow_name, workflows_root)
    if not path.exists():
        return None
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return parse_task_batches_config(raw)


def workflow_has_task_batches(workflow_name: str, workflows_root: Optional[Path] = None) -> bool:
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
    batches_config: Optional[TaskBatchesConfig],
    agents: Dict[str, Any],
    context_variables: Dict[str, Any],
    structured_output: Optional[Dict[str, Any]] = None,
    chat_id: Optional[str] = None,
    app_id: Optional[str] = None,
    user_id: Optional[str] = None,
    transport: Optional[Any] = None,
    wf_logger: Optional[Any] = None,
    fresh_agents_per_task: bool = True,
    agents_factory: Optional[Callable[..., Awaitable[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    """Execute workflow-local task batches triggered by an agent turn.

    Worker agents are called as async functions inside the current workflow run,
    normalized outputs are written back to context, and the parent handoff graph
    continues deterministically.
    """

    if not batches_config or not batches_config.batches:
        return {}

    matching_batches = [
        batch for batch in batches_config.batches if batch.trigger_agent == trigger_agent
    ]
    if not matching_batches:
        return {}

    results: Dict[str, Any] = {}
    for batch in matching_batches:
        source_payload = (
            structured_output
            if batch.source.kind == "structured_output"
            else context_variables
        )
        raw_tasks = resolve_path_value(source_payload, batch.source.path)
        task_items = _normalize_task_items(raw_tasks)
        if not task_items:
            if wf_logger:
                wf_logger.info(
                    "[TASK_BATCH] %s produced no task items from %s",
                    batch.id,
                    batch.source.path,
                )
            continue

        if wf_logger:
            wf_logger.info(
                "[TASK_BATCH] Starting %s tasks=%d trigger=%s",
                batch.id,
                len(task_items),
                trigger_agent,
            )
        _validate_batch_owned_paths(batch, task_items)
        await _emit_task_batch_activity(
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
        await _emit_task_batch_activity(
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
    task_items: List[Dict[str, Any]],
    agents: Dict[str, Any],
    context_variables: Dict[str, Any],
    chat_id: Optional[str],
    app_id: Optional[str],
    user_id: Optional[str],
    wf_logger: Optional[Any],
    fresh_agents_per_task: bool,
    agents_factory: Optional[Callable[..., Awaitable[Dict[str, Any]]]],
) -> Dict[str, Any]:
    pending = {str(item["task_id"]): item for item in task_items}
    completed: Dict[str, Dict[str, Any]] = {}
    failed: Dict[str, Dict[str, Any]] = {}

    while pending:
        ready = [
            item
            for item in pending.values()
            if all(
                str(dep) in completed
                for dep in _task_dependencies(item, batch.execution.dependency_field)
            )
        ]
        if not ready:
            unresolved = {
                task_id: _task_dependencies(item, batch.execution.dependency_field)
                for task_id, item in pending.items()
            }
            raise ValueError(
                f"task batch {batch.id!r} has unresolved or cyclic dependencies: {unresolved}"
            )

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
                _run_task_with_retries(
                    workflow_name=workflow_name,
                    batch=batch,
                    task=item,
                    base_context=context_variables,
                    completed_task_outputs=completed,
                    current_batch_outputs=current_batch_outputs,
                    agents=agents,
                    chat_id=chat_id,
                    app_id=app_id,
                    user_id=user_id,
                    semaphore=semaphore,
                    wf_logger=wf_logger,
                    fresh_agents_per_task=fresh_agents_per_task,
                    agents_factory=agents_factory,
                )
                for item in ready
            ],
            return_exceptions=True,
        )

        for task, outcome in zip(ready, settled):
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
            completed[task_id] = outcome

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


async def _run_task_with_retries(
    *,
    workflow_name: str,
    batch: TaskBatchSpec,
    task: Dict[str, Any],
    base_context: Dict[str, Any],
    completed_task_outputs: Dict[str, Dict[str, Any]],
    current_batch_outputs: Dict[str, Any],
    agents: Dict[str, Any],
    chat_id: Optional[str],
    app_id: Optional[str],
    user_id: Optional[str],
    semaphore: asyncio.Semaphore,
    wf_logger: Optional[Any],
    fresh_agents_per_task: bool,
    agents_factory: Optional[Callable[..., Awaitable[Dict[str, Any]]]],
) -> Dict[str, Any]:
    attempts = batch.execution.retry_limit + 1
    last_error: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        try:
            async with semaphore:
                return await _run_one_task(
                    workflow_name=workflow_name,
                    batch=batch,
                    task=task,
                    base_context=base_context,
                    completed_task_outputs=completed_task_outputs,
                    current_batch_outputs=current_batch_outputs,
                    agents=agents,
                    chat_id=chat_id,
                    app_id=app_id,
                    user_id=user_id,
                    fresh_agents_per_task=fresh_agents_per_task,
                    agents_factory=agents_factory,
                )
        except Exception as exc:
            last_error = exc
            if wf_logger:
                wf_logger.warning(
                    "[TASK_BATCH] task=%s attempt=%d/%d failed: %s",
                    task.get("task_id"),
                    attempt,
                    attempts,
                    exc,
                )
    assert last_error is not None
    raise last_error


async def _run_one_task(
    *,
    workflow_name: str,
    batch: TaskBatchSpec,
    task: Dict[str, Any],
    base_context: Dict[str, Any],
    completed_task_outputs: Dict[str, Dict[str, Any]],
    current_batch_outputs: Dict[str, Any],
    agents: Dict[str, Any],
    chat_id: Optional[str],
    app_id: Optional[str],
    user_id: Optional[str],
    fresh_agents_per_task: bool,
    agents_factory: Optional[Callable[..., Awaitable[Dict[str, Any]]]],
) -> Dict[str, Any]:
    agent_name = str(task.get(batch.worker.agent_field) or "").strip()
    prompt = str(task.get(batch.worker.prompt_field) or "").strip()
    if not agent_name:
        raise ValueError(f"task {task.get('task_id')!r} is missing worker agent")
    if not prompt:
        raise ValueError(f"task {task.get('task_id')!r} is missing worker prompt")

    task_context = _build_task_context(
        base_context=base_context,
        task=task,
        batch=batch,
        completed_task_outputs=completed_task_outputs,
        current_batch_outputs=current_batch_outputs,
        chat_id=chat_id,
        app_id=app_id,
        user_id=user_id,
    )

    if fresh_agents_per_task:
        if agents_factory is None:
            from .agents import create_agents

            async def _default_factory(**kwargs: Any) -> Dict[str, Any]:
                return await create_agents(**kwargs)

            agents_factory = _default_factory
        task_agents = await agents_factory(
            workflow_name=workflow_name,
            context_variables=task_context,
            cache_seed=None,
        )
        agent = task_agents.get(agent_name)
    else:
        agent = agents.get(agent_name)

    if agent is None:
        raise ValueError(f"task {task.get('task_id')!r} references unknown agent {agent_name!r}")

    ask_kwargs = {"variables": dict(task_context)}
    if batch.execution.timeout_seconds:
        reply = await asyncio.wait_for(
            agent.ask(prompt, **ask_kwargs),
            timeout=batch.execution.timeout_seconds,
        )
    else:
        reply = await agent.ask(prompt, **ask_kwargs)

    output = _normalize_agent_reply(reply)
    if not isinstance(output, dict):
        output = {"agent_message": str(output)}
    canonical_code_files = extract_code_file_entries_from_payload(output)
    if canonical_code_files:
        output["code_files"] = canonical_code_files
    if str(task.get("task_type") or "").strip() == "page_bundle":
        output["code_files"] = _normalize_owned_page_files_from_plan(
            output.get("code_files"),
            task=task,
            base_context=base_context,
        )
    _validate_task_output_ownership(batch, task, output)
    output.setdefault("_task_id", str(task["task_id"]))
    output.setdefault("_worker_agent", agent_name)
    output.setdefault("_owned_paths", list(task.get("owned_paths") or []))
    return output


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", str(value or "").strip()).strip("_").lower()


def _page_stem_from_path(path: str) -> Optional[str]:
    safe = safe_relpath(path)
    if not safe:
        return None
    pure = PurePosixPath(safe)
    if len(pure.parts) == 3 and pure.parts[0] == "ui" and pure.parts[1] == "pages" and pure.suffix in {".yaml", ".yml"}:
        return _slug(pure.stem)
    return None


def _page_stems(page: Dict[str, Any]) -> set[str]:
    stems: set[str] = set()
    route = str(page.get("route") or "").strip()
    if route and route != "/":
        stems.add(_slug(route.strip("/").split("/")[-1]))
    for key in ("name", "id", "surface_id"):
        value = str(page.get(key) or "").strip()
        if value:
            stems.add(_slug(value))
    return {stem for stem in stems if stem}


def _planned_pages(base_context: Dict[str, Any]) -> List[Dict[str, Any]]:
    plan = base_context.get("app_build_plan")
    if isinstance(plan, dict) and isinstance(plan.get("pages"), list):
        return [dict(page) for page in plan["pages"] if isinstance(page, dict)]
    pages = base_context.get("pages")
    if isinstance(pages, list):
        return [dict(page) for page in pages if isinstance(page, dict)]
    return []


def _page_from_plan(page: Dict[str, Any], stem: str) -> Dict[str, Any]:
    title = str(page.get("title") or page.get("name") or stem.replace("_", " ").title()).strip()
    route = str(page.get("route") or f"/{stem.replace('_', '-')}").strip()
    sections: List[Dict[str, Any]] = []
    hints = page.get("sections_hint")
    if isinstance(hints, list):
        for index, hint in enumerate(hints):
            if not isinstance(hint, dict):
                continue
            primitive = str(hint.get("primitive") or "PageHeader").strip()
            section_id = str(hint.get("section_id_hint") or f"{stem}-{index + 1}").strip()
            sections.append(
                {
                    "id": section_id,
                    "primitive": primitive,
                    "title": hint.get("title_hint"),
                    "config": hint.get("config_hint") if isinstance(hint.get("config_hint"), dict) else {},
                    "event_triggers": [],
                    "roles": None,
                }
            )
    if not sections:
        sections.append(
            {
                "id": f"{stem}-header",
                "primitive": "PageHeader",
                "title": None,
                "config": {"title": title, "subtitle": str(page.get("purpose") or "").strip()},
                "event_triggers": [],
                "roles": None,
            }
        )
    return {
        "name": str(page.get("name") or title).strip(),
        "route": route,
        "title": title,
        "page_type": str(page.get("page_type") or page.get("ui_layout") or "standard").strip(),
        "layout": str(page.get("layout") or "stack").strip(),
        "shell_mode": str(page.get("shell_mode") or "workspace").strip(),
        "roles": page.get("roles"),
        "navigation": {
            "id": stem,
            "label": title,
            "icon": None,
            "scope": "global",
            "order": 10,
            "visible": True,
            "placement": None,
        },
        "sections": sections,
        "extensions": None,
    }


def _normalize_owned_page_files_from_plan(
    code_files: Any,
    *,
    task: Dict[str, Any],
    base_context: Dict[str, Any],
) -> List[Dict[str, str]]:
    file_map: Dict[str, str] = {}
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

    planned_by_stem: Dict[str, Dict[str, Any]] = {}
    for page in _planned_pages(base_context):
        for stem in _page_stems(page):
            planned_by_stem.setdefault(stem, page)

    for path in owned_page_paths:
        stem = _page_stem_from_path(path)
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


def _normalize_owned_paths(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    paths: List[str] = []
    for item in value:
        safe = safe_relpath(str(item or ""))
        if safe and safe not in paths:
            paths.append(safe)
    return paths


def _validate_batch_owned_paths(batch: TaskBatchSpec, task_items: List[Dict[str, Any]]) -> None:
    if not batch.result.require_owned_paths:
        return

    owner_by_path: Dict[str, str] = {}
    duplicate_paths: Dict[str, List[str]] = {}
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
    task: Dict[str, Any],
    output: Dict[str, Any],
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
    base_context: Dict[str, Any],
    task: Dict[str, Any],
    batch: TaskBatchSpec,
    completed_task_outputs: Dict[str, Dict[str, Any]],
    current_batch_outputs: Dict[str, Any],
    chat_id: Optional[str],
    app_id: Optional[str],
    user_id: Optional[str],
) -> Dict[str, Any]:
    task_context = dict(base_context)
    task_context["task_run_mode"] = True
    task_context["current_task_batch_id"] = batch.id
    task_context["current_task_id"] = str(task["task_id"])
    task_context["current_task"] = dict(task)
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


def _optional_task_output_paths(task: Dict[str, Any]) -> set[str]:
    task_type = str(task.get("task_type") or "").strip()
    if task_type == "page_bundle":
        return {
            "brand/theme_config.json",
            "config/asset_manifest.json",
            "config/data.json",
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
        f"{prefix}/contracts/profile.yaml",
        f"{prefix}/contracts/reactions.yaml",
        f"{prefix}/runtime_extensions.yaml",
    }


def _build_batch_outputs(
    *,
    batch: TaskBatchSpec,
    completed: Dict[str, Dict[str, Any]],
    failed: Dict[str, Dict[str, Any]],
    task_count: int,
    status: str,
) -> Dict[str, Any]:
    outputs: Dict[str, Any] = dict(completed)
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


def _normalize_task_items(raw_tasks: Any) -> List[Dict[str, Any]]:
    raw_tasks = _to_plain_data(raw_tasks)
    if isinstance(raw_tasks, Mapping):
        raw_iterable = list(raw_tasks.values())
    elif isinstance(raw_tasks, list):
        raw_iterable = raw_tasks
    else:
        raw_iterable = []

    items: List[Dict[str, Any]] = []
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


def _task_dependencies(task: Dict[str, Any], dependency_field: str) -> List[str]:
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


async def _emit_task_batch_activity(
    transport: Optional[Any],
    chat_id: Optional[str],
    payload: Dict[str, Any],
) -> None:
    if not transport or not chat_id:
        return
    try:
        await transport.send_event_to_ui(
            {
                "kind": "activity",
                "activity_type": "task_batch",
                **payload,
            },
            chat_id,
        )
    except Exception:
        return


__all__ = [
    "TaskBatchExecution",
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
