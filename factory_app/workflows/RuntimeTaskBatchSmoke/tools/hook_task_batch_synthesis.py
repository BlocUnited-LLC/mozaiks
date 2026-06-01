from __future__ import annotations

import json
from typing import Any


_HEADER = "[DETERMINISTIC TASK BATCH SYNTHESIS]"


def _context_get(context_variables: Any, key: str, default: Any = None) -> Any:
    if context_variables is None:
        return default
    getter = getattr(context_variables, "get", None)
    if callable(getter):
        try:
            return getter(key, default)
        except TypeError:
            value = getter(key)
            return default if value is None else value
    data = getattr(context_variables, "data", None)
    if isinstance(data, dict):
        return data.get(key, default)
    if isinstance(context_variables, dict):
        return context_variables.get(key, default)
    return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _planner_user_intent(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages or []):
        if not isinstance(message, dict):
            continue
        if str(message.get("name") or "") != "TaskPlannerAgent":
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            continue
        plan = payload.get("RuntimeTaskBatchPlan")
        if isinstance(plan, dict):
            intent = str(plan.get("user_intent") or "").strip()
            if intent:
                return intent
    return "Task batch smoke workflow request."


def _build_result_payload(context_variables: Any, messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    results = _context_get(context_variables, "task_batch_results", {})
    if not isinstance(results, dict):
        return None
    meta = results.get("_meta")
    if not isinstance(meta, dict):
        return None

    completed_tasks = [str(task_id) for task_id in _as_list(meta.get("completed_tasks"))]
    failed_tasks = [str(task_id) for task_id in _as_list(meta.get("failed_tasks"))]
    task_count = _as_int(meta.get("task_count"))
    concurrency = _as_int(meta.get("concurrency"))
    result_context_key = str(meta.get("result_context_key") or "task_batch_results")
    batch_status = str(_context_get(context_variables, "task_batch_status", "") or "")
    meta_status = str(meta.get("status") or "")
    all_units_succeeded = (
        batch_status == "completed"
        and meta_status == "completed"
        and not failed_tasks
        and len(completed_tasks) == task_count
    )

    executed_kinds: list[str] = []
    sample_summary = ""
    for task_id in completed_tasks:
        task_output = results.get(task_id)
        if not isinstance(task_output, dict):
            continue
        kind = str(task_output.get("kind") or "").strip()
        if kind and kind not in executed_kinds:
            executed_kinds.append(kind)
        if not sample_summary:
            sample_summary = str(task_output.get("summary") or "").strip()

    if not sample_summary:
        sample_summary = "Task batch executor completed without worker summaries."

    return {
        "agent_message": (
            "Task batch execution completed successfully."
            if all_units_succeeded
            else "Task batch execution completed with failures."
        ),
        "result": f"{len(completed_tasks)} task batch units completed; {len(failed_tasks)} failed.",
        "user_intent": _planner_user_intent(messages),
        "work_unit_count": task_count,
        "task_batch_execution_used": True,
        "max_parallelism": concurrency,
        "executed_task_ids": completed_tasks,
        "executed_kinds": executed_kinds,
        "result_context_key": result_context_key,
        "all_units_succeeded": all_units_succeeded,
        "failure_count": len(failed_tasks),
        "sample_summary": sample_summary,
    }


def _update_section(agent: Any, body: str) -> None:
    current = (
        getattr(agent, "system_message", None)
        or getattr(agent, "_system_message", "")
        or ""
    )
    section = f"{_HEADER}\n{body}"
    if _HEADER in current:
        pre, _, rest = current.partition(_HEADER)
        next_section_idx = rest.find("\n\n[")
        after = rest[next_section_idx:] if next_section_idx > 0 else ""
        new_message = pre.rstrip() + "\n\n" + section + after
    else:
        new_message = current.rstrip() + "\n\n" + section

    updater = getattr(agent, "update_system_message", None)
    if callable(updater):
        updater(new_message)
    else:
        setattr(agent, "system_message", new_message)
        setattr(agent, "_system_message", new_message)


def inject_task_batch_synthesis_context(agent: Any, messages: list[dict[str, Any]]) -> None:
    payload = _build_result_payload(
        getattr(agent, "context_variables", None),
        messages,
    )
    if not payload:
        return

    body = (
        "The task batch executor metadata is authoritative. "
        "Respond with ONLY this RuntimeTaskBatchSmokeResult JSON object; do not change counts, ids, keys, or booleans.\n"
        f"{json.dumps(payload, indent=2)}"
    )
    _update_section(agent, body)

