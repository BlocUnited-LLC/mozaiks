"""AccountDataHandler for the user_onboarding module."""
from __future__ import annotations

from typing import Any

from mozaiksai.core.runtime.persistence.naming import collection_name_for

_MODULE_ID = "user_onboarding"
_ENTITY = "status"


def _collection_name(app_id: str) -> str:
    return collection_name_for(app_id=app_id, module_id=_MODULE_ID, entity_name=_ENTITY)


class AccountDataHandler:
    """Delete and export per-user onboarding tour state."""

    def __init__(self, db: Any) -> None:
        self._db = db

    async def delete_user_data(self, *, app_id: str, user_id: str) -> dict[str, Any]:
        result = await self._db[_collection_name(app_id)].delete_many(
            {"app_id": app_id, "user_id": user_id}
        )
        return {"deleted_count": int(getattr(result, "deleted_count", 0))}

    async def export_user_data(self, *, app_id: str, user_id: str) -> dict[str, Any]:
        records = await self._db[_collection_name(app_id)].find(
            {"app_id": app_id, "user_id": user_id},
            {"_id": 0},
        ).to_list(length=None)
        return {"user_onboarding_status": records}
