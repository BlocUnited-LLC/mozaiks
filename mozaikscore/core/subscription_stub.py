# ==============================================================================
# FILE: mozaikscore/core/subscription_stub.py
# DESCRIPTION: No-op subscription manager (MONETIZATION=0).
#              Always grants access — same interface as SubscriptionManager.
# ORIGIN: Migrated from mozaiks-core-public/backend/core/subscription_stub.py
# ==============================================================================
import logging

logger = logging.getLogger("mozaikscore.subscription_stub")


class SubscriptionStub:
    """Simplified subscription manager that always grants access when MONETIZATION=0."""

    async def is_module_accessible(self, user_id: str, module_name: str) -> bool:
        logger.debug("SubscriptionStub: Granting access to %s for user %s", module_name, user_id)
        return True

    # Legacy alias
    async def is_plugin_accessible(self, user_id: str, plugin_name: str) -> bool:
        return await self.is_module_accessible(user_id, plugin_name)

    async def get_user_subscription(self, user_id: str) -> dict:
        return {
            "user_id": user_id,
            "plan": "unlimited",
            "status": "active",
            "billing_cycle": None,
            "next_billing_date": None,
            "updated_at": None,
            "is_trial": False,
            "trial_info": None,
        }

    async def change_user_subscription(self, user_id: str, new_plan: str, *, _internal_call: bool = False) -> dict:
        logger.info("SubscriptionStub: Ignoring subscription change for user %s", user_id)
        return {"message": "Subscription updated successfully", "new_plan": new_plan}

    async def cancel_user_subscription(self, user_id: str, *, _internal_call: bool = False) -> dict:
        logger.info("SubscriptionStub: Ignoring subscription cancel for user %s", user_id)
        return {"message": "Subscription canceled successfully"}

    async def log_billing_event(
        self, user_id: str, amount: float, event_type: str, status: str, metadata: dict | None = None, *, _internal_call: bool = False
    ) -> bool:
        logger.info("SubscriptionStub: Ignoring billing event for user %s", user_id)
        return True

    def get_available_plans(self) -> list[dict]:
        return [
            {
                "name": "unlimited",
                "display_name": "Unlimited",
                "price": 0,
                "billing_cycle": "none",
                "features": ["Full access to all modules and features"],
                "modules_unlocked": ["*"],
            }
        ]

    async def start_user_trial(self, user_id: str, *, _internal_call: bool = False) -> dict:
        logger.info("SubscriptionStub: Ignoring trial start for user %s", user_id)
        return {"plan": "unlimited", "trial_end_date": None, "trial_days": 0}

    async def check_trial_status(self, user_id: str) -> dict:
        return {"expired": False, "days_remaining": 0}

    def calculate_next_billing_date(self, current_date):
        return None

    async def sync_subscription_from_control_plane(self, user_id: str, subscription_data: dict, *, _internal_call: bool = False) -> dict:
        logger.info("SubscriptionStub: Ignoring control plane sync for user %s", user_id)
        return {"success": True, "user_id": user_id, "plan": "unlimited", "status": "active"}
