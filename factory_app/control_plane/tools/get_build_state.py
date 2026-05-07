from __future__ import annotations

from typing import Any, Optional

from mozaiksai.control_plane.contracts import ControlPlaneToolContext
from mozaiksai.core.data.persistence.artifact_store import BuilderArtifactStore

from ._shared import list_head, normalize_context


async def get_build_state(
    *,
    context: ControlPlaneToolContext | dict[str, Any] | None = None,
    store: Optional[BuilderArtifactStore] = None,
) -> dict[str, Any]:
    tool_context = normalize_context(context)
    app_id = str(tool_context.app_id or "").strip()
    if not app_id:
        return {"present": False, "reason": "missing_app_id"}

    build_store = store or BuilderArtifactStore()
    build_plan = await build_store.get_build_plan(app_id=app_id)
    theme_capture = await build_store.get_theme_capture(app_id=app_id)
    if not isinstance(build_plan, dict) and not isinstance(theme_capture, dict):
        return {"present": False, "app_id": app_id}

    tasks = build_plan.get("tasks") if isinstance(build_plan, dict) else []
    entities = build_plan.get("entities") if isinstance(build_plan, dict) else []
    identity = theme_capture.get("identity") if isinstance(theme_capture, dict) else {}
    if not isinstance(identity, dict):
        identity = {}

    return {
        "present": True,
        "app_id": app_id,
        "build_plan": {
            "build_plan_id": (
                (build_plan.get("build_plan_id") or build_plan.get("id")) if isinstance(build_plan, dict) else None
            ),
            "task_count": len(tasks) if isinstance(tasks, list) else 0,
            "task_ids": [
                str(task.get("task_id") or task.get("id") or "").strip()
                for task in list_head(tasks, limit=10)
                if isinstance(task, dict) and str(task.get("task_id") or task.get("id") or "").strip()
            ],
            "entity_count": len(entities) if isinstance(entities, list) else 0,
        },
        "theme_capture": {
            "present": isinstance(theme_capture, dict),
            "app_url": theme_capture.get("app_url") if isinstance(theme_capture, dict) else None,
            "identity": {
                key: value
                for key, value in identity.items()
                if key in {"app_name", "brand_name", "tone", "persona", "industry"}
            },
        },
    }
