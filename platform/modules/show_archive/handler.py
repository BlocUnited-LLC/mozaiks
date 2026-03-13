from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from mozaiksai.core.core_config import get_mongo_client
from mozaiksai.core.multitenant import build_app_scope_filter, coalesce_app_id

_WORKFLOW_NAME = "MainStage"
_LIMIT = 24


def _mock_shows() -> List[Dict[str, Any]]:
    return [
        {
            "rank": 1,
            "set_title": "Startup Guy at Brunch",
            "direction": "RoastLaneAgent",
            "opening_line": "You can always spot a startup founder because they network like the table is a hostage situation.",
            "closer": "Closer: circle back to the founder's need to pivot even his omelet.",
            "episode": "Backstage 2026-03-08 · A91C",
        },
        {
            "rank": 2,
            "set_title": "My Friend the Wellness Cultist",
            "direction": "ObservationalLaneAgent",
            "opening_line": "Every wellness person says they just want balance, then they hand you a powder that tastes like drywall and hope.",
            "closer": "Closer: admit the cult is just expensive soup with branding.",
            "episode": "Backstage 2026-03-06 · B203",
        },
        {
            "rank": 3,
            "set_title": "Dating App Ghost Story",
            "direction": "AbsurdistLaneAgent",
            "opening_line": "Dating apps are so haunted now I half expect Hinge to ask if my ex can leave a reference.",
            "closer": "Closer: let the app itself be the villain.",
            "episode": "Backstage 2026-03-04 · C417",
        },
    ]


def _episode_label(last_updated_at: Any, chat_id: str) -> str:
    if isinstance(last_updated_at, datetime):
        dt = last_updated_at.astimezone(timezone.utc)
        return f"Backstage {dt.strftime('%Y-%m-%d')} · {chat_id[-4:]}"
    raw = str(last_updated_at or "").strip()
    if raw:
        return f"Backstage {raw[:10]} · {chat_id[-4:]}"
    return f"Backstage · {chat_id[-4:]}"


async def _load_runtime_shows(app_id: str, limit: int = _LIMIT) -> List[Dict[str, Any]]:
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
            "last_updated_at": 1,
            "set_title": 1,
            "show_packet": 1,
        },
        sort=[("last_updated_at", -1)],
        limit=max(limit, 1),
    )

    rows: List[Dict[str, Any]] = []
    async for doc in cursor:
        packet = doc.get("show_packet") or {}
        if not isinstance(packet, dict):
            continue
        chat_id = str(doc.get("_id") or "").strip()
        if not chat_id:
            continue
        rows.append(
            {
                "rank": 0,
                "set_title": str(doc.get("set_title") or packet.get("set_title") or f"Set {chat_id[-4:]}").strip(),
                "direction": str(packet.get("final_direction") or "Observational").strip(),
                "opening_line": str(packet.get("opening_line") or "").strip(),
                "closer": str(packet.get("closer") or "").strip(),
                "episode": _episode_label(doc.get("last_updated_at"), chat_id),
            }
        )

    ranked: List[Dict[str, Any]] = []
    for idx, row in enumerate(rows, start=1):
        row["rank"] = idx
        ranked.append(row)
    return ranked


async def execute(data: Dict[str, Any]) -> Dict[str, Any]:
    action = str(data.get("action") or "list_shows").strip()

    if action == "list_shows":
        app_id = coalesce_app_id(app_id=(data.get("app_id") or (data.get("_context") or {}).get("app_id")))
        shows: List[Dict[str, Any]] = []
        if app_id:
            try:
                shows = await _load_runtime_shows(app_id=app_id)
            except Exception:
                shows = []

        rows = shows or _mock_shows()
        return {
            "source": "runtime" if shows else "mock",
            "shows": rows,
            "count": len(rows),
            "generated_at": datetime.utcnow().isoformat(),
        }

    if action == "health":
        return {
            "status": "ok",
            "module": "show_archive",
            "timestamp": datetime.utcnow().isoformat(),
        }

    return {
        "error": f"Unknown action: {action}",
        "available_actions": ["list_shows", "health"],
    }
