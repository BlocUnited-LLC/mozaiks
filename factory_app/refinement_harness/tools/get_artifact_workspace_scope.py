from __future__ import annotations

from typing import Any

from mozaiksai.control_plane.contracts import ControlPlaneToolContext
from mozaiksai.core.artifacts.store import ArtifactStore, get_artifact_store

from ._artifact_workspace import (
    load_artifact_workspace,
    resolve_related_imports,
    safe_relpath,
    siblings_for,
)
from ._shared import list_head, normalize_context, text_excerpt

_MAX_FILE_COUNT = 120
_MAX_PREVIEW_FILES = 4


async def get_artifact_workspace_scope(
    *,
    context: ControlPlaneToolContext | dict[str, Any] | None = None,
    artifact_store: ArtifactStore | None = None,
) -> dict[str, Any]:
    tool_context = normalize_context(context)
    app_id = str(tool_context.app_id or "").strip()
    build_record_id = str(tool_context.build_record_id or "").strip()
    if not app_id or not build_record_id:
        return {"present": False, "reason": "missing_app_id_or_build_record"}

    store = artifact_store or get_artifact_store()
    workspace = await load_artifact_workspace(
        artifact_store=store,
        app_id=app_id,
        build_record_id=build_record_id,
    )
    if not workspace.get("present"):
        return workspace

    artifact = workspace["artifact"]
    file_map = workspace["file_map"]
    selected_paths = _normalize_selected_paths(tool_context.extra.get("selected_file_paths"))

    file_tree = sorted(file_map.keys())
    selected_scope = {
        path: _build_selected_scope(path=path, file_map=file_map)
        for path in selected_paths
    }

    related_previews = _collect_related_previews(selected_paths=selected_paths, file_map=file_map)
    return {
        "present": True,
        "build_record_id": artifact.id,
        "build_family": artifact.build_family,
        "build_key": artifact.build_key,
        "source": workspace["source"],
        "file_count": len(file_tree),
        "file_tree": list_head(file_tree, limit=_MAX_FILE_COUNT),
        "selected_file_paths": selected_paths,
        "selected_scope": selected_scope,
        "related_file_previews": related_previews,
    }


def _normalize_selected_paths(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    selected: list[str] = []
    for item in value:
        safe = safe_relpath(item)
        if safe and safe not in selected:
            selected.append(safe)
    return selected


def _build_selected_scope(*, path: str, file_map: dict[str, str]) -> dict[str, Any]:
    content = file_map.get(path)
    siblings = siblings_for(path=path, file_map=file_map)
    resolved_imports = resolve_related_imports(path=path, content=content or "", file_map=file_map)
    return {
        "present": content is not None,
        "excerpt": text_excerpt(content, max_length=700) if content is not None else None,
        "sibling_files": siblings,
        "resolved_related_files": resolved_imports,
    }


def _collect_related_previews(*, selected_paths: list[str], file_map: dict[str, str]) -> list[dict[str, Any]]:
    preview_paths: list[tuple[str, str]] = []
    for selected_path in selected_paths:
        for related in resolve_related_imports(path=selected_path, content=file_map.get(selected_path, ""), file_map=file_map):
            preview_paths.append((related, f"imported_by:{selected_path}"))
        for sibling in siblings_for(path=selected_path, file_map=file_map):
            preview_paths.append((sibling, f"sibling_of:{selected_path}"))

    seen: set[str] = set()
    previews: list[dict[str, Any]] = []
    for candidate, reason in preview_paths:
        if candidate in seen:
            continue
        seen.add(candidate)
        previews.append(
            {
                "path": candidate,
                "reason": reason,
                "excerpt": text_excerpt(file_map.get(candidate), max_length=500),
            }
        )
        if len(previews) >= _MAX_PREVIEW_FILES:
            break
    return previews
