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

from datetime import UTC, datetime
from typing import Any

_RECORDS = "files_records"


def _now() -> str:
    return datetime.now(UTC).isoformat()


class AccountDataHandler:
    """GDPR delete + export handler for the files module."""

    def __init__(self, db: Any) -> None:
        self._db = db

    async def delete_user_data(self, *, app_id: str, user_id: str) -> dict[str, Any]:
        """Soft-delete all file records created by the user."""
        result = await self._db[_RECORDS].update_many(
            {"app_id": app_id, "created_by": user_id, "deleted": {"$ne": True}},
            {
                "$set": {
                    "deleted": True,
                    "deleted_at": _now(),
                    "deleted_by": "__gdpr_deletion__",
                }
            },
        )
        return {
            "files_deleted": getattr(result, "modified_count", 0),
        }

    async def export_user_data(self, *, app_id: str, user_id: str) -> dict[str, Any]:
        """Export all non-deleted file records created by the user."""
        files = await self._db[_RECORDS].find(
            {"app_id": app_id, "created_by": user_id, "deleted": {"$ne": True}},
            {"_id": 0, "app_id": 0},
        ).to_list(length=None)
        return {
            "files": files,
        }
