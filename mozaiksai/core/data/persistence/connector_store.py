"""Platform-owned persistence helpers for app connector metadata.

Connector records are app-scoped admin/runtime metadata. They intentionally
store only sanitized state needed for the Studio/Admin adapters surface and
workflow coordination. Secrets belong in a vault-backed connector service, not
in MongoDB.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .namespaces import SYSTEM_DATABASE, PlatformCollections
from .persistence_manager import AG2PersistenceManager


IndexSpec = Tuple[Sequence[Tuple[str, int]], Dict[str, Any]]


class AppConnectorStore:
    """Persistence wrapper for app-scoped connector metadata."""

    def __init__(self, pm: Optional[AG2PersistenceManager] = None) -> None:
        self._pm = pm or AG2PersistenceManager()

    async def _client(self):
        await self._pm.persistence._ensure_client()  # noqa: SLF001 - existing runtime pattern
        client = self._pm.persistence.client
        if client is None:
            raise RuntimeError("Mongo client not initialized")
        return client

    async def _collection(self):
        client = await self._client()
        return client[SYSTEM_DATABASE][PlatformCollections.APP_CONNECTORS]

    async def ensure_indexes(self) -> None:
        coll = await self._collection()
        try:
            existing = await coll.list_indexes().to_list(length=None)
            names = {item.get("name") for item in existing if isinstance(item, dict)}
        except Exception:
            names = set()

        specs: Iterable[IndexSpec] = [
            ((("app_id", 1), ("service", 1)), {"unique": True, "name": "app_connector_unique"}),
            ((("app_id", 1), ("updated_at", -1)), {"name": "app_connector_app_updated"}),
            ((("app_id", 1), ("status", 1)), {"name": "app_connector_app_status"}),
        ]

        for keys, kwargs in specs:
            name = kwargs.get("name")
            if name and name in names:
                continue
            await coll.create_index(list(keys), **kwargs)

    async def upsert_connector(
        self,
        *,
        app_id: str,
        service: str,
        display_name: Optional[str] = None,
        user_id: Optional[str] = None,
        status: str,
        secret_storage: str,
        secret_available: bool,
        key_length: Optional[int] = None,
        expires_at: Optional[str] = None,
        notes: Optional[str] = None,
        source: Optional[Dict[str, Any]] = None,
        status_reason: Optional[str] = None,
        extra_fields: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        await self.ensure_indexes()
        coll = await self._collection()
        now = datetime.now(UTC)
        normalized_service = str(service or "").strip().lower().replace(" ", "_")
        if not normalized_service:
            raise ValueError("service is required")

        set_fields: Dict[str, Any] = {
            "app_id": str(app_id),
            "service": normalized_service,
            "display_name": display_name or normalized_service.replace("_", " ").title(),
            "kind": "api_key",
            "status": status,
            "secret_storage": secret_storage,
            "secret_available": bool(secret_available),
            "key_length": int(key_length or 0),
            "updated_at": now,
        }
        if user_id:
            set_fields["updated_by_user_id"] = str(user_id)
        if expires_at is not None:
            set_fields["expires_at"] = expires_at
        if notes is not None:
            set_fields["notes"] = notes
        if source:
            set_fields["source"] = dict(source)
            set_fields["last_submitted_at"] = source.get("submitted_at") or now.isoformat()
        if status_reason is not None:
            set_fields["status_reason"] = status_reason
        if extra_fields:
            set_fields.update(extra_fields)

        await coll.update_one(
            {"app_id": str(app_id), "service": normalized_service},
            {
                "$set": set_fields,
                "$setOnInsert": {
                    "created_at": now,
                    "created_by_user_id": str(user_id) if user_id else None,
                },
            },
            upsert=True,
        )

        doc = await coll.find_one({"app_id": str(app_id), "service": normalized_service})
        return self._normalize_doc(doc)

    async def get_connector(self, *, app_id: str, service: str) -> Optional[Dict[str, Any]]:
        await self.ensure_indexes()
        coll = await self._collection()
        doc = await coll.find_one({"app_id": str(app_id), "service": str(service).strip().lower().replace(" ", "_")})
        return self._normalize_doc(doc)

    async def list_connectors(self, *, app_id: str) -> List[Dict[str, Any]]:
        await self.ensure_indexes()
        coll = await self._collection()
        cursor = coll.find({"app_id": str(app_id)}).sort("updated_at", -1)
        docs = await cursor.to_list(length=None)
        return [self._normalize_doc(doc) for doc in docs if isinstance(doc, dict)]

    async def patch_connector(
        self,
        *,
        app_id: str,
        service: str,
        user_id: Optional[str] = None,
        display_name: Optional[str] = None,
        notes: Optional[str] = None,
        status: Optional[str] = None,
        expires_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        await self.ensure_indexes()
        coll = await self._collection()
        normalized_service = str(service or "").strip().lower().replace(" ", "_")
        update_fields: Dict[str, Any] = {"updated_at": datetime.now(UTC)}
        if user_id:
            update_fields["updated_by_user_id"] = str(user_id)
        if display_name is not None:
            update_fields["display_name"] = display_name
        if notes is not None:
            update_fields["notes"] = notes
        if status is not None:
            update_fields["status"] = status
        if expires_at is not None:
            update_fields["expires_at"] = expires_at

        if len(update_fields) == 1:
            return await self.get_connector(app_id=app_id, service=normalized_service)

        await coll.update_one(
            {"app_id": str(app_id), "service": normalized_service},
            {"$set": update_fields},
            upsert=False,
        )
        return await self.get_connector(app_id=app_id, service=normalized_service)

    async def delete_connector(self, *, app_id: str, service: str) -> bool:
        await self.ensure_indexes()
        coll = await self._collection()
        normalized_service = str(service or "").strip().lower().replace(" ", "_")
        result = await coll.delete_one({"app_id": str(app_id), "service": normalized_service})
        return bool(getattr(result, "deleted_count", 0))

    @staticmethod
    def _normalize_doc(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not isinstance(doc, dict):
            return None
        normalized = dict(doc)
        normalized.pop("_id", None)
        for key in ("created_at", "updated_at"):
            value = normalized.get(key)
            if hasattr(value, "isoformat"):
                normalized[key] = value.isoformat()
        return normalized


__all__ = ["AppConnectorStore"]
