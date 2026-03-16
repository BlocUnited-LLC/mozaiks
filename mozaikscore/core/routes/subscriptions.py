# ==============================================================================
# FILE: mozaikscore/core/routes/subscriptions.py
# DESCRIPTION: Subscription plan listing, upgrade, and cancellation routes.
#              Only active when MONETIZATION=1.
# ==============================================================================
import os
import logging

from fastapi import APIRouter, Depends, HTTPException

from mozaikscore.core.auth import get_current_user
from mozaikscore.core.config_loader import get_subscription_config
from mozaikscore.core.event_bus import event_bus
from mozaikscore.core.schemas import UpdateSubscriptionRequest
from mozaikscore.core.state_manager import state_manager
from mozaikscore.core.subscription_manager import subscription_manager

logger = logging.getLogger("mozaikscore.routes.subscriptions")

MONETIZATION = os.getenv("MONETIZATION", "0") == "1"

router = APIRouter(tags=["subscriptions"])


if MONETIZATION:

    @router.get("/api/subscription-plans")
    async def get_subscription_plans():
        return get_subscription_config() or {}

    @router.get("/api/user-subscription")
    async def get_user_subscription(user: dict = Depends(get_current_user)):
        cache_key = f"user_subscription:{user['user_id']}"
        cached = state_manager.get(cache_key)
        if cached is not None:
            return cached
        sub = await subscription_manager.get_user_subscription(user["user_id"])
        state_manager.set(cache_key, sub, expire_in=300)
        return sub

    @router.post("/api/update-subscription")
    async def update_subscription(body: UpdateSubscriptionRequest, user: dict = Depends(get_current_user)):
        response = await subscription_manager.change_user_subscription(user["user_id"], body.new_plan)
        state_manager.delete(f"user_subscription:{user['user_id']}")
        state_manager.delete(f"navigation:{user['user_id']}")
        for key in list(state_manager.state.keys()):
            if key.startswith(f"module_access:{user['user_id']}:"):
                state_manager.delete(key)
        event_bus.publish("subscription_updated", {"user_id": user["user_id"], "plan": body.new_plan})
        return response

    @router.post("/api/cancel-subscription")
    async def cancel_subscription(user: dict = Depends(get_current_user)):
        response = await subscription_manager.cancel_user_subscription(user["user_id"])
        state_manager.delete(f"user_subscription:{user['user_id']}")
        state_manager.delete(f"navigation:{user['user_id']}")
        for key in list(state_manager.state.keys()):
            if key.startswith(f"module_access:{user['user_id']}:"):
                state_manager.delete(key)
        event_bus.publish("subscription_canceled", {"user_id": user["user_id"]})
        return response
