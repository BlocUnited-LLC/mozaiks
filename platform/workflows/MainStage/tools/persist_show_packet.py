from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from mozaiksai.core.core_config import get_mongo_client
from mozaiksai.core.multitenant import build_app_scope_filter, coalesce_app_id


async def persist_show_packet(*, context_variables: Any = None) -> Dict[str, Any]:
    if context_variables is None:
        return {"status": "skipped", "reason": "missing_context"}

    try:
        chat_id = str(context_variables.get("chat_id") or "").strip()
        app_id = str(coalesce_app_id(app_id=context_variables.get("app_id")) or "").strip()
        set_title = str(context_variables.get("set_title") or "").strip()
        set_brief = str(context_variables.get("set_brief") or "").strip()
        audience = str(context_variables.get("audience") or "").strip()
        tone = str(context_variables.get("tone") or "warm").strip().lower()
        final_direction = str(context_variables.get("final_direction") or "").strip().lower()
        show_packet = context_variables.get("show_packet")
    except Exception:
        return {"status": "skipped", "reason": "context_read_failed"}

    if not chat_id or not app_id or not isinstance(show_packet, dict):
        return {"status": "skipped", "reason": "packet_not_ready"}

    client = get_mongo_client()
    coll = client["MozaiksAI"]["ChatSessions"]
    update = {
        "set_title": set_title,
        "set_brief": set_brief,
        "audience": audience,
        "tone": tone,
        "show_packet": show_packet,
        "final_direction": final_direction or show_packet.get("final_direction"),
        "last_updated_at": datetime.now(timezone.utc),
    }
    await coll.update_one(
        {"_id": chat_id, **build_app_scope_filter(app_id)},
        {"$set": update},
    )
    return {"status": "persisted", "chat_id": chat_id, "set_title": set_title}
