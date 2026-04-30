from __future__ import annotations

from copy import deepcopy
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from .schema import (
    GlobalJourney,
    GlobalPackGraph,
    JourneyStepGroup,
    WorkflowTransition,
    WorkflowDependency,
    WorkflowEntry,
    WorkflowEntrypoint,
    WorkflowPackGraph,
    normalize_step_groups,
    parse_global_pack_graph,
    parse_workflow_pack_graph,
)
from ..paths import normalize_workflow_roots, primary_workflows_root, resolve_active_app_root, resolve_workflow_path


@dataclass
class _CacheEntry:
    source: Tuple[Tuple[str, float], ...]
    payload: Any


_GLOBAL_CACHE: Optional[_CacheEntry] = None
_WORKFLOW_CACHE: Dict[str, _CacheEntry] = {}

def _workflows_root() -> Path:
    """Resolve canonical workflows root.

    Resolution order:
      1. First entry in MOZAIKS_WORKFLOW_ROOTS
      2. MOZAIKS_WORKFLOWS_PATH
            3. active app root workflows
    """
    return primary_workflows_root(normalize_workflow_roots())


def get_global_pack_graph_path() -> Path:
    """Resolve the highest-precedence global extension registry path."""
    for root in normalize_workflow_roots():
        candidate = (root / "extended_orchestration" / "extension_registry.json").resolve()
        if candidate.exists():
            return candidate
    return (_workflows_root() / "extended_orchestration" / "extension_registry.json").resolve()


def get_workflow_pack_graph_path(workflow_name: str) -> Path:
    """Resolve per-workflow MFJ extension path.

    Canonical path: <workflows_root>/<workflow_name>/extended_orchestration/mfj_extension.json
    """
    wf = str(workflow_name or "").strip()
    if not wf:
        raise ValueError("workflow_name is required")
    workflow_dir = resolve_workflow_path(wf, normalize_workflow_roots())
    if workflow_dir is not None:
        return (workflow_dir / "extended_orchestration" / "mfj_extension.json").resolve()
    return (_workflows_root() / wf / "extended_orchestration" / "mfj_extension.json").resolve()


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


def _load_global_pack_graph_sources() -> List[Path]:
    sources: List[Path] = []
    seen: set[Path] = set()
    for root in normalize_workflow_roots():
        candidate = (root / "extended_orchestration" / "extension_registry.json").resolve()
        if candidate in seen or not candidate.exists():
            continue
        seen.add(candidate)
        sources.append(candidate)
    return sources


def _merge_section_items(
    current: List[Dict[str, Any]],
    incoming: List[Dict[str, Any]],
    *,
    key_fields: Tuple[str, ...],
) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = [deepcopy(item) for item in current if isinstance(item, dict)]
    index_by_key: Dict[Tuple[str, str], int] = {}

    def item_keys(item: Dict[str, Any]) -> List[Tuple[str, str]]:
        keys: List[Tuple[str, str]] = []
        for field in key_fields:
            value = item.get(field)
            if isinstance(value, str) and value.strip():
                keys.append((field, value.strip()))
        return keys

    for index, item in enumerate(merged):
        for key in item_keys(item):
            index_by_key[key] = index

    for item in incoming or []:
        if not isinstance(item, dict):
            continue
        clone = deepcopy(item)
        keys = item_keys(clone)
        target_index = next((index_by_key[key] for key in keys if key in index_by_key), None)
        if target_index is None:
            target_index = len(merged)
            merged.append(clone)
        else:
            merged[target_index] = clone
        for key in item_keys(clone):
            index_by_key[key] = target_index

    return merged


def _merge_global_pack_graph_dicts(raw_graphs: List[Dict[str, Any]]) -> Dict[str, Any]:
    merged: Dict[str, Any] = {
        "version": 3,
        "workflows": [],
        "entrypoints": [],
        "workflow_sequences": [],
        "transitions": [],
    }
    section_keys: Dict[str, Tuple[str, ...]] = {
        "workflows": ("id",),
        "entrypoints": ("id", "path"),
        "workflow_sequences": ("id",),
        "transitions": ("id",),
    }

    for raw in raw_graphs:
        if "version" in raw:
            merged["version"] = raw["version"]
        for scalar in ("pack_name", "description"):
            if scalar in raw and raw[scalar] is not None:
                merged[scalar] = deepcopy(raw[scalar])
        for section, key_fields in section_keys.items():
            merged[section] = _merge_section_items(
                merged.get(section, []),
                raw.get(section) or [],
                key_fields=key_fields,
            )

    return merged


