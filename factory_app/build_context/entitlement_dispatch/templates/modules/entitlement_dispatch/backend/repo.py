from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from mozaiksai.core.runtime.persistence import app_data_from_context

# The billing.subscriptions alias is declared in data/contract.json by the
# entitlement_dispatch_001_collections migration. ConfiguredEntitlementAdapter
# reads from the same alias via assignment_store.data_alias in
# config/subscriptions.yaml. Both sides must resolve to the same collection.
_SUBSCRIPTIONS_ALIAS = "billing.subscriptions"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class EntitlementDispatchRepo:
    """Write-side persistence for subscription assignment records.

    Writes to the billing.subscriptions data contract alias. The app_id field
    is included in every document because ConfiguredEntitlementAdapter queries
    assignments with {app_id_field: app_id} (default field name: "app_id").
    """

    async def activate(
        self,
        ctx: Any,
        *,
        user_id: str,
        plan_id: str,
        activated_at: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Upsert an active subscription assignment for the user."""
        app_id = str(getattr(ctx, "app_id", None) or "").strip()
        col = app_data_from_context(ctx).collection(_SUBSCRIPTIONS_ALIAS)
        await col.update_one(
            {"app_id": app_id, "user_id": user_id},
            {
                "$set": {
                    "app_id": app_id,
                    "user_id": user_id,
                    "plan_id": plan_id,
                    "status": "active",
                    "activated_at": activated_at or _now_iso(),
                    "deactivated_at": None,
                    "metadata": metadata or {},
                }
            },
            upsert=True,
        )

    async def deactivate(
        self,
        ctx: Any,
        *,
        user_id: str,
        plan_id: str | None = None,
    ) -> bool:
        """Mark the active subscription assignment as cancelled.

        Returns True when a record was found and updated, False when no active
        record matched (e.g. already deactivated or never activated).
        """
        app_id = str(getattr(ctx, "app_id", None) or "").strip()
        col = app_data_from_context(ctx).collection(_SUBSCRIPTIONS_ALIAS)
        query: dict[str, Any] = {"app_id": app_id, "user_id": user_id, "status": "active"}
        if plan_id:
            query["plan_id"] = plan_id
        result = await col.update_one(
            query,
            {"$set": {"status": "cancelled", "deactivated_at": _now_iso()}},
        )
        return bool(result.modified_count)
