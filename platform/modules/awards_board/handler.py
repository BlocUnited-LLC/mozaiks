# ==============================================================================
# FILE: modules/awards_board/handler.py
# DESCRIPTION: Awards Board module backend actions.
#              Derives leaderboard entries from persisted RoastChat runtime
#              outcomes (mfj_roast_results) with mock fallback.
# ==============================================================================

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from mozaiksai.core.core_config import get_mongo_client
from mozaiksai.core.multitenant import build_app_scope_filter, coalesce_app_id

_ROAST_WORKFLOW_NAME = "RoastChat"
_LEADERBOARD_LIMIT = 25


def _mock_winners() -> List[Dict[str, Any]]:
    return [
        {
            "rank": 1,
            "name": "Nia 'Risk Engine' Carter",
            "award_title": "Most Brutally Honest Pivot",
            "score": 96,
            "episode": "Roast Night #12",
            "trait_focus": "Obsessive iteration speed",
        },
        {
            "rank": 2,
            "name": "Marco Velasquez",
            "award_title": "Moonshot With Receipts",
            "score": 92,
            "episode": "Roast Night #11",
            "trait_focus": "Big vision plus testable milestones",
        },
        {
            "rank": 3,
            "name": "Priya Kline",
            "award_title": "Customer Pain Whisperer",
            "score": 89,
            "episode": "Roast Night #10",
            "trait_focus": "High empathy and sharp objection handling",
        },
        {
            "rank": 4,
            "name": "Darnell Brooks",
            "award_title": "Comeback Architect",
            "score": 87,
            "episode": "Roast Night #09",
            "trait_focus": "Turned judge objections into roadmap wins",
        },
        {
            "rank": 5,
            "name": "Elena Park",
            "award_title": "Most Chaotic, Still Works",
            "score": 84,
            "episode": "Roast Night #08",
            "trait_focus": "Wild concept with surprisingly clean execution",
        },
    ]


