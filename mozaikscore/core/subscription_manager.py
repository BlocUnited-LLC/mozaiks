# ==============================================================================
# FILE: mozaikscore/core/subscription_manager.py
# DESCRIPTION: Subscription-based access control for modules.
#              READ for app users; WRITE from Control Plane only.
#              Supports plan checking, trials, billing history, CP sync.
# ORIGIN: Migrated from mozaiks-core-public/backend/core/subscription_manager.py
# ==============================================================================
import os
import logging
from datetime import datetime, timezone, timedelta

from dateutil.relativedelta import relativedelta
from fastapi import HTTPException

from mozaikscore.core.database import (
    get_subscriptions_collection,
    get_subscription_history_collection,
    get_billing_history_collection,
)
from mozaikscore.core.config_loader import get_subscription_config

logger = logging.getLogger("mozaikscore.subscription_manager")

# Environment
SUBSCRIPTION_API_URL = os.getenv("SUBSCRIPTION_API_URL", "")
ALLOW_LOCAL_SUBSCRIPTION_WRITES = os.getenv("ALLOW_LOCAL_SUBSCRIPTION_WRITES", "false").lower() in ("1", "true", "yes")
MONETIZATION_ENABLED = os.getenv("MONETIZATION", "0") == "1"

DEFAULT_SUBSCRIPTION_CONFIG: dict = {"subscription_plans": []}


def _require_internal_call(operation: str, _internal_call: bool) -> None:
    if _internal_call:
        return
    if ALLOW_LOCAL_SUBSCRIPTION_WRITES:
        logger.warning("%s: Allowing local write (ALLOW_LOCAL_SUBSCRIPTION_WRITES=true)", operation)
        return
    raise HTTPException(
        status_code=403,
        detail=f"Subscription {operation} not allowed. Changes must come from Control Plane.",
    )


