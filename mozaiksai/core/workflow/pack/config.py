from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..paths import primary_workflows_root
from .schema import (
    GlobalJourney,
    GlobalPackGraph,
    JourneyStepGroup,
    WorkflowDependency,
    WorkflowEntry,
    WorkflowEntrypoint,
    WorkflowTransition,
    normalize_step_groups,
    parse_global_pack_graph,
)


@dataclass
class _CacheEntry:
    source: tuple[tuple[str, float], ...]
    payload: Any


_GLOBAL_CACHE: _CacheEntry | None = None

def _workflows_root() -> Path:
    """Resolve canonical workflows root.

    Resolution order:
      1. MOZAIKS_WORKFLOWS_PATH
      2. workspace-root workflows for the active app
      3. repo-local factory workflows fallback
    """
    return primary_workflows_root()


def get_global_pack_graph_path(workflows_root: Path | None = None) -> Path:
    """Resolve the active global extension registry path."""
    root = workflows_root if workflows_root is not None else _workflows_root()
    return (root / "extended_orchestration" / "extension_registry.json").resolve()


def _load_json_file(path: Path) -> dict[str, Any] | None:
    try:
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"Pack graph must be a JSON object: {path}")
        return raw
    except Exception as exc:
        raise ValueError(f"Failed loading pack graph {path}: {exc}") from exc


def load_global_pack_graph(workflows_root: Path | None = None) -> GlobalPackGraph | None:
    """Load and validate the canonical global pack graph.

    Pass ``workflows_root`` to load from a specific directory without touching
    environment variables — used by tests and scripts that need the factory pack
    graph without leaking process-level state.
    """
    global _GLOBAL_CACHE

    path = get_global_pack_graph_path(workflows_root)
    if not path.exists():
        return None

    signature = ((str(path), path.stat().st_mtime),)
    cached = _GLOBAL_CACHE
    if cached and cached.source == signature:
        payload = cached.payload
        if isinstance(payload, GlobalPackGraph):
            return payload

    raw = _load_json_file(path)
    if raw is None:
        return None

    graph = parse_global_pack_graph(raw)
    _GLOBAL_CACHE = _CacheEntry(source=signature, payload=graph)
    return graph


def list_workflow_ids(pack: GlobalPackGraph) -> list[str]:
    return [w.id for w in pack.workflows]


def get_workflow_entry(pack: GlobalPackGraph, workflow_id: str) -> WorkflowEntry | None:
    wf = str(workflow_id or "").strip()
    if not wf:
        return None
    for entry in pack.workflows:
        if entry.id == wf:
            return entry
    return None


def list_workflow_sequences(pack: GlobalPackGraph) -> list[GlobalJourney]:
    return list(pack.journeys)


def get_workflow_sequence(pack: GlobalPackGraph, sequence_id: str) -> GlobalJourney | None:
    sid = str(sequence_id or "").strip()
    if not sid:
        return None
    for sequence in pack.journeys:
        if sequence.id == sid:
            return sequence
    return None


def list_entrypoints(pack: GlobalPackGraph) -> list[WorkflowEntrypoint]:
    """Return workflow-owned shell entrypoints."""
    return list(pack.entrypoints)


def list_transitions(pack: GlobalPackGraph) -> list[WorkflowTransition]:
    """Return all transitions registered in the global pack graph."""
    return list(pack.transitions)


def get_transition(pack: GlobalPackGraph, transition_id: str) -> WorkflowTransition | None:
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
) -> GlobalJourney | None:
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


def _normalize_dependency_spec(value: str | WorkflowDependency) -> WorkflowDependency | None:
    if isinstance(value, WorkflowDependency):
        return value
    if isinstance(value, str):
        dep = value.strip()
        if not dep:
            return None
        return WorkflowDependency(id=dep)
    return None


def compute_required_dependencies(pack: GlobalPackGraph, workflow_name: str) -> list[dict[str, Any]]:
    """Build required prerequisite dependencies from explicit workflow declarations."""
    target = str(workflow_name or "").strip()
    if not target:
        return []

    required: list[dict[str, Any]] = []

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

    seen: set[tuple[str, str, str]] = set()
    deduped: list[dict[str, Any]] = []
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


def journey_next_step(journey: GlobalJourney, current_workflow: str) -> str | None:
    groups = normalize_step_groups(journey.steps)
    current = str(current_workflow or "").strip()
    if not current or not groups:
        return None

    current_idx: int | None = None
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
    "load_global_pack_graph",
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
