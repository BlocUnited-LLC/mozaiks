from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional


def _normalize_journey_step_groups(steps: Any) -> List[List[str]]:
    """Normalize journeys[].steps into groups.

    Supported input shapes:
    - ["A", "B", "C"]
    - ["A", ["B", "C"], "D"]  # B and C run in parallel
    """
    if not isinstance(steps, list):
        return []

    groups: List[List[str]] = []
    for raw in steps:
        if isinstance(raw, list):
            group = [str(x or "").strip() for x in raw if isinstance(x, (str, int, float)) and str(x or "").strip()]
            if group:
                groups.append(group)
            continue

        if isinstance(raw, (str, int, float)):
            wid = str(raw or "").strip()
            if wid:
                groups.append([wid])
            continue

    return groups


def _repo_root() -> Path:
    try:
        # core/workflow/pack/config.py -> repo root is two parents up from `core/`
        return Path(__file__).resolve().parents[3]
    except Exception:  # pragma: no cover
        return Path.cwd().resolve()


_CACHE: Dict[str, Any] = {"path": None, "mtime": None, "data": None}


def get_pack_config_path() -> Path:
    """Resolve the pack config path.

    Env override: PACK_GRAPH_PATH
    Default: workflows/_pack/workflow_graph.json
    """
    override = str(os.getenv("PACK_GRAPH_PATH") or "").strip()
    candidate = Path(override) if override else Path("workflows") / "_pack" / "workflow_graph.json"
    if not candidate.is_absolute():
        candidate = _repo_root() / candidate
    return candidate


def load_pack_config() -> Optional[Dict[str, Any]]:
    """Load and cache the pack config.

    Returns None if the file does not exist or cannot be parsed.
    """
    path = get_pack_config_path()
    try:
        if not path.exists():
            return None
        mtime = path.stat().st_mtime
        cached_path = _CACHE.get("path")
        cached_mtime = _CACHE.get("mtime")
        if cached_path == str(path) and cached_mtime == mtime and isinstance(_CACHE.get("data"), dict):
            return _CACHE["data"]
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        _CACHE.update({"path": str(path), "mtime": mtime, "data": data})
        return data
    except Exception:
        return None


def list_workflow_ids(pack: Dict[str, Any]) -> List[str]:
    workflows = pack.get("workflows") or []
    if not isinstance(workflows, list):
        return []
    result: List[str] = []
    for w in workflows:
        if not isinstance(w, dict):
            continue
        wid = str(w.get("id") or "").strip()
        if wid and wid not in result:
            result.append(wid)
    return result


def get_workflow_entry(pack: Dict[str, Any], workflow_id: str) -> Optional[Dict[str, Any]]:
    wid = str(workflow_id or "").strip()
    if not wid:
        return None
    workflows = pack.get("workflows") or []
    if not isinstance(workflows, list):
        return None
    for w in workflows:
        if not isinstance(w, dict):
            continue
        if str(w.get("id") or "").strip() == wid:
            return w
    return None


def _normalize_dependency_spec(value: Any) -> Optional[Dict[str, Any]]:
    """Normalize a dependency spec.

    Supported forms:
    - "SomeWorkflow"
    - {"id": "SomeWorkflow", "scope": "app", "reason": "...", "gating": "required"|"optional"}
    - {"workflow": "SomeWorkflow", ...}
    """
    if isinstance(value, str):
        dep_id = value.strip()
        if not dep_id:
            return None
        return {"id": dep_id, "gating": "required"}
    if isinstance(value, dict):
        dep_id = str(value.get("id") or value.get("workflow") or "").strip()
        if not dep_id:
            return None
        scope = str(value.get("scope") or "").strip().lower() or None
        reason = str(value.get("reason") or "").strip() or None
        gating = str(value.get("gating") or "").strip().lower() or None
        required_flag = value.get("required")
        if required_flag is not None:
            gating = "required" if bool(required_flag) else "optional"
        if gating not in {None, "required", "optional"}:
            gating = None
        return {"id": dep_id, "scope": scope, "reason": reason, "gating": gating or "required"}
    return None


def list_journeys(pack: Dict[str, Any]) -> List[Dict[str, Any]]:
    journeys = pack.get("journeys") or []
    if not isinstance(journeys, list):
        return []
    return [j for j in journeys if isinstance(j, dict)]


def get_journey(pack: Dict[str, Any], journey_id: str) -> Optional[Dict[str, Any]]:
    jid = str(journey_id or "").strip()
    if not jid:
        return None
    for j in list_journeys(pack):
        if str(j.get("id") or "").strip() == jid:
            return j
    return None


