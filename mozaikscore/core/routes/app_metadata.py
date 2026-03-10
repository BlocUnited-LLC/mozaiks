# ==============================================================================
# FILE: mozaikscore/core/routes/app_metadata.py
# DESCRIPTION: App metadata and metrics routes — /__mozaiks/admin/app
#              Minimal identity for discovery + dashboard-friendly metrics.
# ORIGIN: Migrated from mozaiks-core-public/backend/core/routes/app_metadata.py
# ==============================================================================
import os
import logging
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status

from mozaikscore.core.auth import require_admin_or_internal
from mozaikscore.core.database import get_database

logger = logging.getLogger("mozaikscore.routes.app_metadata")

router = APIRouter(prefix="/__mozaiks/admin/app", tags=["admin-app"])

APP_ID = os.getenv("MOZAIKS_APP_ID", "dev_app")


def _get_user_events():
    db = get_database()
    return db["user_events"]


async def _count_events(query: dict) -> int:
    coll = _get_user_events()
    return int(await coll.count_documents(query))


async def _count_distinct_users(match: dict) -> int:
    coll = _get_user_events()
    cursor = coll.aggregate([
        {"$match": match},
        {"$group": {"_id": "$userId"}},
        {"$count": "count"},
    ])
    docs = await cursor.to_list(length=1)
    return int(docs[0]["count"]) if docs else 0


@router.get("/metadata", response_model=dict)
async def get_app_metadata():
    """Minimal app identity for discovery. No auth required."""
    return {"appId": APP_ID}


@router.get("/metrics", response_model=dict)
async def get_app_metrics(
    from_date: str = Query(..., alias="from"),
    to_date: str = Query(..., alias="to"),
    granularity: str = Query("day"),
    include_retention: bool = Query(True),
    current_user: dict = Depends(require_admin_or_internal),
):
    """Dashboard-friendly app metrics with daily series."""
    try:
        start_day = date.fromisoformat(from_date)
        end_day = date.fromisoformat(to_date)
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid from/to; expected YYYY-MM-DD")

    if end_day < start_day:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid range: to < from")
    if granularity != "day":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported granularity; only 'day'")

    max_days = 366
    days = (end_day - start_day).days + 1
    if days > max_days:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Range too large (max {max_days} days)")

    base_total_users = await _count_events(
        {"appId": APP_ID, "type": "UserSignedUp", "day": {"$lt": start_day.isoformat()}}
    )

    series = []
    cumulative_new = 0
    last_day_dau = 0
    d = start_day
    while d <= end_day:
        day_key = d.isoformat()
        new_users_day = await _count_events({"appId": APP_ID, "type": "UserSignedUp", "day": day_key})
        cumulative_new += int(new_users_day)
        dau_day = await _count_events({"appId": APP_ID, "type": "UserActive", "day": day_key})
        last_day_dau = int(dau_day)
        series.append({
            "date": day_key,
            "metrics": {
                "dau": int(dau_day),
                "new_users": int(new_users_day),
                "total_users": int(base_total_users + cumulative_new),
            },
        })
        d += timedelta(days=1)

    total_users_end = int(base_total_users + cumulative_new)

    active_range = await _count_distinct_users({
        "appId": APP_ID,
        "type": "UserActive",
        "day": {"$gte": start_day.isoformat(), "$lte": end_day.isoformat()},
    })

    wau_start = end_day - timedelta(days=6)
    mau_start = end_day - timedelta(days=29)
    wau = await _count_distinct_users({
        "appId": APP_ID, "type": "UserActive",
        "day": {"$gte": wau_start.isoformat(), "$lte": end_day.isoformat()},
    })
    mau = await _count_distinct_users({
        "appId": APP_ID, "type": "UserActive",
        "day": {"$gte": mau_start.isoformat(), "$lte": end_day.isoformat()},
    })
    stickiness = float(last_day_dau) / float(mau if mau else 1)

    summary = {
        "total_users": total_users_end,
        "new_users": int(cumulative_new),
        "active_users": int(active_range),
        "dau": last_day_dau,
        "wau": int(wau),
        "mau": int(mau),
        "stickiness_dau_mau": stickiness,
    }

    if include_retention:
        summary.update({"retention_7d": None, "retention_30d": None, "churn_30d": None})

    return {
        "appId": APP_ID,
        "from": start_day.isoformat(),
        "to": end_day.isoformat(),
        "granularity": "day",
        "summary": summary,
        "series": series,
    }
