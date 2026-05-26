from __future__ import annotations

import os
from pathlib import Path

from mozaiksai.resources import resolve_factory_workflows_root


def repo_root() -> Path:
    return Path.cwd().resolve()


def _unconfigured_active_app_root() -> Path:
    return (repo_root() / "__no_active_app__").resolve()


def _repo_factory_workflows_root() -> Path:
    resolved = resolve_factory_workflows_root()
    if resolved is not None:
        return resolved
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


def candidate_app_workflows_roots(app_root: Path) -> tuple[Path, ...]:
    """Return canonical workflow-root candidates for an active app root."""
    roots: list[Path] = []
    if app_root.name == "app":
        roots.append((app_root.parent / "workflows").resolve())
    roots.append((app_root / "workflows").resolve())
    return tuple(dict.fromkeys(roots))


def _normalize_root(value: str | os.PathLike[str]) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = (repo_root() / candidate).resolve()
    return candidate.resolve()


def _reject_path_list(value: str) -> None:
    if os.pathsep in value:
        raise ValueError(
            "MOZAIKS_WORKFLOWS_PATH must be a single workflow root. "
            "Select the app workflow root or the factory workflow root explicitly."
        )


def resolve_workflows_root(
    root: str | os.PathLike[str] | None = None,
) -> Path:
    if root is not None:
        if isinstance(root, (list, tuple, set)):
            raise ValueError("Workflow resolution accepts one workflow root, not a root list.")
        if isinstance(root, str):
            _reject_path_list(root)
        return _normalize_root(root)

    raw_roots = str(os.getenv("MOZAIKS_WORKFLOWS_PATH") or "").strip()
    if raw_roots:
        _reject_path_list(raw_roots)
        return _normalize_root(raw_roots)

    resolved_active_root = resolve_active_app_root()
    unconfigured_root = _unconfigured_active_app_root()
    if resolved_active_root != unconfigured_root:
        for app_workflows_root in candidate_app_workflows_roots(resolved_active_root):
            if app_workflows_root.is_dir():
                return app_workflows_root

    repo_factory_root = _repo_factory_workflows_root()
    if repo_factory_root.is_dir():
        return repo_factory_root

    return candidate_app_workflows_roots(resolved_active_root)[0]


def primary_workflows_root(
    root: str | os.PathLike[str] | None = None,
) -> Path:
    return resolve_workflows_root(root)


def resolve_workflow_path(
    workflow_name: str,
    root: str | os.PathLike[str] | None = None,
) -> Path | None:
    wf = str(workflow_name or "").strip()
    if not wf:
        return None

    root_path = resolve_workflows_root(root)
    candidate = (root_path / wf).resolve()
    if candidate.is_dir() and (candidate / "orchestrator.yaml").exists():
        return candidate
    return None


def discover_workflow_paths(
    root: str | os.PathLike[str] | None = None,
) -> dict[str, Path]:
    discovered: dict[str, Path] = {}
    seen: set[str] = set()
    root_path = resolve_workflows_root(root)
    if not root_path.exists():
        return discovered
    for item in sorted(root_path.iterdir(), key=lambda value: value.name.lower()):
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
    "candidate_app_workflows_roots",
    "discover_workflow_paths",
    "primary_workflows_root",
    "repo_root",
    "resolve_active_app_root",
    "resolve_workflow_path",
    "resolve_workflows_root",
]
