from __future__ import annotations

from typing import Any

from mozaiksai.core.data.models import WorkflowStatus
from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager
from mozaiksai.core.multitenant import build_app_scope_filter
from mozaiksai.core.workflow.pack.config import (
    compute_required_dependencies,
    get_workflow_entry,
    list_workflow_ids,
    load_global_pack_graph,
)


async def validate_pack_prereqs(
    *,
    app_id: str,
    user_id: str,
    workflow_name: str,
    persistence: AG2PersistenceManager | None = None,
) -> tuple[bool, str | None]:
    """Validate workflow prerequisites from canonical global pack graph."""
    try:
        wf = str(workflow_name or "").strip()
        if not wf:
            return False, "workflow_name is required"
        scope_id = str(app_id or "").strip()
        if not scope_id:
            return False, "app_id is required"
        uid = str(user_id or "").strip()
        if not uid:
            return False, "user_id is required"

        pack = load_global_pack_graph()
        if pack is None:
            return True, None

        required_dependencies = compute_required_dependencies(pack, wf)
        if not required_dependencies:
            return True, None

        pm = persistence or AG2PersistenceManager()
        coll = await pm._coll()

        missing_msgs: list[str] = []
        for dependency in required_dependencies:
            if not isinstance(dependency, dict):
                continue
            parent = str(dependency.get("from") or "").strip()
            if not parent:
                continue
            reason = str(dependency.get("reason") or "").strip()
            scope = str(dependency.get("scope") or "app").strip().lower()

            query: dict[str, Any] = {
                "workflow_name": parent,
                "status": int(WorkflowStatus.COMPLETED),
                **build_app_scope_filter(scope_id),
            }
            if scope == "user":
                query["user_id"] = uid

            doc = await coll.find_one(
                query,
                projection={"_id": 1},
                sort=[("completed_at", -1), ("created_at", -1)],
            )
            if not doc:
                missing_msgs.append(reason or f"{wf} requires {parent} to be completed first.")

        if not missing_msgs:
            return True, None

        seen = set()
        uniq: list[str] = []
        for msg in missing_msgs:
            if msg in seen:
                continue
            seen.add(msg)
            uniq.append(msg)
        return False, " ".join(uniq)
    except Exception:
        return False, "Failed to validate workflow prerequisites. Please try again."


async def list_workflow_availability(
    *,
    app_id: str,
    user_id: str,
    persistence: AG2PersistenceManager | None = None,
) -> list[dict[str, Any]]:
    """List workflows declared in global pack graph with availability status."""
    scope_id = str(app_id or "").strip()
    uid = str(user_id or "").strip()
    if not scope_id or not uid:
        return []

    pack = load_global_pack_graph()
    if pack is None:
        return []

    results: list[dict[str, Any]] = []
    for wf in list_workflow_ids(pack):
        ok, reason = await validate_pack_prereqs(
            app_id=scope_id,
            user_id=uid,
            workflow_name=wf,
            persistence=persistence,
        )
        entry = get_workflow_entry(pack, wf)
        results.append(
            {
                "workflow_name": wf,
                "available": bool(ok),
                "reason": reason or "All prerequisites met",
                "description": entry.description if entry else None,
                "required_dependencies": compute_required_dependencies(pack, wf),
            }
        )

    return results


__all__ = ["validate_pack_prereqs", "list_workflow_availability"]
