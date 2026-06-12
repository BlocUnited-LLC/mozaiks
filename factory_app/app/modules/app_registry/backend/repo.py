from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from mozaiksai.core.data.persistence.namespaces import SYSTEM_DATABASE
from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager

IndexSpec = tuple[Sequence[tuple[str, int]], dict[str, Any]]
APP_REGISTRY_COLLECTION = "AppRegistryRecords"


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
        docs = await coll.find({"owner_user_id": owner_user_id}).sort("updated_at", -1).to_list(length=None)
        return [normalized for doc in docs if (normalized := self._normalize_doc(doc))]

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

    @staticmethod
    def _normalize_doc(doc: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(doc, dict):
            return None
        normalized = dict(doc)
        normalized["build_registry_id"] = str(normalized.pop("_id"))
        for key in ("created_at", "updated_at", "last_status_changed_at"):
            value = normalized.get(key)
            if hasattr(value, "isoformat"):
                normalized[key] = value.isoformat()
        return normalized
