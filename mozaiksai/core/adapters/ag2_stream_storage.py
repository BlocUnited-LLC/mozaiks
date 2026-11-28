from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from importlib import import_module
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from ag2.events import ModelResponse, UnknownEvent
from ag2.events.base import BaseEvent
from ag2.history import Storage
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from logs.logging_config import get_workflow_logger
from mozaiksai.core.core_config import get_mongo_client
from mozaiksai.core.data.persistence.namespaces import SYSTEM_DATABASE, RuntimeCollections
from mozaiksai.core.multitenant import build_app_scope_filter, coalesce_app_id, dual_write_app_scope

logger = get_workflow_logger("ag2_stream_storage")
_MESSAGE_KEY_HASH = "wal_message_key_sha256"


def _message_semantic_digest(event: BaseEvent) -> str:
    if not isinstance(event, ModelResponse) or not event.content or event.tool_calls.calls or event.files:
        raise ValueError("Idempotent run storage requires a plain assistant presentation message")
    # Generated event/message timestamps and UUIDs are not message identity.
    metadata = {key: value for key, value in event.metadata.items() if key != _MESSAGE_KEY_HASH}
    return hashlib.sha256(json.dumps({
        "content": event.content, "metadata": metadata, "model": event.model,
        "provider": event.provider, "finish_reason": event.finish_reason,
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()


def _stored_message_key(event: BaseEvent) -> str | None:
    if not isinstance(event, ModelResponse):
        return None
    value = event.metadata.get(_MESSAGE_KEY_HASH)
    if value is None:
        return None
    if not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{64}", value) is None:
        raise ValueError("Stored assistant message key must be a SHA-256 digest")
    return value


def _message_event_id(app_id: str, stream_id: str, key_hash: str) -> str:
    scope_hash = hashlib.sha256(json.dumps(
        [app_id, stream_id, key_hash], separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")).hexdigest()
    return f"{stream_id}:message:{scope_hash}"


def stream_id_for_run(app_id: str, chat_id: str) -> UUID:
    """Return a deterministic AG2 stream id for one workflow run."""
    resolved_app_id = str(coalesce_app_id(app_id=app_id) or "").strip()
    if not resolved_app_id:
        raise ValueError("app_id is required")
    resolved_chat_id = str(chat_id or "").strip()
    if not resolved_chat_id:
        raise ValueError("chat_id is required")
    return uuid5(NAMESPACE_URL, f"mozaiks.ag2.run:{resolved_app_id}:{resolved_chat_id}")


class MongoAG2StreamStorage(Storage):
    """Mongo-backed AG2 stream storage keyed by deterministic run stream ids.

    This makes AG2 stream history the canonical execution-state record for one
    workflow run while keeping app scope enforcement inside the runtime.
    """

    def __init__(
        self,
        *,
        app_id: str,
        events_collection: Any | None = None,
        heads_collection: Any | None = None,
    ) -> None:
        resolved_app_id = str(coalesce_app_id(app_id=app_id) or "").strip()
        if not resolved_app_id:
            raise ValueError("app_id is required")
        self._app_id = resolved_app_id
        self._events_collection = events_collection
        self._heads_collection = heads_collection
        self._client: Any | None = None
        self._client_lock = asyncio.Lock()
        self._indexes_ready = False

    async def save_event(
        self, event: BaseEvent, context: Any, *, idempotency_key: str | None = None,
    ) -> None:
        stream_id = self._resolve_stream_id(context)
        event_id = None
        key_hash = None
        semantic_digest = None
        if idempotency_key is not None:
            if (not isinstance(idempotency_key, str) or not idempotency_key.strip()
                    or len(idempotency_key.encode("utf-8")) > 2048):
                raise ValueError("Assistant message idempotency key must be nonempty and at most 2048 bytes")
            semantic_digest = _message_semantic_digest(event)
            key_hash = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
            if _stored_message_key(event) not in (None, key_hash):
                raise ValueError("Stored assistant message key differs from the requested identity")
            event_id = _message_event_id(self._app_id, stream_id, key_hash)
            coll = await self._events_coll()
            existing = await coll.find_one({"_id": event_id, "stream_id": stream_id, **build_app_scope_filter(self._app_id)})
            if existing is not None:
                if existing.get("message_semantic_sha256") != semantic_digest:
                    raise ValueError("Assistant message idempotency key has different content")
                return
        sequence = await self._next_sequence(stream_id)
        coll = await self._events_coll()
        now = datetime.now(UTC)
        event_payload = event.to_dict()
        if key_hash is not None:
            assert isinstance(event, ModelResponse)  # Checked before allocating a keyed sequence.
            # Keep the hashed identity through AG2 get_history / set_history.
            # No raw caller key, generated UUID or timestamp becomes authority.
            event_payload["message"]["metadata"] = {**event.metadata, _MESSAGE_KEY_HASH: key_hash}
        payload = dual_write_app_scope(
            {
                "_id": event_id or f"{stream_id}:{sequence}",
                "app_id": self._app_id,
                "stream_id": stream_id,
                "sequence": sequence,
                "event_module": type(event).__module__,
                "event_class": type(event).__name__,
                "event_payload": event_payload,
                "created_at": now,
            },
            self._app_id,
        )
        if semantic_digest is not None:
            payload["message_semantic_sha256"] = semantic_digest
        try:
            await coll.insert_one(payload)
        except DuplicateKeyError:
            if event_id is None:
                raise
            existing = await coll.find_one({"_id": event_id, "stream_id": stream_id, **build_app_scope_filter(self._app_id)})
            if existing is None or existing.get("message_semantic_sha256") != semantic_digest:
                raise ValueError("Assistant message idempotency key has different or unverifiable content") from None

    async def get_history(self, stream_id: Any) -> Iterable[BaseEvent]:
        coll = await self._events_coll()
        cursor = coll.find(
            {
                "stream_id": self._normalize_stream_id(stream_id),
                **build_app_scope_filter(self._app_id),
            }
        ).sort("sequence", 1).limit(10_000)
        docs = await cursor.to_list(length=10_000)
        return [self._restore_event(doc) for doc in docs if isinstance(doc, dict)]

    async def set_history(self, stream_id: Any, events: Iterable[BaseEvent]) -> None:
        normalized_stream_id = self._normalize_stream_id(stream_id)
        event_list = [event for event in events if isinstance(event, BaseEvent)]
        if not event_list:
            await self.drop_history(normalized_stream_id)
            return

        coll = await self._events_coll()
        now = datetime.now(UTC)
        docs = []
        message_digests: dict[str, str] = {}
        for index, event in enumerate(event_list, start=1):
            key_hash = _stored_message_key(event)
            event_id = _message_event_id(self._app_id, normalized_stream_id, key_hash) if key_hash else f"{normalized_stream_id}:{index}"
            semantic_digest = _message_semantic_digest(event) if key_hash else None
            if semantic_digest is not None:
                existing = await coll.find_one({"_id": event_id, "stream_id": normalized_stream_id, **build_app_scope_filter(self._app_id)})
                if existing is not None and existing.get("message_semantic_sha256") != semantic_digest:
                    raise ValueError("Assistant message idempotency key has different content")
                if event_id in message_digests:
                    if message_digests[event_id] != semantic_digest:
                        raise ValueError("Assistant message idempotency key has different content")
                    continue
                message_digests[event_id] = semantic_digest
            payload = dual_write_app_scope(
                    {
                        "_id": event_id,
                        "app_id": self._app_id,
                        "stream_id": normalized_stream_id,
                        "sequence": index,
                        "event_module": type(event).__module__,
                        "event_class": type(event).__name__,
                        "event_payload": event.to_dict(),
                        "created_at": now,
                    },
                    self._app_id,
                )
            if semantic_digest is not None:
                payload["message_semantic_sha256"] = semantic_digest
            docs.append(payload)
        await self.drop_history(normalized_stream_id)
        await coll.insert_many(docs)

        heads = await self._heads_coll()
        await heads.update_one(
            {
                "stream_id": normalized_stream_id,
                **build_app_scope_filter(self._app_id),
            },
            {
                "$set": dual_write_app_scope(
                    {
                        "app_id": self._app_id,
                        "stream_id": normalized_stream_id,
                        "next_sequence": len(event_list),
                        "last_updated_at": now,
                    },
                    self._app_id,
                ),
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )

    async def drop_history(self, stream_id: Any) -> None:
        normalized_stream_id = self._normalize_stream_id(stream_id)
        coll = await self._events_coll()
        await coll.delete_many(
            {
                "stream_id": normalized_stream_id,
                **build_app_scope_filter(self._app_id),
            }
        )
        heads = await self._heads_coll()
        await heads.delete_one(
            {
                "stream_id": normalized_stream_id,
                **build_app_scope_filter(self._app_id),
            }
        )

    async def _events_coll(self) -> Any:
        await self._ensure_indexes()
        if self._events_collection is not None:
            return self._events_collection
        client = await self._client_obj()
        return client[SYSTEM_DATABASE][RuntimeCollections.AG2_STREAM_EVENTS]

    async def _heads_coll(self) -> Any:
        await self._ensure_indexes()
        if self._heads_collection is not None:
            return self._heads_collection
        client = await self._client_obj()
        return client[SYSTEM_DATABASE][RuntimeCollections.AG2_STREAM_HEADS]

    async def _client_obj(self) -> Any:
        if self._client is not None:
            return self._client
        async with self._client_lock:
            if self._client is None:
                self._client = get_mongo_client()
        return self._client

    async def _ensure_indexes(self) -> None:
        if self._indexes_ready:
            return
        events = self._events_collection
        heads = self._heads_collection
        if events is None or heads is None:
            client = await self._client_obj()
            events = client[SYSTEM_DATABASE][RuntimeCollections.AG2_STREAM_EVENTS]
            heads = client[SYSTEM_DATABASE][RuntimeCollections.AG2_STREAM_HEADS]

        create_events_index = getattr(events, "create_index", None)
        create_heads_index = getattr(heads, "create_index", None)
        if callable(create_events_index):
            await create_events_index(
                [("app_id", 1), ("stream_id", 1), ("sequence", 1)],
                name="ag2_stream_app_stream_seq",
                unique=True,
            )
            # TTL index: expire old stream events to prevent unbounded growth.
            # Default 30 days; override with AG2_STREAM_EVENT_TTL_DAYS (set to 0
            # to disable). The TTL applies only to the events collection —
            # heads records are small and expire when their stream is dropped.
            ttl_days = int(os.getenv("AG2_STREAM_EVENT_TTL_DAYS", "30"))
            if ttl_days > 0:
                await create_events_index(
                    [("created_at", 1)],
                    name="ag2_stream_events_ttl",
                    expireAfterSeconds=ttl_days * 86400,
                )
        if callable(create_heads_index):
            await create_heads_index(
                [("app_id", 1), ("stream_id", 1)],
                name="ag2_stream_heads_app_stream",
                unique=True,
            )
        self._indexes_ready = True

    async def _next_sequence(self, stream_id: str) -> int:
        heads = await self._heads_coll()
        now = datetime.now(UTC)
        set_fields = {
            "app_id": self._app_id,
            "stream_id": stream_id,
            "last_updated_at": now,
        }
        # Mongo rejects update specs that touch the same field in multiple
        # operators. Keep the counter exclusively under $inc.
        set_fields.pop("next_sequence", None)
        head = await heads.find_one_and_update(
            {
                "stream_id": stream_id,
                **build_app_scope_filter(self._app_id),
            },
            {
                "$inc": {"next_sequence": 1},
                "$set": set_fields,
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return int((head or {}).get("next_sequence", 1) or 1)

    def _resolve_stream_id(self, context: Any) -> str:
        stream = getattr(context, "stream", None)
        stream_id = getattr(stream, "id", None)
        normalized = self._normalize_stream_id(stream_id)
        if not normalized:
            raise ValueError("context.stream.id is required")
        return normalized

    @staticmethod
    def _normalize_stream_id(stream_id: Any) -> str:
        if isinstance(stream_id, UUID):
            return str(stream_id)
        return str(stream_id or "").strip()

    @staticmethod
    def _restore_event(doc: dict[str, Any]) -> BaseEvent:
        event_class_name = str(doc.get("event_class") or "").strip() or "UnknownEvent"
        event_module_name = str(doc.get("event_module") or "").strip()
        event_payload = doc.get("event_payload") if isinstance(doc.get("event_payload"), dict) else {}
        if not event_module_name:
            return UnknownEvent(type_name=event_class_name, data=event_payload)  # type: ignore[arg-type]
        try:
            module = import_module(event_module_name)
            event_cls = getattr(module, event_class_name)
            if isinstance(event_cls, type) and issubclass(event_cls, BaseEvent):
                return event_cls.from_dict(event_payload)  # type: ignore[arg-type]
        except Exception as exc:  # pragma: no cover
            logger.debug("Failed to restore AG2 event %s from %s: %s", event_class_name, event_module_name, exc)
        return UnknownEvent(type_name=event_class_name, data=event_payload)  # type: ignore[arg-type]


__all__ = ["MongoAG2StreamStorage", "stream_id_for_run"]