class SubscriptionManager:
    def __init__(self):
        self.subscription_service_url = SUBSCRIPTION_API_URL
        self.subscription_config = self._load_subscription_config()

    def _load_subscription_config(self) -> dict:
        config = get_subscription_config()
        if not config:
            logger.warning("Subscription config not found, using default")
            return DEFAULT_SUBSCRIPTION_CONFIG
        return config

    # ------------------------------------------------------------------
    # READ — available to all authenticated users
    # ------------------------------------------------------------------
    def get_available_plans(self) -> list[dict]:
        return self.subscription_config.get("subscription_plans", [])

    async def get_user_subscription(self, user_id: str) -> dict:
        coll = get_subscriptions_collection()
        if coll is None:
            raise HTTPException(status_code=500, detail="Database error")

        subscription = await coll.find_one({"user_id": user_id})
        if not subscription:
            return {"user_id": user_id, "plan": "free", "status": "inactive"}

        trial_info = None
        if subscription.get("status") == "trialing":
            settings = self.subscription_config.get("settings", {})
            trial_days = settings.get("trial_period_days", 14)
            start_str = subscription.get("created_at") or subscription.get("updated_at")
            try:
                start_date = datetime.fromisoformat(start_str) if start_str else datetime.now(timezone.utc) - timedelta(days=1)
            except Exception:
                start_date = datetime.now(timezone.utc) - timedelta(days=1)

            end_date = start_date + timedelta(days=trial_days)
            days_remaining = max(0, (end_date - datetime.now(timezone.utc)).days)
            trial_info = {"days_remaining": days_remaining, "end_date": end_date.isoformat()}

            if not subscription.get("trial_end_date"):
                await coll.update_one({"user_id": user_id}, {"$set": {"trial_end_date": end_date.isoformat()}})

        if subscription.get("status") == "trialing" and not trial_info:
            trial_info = {
                "days_remaining": 14,
                "end_date": (datetime.now(timezone.utc) + timedelta(days=14)).isoformat(),
            }
            logger.warning("Defensive fallback: synthesized trial_info for user %s", user_id)

        return {
            "user_id": user_id,
            "plan": subscription["plan"],
            "billing_cycle": subscription.get("billing_cycle", "monthly"),
            "status": subscription["status"],
            "is_trial": subscription.get("status") == "trialing",
            "trial_info": trial_info,
            "next_billing_date": subscription.get("next_billing_date"),
            "updated_at": subscription["updated_at"],
        }

    async def is_module_accessible(self, user_id: str, module_name: str) -> bool:
        """Check subscription gating for a module."""
        await self.check_trial_status(user_id)
        subscription = await self.get_user_subscription(user_id)
        user_plan = subscription["plan"]
        unlocked: list = []
        for plan in self.subscription_config.get("subscription_plans", []):
            if plan["name"].lower() == user_plan.lower():
                unlocked = plan.get("modules_unlocked", plan.get("plugins_unlocked", []))
        return "*" in unlocked or module_name in unlocked

    # Legacy alias
    async def is_plugin_accessible(self, user_id: str, plugin_name: str) -> bool:
        return await self.is_module_accessible(user_id, plugin_name)

    # ------------------------------------------------------------------
    # WRITE — Control Plane only
    # ------------------------------------------------------------------
    async def change_user_subscription(self, user_id: str, new_plan: str, *, _internal_call: bool = False) -> dict:
        _require_internal_call("change", _internal_call)
        valid = [p["name"].lower() for p in self.get_available_plans()]
        if new_plan.lower() not in valid:
            raise HTTPException(status_code=400, detail="Invalid subscription plan")

        now = datetime.now(timezone.utc)
        coll = get_subscriptions_collection()
        previous = await self.get_user_subscription(user_id)
        result = await coll.update_one(
            {"user_id": user_id},
            {"$set": {"plan": new_plan, "status": "active", "updated_at": now.isoformat(), "next_billing_date": self.calculate_next_billing_date(now).isoformat()}},
            upsert=True,
        )
        if result.modified_count > 0 or result.upserted_id:
            logger.info("Subscription updated for user %s: %s", user_id, new_plan)
            history = get_subscription_history_collection()
            await history.insert_one({"user_id": user_id, "previous_plan": previous.get("plan", "free"), "new_plan": new_plan, "timestamp": now.isoformat()})
            return {"message": "Subscription updated successfully", "new_plan": new_plan}
        raise HTTPException(status_code=500, detail="Failed to update subscription")

    async def cancel_user_subscription(self, user_id: str, *, _internal_call: bool = False) -> dict:
        _require_internal_call("cancel", _internal_call)
        now = datetime.now(timezone.utc)
        subscription = await self.get_user_subscription(user_id)
        previous_plan = subscription["plan"]
        coll = get_subscriptions_collection()
        result = await coll.update_one(
            {"user_id": user_id},
            {"$set": {"plan": "free", "status": "inactive", "updated_at": now.isoformat(), "next_billing_date": None}},
        )
        if result.modified_count > 0:
            logger.info("Subscription canceled for user %s", user_id)
            history = get_subscription_history_collection()
            await history.insert_one({"user_id": user_id, "previous_plan": previous_plan, "new_plan": "free", "timestamp": now.isoformat()})
            return {"message": "Subscription canceled successfully"}
        raise HTTPException(status_code=500, detail="Failed to cancel subscription")

    async def start_user_trial(self, user_id: str, *, _internal_call: bool = False) -> dict:
        _require_internal_call("start_trial", _internal_call)
        settings = self.subscription_config.get("settings", {})
        trial_plan = settings.get("trial_plan")
        if not trial_plan:
            highest_price = 0
            for plan in self.get_available_plans():
                if plan.get("name") != "admin" and plan.get("price", 0) > highest_price:
                    highest_price = plan["price"]
                    trial_plan = plan["name"]
            if not trial_plan:
                trial_plan = "premium"
        trial_days = settings.get("trial_period_days", 14)
        now = datetime.now(timezone.utc)
        trial_end = now + relativedelta(days=trial_days)
        coll = get_subscriptions_collection()
        await coll.update_one(
            {"user_id": user_id},
            {"$set": {"user_id": user_id, "plan": trial_plan, "status": "trialing", "is_trial": True, "trial_start_date": now.isoformat(), "trial_end_date": trial_end.isoformat(), "updated_at": now.isoformat()}},
            upsert=True,
        )
        logger.info("Trial started for user %s: Plan=%s, Days=%d", user_id, trial_plan, trial_days)
        return {"plan": trial_plan, "trial_end_date": trial_end.isoformat(), "trial_days": trial_days}

    async def check_trial_status(self, user_id: str):
        coll = get_subscriptions_collection()
        subscription = await coll.find_one({"user_id": user_id})
        if not subscription or subscription.get("status") != "trialing" or not subscription.get("is_trial"):
            return False
        trial_end = datetime.fromisoformat(subscription["trial_end_date"])
        now = datetime.now(timezone.utc)
        if now > trial_end:
            await coll.update_one({"user_id": user_id}, {"$set": {"plan": "free", "status": "inactive", "is_trial": False, "updated_at": now.isoformat()}})
            return {"expired": True, "downgraded": True}
        return {"expired": False, "days_remaining": (trial_end - now).days}

    async def log_billing_event(self, user_id: str, amount: float, event_type: str, status: str, metadata: dict | None = None, *, _internal_call: bool = False):
        _require_internal_call("log_billing", _internal_call)
        now = datetime.now(timezone.utc)
        billing = get_billing_history_collection()
        event = {"user_id": user_id, "amount": amount, "event_type": event_type, "status": status, "timestamp": now.isoformat(), "metadata": metadata or {}}
        await billing.insert_one(event)
        logger.info("Billing event logged: %s", event)

    async def sync_subscription_from_control_plane(self, user_id: str, subscription_data: dict, *, _internal_call: bool = False) -> dict:
        _require_internal_call("sync_from_control_plane", _internal_call)
        now = datetime.now(timezone.utc)
        update_doc = {
            "user_id": user_id,
            "plan": subscription_data.get("plan", "free"),
            "status": subscription_data.get("status", "inactive"),
            "billing_cycle": subscription_data.get("billing_cycle", "monthly"),
            "updated_at": now.isoformat(),
            "synced_from_control_plane": True,
            "last_sync_at": now.isoformat(),
        }
        app_id = subscription_data.get("app_id") or subscription_data.get("appId")
        if app_id:
            update_doc["app_id"] = app_id
        if subscription_data.get("next_billing_date"):
            update_doc["next_billing_date"] = subscription_data["next_billing_date"]
        if subscription_data.get("trial_end_date"):
            update_doc["trial_end_date"] = subscription_data["trial_end_date"]
            update_doc["is_trial"] = subscription_data.get("status") == "trialing"
        if subscription_data.get("stripe_subscription_id"):
            update_doc["external_subscription_id"] = subscription_data["stripe_subscription_id"]
        coll = get_subscriptions_collection()
        await coll.update_one({"user_id": user_id}, {"$set": update_doc}, upsert=True)
        logger.info("Subscription synced from Control Plane for user %s: plan=%s, status=%s", user_id, update_doc["plan"], update_doc["status"])
        return {"success": True, "user_id": user_id, "plan": update_doc["plan"], "status": update_doc["status"]}

    def calculate_next_billing_date(self, current_date):
        return current_date + relativedelta(months=1)


# ------------------------------------------------------------------
# Singleton — choose full or stub based on MONETIZATION env var
# ------------------------------------------------------------------
def _create_subscription_manager():
    if MONETIZATION_ENABLED:
        return SubscriptionManager()
    else:
        from mozaikscore.core.subscription_stub import SubscriptionStub
        logger.info("MONETIZATION=0 — using SubscriptionStub (all access granted)")
        return SubscriptionStub()


subscription_manager = _create_subscription_manager()
