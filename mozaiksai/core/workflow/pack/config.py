# === MOZAIKS-CORE-HEADER ===
# ==============================================================================
# FILE: core/workflow/pack/config.py
# DESCRIPTION: Unified pack configuration loader and accessor.
#
# This is the SINGLE SOURCE OF TRUTH for reading pack config files
# (workflow_graph.json). It consolidates the previously-split config.py
# and graph.py into one module with a clear cache strategy.
#
# Two config scopes:
#   1. Global pack config — workflows/_pack/workflow_graph.json
#      Contains: workflows[], journeys[], gates[], global settings.
#      Used by: gating, journey resolution, UI availability listing.
#
#   2. Per-workflow pack graph — workflows/<name>/_pack/workflow_graph.json
#      Contains: MFJ triggers (journeys[]), spawn_mode, merge_mode, etc.
#      Used by: WorkflowPackCoordinator for fan-out/fan-in detection.
#
# Caching:
#   Global config is mtime-cached (re-read only when file changes on disk).
#   Per-workflow graphs are NOT cached (loaded on every trigger check).
#   This is intentional — per-workflow graphs are read only on
#   structured_output_ready events, not on every request.
#
# Repo root resolution:
#   Uses a heuristic search (walks up from __file__ looking for
#   workflows/ + mozaiksai/ dirs) rather than a fragile parents[N] offset.
#   Override: set PACK_GRAPH_PATH env var for the global config path.
#
# For future coding agents:
#   - All JSON loading goes through this module. Do NOT add new loaders.
#   - normalize_step_groups() is the canonical step parser. Import it here.
#   - load_pack_config() = global config. load_pack_graph() = per-workflow.
# ==============================================================================

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Repo root resolution
# ---------------------------------------------------------------------------

def _find_repo_root() -> Path:
    """Walk upward from this file to find the repository root.

    Looks for a directory containing both ``workflows/`` and ``mozaiksai/``.
    Falls back to cwd if the heuristic fails (e.g. running from a test
    fixture with a synthetic file tree).
    """
    try:
        here = Path(__file__).resolve()
    except Exception:  # pragma: no cover
        return Path.cwd().resolve()

    for parent in [here] + list(here.parents):
        try:
            if (parent / "workflows").is_dir() and (parent / "mozaiksai").is_dir():
                return parent
        except Exception:
            continue

    return Path.cwd().resolve()


# ---------------------------------------------------------------------------
# Step-group normalizer (shared by config helpers AND coordinator)
# ---------------------------------------------------------------------------

def normalize_step_groups(steps: Any) -> List[List[str]]:
    """Normalize a journey's ``steps`` field into ordered groups.

    Each group is a list of workflow IDs that can run in parallel within
    that step. Groups are executed sequentially (group 0 before group 1, etc).

    Supported input shapes::

        ["A", "B", "C"]              → [["A"], ["B"], ["C"]]
        ["A", ["B", "C"], "D"]       → [["A"], ["B", "C"], ["D"]]

    Returns an empty list if ``steps`` is not a list or contains no valid IDs.
    """
    if not isinstance(steps, list):
        return []

    groups: List[List[str]] = []
    for raw in steps:
        if isinstance(raw, list):
            group = [
                str(x or "").strip()
                for x in raw
                if isinstance(x, (str, int, float)) and str(x or "").strip()
            ]
            if group:
                groups.append(group)
            continue

        if isinstance(raw, (str, int, float)):
            wid = str(raw or "").strip()
            if wid:
                groups.append([wid])
            continue

    return groups


# Backward-compat alias — old imports used the underscore-prefixed name.
_normalize_journey_step_groups = normalize_step_groups


# ---------------------------------------------------------------------------
# Global pack config (workflows/_pack/workflow_graph.json)
# ---------------------------------------------------------------------------

_CACHE: Dict[str, Any] = {"path": None, "mtime": None, "data": None}


def get_pack_config_path() -> Path:
    """Resolve the global pack config file path.

    Priority:
      1. ``PACK_GRAPH_PATH`` env var (absolute or relative to repo root)
      2. ``<repo_root>/workflows/_pack/workflow_graph.json``
    """
    override = str(os.getenv("PACK_GRAPH_PATH") or "").strip()
    candidate = Path(override) if override else Path("workflows") / "_pack" / "workflow_graph.json"
    if not candidate.is_absolute():
        candidate = _find_repo_root() / candidate
    return candidate


