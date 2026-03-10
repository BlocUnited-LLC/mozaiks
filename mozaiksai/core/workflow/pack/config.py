from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from .schema import (
    GlobalJourney,
    GlobalPackGraph,
    JourneyStep,
    WorkflowDependency,
    WorkflowEntry,
    WorkflowPackGraph,
    normalize_step_groups,
    parse_global_pack_graph,
    parse_workflow_pack_graph,
)


@dataclass
class _CacheEntry:
    path: str
    mtime: float
    payload: Any


_GLOBAL_CACHE: Optional[_CacheEntry] = None
_WORKFLOW_CACHE: Dict[str, _CacheEntry] = {}


def _repo_root() -> Path:
    """Resolve monorepo root for canonical path resolution."""
    here = Path(__file__).resolve()
    for parent in [here] + list(here.parents):
        try:
            if (parent / "mozaiksai").is_dir():
                return parent
        except Exception:
            continue
    return Path.cwd().resolve()


def _workflows_root() -> Path:
    """Resolve canonical workflows root.

    Resolution order:
      1. MOZAIKS_WORKFLOWS_PATH (absolute or repo-relative)
      2. <repo_root>/platform/workflows
    """
    override = str(os.getenv("MOZAIKS_WORKFLOWS_PATH") or "").strip()
    if override:
        candidate = Path(override)
        if not candidate.is_absolute():
            candidate = (_repo_root() / candidate).resolve()
        return candidate
    return (_repo_root() / "platform" / "workflows").resolve()


def get_global_pack_graph_path() -> Path:
    """Resolve global pack graph path.

    Canonical default: <workflows_root>/_pack/workflow_graph.json
    """
    return (_workflows_root() / "_pack" / "workflow_graph.json").resolve()


def get_workflow_pack_graph_path(workflow_name: str) -> Path:
    """Resolve per-workflow pack graph path.

    Canonical path: <workflows_root>/<workflow_name>/_pack/workflow_graph.json
    """
    wf = str(workflow_name or "").strip()
    if not wf:
        raise ValueError("workflow_name is required")
    return (_workflows_root() / wf / "_pack" / "workflow_graph.json").resolve()


def _load_json_file(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"Pack graph must be a JSON object: {path}")
        return raw
    except Exception as exc:
        raise ValueError(f"Failed loading pack graph {path}: {exc}") from exc


def load_global_pack_graph() -> Optional[GlobalPackGraph]:
    """Load and validate the canonical global pack graph."""
    global _GLOBAL_CACHE

    path = get_global_pack_graph_path()
    if not path.exists():
        return None

    mtime = path.stat().st_mtime
    cached = _GLOBAL_CACHE
    if cached and cached.path == str(path) and cached.mtime == mtime:
        payload = cached.payload
        if isinstance(payload, GlobalPackGraph):
            return payload

    raw = _load_json_file(path)
    if raw is None:
        return None
    graph = parse_global_pack_graph(raw)
    _GLOBAL_CACHE = _CacheEntry(path=str(path), mtime=mtime, payload=graph)
    return graph


def load_workflow_pack_graph(workflow_name: str) -> Optional[WorkflowPackGraph]:
    """Load and validate canonical per-workflow pack graph."""
    wf = str(workflow_name or "").strip()
    if not wf:
        return None

    path = get_workflow_pack_graph_path(wf)
    if not path.exists():
        return None

    mtime = path.stat().st_mtime
    cached = _WORKFLOW_CACHE.get(wf)
    if cached and cached.path == str(path) and cached.mtime == mtime:
        payload = cached.payload
        if isinstance(payload, WorkflowPackGraph):
            return payload

    raw = _load_json_file(path)
    if raw is None:
        return None
    graph = parse_workflow_pack_graph(raw)
    _WORKFLOW_CACHE[wf] = _CacheEntry(path=str(path), mtime=mtime, payload=graph)
    return graph


