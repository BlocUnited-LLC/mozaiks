"""Factory task ownership, repair admission, and final artifact completeness."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any, NoReturn

from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.generator_support.code_files import safe_relpath
from mozaiksai.core.workflow.generator_support.page_plan_utils import (
    _page_stem_from_path,
    _page_stems,
)
from mozaiksai.core.workflow.task_batches import (
    optional_task_output_paths,
    task_evidence_digest,
    task_inventory_digest,
)


class RepairOwnershipError(ValueError):
    """The entire repair candidate was rejected before artifact mutation."""


def _get(context: Any, key: str, default: Any = None) -> Any:
    return detach(context.get(key, default)) if context is not None else default


def _set(context: Any, key: str, value: Any) -> None:
    if isinstance(context, dict):
        context[key] = value
    else:
        context.set(key, value)


def _task_contract(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": task.get("task_id"),
        "task_type": task.get("task_type"),
        "initial_agent": task.get("initial_agent"),
        "owned_paths": sorted(safe_relpath(str(path)) for path in task.get("owned_paths", [])),
        "depends_on": sorted(task.get("depends_on") or []),
    }


def approved_task_inventory(context: Any) -> list[dict[str, Any]]:
    """Read the approved plan and verify its execution projection has not drifted."""
    plan = _get(context, "app_build_plan") or {}
    planned = plan.get("build_tasks") if isinstance(plan, dict) else None
    factory_bound = (
        _get(context, "workflow_name") == "AppGenerator"
        or bool(_get(context, "app_plan_ready"))
        or _get(context, "app_task_batch_status") is not None
        or bool(_get(context, "run_build_binding"))
    )
    if factory_bound and not isinstance(planned, list):
        raise ValueError("Factory generation requires approved app_build_plan.build_tasks")
    projected = _get(context, "app_task_batch_items")
    tasks = planned if isinstance(planned, list) else projected
    if tasks is None:
        return []
    if not isinstance(tasks, list) or any(not isinstance(task, dict) for task in tasks):
        raise ValueError("approved task inventory is invalid")
    if isinstance(planned, list) and isinstance(projected, list):
        if [_task_contract(task) for task in planned] != [_task_contract(task) for task in projected]:
            raise ValueError("approved plan differs from the task execution inventory")
    results = _get(context, "app_task_batch_results") or {}
    meta = (results.get("_meta") or {}) if isinstance(results, dict) else {}
    expected_inventory = meta.get("task_inventory_digest")
    if expected_inventory and expected_inventory != task_inventory_digest(projected if isinstance(projected, list) else tasks):
        raise ValueError("approved task inventory differs from its original execution evidence")
    expected_plan = (meta.get("input_digests") or {}).get("app_build_plan")
    if expected_plan and expected_plan != task_evidence_digest(_get(context, "app_build_plan")):
        raise ValueError("approved build plan differs from its original execution evidence")
    ids: set[str] = set()
    owners: dict[str, str] = {}
    for task in tasks:
        task_id = str(task.get("task_id") or "")
        if not task_id or task_id in ids:
            raise ValueError("approved task inventory has missing or duplicate task identities")
        ids.add(task_id)
        for raw in task.get("owned_paths") or []:
            path = safe_relpath(str(raw))
            if not path or path in owners:
                raise ValueError(f"approved task ownership is invalid or ambiguous for {raw!r}")
            owners[path] = task_id
    return tasks


def task_allowed_paths(task: dict[str, Any], tasks: list[dict[str, Any]]) -> set[str]:
    """Use the batch's optionality rules without overriding another explicit owner."""
    own = {
        path for raw in task.get("owned_paths") or []
        if (path := safe_relpath(str(raw))) is not None
    }
    foreign = {
        path
        for other in tasks if other.get("task_id") != task.get("task_id")
        for raw in other.get("owned_paths") or []
        if (path := safe_relpath(str(raw))) is not None
    }
    return (own | optional_task_output_paths(task)) - foreign - {""}