def load_pack_config() -> Optional[Dict[str, Any]]:
    """Load and mtime-cache the global pack config.

    Returns ``None`` if the file does not exist or cannot be parsed.
    Re-reads from disk only when the file's mtime changes.
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


# ---------------------------------------------------------------------------
# Per-workflow pack graph (workflows/<name>/_pack/workflow_graph.json)
# ---------------------------------------------------------------------------

def load_pack_graph(workflow_name: str) -> Optional[Dict[str, Any]]:
    """Load the per-workflow pack graph for MFJ trigger detection.

    Path: ``<repo_root>/workflows/<workflow_name>/_pack/workflow_graph.json``

    This is NOT cached — it's only called during structured_output_ready
    processing, which is infrequent enough that caching adds complexity
    without measurable benefit.

    The returned dict is validated against the Pydantic ``PerWorkflowPackGraph``
    schema, which normalizes nested_chats → journeys and v3 mid_flight_journeys.
    Validation errors are logged but non-fatal (returns the raw dict anyway).
    """
    wf = str(workflow_name or "").strip()
    if not wf:
        return None
    root = _find_repo_root()
    path = root / "workflows" / wf / "_pack" / "workflow_graph.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

    # Validate with Pydantic schema — log errors but return raw dict
    # so the coordinator can still operate with partial/invalid configs.
    try:
        from mozaiksai.core.workflow.pack.schema import parse_pack_graph
        parsed = parse_pack_graph(data)
        # Normalize: if legacy nested_chats, promote to journeys
        if parsed.nested_chats and not parsed.journeys:
            data["journeys"] = data.pop("nested_chats", data.get("journeys", []))
    except Exception as exc:
        logger.warning(
            "Pack graph validation warning for %s: %s (file: %s). "
            "Using raw config — coordinator will apply defensive defaults.",
            wf, exc, path,
        )

    return data


def workflow_has_journeys(workflow_name: str) -> bool:
    """Check whether a workflow has MFJ triggers (per-workflow pack graph).

    Returns True if the per-workflow ``workflow_graph.json`` exists and
    contains a non-empty ``journeys`` or ``mid_flight_journeys`` list.
    """
    cfg = load_pack_graph(workflow_name)
    if not cfg or not isinstance(cfg, dict):
        return False
    # v3 key takes priority
    mfj = cfg.get("mid_flight_journeys")
    if isinstance(mfj, list) and mfj:
        return True
    # v2 / legacy (nested_chats already normalized to journeys by loader)
    nested = cfg.get("journeys")
    return isinstance(nested, list) and bool(nested)


# ---------------------------------------------------------------------------
# Workflow entry accessors (operate on a loaded global pack config dict)
# ---------------------------------------------------------------------------

def list_workflow_ids(pack: Dict[str, Any]) -> List[str]:
    """Return ordered list of workflow IDs declared in the pack config."""
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
    """Find a workflow entry by ID in the pack config."""
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


# ---------------------------------------------------------------------------
# Dependency normalization
# ---------------------------------------------------------------------------

def _normalize_dependency_spec(value: Any) -> Optional[Dict[str, Any]]:
    """Normalize a dependency declaration into a canonical dict.

    Supported input forms::

        "SomeWorkflow"
        {"id": "SomeWorkflow", "scope": "app", "reason": "...", "gating": "required"|"optional"}
        {"workflow": "SomeWorkflow", ...}  (alternate key)
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


# ---------------------------------------------------------------------------
# Journey accessors (operate on a loaded global pack config dict)
# ---------------------------------------------------------------------------

def list_journeys(pack: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return all journey dicts from the pack config."""
    journeys = pack.get("journeys") or []
    if not isinstance(journeys, list):
        return []
    return [j for j in journeys if isinstance(j, dict)]


def get_journey(pack: Dict[str, Any], journey_id: str) -> Optional[Dict[str, Any]]:
    """Find a journey by ID."""
    jid = str(journey_id or "").strip()
    if not jid:
        return None
    for j in list_journeys(pack):
        if str(j.get("id") or "").strip() == jid:
            return j
    return None


def infer_auto_journey_for_start(pack: Dict[str, Any], workflow_name: str) -> Optional[Dict[str, Any]]:
    """Infer a journey to auto-attach when starting ``workflow_name``.

    Matches the first journey whose steps[0] group contains ``workflow_name``.
    Used by the transport layer to automatically enroll a user into a journey
    when they start its first workflow.
    """
    wf = str(workflow_name or "").strip()
    if not wf:
        return None
    for j in list_journeys(pack):
        groups = normalize_step_groups(j.get("steps"))
        if not groups:
            continue
        if wf in groups[0]:
            return j
    return None


# ---------------------------------------------------------------------------
# Gate computation
# ---------------------------------------------------------------------------

def compute_required_gates(pack: Dict[str, Any], workflow_name: str) -> List[Dict[str, Any]]:
    """Return required prerequisite gates for ``workflow_name``.

    Gate sources (checked in order, deduplicated at the end):

    1. **Explicit gates** — ``pack["gates"]`` entries with ``gating: "required"``.
    2. **Per-workflow dependencies** — ``workflows[].dependencies`` or
       ``workflows[].requires`` arrays (preferred schema).
    3. **Implicit journey step-order** — if workflow X is in step group[i],
       all workflows in groups[0..i-1] become required gates.
    """
    target = str(workflow_name or "").strip()
    if not target:
        return []

    required: List[Dict[str, Any]] = []

    # --- Source 1: explicit pack["gates"] ---
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

    # --- Source 2: per-workflow dependencies ---
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

    # --- Source 3: implicit journey step-order ---
    for j in list_journeys(pack):
        groups = normalize_step_groups(j.get("steps"))
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

    # --- Deduplicate by (from, to, scope) ---
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
    """Return the first workflow ID in the next step group after ``current_workflow``.

    Returns None if ``current_workflow`` is in the last group or not found.
    Used by the coordinator's sequential journey auto-advance.
    """
    groups = normalize_step_groups(journey.get("steps"))
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
    # Return the first workflow in the next group.
    return groups[group_idx + 1][0] if groups[group_idx + 1] else None


__all__ = [
    # Repo root
    "_find_repo_root",
    # Step-group normalizer
    "normalize_step_groups",
    "_normalize_journey_step_groups",  # backward-compat alias
    # Global pack config
    "get_pack_config_path",
    "load_pack_config",
    # Per-workflow pack graph
    "load_pack_graph",
    "workflow_has_journeys",
    # Workflow accessors
    "list_workflow_ids",
    "get_workflow_entry",
    # Journey accessors
    "list_journeys",
    "get_journey",
    "infer_auto_journey_for_start",
    "journey_next_step",
    # Gate computation
    "compute_required_gates",
]