def load_global_pack_graph() -> Optional[GlobalPackGraph]:
    """Load and validate the canonical global pack graph.

    Shared generation-core orchestration is the base layer. App/workspace roots
    may provide an overlay registry that augments or overrides entries by id.
    """
    global _GLOBAL_CACHE

    paths = _load_global_pack_graph_sources()
    if not paths:
        return None

    signature = tuple((str(path), path.stat().st_mtime) for path in paths)
    cached = _GLOBAL_CACHE
    if cached and cached.source == signature:
        payload = cached.payload
        if isinstance(payload, GlobalPackGraph):
            return payload

    raw_graphs: List[Dict[str, Any]] = []
    for path in reversed(paths):
        raw = _load_json_file(path)
        if raw is not None:
            raw_graphs.append(raw)
    if not raw_graphs:
        return None

    graph = parse_global_pack_graph(_merge_global_pack_graph_dicts(raw_graphs))
    _GLOBAL_CACHE = _CacheEntry(source=signature, payload=graph)
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
    signature = ((str(path), mtime),)
    if cached and cached.source == signature:
        payload = cached.payload
        if isinstance(payload, WorkflowPackGraph):
            return payload

    raw = _load_json_file(path)
    if raw is None:
        return None
    graph = parse_workflow_pack_graph(raw)
    _WORKFLOW_CACHE[wf] = _CacheEntry(source=signature, payload=graph)
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


def list_workflow_sequences(pack: GlobalPackGraph) -> List[GlobalJourney]:
    return list(pack.journeys)


def get_workflow_sequence(pack: GlobalPackGraph, sequence_id: str) -> Optional[GlobalJourney]:
    sid = str(sequence_id or "").strip()
    if not sid:
        return None
    for sequence in pack.journeys:
        if sequence.id == sid:
            return sequence
    return None


def list_entrypoints(pack: GlobalPackGraph) -> List[WorkflowEntrypoint]:
    """Return workflow-owned shell entrypoints."""
    return list(pack.entrypoints)


def list_transitions(pack: GlobalPackGraph) -> List[WorkflowTransition]:
    """Return all transitions registered in the global pack graph."""
    return list(pack.transitions)


def get_transition(pack: GlobalPackGraph, transition_id: str) -> Optional[WorkflowTransition]:
    """Look up a transition by id. Returns None if not found."""
    tid = str(transition_id or "").strip()
    if not tid:
        return None
    for transition in pack.transitions:
        if transition.id == tid:
            return transition
    return None


def infer_auto_workflow_sequence_for_start(
    pack: GlobalPackGraph, workflow_name: str
) -> Optional[GlobalJourney]:
    """Infer sequence whose first workflow step contains the requested workflow."""
    wf = str(workflow_name or "").strip()
    if not wf:
        return None
    for sequence in pack.journeys:
        groups = normalize_step_groups(sequence.steps)
        for group in groups:
            if not group:
                continue
            if wf in group:
                return sequence
            break
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


def compute_required_dependencies(pack: GlobalPackGraph, workflow_name: str) -> List[Dict[str, Any]]:
    """Build required prerequisite dependencies from explicit workflow declarations."""
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

    seen: set[Tuple[str, str, str]] = set()
    deduped: List[Dict[str, Any]] = []
    for dependency in required:
        parent = str(dependency.get("from") or "").strip()
        child = str(dependency.get("to") or "").strip()
        scope = str(dependency.get("scope") or "app").strip().lower() or "app"
        if not parent or not child:
            continue
        key = (parent, child, scope)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(dependency)
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
    "list_workflow_sequences",
    "get_workflow_sequence",
    "list_entrypoints",
    # Routing transitions
    "list_transitions",
    "get_transition",
    # Journey helpers
    "infer_auto_workflow_sequence_for_start",
    "compute_required_dependencies",
    "journey_next_step",
    "normalize_step_groups",
    "JourneyStepGroup",
]