def planned_artifact_diagnostics(context: Any, files: dict[str, str]) -> list[dict[str, Any]]:
    """Require accepted task evidence and all required artifacts in the final snapshot."""
    diagnostics: list[dict[str, Any]] = []

    def add(code: str, path: str = "", task: dict[str, Any] | None = None,
            reason: str = "", blocked_by: list[str] | None = None) -> None:
        task = task or {}
        diagnostics.append({
            "code": code, "path": path, "task_id": str(task.get("task_id") or ""),
            "owner_agent": str(task.get("initial_agent") or ""),
            "blocked_by": blocked_by or [], "block_reason": reason,
            "error": f"{path or task.get('task_id') or 'approved plan'}: {reason}",
        })

    try:
        tasks = approved_task_inventory(context)
    except (TypeError, ValueError) as exc:
        add("PLAN_INVENTORY_INVALID", reason=str(exc))
        return diagnostics
    results = _get(context, "app_task_batch_results") or {}
    if not isinstance(results, dict):
        add("TASK_EVIDENCE_UNAVAILABLE", reason="task execution results are invalid")
        results = {}
    if not tasks and results:
        add("PLAN_INVENTORY_UNAVAILABLE", reason="task results have no approved task inventory")
    failed = results.get("_failed") or {}
    meta = results.get("_meta") or {}
    in_flight = meta.get("in_flight") or {}
    failed_ids = set(failed) | set(meta.get("failed_tasks") or [])
    by_id = {str(task["task_id"]): task for task in tasks}
    owners = {
        path: task
        for task in tasks for raw in task.get("owned_paths") or []
        if (path := safe_relpath(str(raw))) is not None
    }
    for task in tasks:
        task_id = str(task["task_id"])
        blocked = [str(dep) for dep in task.get("depends_on") or [] if dep in failed_ids or dep in in_flight]
        if task_id in in_flight:
            add("TASK_EXECUTION_UNCERTAIN", task=task, reason="task attempt has no settled execution result")
        elif task_id in failed_ids:
            evidence = failed.get(task_id) or {}
            reason = str(evidence.get("error") or "original task failure diagnostic unavailable")
            blocked = list(evidence.get("blocked_by") or blocked)
            add("TASK_DEPENDENCY_BLOCKED" if blocked else "TASK_FAILED", task=task,
                reason=reason, blocked_by=blocked)
        elif not isinstance(results.get(task_id), dict) or not results[task_id]:
            history = (meta.get("failure_history") or {}).get(task_id) or []
            prior = history[-1] if history else {}
            if blocked:
                add("TASK_DEPENDENCY_BLOCKED", task=task, blocked_by=blocked,
                    reason=str(prior.get("error") or "prerequisite has no accepted execution result"))
            else:
                add("TASK_EVIDENCE_UNAVAILABLE", task=task, reason="accepted task output unavailable")
        elif meta.get("evidence_version") == 1 and (
            (meta.get("accepted_output_digests") or {}).get(task_id) != task_evidence_digest(results[task_id])
        ):
            add("TASK_EVIDENCE_INVALID", task=task, reason="accepted task output differs from its execution evidence")
        for path in sorted(set(task.get("owned_paths") or []) - optional_task_output_paths(task)):
            normalized_path = safe_relpath(str(path))
            assert normalized_path is not None  # Approved inventory already validated every owned path.
            if normalized_path not in files:
                add("PLANNED_ARTIFACT_MISSING", normalized_path, task, "required planned artifact is missing", blocked)

    plan = _get(context, "app_build_plan") or {}
    if not isinstance(plan, dict) or not (tasks or plan.get("pages") or plan.get("modules")):
        return diagnostics
    if "app.json" not in files and not any(item["path"] == "app.json" for item in diagnostics):
        add("PLANNED_ARTIFACT_MISSING", "app.json", owners.get("app.json"), "required app manifest is missing")
    present_pages = {_page_stem_from_path(path) for path in files}
    for page in plan.get("pages") or []:
        if not isinstance(page, dict) or present_pages.intersection(_page_stems(page)):
            continue
        planned_paths = [path for path in owners if _page_stem_from_path(path) in _page_stems(page)]
        for path in planned_paths or [f"ui/pages/{str(page.get('page_id') or page.get('name') or 'unknown')}.yaml"]:
            if not any(item["path"] == path for item in diagnostics):
                add("PLANNED_ARTIFACT_MISSING", path, owners.get(path), "required planned page is missing")
    for module in plan.get("modules") or []:
        if not isinstance(module, dict) or not module.get("module_id"):
            continue
        path = f"modules/{module['module_id']}/module.yaml"
        if path not in files and not any(item["path"] == path for item in diagnostics):
            add("PLANNED_ARTIFACT_MISSING", path, owners.get(path), "required planned module is missing")
    for task_id in failed_ids - by_id.keys():
        add("PLAN_INVENTORY_INVALID", reason=f"failed task {task_id!r} is absent from the approved inventory")
    return diagnostics


