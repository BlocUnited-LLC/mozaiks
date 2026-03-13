from __future__ import annotations

from typing import Any, Dict, Optional

from mozaiksai.core.core_config import get_mongo_client
from mozaiksai.core.multitenant import build_app_scope_filter, coalesce_app_id


async def _find_green_room_doc(
    *,
    app_id: str,
    user_id: str,
    journey_id: Optional[str],
) -> Optional[Dict[str, Any]]:
    client = get_mongo_client()
    coll = client["MozaiksAI"]["ChatSessions"]

    base_query = {
        "workflow_name": "GreenRoom",
        "set_brief_ready": True,
        "user_id": user_id,
        **build_app_scope_filter(app_id),
    }
    projection = {
        "_id": 1,
        "set_title": 1,
        "set_brief": 1,
        "audience": 1,
        "tone": 1,
        "set_brief_packet": 1,
        "journey_id": 1,
    }

    if journey_id:
        doc = await coll.find_one(
            {**base_query, "journey_id": journey_id},
            projection=projection,
            sort=[("last_updated_at", -1)],
        )
        if isinstance(doc, dict):
            return doc

    return await coll.find_one(
        base_query,
        projection=projection,
        sort=[("last_updated_at", -1)],
    )


async def load_set_context(*, context_variables: Any = None) -> Dict[str, Any]:
    if context_variables is None:
        return {"status": "skipped", "reason": "missing_context"}

    try:
        if context_variables.get("room_seed_ready") is True and context_variables.get("set_brief_packet"):
            return {"status": "skipped", "reason": "already_seeded"}
    except Exception:
        pass

    try:
        app_id = str(coalesce_app_id(app_id=context_variables.get("app_id")) or "").strip()
        user_id = str(context_variables.get("user_id") or "").strip()
        journey_id = str(context_variables.get("journey_id") or "").strip()
    except Exception:
        return {"status": "skipped", "reason": "context_read_failed"}

    if not app_id or not user_id:
        return {"status": "skipped", "reason": "missing_identity"}

    doc = await _find_green_room_doc(
        app_id=app_id,
        user_id=user_id,
        journey_id=journey_id or None,
    )
    if not isinstance(doc, dict):
        return {"status": "skipped", "reason": "no_set_brief"}

    try:
        context_variables.set("set_title", str(doc.get("set_title") or "").strip())
        context_variables.set("set_brief", str(doc.get("set_brief") or "").strip())
        context_variables.set("audience", str(doc.get("audience") or "").strip())
        context_variables.set("tone", str(doc.get("tone") or "warm").strip().lower())
        context_variables.set("set_brief_packet", doc.get("set_brief_packet") or {})
        context_variables.set("set_source_chat_id", str(doc.get("_id") or "").strip())
        context_variables.set("room_seed_ready", True)
    except Exception:
        return {"status": "skipped", "reason": "context_write_failed"}

    return {
        "status": "loaded",
        "set_title": str(doc.get("set_title") or "").strip(),
        "source_chat_id": str(doc.get("_id") or "").strip(),
    }