def infer_auto_journey_for_start(pack: Dict[str, Any], workflow_name: str) -> Optional[Dict[str, Any]]:
    """Infer a journey to auto-attach when starting `workflow_name`.

    Default-simple behavior: journeys are assumed to auto-attach.

    A journey matches when steps[0] == workflow_name.
    """
    wf = str(workflow_name or "").strip()
    if not wf:
        return None
    for j in list_journeys(pack):
        groups = _normalize_journey_step_groups(j.get("steps"))
        if not groups:
            continue
        if wf in groups[0]:
            return j
    return None


def compute_required_gates(pack: Dict[str, Any], workflow_name: str) -> List[Dict[str, Any]]:
    """Return required prerequisite gates for `workflow_name`.

    Sources:
    - Explicit pack["gates"] entries (legacy)
    - Per-workflow dependencies (preferred)
    - Implicit step-order gates from journeys (default)
    """
    target = str(workflow_name or "").strip()
    if not target:
        return []

    required: List[Dict[str, Any]] = []

    gates = pack.get("gates") or []
    if isinstance(gates, list):
        for g in gates:
            if not isinstance(g, dict):
                continue
            if str(g.get("to") or "").strip() != target:
                continue
            if str(g.get("gating") or "").lower().strip() != "required":
                continue
            required.append(g)

    # Simpler alternative schema: per-workflow dependencies.
    # This allows pack configs to avoid top-level "gates" and instead write:
    #   workflows: [{ id: "SecondWorkflow", dependencies: ["SomeWorkflow"] }]
    entry = get_workflow_entry(pack, target)
    if isinstance(entry, dict):
        deps = entry.get("dependencies")
        if deps is None:
            deps = entry.get("requires")
        if isinstance(deps, list):
            for raw in deps:
                dep = _normalize_dependency_spec(raw)
                if not dep:
                    continue
                if str(dep.get("gating") or "required").strip().lower() != "required":
                    continue
                parent = str(dep.get("id") or "").strip()
                if not parent:
                    continue

                scope = str(dep.get("scope") or entry.get("dependency_scope") or "app").strip().lower() or "app"
                reason = str(dep.get("reason") or entry.get("dependency_reason") or "").strip()
                if not reason:
                    reason = f"{target} requires {parent} to be completed first."

                required.append(
                    {
                        "from": parent,
                        "to": target,
                        "gating": "required",
                        "scope": scope,
                        "reason": reason,
                        "_implicit": True,
                        "_source": "workflow.dependencies",
                    }
                )

    # Default-simple behavior: journey step order is always enforced as required prerequisites.
    for j in list_journeys(pack):
        groups = _normalize_journey_step_groups(j.get("steps"))
        if len(groups) < 2:
            continue
        jid = str(j.get("id") or "").strip()

        # For a target in group[i], require all workflows in groups[0..i-1].
        for group_idx in range(1, len(groups)):
            if target not in groups[group_idx]:
                continue
            for prev_idx in range(0, group_idx):
                for parent in groups[prev_idx]:
                    required.append(
                        {
                            "from": parent,
                            "to": target,
                            "gating": "required",
                            "scope": "app",
                            "reason": f"Journey '{jid}' step order",
                            "_implicit": True,
                            "_source": "journey.steps",
                        }
                    )

    # Deduplicate by (from,to,scope)
    seen: set[tuple[str, str, str]] = set()
    deduped: List[Dict[str, Any]] = []
    for g in required:
        try:
            parent = str(g.get("from") or "").strip()
            child = str(g.get("to") or "").strip()
            scope = str(g.get("scope") or "user").lower().strip() or "user"
        except Exception:
            continue
        if not parent or not child:
            continue
        key = (parent, child, scope)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(g)
    return deduped


def journey_next_step(journey: Dict[str, Any], current_workflow: str) -> Optional[str]:
    groups = _normalize_journey_step_groups(journey.get("steps"))
    cur = str(current_workflow or "").strip()
    if not groups or not cur:
        return None

    group_idx = None
    for idx, group in enumerate(groups):
        if cur in group:
            group_idx = idx
            break
    if group_idx is None or group_idx >= len(groups) - 1:
        return None
    # Return the first workflow in the next group (UI-centric helper).
    return groups[group_idx + 1][0] if groups[group_idx + 1] else None


__all__ = [
    "get_pack_config_path",
    "load_pack_config",
    "list_workflow_ids",
    "get_workflow_entry",
    "list_journeys",
    "get_journey",
    "infer_auto_journey_for_start",
    "compute_required_gates",
    "journey_next_step",
]
