from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from mozaiksai.core.data.persistence.namespaces import SYSTEM_DATABASE, RuntimeCollections
from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager
from mozaiksai.core.multitenant import build_app_scope_filter

IndexSpec = tuple[Sequence[tuple[str, int]], dict[str, Any]]
APP_REGISTRY_COLLECTION = "AppRegistryRecords"
BUILD_CONTINUE_STATES = frozenset({"draft", "building", "review", "configuring", "needs_revision"})


class AppRegistryRepo:
    def __init__(self, pm: AG2PersistenceManager | None = None) -> None:
        self._pm = pm or AG2PersistenceManager()

    async def _client(self):
        await self._pm.persistence._ensure_client()  # noqa: SLF001
        client = self._pm.persistence.client
        if client is None:
            raise RuntimeError("Mongo client not initialized")
        return client

    async def _collection(self):
        client = await self._client()
        return client[SYSTEM_DATABASE][APP_REGISTRY_COLLECTION]

    async def ensure_indexes(self) -> None:
        coll = await self._collection()
        try:
            existing = await coll.list_indexes().to_list(length=None)
            names = {item.get("name") for item in existing if isinstance(item, dict)}
        except Exception:
            names = set()

        specs: Iterable[IndexSpec] = [
            ((("app_id", 1),), {"unique": True, "name": "app_registry_app_id_unique"}),
            ((("owner_user_id", 1), ("updated_at", -1)), {"name": "app_registry_owner_updated"}),
            ((("lifecycle_state", 1), ("updated_at", -1)), {"name": "app_registry_state_updated"}),
        ]
        for keys, kwargs in specs:
            name = kwargs.get("name")
            if name and name in names:
                continue
            await coll.create_index(list(keys), **kwargs)

    async def upsert_app_record(
        self,
        *,
        owner_user_id: str,
        name: str,
        description: str | None,
        lifecycle_state: str,
        app_id: str,
        active_chat_id: str | None = None,
        active_workflow_id: str | None = None,
    ) -> dict[str, Any]:
        await self.ensure_indexes()
        coll = await self._collection()
        now = datetime.now(UTC)
        existing = await coll.find_one({"app_id": app_id})
        build_registry_id = str((existing or {}).get("_id") or f"appreg_{uuid4().hex}")
        set_fields: dict[str, Any] = {
            "app_id": app_id,
            "owner_user_id": owner_user_id,
            "name": name,
            "description": description,
            "lifecycle_state": lifecycle_state,
            "updated_at": now,
            "last_status_changed_at": now,
        }
        if active_chat_id:
            set_fields["active_chat_id"] = active_chat_id
        if active_workflow_id:
            set_fields["active_workflow_id"] = active_workflow_id
        await coll.update_one(
            {"_id": build_registry_id},
            {
                "$set": set_fields,
                "$setOnInsert": {
                    "created_at": now,
                    "bundle_path": None,
                },
            },
            upsert=True,
        )
        doc = await coll.find_one({"_id": build_registry_id})
        normalized = self._normalize_doc(doc)
        if normalized is None:
            raise RuntimeError("App record could not be loaded after upsert")
        return normalized

    async def update_lifecycle_state(
        self,
        *,
        build_registry_id: str,
        lifecycle_state: str,
        bundle_path: str | None = None,
    ) -> dict[str, Any] | None:
        await self.ensure_indexes()
        coll = await self._collection()
        update_fields: dict[str, Any] = {
            "lifecycle_state": lifecycle_state,
            "updated_at": datetime.now(UTC),
            "last_status_changed_at": datetime.now(UTC),
        }
        if bundle_path is not None:
            update_fields["bundle_path"] = bundle_path
        await coll.update_one({"_id": build_registry_id}, {"$set": update_fields}, upsert=False)
        return await self.get_by_build_registry_id(build_registry_id=build_registry_id)

    async def list_apps_for_user(self, *, owner_user_id: str) -> list[dict[str, Any]]:
        await self.ensure_indexes()
        coll = await self._collection()
        docs = await coll.find({"owner_user_id": owner_user_id}).sort("updated_at", -1).to_list(length=500)
        records = [normalized for doc in docs if (normalized := self._normalize_doc(doc))]
        return [await self._with_active_chat_fallback(record, owner_user_id=owner_user_id) for record in records]

    async def get_by_app_id(self, *, app_id: str) -> dict[str, Any] | None:
        await self.ensure_indexes()
        coll = await self._collection()
        doc = await coll.find_one({"app_id": app_id})
        return self._normalize_doc(doc)

    async def get_by_build_registry_id(self, *, build_registry_id: str) -> dict[str, Any] | None:
        await self.ensure_indexes()
        coll = await self._collection()
        doc = await coll.find_one({"_id": build_registry_id})
        return self._normalize_doc(doc)

    async def _with_active_chat_fallback(
        self,
        record: dict[str, Any],
        *,
        owner_user_id: str,
    ) -> dict[str, Any]:
        if record.get("active_chat_id"):
            return record
        if str(record.get("lifecycle_state") or "") not in BUILD_CONTINUE_STATES:
            return record
        app_id = str(record.get("app_id") or "").strip()
        if not app_id:
            return record
        try:
            client = await self._client()
            coll = client[SYSTEM_DATABASE][RuntimeCollections.CHAT_SESSIONS]
            doc = await coll.find_one(
                {"user_id": owner_user_id, **build_app_scope_filter(app_id)},
                {"_id": 1, "workflow_name": 1, "last_updated_at": 1, "created_at": 1},
                sort=[("last_updated_at", -1), ("created_at", -1)],
            )
        except Exception:
            return record
        if not isinstance(doc, dict) or not doc.get("_id"):
            return record
        enriched = dict(record)
        enriched["active_chat_id"] = str(doc["_id"])
        enriched["active_workflow_id"] = str(doc.get("workflow_name") or "ValueEngine")
        return enriched

    @staticmethod
    def _normalize_doc(doc: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(doc, dict):
            return None
        normalized = dict(doc)
        normalized["build_registry_id"] = str(normalized.pop("_id"))
        for key in ("created_at", "updated_at", "last_status_changed_at"):
            value = normalized.get(key)
            if hasattr(value, "isoformat"):
                normalized[key] = value.isoformat()  # type: ignore[union-attr]
        return normalized
