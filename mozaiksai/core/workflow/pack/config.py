from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mozaiksai.resources import resolve_factory_workflows_root

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
DEFAULT_WORKFLOW_REGISTRY_EXTENDS = "mozaiks.default_workflow_registry"

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

    raw = _load_json_file(path)
    if raw is None:
        return None
    base_path = _resolve_default_registry_path(path, raw)
    signature_items = [(str(path), path.stat().st_mtime)]
    if base_path is not None:
        signature_items.append((str(base_path), base_path.stat().st_mtime))
    signature = tuple(signature_items)
    cached = _GLOBAL_CACHE
    if cached and cached.source == signature:
        payload = cached.payload
        if isinstance(payload, GlobalPackGraph):
            return payload

    if base_path is not None:
        base_raw = _load_json_file(base_path)
        if base_raw is None:
            raise ValueError(f"Default workflow registry could not be loaded: {base_path}")
        raw = _merge_registry_overlay(base_raw, raw)

    graph = parse_global_pack_graph(raw)
    _GLOBAL_CACHE = _CacheEntry(source=signature, payload=graph)
    return graph


def _resolve_default_registry_path(path: Path, raw: dict[str, Any]) -> Path | None:
    extends = str(raw.get("extends") or "").strip()
    if not extends:
        return None
    if extends != DEFAULT_WORKFLOW_REGISTRY_EXTENDS:
        raise ValueError(
            f"extension_registry.json extends must be {DEFAULT_WORKFLOW_REGISTRY_EXTENDS!r}"
        )
    factory_root = resolve_factory_workflows_root()
    if factory_root is None:
        raise ValueError("Default workflow registry was not found in the installed mozaiks package")
    base_path = get_global_pack_graph_path(factory_root)
    if base_path.resolve() == path.resolve():
        raise ValueError("Default workflow registry cannot extend itself")
    return base_path


def _merge_registry_overlay(base_raw: dict[str, Any], overlay_raw: dict[str, Any]) -> dict[str, Any]:
    base = deepcopy(base_raw)
    overlay = deepcopy(overlay_raw)
    overlay.pop("extends", None)

    base_version = base.get("version")
    overlay_version = overlay.get("version", base_version)
    if overlay_version != base_version:
        raise ValueError("extension_registry.json overlay cannot change version")

    merged = base
    for key in ("pack_name", "description"):
        if key in overlay:
            merged[key] = overlay[key]

    merged["artifact_dependency_graph"] = _merge_artifact_dependency_graph(
        base.get("artifact_dependency_graph") or {},
        overlay.get("artifact_dependency_graph") or {},
    )
    for key in ("workflows", "entrypoints", "workflow_sequences", "transitions"):
        merged[key] = _merge_registry_list_by_id(base.get(key) or [], overlay.get(key) or [])
    return merged


def _merge_artifact_dependency_graph(
    base_graph: dict[str, Any],
    overlay_graph: dict[str, Any],
) -> dict[str, list[str]]:
    merged: dict[str, list[str]] = {
        str(family): [str(dep) for dep in dependencies]
        for family, dependencies in base_graph.items()
        if isinstance(dependencies, list)
    }
    for raw_family, raw_dependencies in overlay_graph.items():
        family = str(raw_family or "").strip()
        if not family:
            raise ValueError("artifact_dependency_graph overlay family ids must be non-empty")
        if not isinstance(raw_dependencies, list):
            raise ValueError(f"artifact_dependency_graph family {family!r} dependencies must be a list")
        dependencies = merged.setdefault(family, [])
        for raw_dependency in raw_dependencies:
            dependency = str(raw_dependency or "").strip()
            if dependency and dependency not in dependencies:
                dependencies.append(dependency)
    return merged


def _merge_registry_list_by_id(base_items: list[Any], overlay_items: list[Any]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    positions: dict[str, int] = {}
    for item in base_items:
        normalized = _normalize_registry_item(item)
        item_id = normalized["id"]
        positions[item_id] = len(merged)
        merged.append(normalized)

    for item in overlay_items:
        normalized = _normalize_registry_item(item)
        item_id = normalized["id"]
        if normalized.get("remove") is True:
            if item_id in positions:
                index = positions.pop(item_id)
                merged.pop(index)
                positions = {entry["id"]: idx for idx, entry in enumerate(merged)}
            continue
        normalized.pop("remove", None)
        if item_id in positions:
            merged[positions[item_id]] = _deep_merge_mapping(merged[positions[item_id]], normalized)
        else:
            positions[item_id] = len(merged)
            merged.append(normalized)
    return merged


def _normalize_registry_item(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError("extension_registry.json list entries must be objects")
    normalized = deepcopy(item)
    item_id = str(normalized.get("id") or "").strip()
    if not item_id:
        raise ValueError("extension_registry.json list entries must declare id")
    normalized["id"] = item_id
    return normalized


def _deep_merge_mapping(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_mapping(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


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
    "DEFAULT_WORKFLOW_REGISTRY_EXTENDS",
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
