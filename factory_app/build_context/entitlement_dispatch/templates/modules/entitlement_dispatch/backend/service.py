from __future__ import annotations

from typing import Any

from .repo import EntitlementDispatchRepo


class EntitlementDispatchService:
    """Business logic for the entitlement dispatch write path.

    Validates inputs and delegates persistence to EntitlementDispatchRepo.
    Does not call payment providers, check entitlements, or emit billing events.
    """

    def __init__(self) -> None:
        self._repo = EntitlementDispatchRepo()

    async def activate_subscription(
        self,
        ctx: Any,
        *,
        user_id: str,
        plan_id: str,
        activated_at: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Write an active subscription assignment record.

        ConfiguredEntitlementAdapter reads the record to grant plan capabilities.
        Call this after payment confirmation or explicit plan assignment.
        """
        if not user_id or not plan_id:
            return {"activated": False, "error": "user_id and plan_id are required"}
        await self._repo.activate(
            ctx,
            user_id=user_id,
            plan_id=plan_id,
            activated_at=activated_at,
            metadata=metadata,
        )
        return {"activated": True, "plan_id": plan_id}

    async def deactivate_subscription(
        self,
        ctx: Any,
        *,
        user_id: str,
        plan_id: str | None = None,
    ) -> dict[str, Any]:
        """Mark the active assignment as cancelled.

        ConfiguredEntitlementAdapter stops granting capabilities once the record
        status is no longer in active_statuses.
        """
        if not user_id:
            return {"deactivated": False, "error": "user_id is required"}
        deactivated = await self._repo.deactivate(ctx, user_id=user_id, plan_id=plan_id)
        return {"deactivated": deactivated}
