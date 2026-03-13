from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from mozaiksai.core.core_config import get_mongo_client
from mozaiksai.core.multitenant import build_app_scope_filter, coalesce_app_id

_WORKFLOW_NAME = "MainStage"
_LIMIT = 12


def _mock_lineup() -> List[Dict[str, Any]]:
    return [
        {
            "slot": "09:00 PM",
            "performer": "Startup Guy at Brunch",
            "status": "ready",
            "direction": "RoastLaneAgent",
            "headline": "Networking at brunch is just hostage negotiation with oat milk.",
        },
        {
            "slot": "09:12 PM",
            "performer": "My Friend the Wellness Cultist",
            "status": "warming_up",
            "direction": "ObservationalLaneAgent",
            "headline": "Wellness people always look two sips away from selling you pond water.",
        },
        {
            "slot": "09:24 PM",
            "performer": "Dating App Ghost Story",
            "status": "queued",
            "direction": "AbsurdistLaneAgent",
            "headline": "My dating profile needs a priest, not a photographer.",
        },
    ]


async def _load_runtime_lineup(app_id: str, limit: int = _LIMIT) -> List[Dict[str, Any]]:
    scoped_app = coalesce_app_id(app_id=app_id)
    if not scoped_app:
        return []

    client = get_mongo_client()
    coll = client["MozaiksAI"]["ChatSessions"]
    cursor = coll.find(
        {
            "workflow_name": _WORKFLOW_NAME,
            "show_packet": {"$exists": True},
            **build_app_scope_filter(scoped_app),
        },
        projection={
            "_id": 1,
            "set_title": 1,
            "show_packet": 1,
            "last_updated_at": 1,
        },
        sort=[("last_updated_at", -1)],
        limit=max(limit, 1),
    )

    rows: List[Dict[str, Any]] = []
    async for doc in cursor:
        packet = doc.get("show_packet") or {}
        if not isinstance(packet, dict):
            continue
        dt = doc.get("last_updated_at")
        slot = "Tonight"
        if isinstance(dt, datetime):
            slot = dt.astimezone(timezone.utc).strftime("%I:%M %p")
        rows.append(
            {
                "slot": slot,
                "performer": str(doc.get("set_title") or packet.get("set_title") or "Untitled Set").strip(),
                "status": "ready" if len(rows) == 0 else "queued",
                "direction": str(packet.get("final_direction") or "Observational").strip(),
                "headline": str(packet.get("opening_line") or "").strip(),
            }
        )
    return rows


async def execute(data: Dict[str, Any]) -> Dict[str, Any]:
    action = str(data.get("action") or "list_lineup").strip()

    if action == "list_lineup":
        app_id = coalesce_app_id(app_id=(data.get("app_id") or (data.get("_context") or {}).get("app_id")))
        lineup: List[Dict[str, Any]] = []
        if app_id:
            try:
                lineup = await _load_runtime_lineup(app_id=app_id)
            except Exception:
                lineup = []

        rows = lineup or _mock_lineup()
        return {
            "source": "runtime" if lineup else "mock",
            "lineup": rows,
            "count": len(rows),
            "generated_at": datetime.utcnow().isoformat(),
        }

    if action == "health":
        return {
            "status": "ok",
            "module": "lineup_board",
            "timestamp": datetime.utcnow().isoformat(),
        }

    return {
        "error": f"Unknown action: {action}",
        "available_actions": ["list_lineup", "health"],
    }
