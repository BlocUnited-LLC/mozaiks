# ==============================================================================
# FILE: mozaikscore/core/routes/analytics.py
# DESCRIPTION: Admin analytics routes — /__mozaiks/admin/analytics
#              KPI dashboard, daily snapshots, date-range series.
#              Uses user_events collection directly for basic metrics.
#              Advanced KPI snapshot service is stubbed for Phase 3.
# ORIGIN: Migrated from mozaiks-core-public/backend/core/routes/analytics.py
# ==============================================================================
import os
import logging
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status

from mozaikscore.core.auth import require_admin_or_internal, require_admin_user
from mozaikscore.core.database import get_database

logger = logging.getLogger("mozaikscore.routes.analytics")

router = APIRouter(
    prefix="/__mozaiks/admin/analytics",
    tags=["admin-analytics"],
)

APP_ID = os.getenv("MOZAIKS_APP_ID", "dev_app")


def _get_user_events():
    """Access user_events collection."""
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


@router.get("/kpis", response_model=dict)
async def get_dashboard_kpis(current_user: dict = Depends(require_admin_user)):
    """Basic KPI summary from user_events collection."""
    try:
        db = get_database()
        users_coll = db["users"]
        today = date.today().isoformat()

        total_users = await users_coll.count_documents({})
        active_today = await _count_events({"appId": APP_ID, "type": "UserActive", "day": today})
        new_today = await _count_events({"appId": APP_ID, "type": "UserSignedUp", "day": today})

        # 7-day active users
        week_ago = (date.today() - timedelta(days=6)).isoformat()
        wau = await _count_distinct_users({
            "appId": APP_ID,
            "type": "UserActive",
            "day": {"$gte": week_ago, "$lte": today},
        })

        # 30-day active users
        month_ago = (date.today() - timedelta(days=29)).isoformat()
        mau = await _count_distinct_users({
            "appId": APP_ID,
            "type": "UserActive",
            "day": {"$gte": month_ago, "$lte": today},
        })

        return {
            "total_users": total_users,
            "dau": int(active_today),
            "new_users_today": int(new_today),
            "wau": int(wau),
            "mau": int(mau),
            "stickiness_dau_mau": float(active_today) / float(mau if mau else 1),
        }
    except Exception as exc:
        logger.error("Error fetching KPIs: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not fetch analytics data",
        )


@router.get("/app-kpi-snapshot", response_model=dict)
async def get_app_kpi_snapshot(
    snapshot_date: str = Query(default_factory=lambda: date.today().isoformat(), alias="date"),
    current_user: dict = Depends(require_admin_or_internal),
):
    """Basic daily snapshot from user_events."""
    try:
        day = date.fromisoformat(snapshot_date)
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid date; expected YYYY-MM-DD")

    day_key = day.isoformat()
    dau = await _count_events({"appId": APP_ID, "type": "UserActive", "day": day_key})
    new_users = await _count_events({"appId": APP_ID, "type": "UserSignedUp", "day": day_key})

    return {
        "date": day_key,
        "dau": int(dau),
        "new_users": int(new_users),
    }


@router.get("/app-kpi-snapshots", response_model=dict)
async def get_app_kpi_snapshots(
    from_date: str = Query(..., alias="from"),
    to_date: str = Query(..., alias="to"),
    current_user: dict = Depends(require_admin_or_internal),
):
    """Date-range series of daily KPI snapshots."""
    try:
        start = date.fromisoformat(from_date)
        end = date.fromisoformat(to_date)
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid from/to; expected YYYY-MM-DD")

    if end < start:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid range: to < from")

    max_days = 366
    if (end - start).days + 1 > max_days:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Range too large (max {max_days} days)")

    series = []
    d = start
    while d <= end:
        day_key = d.isoformat()
        dau = await _count_events({"appId": APP_ID, "type": "UserActive", "day": day_key})
        new_users = await _count_events({"appId": APP_ID, "type": "UserSignedUp", "day": day_key})
        series.append({"date": day_key, "dau": int(dau), "new_users": int(new_users)})
        d += timedelta(days=1)

    return {"from": start.isoformat(), "to": end.isoformat(), "series": series}