def artifact_snapshot_digest(context: Any, files: dict[str, str]) -> str:
    """Bind acceptance to its approved inputs and the exact exported contents."""
    snapshot = {
        "plan": _get(context, "app_build_plan"),
        "tasks": _get(context, "app_task_batch_items"),
        "results": _get(context, "app_task_batch_results"),
        "binding": _get(context, "run_build_binding"),
        "files": files,
    }
    encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _settle_repair(context: Any, status: str, error: str = "") -> None:
    result = _get(context, "bundle_repair_result") or {}
    active = result.get("active")
    if not isinstance(active, dict):
        return
    result["active"] = {**active, "status": status, "error": error}
    _set(context, "bundle_repair_result", result)


def mark_repair_responded(context: Any) -> None:
    _settle_repair(context, "responded")


def mark_repair_rejected(context: Any, error: str) -> bool:
    """Settle a pending repair's invalid candidate; infrastructure failures stay uncertain."""
    active = (_get(context, "bundle_repair_result") or {}).get("active")
    if not isinstance(active, dict) or active.get("status") != "selected":
        return False
    _settle_repair(context, "rejected", error)
    return True


def validate_repair_candidate(
    context: Any, incoming: dict[str, str], deleted: Iterable[str] = (),
    *, existing: dict[str, str] | None = None,
) -> dict[str, str]:
    """Admit one owned repair atomically; foreign identical readback is omitted."""
    result = _get(context, "bundle_repair_result") or {}
    active = result.get("active")
    if not isinstance(active, dict):
        if _get(context, "bundle_repair_target"):
            raise RepairOwnershipError("repair has no approved task ownership request")
        return incoming

    def reject(reason: str) -> NoReturn:
        _settle_repair(context, "rejected", reason)
        raise RepairOwnershipError(reason)

    if active.get("status") != "selected" or not active.get("request_id"):
        reject("repair request is not pending; replay or interrupted dispatch is blocked")
    try:
        tasks = approved_task_inventory(context)
    except (TypeError, ValueError) as exc:
        reject(str(exc))
    task = next((task for task in tasks if task.get("task_id") == active.get("task_id")), None)
    if task is None or task.get("initial_agent") != active.get("target_agent"):
        reject("repair task owner does not match the approved task inventory")
    allowed = {safe_relpath(str(path)) for path in active.get("allowed_paths") or []}
    if not allowed or not allowed.issubset(task_allowed_paths(task, tasks)):
        reject("repair allowed_paths extend beyond the approved task owner")
    existing = existing or {}
    unauthorized = sorted(
        path for path, content in incoming.items()
        if safe_relpath(path) != path or (path not in allowed and existing.get(path) != content)
    )
    unauthorized.extend(path for path in deleted if path not in allowed or safe_relpath(path) != path)
    if unauthorized:
        reject(f"repair emitted changes outside owned_paths: {sorted(set(unauthorized))}")
    return {path: content for path, content in incoming.items() if path in allowed}
