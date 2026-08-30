from __future__ import annotations

from typing import Any


class AccountDataHandler:
    """GDPR data handler for support module records.

    Support requests carry requester_id (the user who submitted the request)
    and subject_app_id (the app context). Both are user-linked PII.

    Note: support requests may also be linked to a messages thread
    (message_thread_id). The messages module owns thread content; deleting
    the support record here removes the metadata but the messages module's
    account_data_handler handles the conversation bodies.
    """

    def __init__(self, db: Any) -> None:
        self._db = db

    async def delete_user_data(self, *, app_id: str, user_id: str) -> dict[str, Any]:
        """Delete all support request records submitted by this user."""
        coll = self._db.get_collection("support", "requests")
        result = await coll.delete_many({"app_id": app_id, "requester_id": user_id})
        deleted = int(getattr(result, "deleted_count", 0) or 0)
        return {"module": "support", "requests_deleted": deleted}

    async def export_user_data(self, *, app_id: str, user_id: str) -> dict[str, Any]:
        """Export all support request records submitted by this user."""
        coll = self._db.get_collection("support", "requests")
        requests = await coll.find_many({"app_id": app_id, "requester_id": user_id})
        return {
            "module": "support",
            "requests": [dict(r) for r in (requests or [])],
        }
