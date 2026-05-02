from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Iterable, List, Optional


def repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in [here] + list(here.parents):
        try:
            if (parent / "mozaiksai").is_dir():
                return parent
        except Exception:
            continue
    return Path.cwd().resolve()


def _unconfigured_active_app_root() -> Path:
    return (repo_root() / "__no_active_app__").resolve()


def _repo_factory_workflows_root() -> Path:
    return (repo_root() / "factory_app" / "workflows").resolve()


def _resolve_app_root_candidate(value: str | os.PathLike[str]) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = (repo_root() / candidate).resolve()
    direct_app_json = candidate / "app.json"
    nested_app_json = candidate / "app" / "app.json"
    if direct_app_json.exists():
        return candidate.resolve()
    if nested_app_json.exists():
        return (candidate / "app").resolve()
    return candidate.resolve()


def resolve_active_app_root() -> Path:
    override = str(os.getenv("PLATFORM_PATH") or "").strip()
    if override:
        return _resolve_app_root_candidate(override)

    workspace_override = str(os.getenv("MOZAIKS_APP_WORKSPACE_PATH") or "").strip()
    if workspace_override:
        return _resolve_app_root_candidate(workspace_override)

    return _unconfigured_active_app_root()


def _split_roots(raw: str) -> List[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(os.pathsep) if part.strip()]


def _normalize_root(value: str | os.PathLike[str]) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = (repo_root() / candidate).resolve()
    return candidate.resolve()


def normalize_workflow_roots(
    roots: Optional[Iterable[str | os.PathLike[str]]] = None,
) -> List[Path]:
    candidates: List[Path] = []
    seen: set[Path] = set()

    def add(path_like: str | os.PathLike[str]) -> None:
        path = _normalize_root(path_like)
        if path in seen:
            return
        seen.add(path)
        candidates.append(path)

    if roots is not None:
        for value in roots:
            add(value)
        return candidates

    explicit_roots = _split_roots(str(os.getenv("MOZAIKS_WORKFLOW_ROOTS") or "").strip())
    if explicit_roots:
        for value in explicit_roots:
            add(value)
        return candidates

    single_root = str(os.getenv("MOZAIKS_WORKFLOWS_PATH") or "").strip()
    if single_root:
        add(single_root)
        return candidates

    resolved_active_root = resolve_active_app_root()
    unconfigured_root = _unconfigured_active_app_root()
    if resolved_active_root != unconfigured_root:
        add((resolved_active_root / "workflows").resolve())

    repo_factory_root = _repo_factory_workflows_root()
    if repo_factory_root.is_dir():
        add(repo_factory_root)

    if not candidates:
        add((resolved_active_root / "workflows").resolve())
    return candidates


def primary_workflows_root(
    roots: Optional[Iterable[str | os.PathLike[str]]] = None,
) -> Path:
    normalized = normalize_workflow_roots(roots)
    if normalized:
        return normalized[0]
    return (resolve_active_app_root() / "workflows").resolve()


def resolve_workflow_path(
    workflow_name: str,
    roots: Optional[Iterable[str | os.PathLike[str]]] = None,
) -> Optional[Path]:
    wf = str(workflow_name or "").strip()
    if not wf:
        return None

    for root in normalize_workflow_roots(roots):
        candidate = (root / wf).resolve()
        if not candidate.is_dir():
            continue
        if (candidate / "orchestrator.yaml").exists():
            return candidate
    return None


def discover_workflow_paths(
    roots: Optional[Iterable[str | os.PathLike[str]]] = None,
) -> Dict[str, Path]:
    discovered: Dict[str, Path] = {}
    seen: set[str] = set()
    for root in normalize_workflow_roots(roots):
        if not root.exists():
            continue
        for item in sorted(root.iterdir(), key=lambda value: value.name.lower()):
            if not item.is_dir() or item.name.startswith(".") or item.name == "extended_orchestration":
                continue
            if not (item / "orchestrator.yaml").exists():
                continue
            normalized = item.name.lower()
            if normalized in seen:
                continue
            seen.add(normalized)
            discovered[item.name] = item.resolve()
    return discovered


__all__ = [
    "discover_workflow_paths",
    "normalize_workflow_roots",
    "primary_workflows_root",
    "repo_root",
    "resolve_active_app_root",
    "resolve_workflow_path",
]
