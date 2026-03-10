# ==============================================================================
# FILE: mozaikscore/core/routes/push_subscriptions.py
# DESCRIPTION: Web push subscription routes — /api/push
#              VAPID key, subscribe/unsubscribe, status, remove devices.
#              Web push channel service is stubbed until Phase 3.
# ORIGIN: Migrated from mozaiks-core-public/backend/core/routes/push_subscriptions.py
# ==============================================================================
import os
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from mozaikscore.core.auth import get_current_user
from mozaikscore.core.database import get_database

logger = logging.getLogger("mozaikscore.routes.push_subscriptions")

router = APIRouter(prefix="/api/push", tags=["push-notifications"])

VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "")
PUSH_ENABLED = bool(VAPID_PUBLIC_KEY)


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------

class PushSubscription(BaseModel):
    endpoint: str
    keys: dict
    expirationTime: Optional[int] = None


class SubscribeRequest(BaseModel):
    subscription: PushSubscription


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/vapid-public-key")
async def get_vapid_public_key():
    """Get VAPID public key for push subscription."""
    return {"publicKey": VAPID_PUBLIC_KEY or None, "enabled": PUSH_ENABLED}


@router.post("/subscribe")
async def subscribe_to_push(
    request: SubscribeRequest,
    req: Request,
    current_user: dict = Depends(get_current_user),
):
    """Subscribe the current device to push notifications."""
    if not PUSH_ENABLED:
        raise HTTPException(status_code=503, detail="Push notifications are not configured on this server")

    user_id = current_user.get("user_id", str(current_user.get("_id", "")))
    user_agent = req.headers.get("user-agent", "")

    db = get_database()
    coll = db["push_subscriptions"]
    await coll.update_one(
        {"user_id": user_id, "endpoint": request.subscription.endpoint},
        {
            "$set": {
                "user_id": user_id,
                "subscription": request.subscription.model_dump(),
                "user_agent": user_agent,
            }
        },
        upsert=True,
    )

    return {"success": True, "message": "Successfully subscribed to push notifications"}


@router.post("/unsubscribe")
async def unsubscribe_from_push(
    request: SubscribeRequest,
    current_user: dict = Depends(get_current_user),
):
    """Unsubscribe the current device from push notifications."""
    user_id = current_user.get("user_id", str(current_user.get("_id", "")))

    db = get_database()
    coll = db["push_subscriptions"]
    result = await coll.delete_one({"user_id": user_id, "endpoint": request.subscription.endpoint})

    success = result.deleted_count > 0
    return {
        "success": success,
        "message": "Unsubscribed from push notifications" if success else "Subscription not found",
    }


@router.get("/status")
async def get_push_status(current_user: dict = Depends(get_current_user)):
    """Get push notification status for the current user."""
    user_id = current_user.get("user_id", str(current_user.get("_id", "")))

    db = get_database()
    coll = db["push_subscriptions"]
    count = await coll.count_documents({"user_id": user_id})

    return {
        "enabled": PUSH_ENABLED,
        "subscribed_devices": count,
        "vapid_public_key": VAPID_PUBLIC_KEY or None,
    }


@router.delete("/devices")
async def remove_all_subscriptions(current_user: dict = Depends(get_current_user)):
    """Remove all push subscriptions for the current user."""
    user_id = current_user.get("user_id", str(current_user.get("_id", "")))

    db = get_database()
    coll = db["push_subscriptions"]
    result = await coll.delete_many({"user_id": user_id})

    return {"success": True, "removed_count": result.deleted_count}
