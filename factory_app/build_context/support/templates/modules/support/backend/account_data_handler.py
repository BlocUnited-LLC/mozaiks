from __future__ import annotations

from typing import Any

from mozaiksai.core.runtime.composition.bson_safe import json_safe_bson

_PAGE_SIZE = 100


class AccountDataHandler:
    """GDPR data handler for support module records.

    Support requests carry requester_id (the user who submitted the request)
    and subject_app_id (the app context). Both are user-linked PII.

    Note: support requests may also be linked to a messages thread
    (message_thread_id). The messages module owns thread content; deleting
    the support record here removes the metadata but the messages module's
    account_data_handler handles the conversation bodies.
    """

    def __init__(self, persistence: Any) -> None:
        self._persistence = persistence

    def _collection(self, app_id: str, user_id: str) -> Any:
        if self._persistence.app_id != app_id:
            raise PermissionError("account persistence is bound to another app")
        principal = self._persistence.principal
        if principal is None or not user_id or principal.user_id != user_id:
            raise PermissionError("account persistence is bound to another user")
        return self._persistence.collection("support", "requests")

    async def delete_user_data(self, *, app_id: str, user_id: str) -> dict[str, Any]:
        """Delete all support request records submitted by this user."""
        coll = self._collection(app_id, user_id)
        result = await coll.delete_many({"requester_id": user_id})
        deleted = int(getattr(result, "deleted_count", 0) or 0)
        return {"module": "support", "requests_deleted": deleted}

    async def export_user_data(self, *, app_id: str, user_id: str) -> dict[str, Any]:
        """Export all support request records submitted by this user."""
        coll = self._collection(app_id, user_id)
        query: dict[str, Any] = {"requester_id": user_id}
        requests = []
        while True:
            page = await coll.find_many(query, limit=_PAGE_SIZE, sort=[("_id", 1)])
            requests.extend({key: value for key, value in row.items() if key != "_id"} for row in page)
            if len(page) < _PAGE_SIZE:
                return json_safe_bson({"module": "support", "requests": requests})
            query = {"requester_id": user_id, "_id": {"$gt": page[-1]["_id"]}}
