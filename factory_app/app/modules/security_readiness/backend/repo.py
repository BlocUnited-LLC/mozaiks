from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mozaiksai.core.runtime.composition.module_context import ModuleContext

_MODULE_ID = "security_readiness"
_ENTITY = "findings"


def _collection(ctx: ModuleContext):
    persistence = getattr(ctx, "persistence", None)
    if persistence is None:
        raise RuntimeError("Persistence is not available for this app context.")
    return persistence.collection(_MODULE_ID, _ENTITY)


def _document(value: Any) -> dict[str, Any] | None:
    return dict(value) if isinstance(value, Mapping) else None


class SecurityReadinessRepo:
    async def insert_findings(self, ctx: ModuleContext, findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not findings:
            return []
        collection = _collection(ctx)
        for finding in findings:
            await collection.update_one(
                {"finding_id": finding["finding_id"], "owner_user_id": finding["owner_user_id"]},
                {"$set": finding},
                upsert=True,
            )
        return findings

    async def list_findings(
        self,
        ctx: ModuleContext,
        *,
        query: dict[str, Any],
        limit: int,
    ) -> list[dict[str, Any]]:
        collection = _collection(ctx)
        rows = await collection.find_many(query, limit=limit, sort=[("updated_at", -1)])
        return [public for row in rows if (public := _document(row)) is not None]

    async def update_status(
        self,
        ctx: ModuleContext,
        *,
        query: dict[str, Any],
        status: str,
        note: str | None,
        updated_by: str,
        updated_at: str,
    ) -> dict[str, Any] | None:
        collection = _collection(ctx)
        update = {
            "status": status,
            "status_note": note,
            "status_updated_by": updated_by,
            "status_updated_at": updated_at,
            "updated_at": updated_at,
        }
        await collection.update_one(query, {"$set": update}, upsert=False)
        return _document(await collection.find_one(query))
