from __future__ import annotations

from typing import Any


class AccountDataHandler:
    """GDPR data handler for user onboarding state records.

    Onboarding state is user-owned (completed steps, dismissal flag).
    The module sets user_data_scope: true; this handler implements the
    required delete and export operations.
    """

    def __init__(self, db: Any) -> None:
        self._db = db

    async def delete_user_data(self, *, app_id: str, user_id: str) -> dict[str, Any]:
        """Delete all onboarding state records for this user."""
        coll = self._db.get_collection("user_onboarding", "states")
        result = await coll.delete_many({"app_id": app_id, "user_id": user_id})
        deleted = int(getattr(result, "deleted_count", 0) or 0)
        return {"module": "user_onboarding", "states_deleted": deleted}

    async def export_user_data(self, *, app_id: str, user_id: str) -> dict[str, Any]:
        """Export onboarding state records for this user."""
        coll = self._db.get_collection("user_onboarding", "states")
        doc = await coll.find_one({"app_id": app_id, "user_id": user_id})
        if doc is None:
            return {"module": "user_onboarding", "state": None}
        return {
            "module": "user_onboarding",
            "state": {
                "completed_steps": doc.get("completed_steps") or [],
                "dismissed": bool(doc.get("dismissed", False)),
                "created_at": doc.get("created_at"),
                "updated_at": doc.get("updated_at"),
            },
        }