def list_workflow_ids(pack: GlobalPackGraph) -> List[str]:
    return [w.id for w in pack.workflows]


def get_workflow_entry(pack: GlobalPackGraph, workflow_id: str) -> Optional[WorkflowEntry]:
    wf = str(workflow_id or "").strip()
    if not wf:
        return None
    for entry in pack.workflows:
        if entry.id == wf:
            return entry
    return None


def list_journeys(pack: GlobalPackGraph) -> List[GlobalJourney]:
    return list(pack.journeys)


def get_journey(pack: GlobalPackGraph, journey_id: str) -> Optional[GlobalJourney]:
    jid = str(journey_id or "").strip()
    if not jid:
        return None
    for journey in pack.journeys:
        if journey.id == jid:
            return journey
    return None


def infer_auto_journey_for_start(pack: GlobalPackGraph, workflow_name: str) -> Optional[GlobalJourney]:
    """Infer journey whose first step contains the requested workflow."""
    wf = str(workflow_name or "").strip()
    if not wf:
        return None
    for journey in pack.journeys:
        groups = normalize_step_groups(journey.steps)
        if groups and wf in groups[0]:
            return journey
    return None


def _normalize_dependency_spec(value: Union[str, WorkflowDependency]) -> Optional[WorkflowDependency]:
    if isinstance(value, WorkflowDependency):
        return value
    if isinstance(value, str):
        dep = value.strip()
        if not dep:
            return None
        return WorkflowDependency(id=dep)
    return None


def compute_required_gates(pack: GlobalPackGraph, workflow_name: str) -> List[Dict[str, Any]]:
    """Build required prerequisite gates from canonical global graph data."""
    target = str(workflow_name or "").strip()
    if not target:
        return []

    required: List[Dict[str, Any]] = []

    entry = get_workflow_entry(pack, target)
    if isinstance(entry, WorkflowEntry):
        for raw_dep in entry.dependencies:
            dep = _normalize_dependency_spec(raw_dep)
            if not dep:
                continue
            if dep.gating != "required":
                continue
            reason = dep.reason or f"{target} requires {dep.id} to be completed first."
            required.append(
                {
                    "from": dep.id,
                    "to": target,
                    "gating": "required",
                    "scope": dep.scope,
                    "reason": reason,
                    "_source": "workflow.dependencies",
                }
            )

    for journey in pack.journeys:
        groups = normalize_step_groups(journey.steps)
        if len(groups) < 2:
            continue

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
                            "reason": f"Journey '{journey.id}' step order",
                            "_source": "journey.steps",
                        }
                    )

    seen: set[Tuple[str, str, str]] = set()
    deduped: List[Dict[str, Any]] = []
    for gate in required:
        parent = str(gate.get("from") or "").strip()
        child = str(gate.get("to") or "").strip()
        scope = str(gate.get("scope") or "app").strip().lower() or "app"
        if not parent or not child:
            continue
        key = (parent, child, scope)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(gate)
    return deduped


def journey_next_step(journey: GlobalJourney, current_workflow: str) -> Optional[str]:
    groups = normalize_step_groups(journey.steps)
    current = str(current_workflow or "").strip()
    if not current or not groups:
        return None

    current_idx: Optional[int] = None
    for idx, group in enumerate(groups):
        if current in group:
            current_idx = idx
            break
    if current_idx is None or current_idx >= len(groups) - 1:
        return None

    next_group = groups[current_idx + 1]
    return next_group[0] if next_group else None


__all__ = [
    "get_global_pack_graph_path",
    "get_workflow_pack_graph_path",
    "load_global_pack_graph",
    "load_workflow_pack_graph",
    "list_workflow_ids",
    "get_workflow_entry",
    "list_journeys",
    "get_journey",
    "infer_auto_journey_for_start",
    "compute_required_gates",
    "journey_next_step",
    "normalize_step_groups",
    "JourneyStep",
]
