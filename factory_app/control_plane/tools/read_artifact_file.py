from __future__ import annotations

from typing import Any, Optional

from mozaiksai.core.artifacts.store import ArtifactStore, get_artifact_store
from mozaiksai.control_plane.contracts import ControlPlaneToolContext

from ._shared import normalize_context, text_excerpt
from ._artifact_workspace import load_artifact_workspace, safe_relpath

_MAX_CONTENT_CHARS = 80_000


async def read_artifact_file(
    *,
    context: ControlPlaneToolContext | dict[str, Any] | None = None,
    artifact_store: Optional[ArtifactStore] = None,
) -> dict[str, Any]:
    """
    Read a single file from the artifact workspace by path.

    The path must be a valid relative workspace path (e.g. "modules/orders/module.yaml").
    The file content is returned as a string up to _MAX_CONTENT_CHARS.

    Returns::

        {
          "present": True,
          "path": "modules/orders/module.yaml",
          "content": "...",
          "truncated": False,
          "artifact_version_id": "...",
          "source": "workspace_dir" | "artifact_zip" | "content_store:...",
        }

    or an error shape when the workspace or file is not found.
    """
    tool_context = normalize_context(context)
    app_id = str(tool_context.app_id or "").strip()
    artifact_version_id = str(tool_context.artifact_version_id or "").strip()
    path_raw = (tool_context.extra or {}).get("path") or ""
    path = safe_relpath(str(path_raw).strip())

    if not app_id or not artifact_version_id:
        return {"present": False, "reason": "missing_app_id_or_artifact_version"}
    if not path:
        return {"present": False, "reason": "missing_or_invalid_path", "path_raw": str(path_raw)}

    store = artifact_store or get_artifact_store()
    workspace = await load_artifact_workspace(
        artifact_store=store,
        app_id=app_id,
        artifact_version_id=artifact_version_id,
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
            "artifact_version_id": artifact.id,
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
        "artifact_version_id": artifact.id,
        "artifact_kind": artifact.artifact_kind,
        "source": workspace["source"],
    }
