"""AccountDataHandler for the files module.

GDPR deletion and export for user-uploaded file records.

Deletion policy:
  - File records where created_by == deleted user: soft-deleted (deleted flag set).
    The storage_url is an opaque provider reference; blob cleanup is handled by the
    app's storage adapter using domain.files.file_deleted events or a separate
    retention sweep. This handler marks metadata records as deleted so they are
    excluded from queries.

Note: actual blob data lives outside this module in provider storage. This handler
only operates on metadata records in the files.records collection.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from mozaiksai.core.runtime.composition.bson_safe import json_safe_bson

_PAGE_SIZE = 100


def _now() -> str:
    return datetime.now(UTC).isoformat()


class AccountDataHandler:
    """GDPR delete + export handler for the files module."""

    def __init__(self, persistence: Any) -> None:
        self._persistence = persistence

    def _collection(self, app_id: str, user_id: str) -> Any:
        if self._persistence.app_id != app_id:
            raise PermissionError("account persistence is bound to another app")
        principal = self._persistence.principal
        if principal is None or not user_id or principal.user_id != user_id:
            raise PermissionError("account persistence is bound to another user")
        return self._persistence.collection("files", "records")

    async def _records(self, collection: Any, query: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        cursor = dict(query)
        while True:
            page = await collection.find_many(cursor, limit=_PAGE_SIZE, sort=[("_id", 1)])
            for row in page:
                yield dict(row)
            if len(page) < _PAGE_SIZE:
                return
            cursor = {**query, "_id": {"$gt": page[-1]["_id"]}}

    async def delete_user_data(self, *, app_id: str, user_id: str) -> dict[str, Any]:
        """Soft-delete all file records created by the user."""
        collection = self._collection(app_id, user_id)
        query = {"created_by": user_id, "deleted": {"$ne": True}}
        update = {"$set": {"deleted": True, "deleted_at": _now(), "deleted_by": "__gdpr_deletion__"}}
        deleted = 0
        async for row in self._records(collection, query):
            result = await collection.update_one({**query, "_id": row["_id"]}, update)
            deleted += int(result.modified_count)
        return {"files_deleted": deleted}

    async def export_user_data(self, *, app_id: str, user_id: str) -> dict[str, Any]:
        """Export all non-deleted file records created by the user."""
        collection = self._collection(app_id, user_id)
        files = [
            {key: value for key, value in row.items() if key not in {"_id", "app_id"}}
            async for row in self._records(collection, {"created_by": user_id, "deleted": {"$ne": True}})
        ]
        return {
            "files": json_safe_bson(files),
        }
