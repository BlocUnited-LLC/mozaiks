"""Factory repair policy over the approved task inventory and retained evidence.

This selects work; the workflow graph and its existing AG2 batch execute it.
"""

from __future__ import annotations

import hashlib
import json
from fnmatch import fnmatchcase
from typing import Any

from mozaiksai.core.workflow.context.frozen import detach

from .task_integrity import approved_task_inventory


def _get(context: Any, key: str, default: Any = None) -> Any:
    return detach(context.get(key, default)) if context is not None else default


def _set(context: Any, key: str, value: Any) -> None:
    if isinstance(context, dict):
        context[key] = value
    elif context is not None:
        context.set(key, value)


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def owned_diagnostics(context: Any, diagnostics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Resolve exact paths, never infer authority from an agent-name heuristic."""
    try:
        inventory = approved_task_inventory(context)
    except (TypeError, ValueError) as exc:
        return [{"code": "PLAN_INVENTORY_INVALID", "error": str(exc), "block_reason": "invalid_inventory"}]
    owners: dict[str, list[dict[str, Any]]] = {}
    for task in inventory:
        for path in task.get("owned_paths", []):
            owners.setdefault(path, []).append(task)
    results = _get(context, "app_task_batch_results", {}) or {}
    failures = results.get("_failed", {})
    resolved = []
    for diagnostic in diagnostics:
        item = dict(diagnostic)
        error = str(item.get("error") or item.get("message") or "")
        path = str(item.get("path") or "").replace("\\", "/")
        if not path:
            prefix = error.split(":", 1)[0].strip().replace("\\", "/")
            if prefix in owners:
                path = prefix
            elif error == "Generated app bundles must include app.json.":
                path = "app.json"
        matches = owners.get(path, [])
        if "*" in path:
            matching_paths = [owned for owned in owners if fnmatchcase(owned, path)]
            matches = list({
                task["task_id"]: task for owned in matching_paths for task in owners[owned]
            }.values())
            if len(matching_paths) == 1:
                path = matching_paths[0]
        if not path and item.get("task_id"):
            matches = [task for task in inventory if task["task_id"] == item["task_id"]]
        item.update(error=error, path=path)
        if item.get("code") == "PLANNED_ARTIFACT_MISSING":
            item.pop("block_reason", None)
        if len(matches) != 1:
            item.update(owner_agent=None, block_reason="missing_or_ambiguous_owner")
        else:
            task = matches[0]
            task_id = task["task_id"]
            item.update(task_id=task_id, owner_agent=task["initial_agent"])
            if task_id in failures:
                item["block_reason"] = "task_failure_requires_batch_recovery"
            elif task_id not in results:
                item["block_reason"] = "missing_accepted_task_evidence"
            elif any(dep not in results for dep in task.get("depends_on", [])):
                item["block_reason"] = "prerequisite_not_accepted"
        resolved.append(item)
    return resolved


def prepare_task_recovery(context: Any) -> dict[str, Any] | None:
    """Request only evidenced original output rejections; runtime revalidates all inputs."""
    results = _get(context, "app_task_batch_results", {}) or {}
    meta = results.get("_meta", {})
    failures = results.get("_failed", {})
    roots = []
    if meta.get("evidence_version") == 1 and not meta.get("in_flight"):
        attempted = meta.get("recovery_attempted_tasks", [])
        for task_id, failure in failures.items():
            if (
                failure.get("failure_kind") == "output_rejected"
                and failure.get("recoverable") is True
                and failure.get("error")
                and failure.get("rejected_output") is not None
                and task_id not in attempted
            ):
                roots.append(task_id)
    request = None
    if roots and meta.get("input_fingerprint"):
        request = {
            "batch_id": "app_build_tasks",
            "input_fingerprint": meta["input_fingerprint"],
            "root_task_ids": sorted(roots),
        }
        request["request_id"] = _digest(request)
        previous = _get(context, "app_task_recovery_result", {}) or {}
        if previous.get("request_id") == request["request_id"]:
            request = None
    _set(context, "app_task_recovery_request", request)
    return request


def prepare_bundle_repair(
    scan: dict[str, Any], context: Any, *, max_attempts: int = 2, select_repairs: bool = True,
) -> dict[str, Any]:
    """Choose one eligible task without allowing a blocked lane to starve another."""
    prior = _get(context, "bundle_repair_result", {}) or {}
    history = list(prior.get("history", []))
    active = prior.get("active") or {}
    attempts = int(_get(context, "bundle_repair_attempt_count", 0) or 0)
    diagnostics = owned_diagnostics(context, scan.get("diagnostics") or [
        {"error": str(error)} for error in scan.get("errors", [])
    ])
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in diagnostics:
        if item.get("owner_agent") and not item.get("block_reason"):
            grouped.setdefault(item["task_id"], []).append(item)
    try:
        inventory = {task["task_id"]: task for task in approved_task_inventory(context)}
    except (TypeError, ValueError):
        inventory = {}
    # A selected proposal which reaches validation without a settled save has
    # uncertain execution. Do not silently dispatch it again after interruption.
    if select_repairs and active and active.get("status") == "selected":
        active = {**active, "status": "interrupted"}
    if select_repairs and active:
        history.append(active)
    chosen = None
    no_progress = False
    for task_id in sorted(grouped) if select_repairs else []:
        items = grouped[task_id]
        fingerprint = _digest({"task_id": task_id, "errors": sorted(item["error"] for item in items)})
        past = [item for item in history if item["task_id"] == task_id]
        blocked = any(
            item.get("status") in {"interrupted", "rejected"}
            or item.get("failure_fingerprint") == fingerprint for item in past
        )
        if blocked:
            no_progress = True
            for item in items:
                item["block_reason"] = "interrupted_or_no_progress"
            continue
        if attempts < max_attempts:
            task = inventory[task_id]
            chosen = {
                "task_id": task_id, "target_agent": task["initial_agent"],
                "allowed_paths": list(task["owned_paths"]),
                "failure_fingerprint": fingerprint,
                "request_id": _digest({"fingerprint": fingerprint, "attempt": attempts + 1}),
                "status": "selected",
            }
            attempts += 1
            break
    target_errors = [item["error"] for item in grouped.get(chosen["task_id"], [])] if chosen else []
    errors = [item["error"] for item in diagnostics]
    deferred = [error for error in errors if error not in target_errors]
    status = "passed" if scan.get("passed") else "needs_revision" if chosen else "blocked"
    request = None
    if chosen:
        task = inventory[chosen["task_id"]]
        request = "\n".join([
            f"Repair approved task {task['task_id']} only, preserving its identities and public contracts.",
            "Read generated_files and the accepted prerequisite contracts. Emit only the allowed paths; preserve unrelated files.",
            "Allowed paths: " + ", ".join(chosen["allowed_paths"]),
            *[f"- {error}" for error in target_errors],
            "Deferred diagnostics remain blocked or belong to other tasks; do not broaden this repair.",
        ])
        _set(context, "current_build_task", task)
        _set(context, "current_build_task_id", task["task_id"])
        _set(context, "current_build_task_type", task.get("task_type"))
        results = _get(context, "app_task_batch_results", {}) or {}
        _set(context, "dependency_task_outputs", {
            dep: results[dep] for dep in task.get("depends_on", []) if dep in results
        })
    result = {
        "status": status, "repairable": bool(chosen),
        "target_agent": chosen["target_agent"] if chosen else None,
        "error_count": len(errors), "attempt": attempts, "max_attempts": max_attempts,
        "repair_request": request, "errors": errors, "target_errors": target_errors,
        "deferred_errors": deferred, "diagnostics": diagnostics,
        "failure_fingerprint": chosen["failure_fingerprint"] if chosen else active.get("failure_fingerprint"),
        "no_progress": no_progress, "active": chosen, "history": history,
    }
    for suffix, value in {
        "status": status, "target": result["target_agent"], "attempt_count": attempts,
        "max_attempts": max_attempts, "request": request, "errors": errors,
        "failure_fingerprint": result["failure_fingerprint"], "no_progress": no_progress,
        "result": result,
    }.items():
        _set(context, "bundle_repair_" + suffix, value)
    return result
