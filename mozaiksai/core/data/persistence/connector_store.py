"""Platform-owned persistence helpers for app connector metadata.

Connector records are app-scoped admin/runtime metadata. They intentionally
store only sanitized state needed for the app admin integrations surface and
workflow coordination. Secrets belong in a vault-backed connector service, not
in MongoDB.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any

from .namespaces import SYSTEM_DATABASE, PlatformCollections
from .persistence_manager import AG2PersistenceManager

IndexSpec = tuple[Sequence[tuple[str, int]], dict[str, Any]]
SECRET_METADATA_KEYS = {"secret_value", "secret", "api_key", "apikey", "token", "password"}


def _redact_public_config(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    redacted: dict[str, Any] = {}
    for key, item in dict(value).items():
        key_text = str(key)
        if key_text.strip().lower() in SECRET_METADATA_KEYS:
            continue
        if isinstance(item, dict):
            redacted[key_text] = _redact_public_config(item)
        elif isinstance(item, list):
            redacted[key_text] = [
                _redact_public_config(child) if isinstance(child, dict) else child
                for child in item
            ]
        else:
            redacted[key_text] = item
    return redacted


class AppConnectorStore:
    """Persistence wrapper for app-scoped connector metadata."""

    def __init__(self, pm: AG2PersistenceManager | None = None) -> None:
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
        display_name: str | None = None,
        user_id: str | None = None,
        status: str,
        secret_storage: str,
        secret_available: bool,
        key_length: int | None = None,
        expires_at: str | None = None,
        notes: str | None = None,
        public_config: dict[str, Any] | None = None,
        required_fields: list[dict[str, Any]] | None = None,
        source: dict[str, Any] | None = None,
        status_reason: str | None = None,
        extra_fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        await self.ensure_indexes()
        coll = await self._collection()
        now = datetime.now(UTC)
        normalized_service = str(service or "").strip().lower().replace(" ", "_")
        if not normalized_service:
            raise ValueError("service is required")

        set_fields: dict[str, Any] = {
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
        if public_config is not None:
            set_fields["public_config"] = _redact_public_config(public_config) or {}
        if required_fields is not None:
            set_fields["required_fields"] = [dict(field) for field in required_fields if isinstance(field, dict)]
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
        return self._normalize_doc(doc)  # type: ignore[return-value]

    async def get_connector(self, *, app_id: str, service: str) -> dict[str, Any] | None:
        await self.ensure_indexes()
        coll = await self._collection()
        doc = await coll.find_one({"app_id": str(app_id), "service": str(service).strip().lower().replace(" ", "_")})
        return self._normalize_doc(doc)

    async def list_connectors(self, *, app_id: str) -> list[dict[str, Any]]:
        await self.ensure_indexes()
        coll = await self._collection()
        cursor = coll.find({"app_id": str(app_id)}).sort("updated_at", -1)
        docs = await cursor.to_list(length=None)
        return [self._normalize_doc(doc) for doc in docs if isinstance(doc, dict)]  # type: ignore[misc]

    async def patch_connector(
        self,
        *,
        app_id: str,
        service: str,
        user_id: str | None = None,
        display_name: str | None = None,
        notes: str | None = None,
        status: str | None = None,
        expires_at: str | None = None,
        public_config: dict[str, Any] | None = None,
        required_fields: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        await self.ensure_indexes()
        coll = await self._collection()
        normalized_service = str(service or "").strip().lower().replace(" ", "_")
        update_fields: dict[str, Any] = {"updated_at": datetime.now(UTC)}
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
        if public_config is not None:
            update_fields["public_config"] = _redact_public_config(public_config) or {}
        if required_fields is not None:
            update_fields["required_fields"] = [dict(field) for field in required_fields if isinstance(field, dict)]

        if len(update_fields) == 1:
            return await self.get_connector(app_id=app_id, service=normalized_service)

        await coll.update_one(
            {"app_id": str(app_id), "service": normalized_service},
            {"$set": update_fields},
            upsert=False,
        )
        return await self.get_connector(app_id=app_id, service=normalized_service)

    async def update_connector_health(
        self,
        *,
        app_id: str,
        service: str,
        health_status: str,
        health_message: str | None = None,
        last_checked_at: str | None = None,
        checked_by: str = "manual",
        health_details: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> dict[str, Any] | None:
        await self.ensure_indexes()
        coll = await self._collection()
        normalized_service = str(service or "").strip().lower().replace(" ", "_")
        update_fields: dict[str, Any] = {
            "updated_at": datetime.now(UTC),
            "health_status": str(health_status or "unknown"),
            "health_message": health_message,
            "last_checked_at": last_checked_at or datetime.now(UTC).isoformat(),
            "checked_by": str(checked_by or "manual"),
            "health_details": _redact_public_config(health_details or {}) or {},
        }
        if error_code:
            update_fields["health_error_code"] = str(error_code)

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
    def _normalize_doc(doc: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(doc, dict):
            return None
        normalized = dict(doc)
        normalized.pop("_id", None)
        for key in ("created_at", "updated_at"):
            value = normalized.get(key)
            if hasattr(value, "isoformat"):
                normalized[key] = value.isoformat()  # type: ignore[union-attr]
        return normalized


__all__ = ["AppConnectorStore"]
