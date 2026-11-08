from __future__ import annotations

from typing import Any

from mozaiksai.core.runtime.composition.bson_safe import json_safe_bson


class AccountDataHandler:
    """GDPR data handler for notification preference records.

    Notification preferences are user-owned PII (opt-in choices per channel).
    The module sets user_data_scope: true; this handler implements the
    required delete and export operations.
    """

    def __init__(self, persistence: Any) -> None:
        self._persistence = persistence

    def _collection(self, app_id: str, user_id: str) -> Any:
        if self._persistence.app_id != app_id:
            raise PermissionError("account persistence is bound to another app")
        principal = self._persistence.principal
        if principal is None or not user_id or principal.user_id != user_id:
            raise PermissionError("account persistence is bound to another user")
        return self._persistence.collection("notification_settings", "preferences")

    async def delete_user_data(self, *, app_id: str, user_id: str) -> dict[str, Any]:
        """Delete all notification preference records for this user."""
        coll = self._collection(app_id, user_id)
        result = await coll.delete_many({"user_id": user_id})
        deleted = int(getattr(result, "deleted_count", 0) or 0)
        return {"module": "notification_settings", "preferences_deleted": deleted}

    async def export_user_data(self, *, app_id: str, user_id: str) -> dict[str, Any]:
        """Export notification preference records for this user."""
        coll = self._collection(app_id, user_id)
        doc = await coll.find_one({"user_id": user_id})
        if doc is None:
            return {"module": "notification_settings", "preferences": None}
        return json_safe_bson({
            "module": "notification_settings",
            "preferences": {
                "email_enabled": doc.get("email_enabled", True),
                "push_enabled": doc.get("push_enabled", True),
                "sms_enabled": doc.get("sms_enabled", False),
                "updated_at": doc.get("updated_at"),
            },
        })