def _pick_first_model(row: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(row, dict):
        return {}
    nested = row.get("mfj_child_outputs")
    if isinstance(nested, dict):
        for value in nested.values():
            if isinstance(value, dict):
                return value
    return row


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _normalize_verdict(value: Any) -> str:
    verdict = str(value or "").strip().lower()
    if verdict in {"accept", "reject", "conditional"}:
        return verdict
    return "conditional"


def _extract_cards(mfj_results: Dict[str, Any]) -> List[Dict[str, Any]]:
    cards: List[Dict[str, Any]] = []
    if not isinstance(mfj_results, dict):
        return cards

    for task_key, raw in mfj_results.items():
        if str(task_key).startswith("_"):
            continue
        if not isinstance(raw, dict):
            continue

        payload = _pick_first_model(raw)
        if not isinstance(payload, dict):
            continue

        score = max(0, _safe_int(payload.get("score"), 0))
        cards.append(
            {
                "task_key": str(task_key),
                "judge_name": str(payload.get("judge_name") or task_key),
                "trait_focus": str(payload.get("trait_focus") or "Unspecified"),
                "verdict": _normalize_verdict(payload.get("verdict")),
                "score": score,
                "profile_line": str(
                    payload.get("profile_line")
                    or raw.get("profile_line")
                    or ""
                ).strip(),
            }
        )
    return cards


def _overall_verdict(cards: List[Dict[str, Any]]) -> str:
    counts = {"accept": 0, "conditional": 0, "reject": 0}
    for card in cards:
        verdict = _normalize_verdict(card.get("verdict"))
        counts[verdict] = counts.get(verdict, 0) + 1
    # Deterministic tie-break preference: accept > conditional > reject
    return sorted(
        counts.items(),
        key=lambda item: (-item[1], {"accept": 3, "conditional": 2, "reject": 1}.get(item[0], 0)),
    )[0][0]


def _award_title(score: int, verdict: str) -> str:
    if score >= 95:
        return "Hall of Flame Champion"
    if score >= 90:
        return "Jury Grand Slam"
    if score >= 85:
        return "High-Impact Roast Medal"
    if score >= 75 and verdict != "reject":
        return "Promising Chaos Ribbon"
    if verdict == "reject":
        return "Comeback Loading Trophy"
    return "Wildcard Spotlight"


def _display_name(profile_line: str, chat_id: str) -> str:
    candidate = str(profile_line or "").strip()
    if candidate:
        # Use first segment from multi-line/long profile text.
        for sep in ("\n", ".", "!", "?"):
            if sep in candidate:
                candidate = candidate.split(sep, 1)[0].strip()
        if len(candidate) > 42:
            candidate = f"{candidate[:39].rstrip()}..."
        return candidate or f"Roaster {chat_id[-4:]}"
    return f"Roaster {chat_id[-4:]}"


def _episode_label(last_updated_at: Any, chat_id: str) -> str:
    if isinstance(last_updated_at, datetime):
        dt = last_updated_at.astimezone(timezone.utc)
        return f"Roast Session {dt.strftime('%Y-%m-%d')} · {chat_id[-4:]}"
    raw = str(last_updated_at or "").strip()
    if raw:
        return f"Roast Session {raw[:10]} · {chat_id[-4:]}"
    return f"Roast Session · {chat_id[-4:]}"


async def _load_runtime_winners(app_id: str, limit: int = _LEADERBOARD_LIMIT) -> List[Dict[str, Any]]:
    scoped_app = coalesce_app_id(app_id=app_id)
    if not scoped_app:
        return []

    client = get_mongo_client()
    coll = client["MozaiksAI"]["ChatSessions"]
    query = {
        "workflow_name": _ROAST_WORKFLOW_NAME,
        "status": 1,  # WorkflowStatus.COMPLETED
        "mfj_roast_results": {"$exists": True},
        **build_app_scope_filter(scoped_app),
    }
    projection = {
        "_id": 1,
        "last_updated_at": 1,
        "profile_line": 1,
        "mfj_roast_results": 1,
    }
    cursor = coll.find(
        query,
        projection=projection,
        sort=[("last_updated_at", -1)],
        limit=max(limit * 3, 60),
    )

    rows: List[Dict[str, Any]] = []
    async for doc in cursor:
        if not isinstance(doc, dict):
            continue
        chat_id = str(doc.get("_id") or "").strip()
        if not chat_id:
            continue

        cards = _extract_cards(doc.get("mfj_roast_results") or {})
        if not cards:
            continue

        avg_score = int(round(sum(card["score"] for card in cards) / len(cards)))
        top_card = sorted(cards, key=lambda c: c.get("score", 0), reverse=True)[0]
        verdict = _overall_verdict(cards)
        profile_line = str(doc.get("profile_line") or top_card.get("profile_line") or "").strip()

        rows.append(
            {
                "rank": 0,  # assigned after sort
                "name": _display_name(profile_line, chat_id),
                "award_title": _award_title(avg_score, verdict),
                "score": avg_score,
                "episode": _episode_label(doc.get("last_updated_at"), chat_id),
                "trait_focus": str(top_card.get("trait_focus") or "Unspecified"),
                "verdict": verdict,
                "judge_count": len(cards),
                "_sort_updated": str(doc.get("last_updated_at") or ""),
            }
        )

    rows.sort(
        key=lambda row: (
            int(row.get("score") or 0),
            str(row.get("_sort_updated") or ""),
        ),
        reverse=True,
    )

    ranked: List[Dict[str, Any]] = []
    for idx, row in enumerate(rows[: max(1, int(limit))], start=1):
        cleaned = {k: v for k, v in row.items() if not str(k).startswith("_")}
        cleaned["rank"] = idx
        ranked.append(cleaned)

    return ranked


async def execute(data: Dict[str, Any]) -> Dict[str, Any]:
    action = str(data.get("action") or "list_winners").strip()

    if action == "list_winners":
        app_id = coalesce_app_id(
            app_id=(data.get("app_id") or (data.get("_context") or {}).get("app_id"))
        )

        runtime_winners: List[Dict[str, Any]] = []
        if app_id:
            try:
                runtime_winners = await _load_runtime_winners(app_id=app_id)
            except Exception:
                runtime_winners = []

        winners = runtime_winners or _mock_winners()
        source = "runtime" if runtime_winners else "mock"
        return {
            "source": source,
            "winners": winners,
            "count": len(winners),
            "generated_at": datetime.utcnow().isoformat(),
        }

    if action == "health":
        return {
            "status": "ok",
            "module": "awards_board",
            "timestamp": datetime.utcnow().isoformat(),
        }

    return {
        "error": f"Unknown action: {action}",
        "available_actions": ["list_winners", "health"],
    }
