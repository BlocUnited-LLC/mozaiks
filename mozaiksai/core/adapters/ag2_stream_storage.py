from __future__ import annotations

import asyncio
from collections.abc import Iterable
from datetime import UTC, datetime
from importlib import import_module
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from autogen.beta.events import UnknownEvent
from autogen.beta.events.base import BaseEvent
from autogen.beta.history import Storage
from pymongo import ReturnDocument

from logs.logging_config import get_workflow_logger
from mozaiksai.core.core_config import get_mongo_client
from mozaiksai.core.data.persistence.namespaces import SYSTEM_DATABASE, RuntimeCollections
from mozaiksai.core.multitenant import build_app_scope_filter, coalesce_app_id, dual_write_app_scope

logger = get_workflow_logger("ag2_stream_storage")


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

    async def save_event(self, event: BaseEvent, context: Any) -> None:
        stream_id = self._resolve_stream_id(context)
        sequence = await self._next_sequence(stream_id)
        coll = await self._events_coll()
        now = datetime.now(UTC)
        payload = dual_write_app_scope(
            {
                "_id": f"{stream_id}:{sequence}",
                "app_id": self._app_id,
                "stream_id": stream_id,
                "sequence": sequence,
                "event_module": type(event).__module__,
                "event_class": type(event).__name__,
                "event_payload": event.to_dict(),
                "created_at": now,
            },
            self._app_id,
        )
        await coll.insert_one(payload)

    async def get_history(self, stream_id: Any) -> Iterable[BaseEvent]:
        coll = await self._events_coll()
        cursor = coll.find(
            {
                "stream_id": self._normalize_stream_id(stream_id),
                **build_app_scope_filter(self._app_id),
            }
        ).sort("sequence", 1)
        docs = await cursor.to_list(length=None)
        return [self._restore_event(doc) for doc in docs if isinstance(doc, dict)]

    async def set_history(self, stream_id: Any, events: Iterable[BaseEvent]) -> None:
        normalized_stream_id = self._normalize_stream_id(stream_id)
        await self.drop_history(normalized_stream_id)
        event_list = [event for event in events if isinstance(event, BaseEvent)]
        if not event_list:
            return

        coll = await self._events_coll()
        now = datetime.now(UTC)
        docs = []
        for index, event in enumerate(event_list, start=1):
            docs.append(
                dual_write_app_scope(
                    {
                        "_id": f"{normalized_stream_id}:{index}",
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
            )
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
                        "next_sequence": len(docs),
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
