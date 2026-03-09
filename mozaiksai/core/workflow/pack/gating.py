from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from mozaiksai.core.data.models import WorkflowStatus
from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager
from mozaiksai.core.multitenant import build_app_scope_filter
from mozaiksai.core.workflow.pack.config import compute_required_gates, load_pack_config


async def validate_pack_prereqs(
    *,
    app_id: str,
    user_id: str,
    workflow_name: str,
    persistence: Optional[AG2PersistenceManager] = None,
) -> Tuple[bool, Optional[str]]:
    """Validate workflow prerequisites from the pack config (if present).

    - Required gates block starting/resuming a workflow until upstream workflows have at least one
      COMPLETED chat session in the applicable scope.
    - Journey step order is enforced via implicit required gates when a journey has
      `enforce_step_gating=true`.

    Note: gating checks for existence of *any* completed upstream run (not necessarily the most recent),
    so refactors/new attempts do not re-lock downstream workflows.
    """
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

        pack = load_pack_config()
        if not pack:
            return True, None

        required_gates = compute_required_gates(pack, wf)
        if not required_gates:
            return True, None

        pm = persistence or AG2PersistenceManager()
        coll = await pm._coll()

        missing_msgs: List[str] = []
        for gate in required_gates:
            if not isinstance(gate, dict):
                continue
            parent = str(gate.get("from") or "").strip()
            if not parent:
                continue
            reason = str(gate.get("reason") or "").strip()

            query: Dict[str, Any] = {
                "workflow_name": parent,
                "user_id": uid,
                "status": int(WorkflowStatus.COMPLETED),
                **build_app_scope_filter(scope_id),
            }

            doc = await coll.find_one(
                query,
                projection={"_id": 1},
                sort=[("completed_at", -1), ("created_at", -1)],
            )
            if not doc:
                missing_msgs.append(reason or f"{wf} requires {parent} to be completed first.")

        if not missing_msgs:
            return True, None

        # De-dupe while preserving order.
        seen = set()
        uniq: List[str] = []
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
    persistence: Optional[AG2PersistenceManager] = None,
) -> List[Dict[str, Any]]:
    """List workflows declared in the pack config and whether they are startable."""
    scope_id = str(app_id or "").strip()
    uid = str(user_id or "").strip()
    if not scope_id or not uid:
        return []

    pack = load_pack_config()
    if not pack:
        return []

    workflows = pack.get("workflows") or []
    if not isinstance(workflows, list):
        return []

    pm = persistence or AG2PersistenceManager()
    coll = await pm._coll()

    results: List[Dict[str, Any]] = []
    for w in workflows:
        if not isinstance(w, dict):
            continue
        wf = str(w.get("id") or "").strip()
        if not wf:
            continue

        ok, reason = await validate_pack_prereqs(
            app_id=scope_id,
            user_id=uid,
            workflow_name=wf,
            persistence=pm,
        )
        results.append(
            {
                "workflow_name": wf,
                "available": bool(ok),
                "reason": reason or "All prerequisites met",
                "type": str(w.get("type") or "").strip() or None,
                "description": str(w.get("description") or "").strip() or None,
                "required_gates": compute_required_gates(pack, wf),
            }
        )

    return results


__all__ = ["validate_pack_prereqs", "list_workflow_availability"]
