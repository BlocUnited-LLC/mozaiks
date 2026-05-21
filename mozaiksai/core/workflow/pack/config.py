from __future__ import annotations

import json
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
from ..paths import normalize_workflow_roots, primary_workflows_root, resolve_workflow_path


@dataclass
class _CacheEntry:
    source: Tuple[Tuple[str, float], ...]
    payload: Any


_GLOBAL_CACHE: Optional[_CacheEntry] = None
_WORKFLOW_CACHE: Dict[str, _CacheEntry] = {}

def _workflows_root() -> Path:
    """Resolve canonical workflows root.

    Resolution order:
      1. MOZAIKS_WORKFLOWS_PATH
      2. First entry in legacy MOZAIKS_WORKFLOW_ROOTS
      3. active app root workflows
      4. repo-local factory workflows fallback
    """
    return primary_workflows_root()


def get_global_pack_graph_path() -> Path:
    """Resolve the active global extension registry path."""
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


def _merge_raw_registries(registries: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Merge multiple raw registry dicts into one.

    Registries are provided in priority order — earlier entries win on ID conflicts.
    This allows a product overlay (mozaiks-app) to take precedence over the base
    factory registry while still inheriting all factory workflows, sequences, and
    transitions the overlay doesn't declare.

    List fields are deduplicated by 'id'; scalar fields use first non-null
    value. Artifact dependency graph entries are merged by family, preserving
    priority order and deduplicating upstream edges.
    """
    if not registries:
        return None

    list_fields = ("workflows", "entrypoints", "transitions", "journeys", "workflow_sequences")
    merged: Dict[str, Any] = {"version": 3}
    seen_ids: Dict[str, set] = {f: set() for f in list_fields}
    artifact_dependency_graph: Dict[str, List[str]] = {}

    for registry in registries:
        if not merged.get("pack_name"):
            merged["pack_name"] = registry.get("pack_name")
        if not merged.get("description"):
            merged["description"] = registry.get("description")

        for field in list_fields:
            entries = registry.get(field)
            if not entries:
                continue
            existing = merged.setdefault(field, [])
            for entry in entries:
                entry_id = entry.get("id") if isinstance(entry, dict) else None
                if entry_id is not None:
                    if entry_id in seen_ids[field]:
                        continue  # primary (earlier) already claimed this id
                    seen_ids[field].add(entry_id)
                existing.append(entry)

        graph = registry.get("artifact_dependency_graph")
        if graph is None:
            continue
        if not isinstance(graph, dict):
            raise ValueError("artifact_dependency_graph must be a JSON object")
        for family, dependencies in graph.items():
            family_id = str(family or "").strip()
            if not family_id:
                raise ValueError("artifact_dependency_graph family ids must be non-empty strings")
            if not isinstance(dependencies, list):
                raise ValueError(
                    f"artifact_dependency_graph family '{family_id}' dependencies must be a list"
                )
            merged_dependencies = artifact_dependency_graph.setdefault(family_id, [])
            for dependency in dependencies:
                dependency_id = str(dependency or "").strip()
                if not dependency_id:
                    raise ValueError(
                        f"artifact_dependency_graph family '{family_id}' dependencies must be non-empty strings"
                    )
                if dependency_id not in merged_dependencies:
                    merged_dependencies.append(dependency_id)

    if artifact_dependency_graph:
        merged["artifact_dependency_graph"] = artifact_dependency_graph

    return merged


def load_global_pack_graph() -> Optional[GlobalPackGraph]:
    """Load and validate the canonical global pack graph.

    When multiple workflow roots are configured (e.g. a product overlay on top
    of the factory build pipeline), all extension_registry.json files are merged.
    Earlier roots (higher-priority) win on ID conflicts; later roots (base/factory)
    provide the build sequences and transitions the overlay does not declare.
    """
    global _GLOBAL_CACHE

    roots = normalize_workflow_roots()
    reg_paths = [
        (root / "extended_orchestration" / "extension_registry.json").resolve()
        for root in roots
    ]
    existing_paths = [p for p in reg_paths if p.exists()]

    if not existing_paths:
        return None

    signature = tuple((str(p), p.stat().st_mtime) for p in existing_paths)
    cached = _GLOBAL_CACHE
    if cached and cached.source == signature:
        payload = cached.payload
        if isinstance(payload, GlobalPackGraph):
            return payload

    registries = [r for p in existing_paths if (r := _load_json_file(p)) is not None]
    if not registries:
        return None

    raw = _merge_raw_registries(registries) if len(registries) > 1 else registries[0]
    if raw is None:
        return None

    graph = parse_global_pack_graph(raw)
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
