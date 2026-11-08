"""Tenant-scoped Mongo implementation of AG2's KnowledgeStore protocol.

AG2 owns Network persistence semantics and Hub hydration. Small path heads
publish immutable binary chunk chains, keeping virtual files independent of
MongoDB's document size limit without changing AG2's byte-offset contract.
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from ag2.knowledge import NoopChangeSubscription
from pymongo.errors import DuplicateKeyError

from mozaiksai.core.core_config import get_mongo_client
from mozaiksai.core.data.persistence.namespaces import SYSTEM_DATABASE, RuntimeCollections
from mozaiksai.core.multitenant import build_app_scope_filter, coalesce_app_id, dual_write_app_scope
from mozaiksai.core.runtime.persistence.distributed_lock import (
    ChatLockMode,
    assert_chat_mutable,
    chat_execution_lease,
)

_CHUNK_BYTES = 1024 * 1024
_STORAGE_FORMAT = "chunks-v1"


def _normalize_path(path: str) -> str:
    value = "/" + str(path or "").strip().lstrip("/")
    return value.rstrip("/") or "/"


class MongoAG2KnowledgeStore:
    """Persist one AG2 Network namespace for one app workflow chat.

    Replacements and deletions retain published chunks: a reader may still own
    an earlier head snapshot. Only chunks from a definitely unpublished CAS
    attempt are reclaimed here. Namespace retirement must remove heads and
    chunks together after all chat readers/writers have stopped.
    """

    def __init__(
        self,
        *,
        app_id: str,
        chat_id: str,
        collection: Any | None = None,
        chunks_collection: Any | None = None,
    ) -> None:
        resolved_app_id = str(coalesce_app_id(app_id=app_id) or "").strip()
        resolved_chat_id = str(chat_id or "").strip()
        if not resolved_app_id:
            raise ValueError("app_id is required")
        if not resolved_chat_id:
            raise ValueError("chat_id is required")
        self._app_id = resolved_app_id
        self._chat_id = resolved_chat_id
        self._collection = collection
        self._chunks_collection = chunks_collection
        self._client: Any | None = None
        self._lock = asyncio.Lock()
        # Preserve successive metadata write call order within a Hub. The head
        # CAS also protects independent adapter instances sharing this namespace.
        self._mutation_lock = asyncio.Lock()
        self._indexes_ready = False
        self._chunk_indexes_ready = False

    async def read(self, path: str) -> str | None:
        normalized = _normalize_path(path)
        doc = await (await self._coll()).find_one(self._filter(normalized))
        if doc is None:
            return None
        return (await self._read_bytes(normalized, doc, 0, None)).decode("utf-8", errors="strict")

    async def write(self, path: str, content: str) -> None:
        async with self._mutation_lock:
            await self._publish(_normalize_path(path), str(content).encode("utf-8"), append=False)

    async def list(self, path: str = "/") -> list[str]:
        normalized = _normalize_path(path)
        prefix = normalized.rstrip("/") + "/"
        query = self._scope_filter()
        query["path"] = {"$regex": f"^{re.escape(prefix)}"}
        cursor = (await self._coll()).find(query, {"path": 1})
        docs = await cursor.to_list(length=100_000)
        children: set[str] = set()
        for doc in docs:
            remainder = str(doc.get("path") or "")[len(prefix) :]
            if not remainder:
                continue
            children.add(remainder.split("/", 1)[0] + ("/" if "/" in remainder else ""))
        return sorted(children)

    async def delete(self, path: str) -> None:
        async with self._mutation_lock:
            self._assert_mutable()
            normalized = _normalize_path(path)
            query = self._scope_filter()
            query["$or"] = [
                {"path": normalized},
                {"path": {"$regex": f"^{re.escape(normalized.rstrip('/') + '/')}"}},
            ]
            collection = await self._coll()
            self._assert_mutable()
            await collection.delete_many(query)

    async def exists(self, path: str) -> bool:
        normalized = _normalize_path(path)
        query = self._scope_filter()
        query["$or"] = [
            {"path": normalized},
            {"path": {"$regex": f"^{re.escape(normalized.rstrip('/') + '/')}"}},
        ]
        return await (await self._coll()).find_one(query, {"_id": 1}) is not None

    async def purge_namespace(self) -> None:
        """Retire this namespace after its owner has drained all chat readers.

        This maintenance operation is not AG2's file deletion primitive. The
        canonical execution lease excludes host writers, but does not track
        read-only snapshots: callers must stop those before invoking it.
        """
        async with chat_execution_lease(app_id=self._app_id, chat_id=self._chat_id) as lease:
            if lease.mode is not ChatLockMode.REQUIRED:
                raise ValueError("AG2 knowledge purge requires a distributed chat lease")
            async with self._mutation_lock:
                heads, chunks = await self._coll(), await self._chunks()
                self._assert_mutable()
                await heads.delete_many(self._scope_filter())
                self._assert_mutable()
                await chunks.delete_many(self._scope_filter())

    async def append(self, path: str, content: str) -> int:
        """Atomically append UTF-8 content and return its prior byte offset."""
        async with self._mutation_lock:
            return await self._publish(_normalize_path(path), str(content).encode("utf-8"), append=True)

    async def read_range(self, path: str, start: int, end: int | None = None) -> str:
        normalized = _normalize_path(path)
        doc = await (await self._coll()).find_one(self._filter(normalized))
        if doc is None:
            return ""
        return (await self._read_bytes(normalized, doc, start, end)).decode("utf-8", errors="strict")

    async def on_change(self, path: str, callback: Any) -> NoopChangeSubscription:
        _ = path, callback
        return NoopChangeSubscription()

    def _assert_mutable(self) -> None:
        assert_chat_mutable(app_id=self._app_id, chat_id=self._chat_id)

    @staticmethod
    def _inline_bytes(doc: dict[str, Any]) -> bytes | None:
        if "storage_format" in doc:
            if doc["storage_format"] != _STORAGE_FORMAT:
                raise ValueError("Unsupported AG2 knowledge storage format")
            return None
        content = doc.get("content", "")
        if not isinstance(content, str):
            raise ValueError("Invalid inline AG2 knowledge content")
        return content.encode("utf-8")

    @staticmethod
    def _head_state(doc: dict[str, Any]) -> tuple[int, str | None]:
        length, tail, revision = doc.get("length"), doc.get("tail"), doc.get("revision")
        if (
            type(length) is not int or length < 0
            or not isinstance(revision, str) or not revision
            or (length == 0 and tail is not None)
            or (length > 0 and (not isinstance(tail, str) or not tail))
        ):
            raise ValueError("Invalid AG2 knowledge head")
        return length, tail

    async def _read_bytes(
        self, path: str, head: dict[str, Any], start: int, end: int | None,
    ) -> bytes:
        inline = self._inline_bytes(head)
        length, chunk_id = self._head_state(head) if inline is None else (len(inline), None)
        stop = length if end is None else min(end, length)
        if start >= stop:
            return b""
        if inline is not None:
            return inline[start:stop]
        start, stop, _ = slice(start, stop).indices(length)
        if start >= stop:
            return b""
        collection = await self._chunks()
        expected_end = length
        pieces: list[bytes] = []
        while expected_end > start:
            chunk = await collection.find_one({"_id": chunk_id, **self._filter(path)})
            if chunk is None:
                raise ValueError("Missing committed AG2 knowledge chunk")
            lower, upper, data, previous = (
                chunk.get("start"), chunk.get("end"), chunk.get("data"), chunk.get("previous"),
            )
            if (
                type(lower) is not int or type(upper) is not int
                or lower < 0 or upper != expected_end or lower >= upper
                or not isinstance(data, bytes) or len(data) != upper - lower
                or len(data) > _CHUNK_BYTES
                or (lower == 0 and previous is not None)
                or (lower > 0 and (not isinstance(previous, str) or not previous))
            ):
                raise ValueError("Invalid committed AG2 knowledge chunk")
            if lower < stop:
                pieces.append(data[max(start - lower, 0):min(stop - lower, len(data))])
            expected_end, chunk_id = lower, previous
        return b"".join(reversed(pieces))

    async def _stage(
        self, path: str, operation_id: str, data: bytes, offset: int, previous: str | None,
    ) -> tuple[int, str | None]:
        if not data:
            return offset, previous
        collection = await self._chunks()
        for index in range(0, len(data), _CHUNK_BYTES):
            self._assert_mutable()
            payload = data[index:index + _CHUNK_BYTES]
            chunk_id = uuid4().hex
            await collection.insert_one(dual_write_app_scope({
                "_id": chunk_id,
                "app_id": self._app_id,
                "chat_id": self._chat_id,
                "path": path,
                "operation_id": operation_id,
                "start": offset,
                "end": offset + len(payload),
                "previous": previous,
                "data": payload,
            }, self._app_id))
            offset += len(payload)
            previous = chunk_id
        return offset, previous

    async def _discard_unpublished(self, path: str, operation_id: str) -> None:
        collection = await self._chunks()
        self._assert_mutable()
        await collection.delete_many({
            "operation_id": operation_id, **self._filter(path),
        })

    async def _publish(self, path: str, data: bytes, *, append: bool) -> int:
        self._assert_mutable()
        collection = await self._coll()
        while True:
            self._assert_mutable()
            prior = await collection.find_one(self._filter(path))
            inline = self._inline_bytes(prior) if prior is not None else b""
            prior_length, prior_tail = (
                self._head_state(prior) if prior is not None and inline is None else (len(inline or b""), None)
            )
            operation_id = uuid4().hex
            length, tail = (prior_length, prior_tail) if append else (0, None)
            # Old inline paths remain readable and are converted only by this
            # successful publication. No startup rewrite or history truncation.
            if append and inline:
                length, tail = await self._stage(path, operation_id, inline, 0, None)
            length, tail = await self._stage(path, operation_id, data, length, tail)
            now = datetime.now(UTC)
            fields = dual_write_app_scope({
                "app_id": self._app_id,
                "chat_id": self._chat_id,
                "path": path,
                "storage_format": _STORAGE_FORMAT,
                "revision": operation_id,
                "length": length,
                "tail": tail,
                "updated_at": now,
            }, self._app_id)
            self._assert_mutable()
            # Do not catch ambiguous Mongo acknowledgement failures and retry:
            # publication may have succeeded. Retain its chunks and surface the
            # error rather than append twice or remove potentially visible data.
            if prior is None:
                try:
                    await collection.insert_one({
                        "_id": uuid4().hex, "created_at": now, **fields,
                    })
                    return prior_length
                except DuplicateKeyError:
                    pass
            else:
                expected = {"_id": prior["_id"], **self._filter(path)}
                expected.update(
                    {"revision": prior["revision"]}
                    if inline is None else {"storage_format": {"$exists": False}}
                )
                result = await collection.update_one(
                    expected, {"$set": fields, "$unset": {"content": ""}},
                )
                if result.matched_count:
                    return prior_length
            # A negative CAS result proves this operation was never visible.
            await self._discard_unpublished(path, operation_id)

    def _scope_filter(self) -> dict[str, Any]:
        return {"chat_id": self._chat_id, **build_app_scope_filter(self._app_id)}

    def _filter(self, path: str) -> dict[str, Any]:
        return {"path": path, **self._scope_filter()}

    async def _coll(self) -> Any:
        if self._collection is None:
            async with self._lock:
                if self._client is None:
                    self._client = get_mongo_client()
                self._collection = self._client[SYSTEM_DATABASE][RuntimeCollections.AG2_NETWORK_KNOWLEDGE]
        if not self._indexes_ready:
            await self._collection.create_index(
                [("app_id", 1), ("chat_id", 1), ("path", 1)],
                name="ag2_network_app_chat_path", unique=True,
            )
            self._indexes_ready = True
        return self._collection

    async def _chunks(self) -> Any:
        if self._chunks_collection is None:
            async with self._lock:
                if self._client is None:
                    self._client = get_mongo_client()
                self._chunks_collection = self._client[SYSTEM_DATABASE][
                    RuntimeCollections.AG2_NETWORK_KNOWLEDGE_CHUNKS
                ]
        if not self._chunk_indexes_ready:
            await self._chunks_collection.create_index(
                [("app_id", 1), ("chat_id", 1), ("path", 1), ("operation_id", 1)],
                name="ag2_network_chunks_scope_operation",
            )
            self._chunk_indexes_ready = True
        return self._chunks_collection


__all__ = ["MongoAG2KnowledgeStore"]
