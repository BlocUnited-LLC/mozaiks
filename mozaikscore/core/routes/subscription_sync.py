# ==============================================================================
# FILE: mozaikscore/core/routes/subscription_sync.py
# DESCRIPTION: Internal subscription sync route — /api/internal/subscription/sync
#              Control Plane pushes subscription state here.
# ORIGIN: Migrated from mozaiks-core-public/backend/core/routes/subscription_sync.py
# ==============================================================================
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from mozaikscore.core.subscription_manager import subscription_manager
from mozaikscore.core.auth import require_internal_api_key

router = APIRouter(prefix="/api/internal/subscription", tags=["internal-subscription"])


class SubscriptionSyncRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    user_id: str = Field(..., alias="userId")
    plan: str
    status: str
    billing_cycle: str | None = Field(default=None, alias="billingCycle")
    next_billing_date: str | None = Field(default=None, alias="nextBillingDate")
    trial_end_date: str | None = Field(default=None, alias="trialEndDate")
    stripe_subscription_id: str | None = Field(default=None, alias="stripeSubscriptionId")
    app_id: str | None = Field(default=None, alias="appId")


@router.post("/sync")
async def sync_subscription(
    payload: SubscriptionSyncRequest,
    _internal: None = Depends(require_internal_api_key),
):
    """Receive subscription state push from the Control Plane."""
    subscription_data = payload.model_dump(exclude={"user_id"}, exclude_none=True, by_alias=False)
    return await subscription_manager.sync_subscription_from_control_plane(
        payload.user_id,
        subscription_data,
    )
