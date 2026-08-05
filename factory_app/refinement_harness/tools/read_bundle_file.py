from __future__ import annotations

from typing import Any

from mozaiksai.control_plane.contracts import ControlPlaneToolContext
from mozaiksai.core.artifacts.store import BuildRecordStore, get_build_record_store

from ._bundle_workspace import load_bundle_workspace, safe_relpath
from ._shared import normalize_context

_MAX_CONTENT_CHARS = 80_000


async def read_bundle_file(
    *,
    context: ControlPlaneToolContext | dict[str, Any] | None = None,
    record_store: BuildRecordStore | None = None,
) -> dict[str, Any]:
    """
    Read a single file from the bundle workspace by path.

    The path must be a valid relative workspace path (e.g. "modules/orders/module.yaml").
    The file content is returned as a string up to _MAX_CONTENT_CHARS.

    Returns::

        {
          "present": True,
          "path": "modules/orders/module.yaml",
          "content": "...",
          "truncated": False,
          "build_record_id": "...",
          "source": "workspace_dir" | "artifact_zip" | "content_store:...",
        }

    or an error shape when the workspace or file is not found.
    """
    tool_context = normalize_context(context)
    app_id = str(tool_context.app_id or "").strip()
    build_record_id = str(tool_context.artifact_version_id or "").strip()
    path_raw = (tool_context.extra or {}).get("path") or ""
    path = safe_relpath(str(path_raw).strip())

    if not app_id or not build_record_id:
        return {"present": False, "reason": "missing_app_id_or_artifact_version"}
    if not path:
        return {"present": False, "reason": "missing_or_invalid_path", "path_raw": str(path_raw)}

    store = record_store or get_build_record_store()
    workspace = await load_bundle_workspace(
        record_store=store,
        app_id=app_id,
        build_record_id=build_record_id,
    )
    if not workspace.get("present"):
        return workspace

    artifact = workspace["artifact"]
    file_map: dict[str, str] = workspace["file_map"]

    if path not in file_map:
        available_prefixes = sorted({p.split("/")[0] for p in file_map})
        return {
            "present": False,
            "reason": "file_not_found",
            "path": path,
            "build_record_id": artifact.id,
            "available_top_level": available_prefixes[:20],
        }

    content = file_map[path]
    truncated = len(content) > _MAX_CONTENT_CHARS
    return {
        "present": True,
        "path": path,
        "content": content[:_MAX_CONTENT_CHARS],
        "truncated": truncated,
        "char_count": len(content),
        "build_record_id": artifact.id,
        "build_family": artifact.build_family,
        "source": workspace["source"],
    }
