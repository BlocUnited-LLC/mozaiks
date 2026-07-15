from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from mozaiksai.core.data.persistence.namespaces import SYSTEM_DATABASE
from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager

if TYPE_CHECKING:
    from mozaiksai.core.runtime.composition.module_context import ModuleContext

_MODULE_ID = "workspace_integrations"
_ENTITY = "integration_notes"
_DECLARATIONS_COLLECTION = "AppIntegrationDeclarations"


def _collection(ctx: ModuleContext, entity: str):
    persistence = getattr(ctx, "persistence", None)
    if persistence is None:
        raise RuntimeError("Persistence is not available for this app context.")
    return persistence.collection(_MODULE_ID, entity)


def _document(value: Any) -> dict[str, Any] | None:
    return dict(value) if isinstance(value, Mapping) else None


class WorkspaceIntegrationsRepo:
    async def get_note(self, ctx: ModuleContext, integration_id: str) -> dict[str, Any] | None:
        collection = _collection(ctx, _ENTITY)
        return _document(await collection.find_one({"integration_id": integration_id}))

    async def get_all_notes(self, ctx: ModuleContext) -> list[dict[str, Any]]:
        collection = _collection(ctx, _ENTITY)
        rows = await collection.find_many({}, limit=100, sort=[("integration_id", 1)])
        return [dict(row) for row in rows]

    async def upsert_note(
        self,
        ctx: ModuleContext,
        *,
        integration_id: str,
        note: str,
        updated_by: str,
    ) -> dict[str, Any]:
        collection = _collection(ctx, _ENTITY)
        document = {
            "integration_id": integration_id,
            "note": note,
            "updated_by": updated_by,
        }
        await collection.update_one(
            {"integration_id": integration_id},
            {"$set": document},
            upsert=True,
        )
        return document


class IntegrationDeclarationsRepo:
    """Persists per-app integration declarations emitted by the factory after builds.

    Uses AG2PersistenceManager directly (like AppRegistryRepo) so factory tools
    can write declarations without going through the module HTTP API or needing
    a ModuleContext.
    """

    def __init__(self, pm: AG2PersistenceManager | None = None) -> None:
        self._pm = pm or AG2PersistenceManager()

    async def _collection(self):
        await self._pm.persistence._ensure_client()  # noqa: SLF001
        client = self._pm.persistence.client
        if client is None:
            raise RuntimeError("Mongo client not initialized")
        return client[SYSTEM_DATABASE][_DECLARATIONS_COLLECTION]

    async def upsert_declarations(
        self,
        *,
        app_id: str,
        declarations: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Upsert declaration records keyed by (app_id, service). Returns saved docs."""
        if not declarations:
            return []
        coll = await self._collection()
        now = datetime.now(UTC).isoformat()
        saved = []
        for decl in declarations:
            service = str(decl.get("service") or "").strip()
            if not service or not app_id:
                continue
            doc = {**decl, "app_id": app_id, "updated_at": now}
            if not doc.get("declared_at"):
                doc["declared_at"] = now
            existing = await coll.find_one({"app_id": app_id, "service": service}, {"removed": 1})
            if existing and existing.get("removed") and bool(doc.get("defaulted")):
                continue
            doc["removed"] = False
            update: dict[str, Any] = {
                "$set": doc,
                "$unset": {"removed_at": "", "removed_by": ""},
            }
            await coll.update_one(
                {"app_id": app_id, "service": service},
                update,
                upsert=True,
            )
            saved.append(doc)
        return saved

    async def soft_delete_declaration(
        self,
        *,
        app_id: str,
        service: str,
        removed_by: str | None = None,
    ) -> bool:
        """Mark a declaration removed so user removals survive default re-adds."""
        normalized_service = str(service or "").strip()
        if not app_id or not normalized_service:
            return False
        coll = await self._collection()
        now = datetime.now(UTC).isoformat()
        result = await coll.update_one(
            {"app_id": app_id, "service": normalized_service},
            {
                "$set": {
                    "app_id": app_id,
                    "service": normalized_service,
                    "removed": True,
                    "removed_at": now,
                    "removed_by": removed_by or "system",
                    "updated_at": now,
                }
            },
            upsert=True,
        )
        return bool(getattr(result, "matched_count", 0) or getattr(result, "upserted_id", None))

    async def get_for_app(self, *, app_id: str) -> list[dict[str, Any]]:
        """Return all declarations for an app, sorted by service name."""
        coll = await self._collection()
        docs = await coll.find({"app_id": app_id}).sort("service", 1).to_list(length=200)
        return [self._strip_mongo_id(d) for d in docs if isinstance(d, dict)]

    async def get_catalog_usage_counts(self, *, catalog_ids: list[str] | None = None) -> dict[str, int]:
        """Return {catalog_id: distinct_app_count} for the given catalog IDs (or all)."""
        coll = await self._collection()
        match_filter: dict[str, Any] = {"catalog_id": {"$ne": None}, "removed": {"$ne": True}}
        if catalog_ids:
            match_filter["catalog_id"] = {"$in": catalog_ids}
        pipeline = [
            {"$match": match_filter},
            {"$group": {"_id": "$catalog_id", "app_count": {"$addToSet": "$app_id"}}},
            {"$project": {"catalog_id": "$_id", "count": {"$size": "$app_count"}}},
        ]
        results = await coll.aggregate(pipeline).to_list(length=500)
        return {r["catalog_id"]: r["count"] for r in results if r.get("catalog_id")}

    @staticmethod
    def _strip_mongo_id(doc: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in doc.items() if k != "_id"}
