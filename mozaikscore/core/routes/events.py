# ==============================================================================
# FILE: mozaikscore/core/routes/events.py
# DESCRIPTION: User analytics event routes — /api/events
#              Immutable append-only events: UserSignedUp, UserActive.
#              Used by KPI snapshots and analytics dashboard.
# ORIGIN: Migrated from mozaiks-core-public/backend/core/routes/events.py
# ==============================================================================
import os
import logging
from datetime import datetime, date, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from mozaikscore.core.auth import get_current_user
from mozaikscore.core.database import get_database

logger = logging.getLogger("mozaikscore.routes.events")

router = APIRouter(prefix="/api/events", tags=["events"])

APP_ID = os.getenv("MOZAIKS_APP_ID", "dev_app")


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------

class _BaseEvent(BaseModel):
    appId: Optional[str] = None
    timestamp: Optional[datetime] = None


class UserSignedUpIn(_BaseEvent):
    userId: str = Field(min_length=1)


class UserActiveIn(_BaseEvent):
    userId: str = Field(min_length=1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_user_events():
    db = get_database()
    return db["user_events"]


async def _append_event(event_type: str, user_id: str, app_id: Optional[str], timestamp: Optional[datetime]):
    """Append an immutable analytics event."""
    coll = _get_user_events()
    ts = timestamp or datetime.now(tz=timezone.utc)
    day = ts.strftime("%Y-%m-%d") if isinstance(ts, datetime) else date.today().isoformat()
    resolved_app = app_id or APP_ID

    doc = {
        "type": event_type,
        "userId": user_id,
        "appId": resolved_app,
        "day": day,
        "timestamp": ts.isoformat() if isinstance(ts, datetime) else str(ts),
    }

    # Upsert to prevent duplicates per user/type/day
    await coll.update_one(
        {"type": event_type, "userId": user_id, "appId": resolved_app, "day": day},
        {"$setOnInsert": doc},
        upsert=True,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/user/signed-up")
async def user_signed_up(payload: UserSignedUpIn, current_user: dict = Depends(get_current_user)):
    """Record a UserSignedUp event."""
    if payload.userId != current_user.get("user_id"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid userId")
    try:
        await _append_event("UserSignedUp", payload.userId, payload.appId, payload.timestamp)
        return {"ok": True}
    except Exception as exc:
        logger.error("Failed to append UserSignedUp: %s", exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to write event")


@router.post("/user/active")
async def user_active(payload: UserActiveIn, current_user: dict = Depends(get_current_user)):
    """Record a UserActive event (daily heartbeat)."""
    if payload.userId != current_user.get("user_id"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid userId")
    try:
        await _append_event("UserActive", payload.userId, payload.appId, payload.timestamp)
        return {"ok": True}
    except Exception as exc:
        logger.error("Failed to append UserActive: %s", exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to write event")
