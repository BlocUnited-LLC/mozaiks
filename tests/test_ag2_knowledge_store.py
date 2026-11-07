from __future__ import annotations

import asyncio
import copy
import re
from types import SimpleNamespace
from typing import Any

import pytest
from ag2.knowledge import MemoryKnowledgeStore
from pymongo.errors import AutoReconnect, DuplicateKeyError

from mozaiksai.core.adapters import ag2_knowledge_store as module
from mozaiksai.core.adapters.ag2_knowledge_store import MongoAG2KnowledgeStore
from mozaiksai.core.runtime.persistence import distributed_lock as dl


def _matches(doc: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, value in query.items():
        if key == "$or":
            if not any(_matches(doc, item) for item in value):
                return False
        elif isinstance(value, dict) and "$exists" in value:
            if (key in doc) != value["$exists"]:
                return False
        elif isinstance(value, dict) and "$regex" in value:
            if re.search(value["$regex"], doc.get(key, "")) is None:
                return False
        elif doc.get(key) != value:
            return False
    return True


class _Cursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self.docs = docs

    async def to_list(self, *, length: int) -> list[dict[str, Any]]:
        return copy.deepcopy(self.docs[:length])


class _Collection:
    def __init__(self, *, heads: bool = False) -> None:
        self.docs: dict[str, dict[str, Any]] = {}
        self.heads = heads
        self.index_calls: list[tuple[Any, Any]] = []
        self.find_one_calls: list[dict[str, Any]] = []
        self.mutations: list[str] = []

    async def create_index(self, *args: Any, **kwargs: Any) -> None:
        self.index_calls.append((args, kwargs))

    async def find_one(self, query: dict[str, Any], *args: Any) -> dict[str, Any] | None:
        self.find_one_calls.append(copy.deepcopy(query))
        return next((copy.deepcopy(doc) for doc in self.docs.values() if _matches(doc, query)), None)

    async def insert_one(self, doc: dict[str, Any]) -> None:
        if doc["_id"] in self.docs or (self.heads and any(
            all(old.get(key) == doc.get(key) for key in ("app_id", "chat_id", "path"))
            for old in self.docs.values()
        )):
            raise DuplicateKeyError("duplicate")
        self.mutations.append("insert")
        self.docs[doc["_id"]] = copy.deepcopy(doc)

    async def update_one(self, query: Any, update: Any, **kwargs: Any) -> Any:
        self.mutations.append("update")
        for doc in self.docs.values():
            if _matches(doc, query):
                doc.update(copy.deepcopy(update.get("$set", {})))
                for key in update.get("$unset", {}):
                    doc.pop(key, None)
                return SimpleNamespace(matched_count=1)
        return SimpleNamespace(matched_count=0)

    def find(self, query: Any, *args: Any) -> _Cursor:
        return _Cursor([doc for doc in self.docs.values() if _matches(doc, query)])

    async def delete_many(self, query: dict[str, Any]) -> None:
        self.mutations.append("delete")
        self.docs = {key: doc for key, doc in self.docs.items() if not _matches(doc, query)}


def _store(heads: Any, chunks: Any, *, app_id: str = "app-1", chat_id: str = "chat-1") -> MongoAG2KnowledgeStore:
    return MongoAG2KnowledgeStore(
        app_id=app_id, chat_id=chat_id, collection=heads, chunks_collection=chunks,
    )


@pytest.mark.asyncio
async def test_mongo_ag2_knowledge_store_scopes_paths_to_app_and_chat() -> None:
    heads, chunks = _Collection(heads=True), _Collection()
    heads.docs["inline"] = {"_id": "inline", "app_id": "app-1", "chat_id": "chat-1", "path": "/hub/agents/a.json", "content": "hydrated"}
    store = _store(heads, chunks)
    assert await store.read("hub/agents/a.json") == "hydrated"
    assert heads.find_one_calls[0] == {"path": "/hub/agents/a.json", "chat_id": "chat-1", "app_id": "app-1"}
    assert heads.index_calls[0][1]["unique"] is True
    assert heads.mutations == chunks.mutations == []
    assert await _store(heads, chunks, app_id="other").read("hub/agents/a.json") is None
    assert await _store(heads, chunks, chat_id="other").read("hub/agents/a.json") is None


@pytest.mark.asyncio
async def test_native_root_presence_keeps_app_and_chat_scope_without_mutation() -> None:
    heads, chunks = _Collection(heads=True), _Collection()
    store = _store(heads, chunks)
    await store.write("/hub/a", "value")
    heads.mutations.clear()
    chunks.mutations.clear()
    assert await store.exists("/") is True
    assert heads.find_one_calls[-1] == {
        "app_id": "app-1", "chat_id": "chat-1",
        "$or": [{"path": "/"}, {"path": {"$regex": "^/"}}],
    }
    assert heads.mutations == chunks.mutations == []


@pytest.mark.asyncio
async def test_mongo_ag2_knowledge_store_lists_virtual_children() -> None:
    heads, chunks = _Collection(heads=True), _Collection()
    store = _store(heads, chunks)
    for path in ("/hub/agents/one.json", "/hub/agents/nested/two.json", "/hub/agents/nested/three.json"):
        await store.write(path, path)
    await _store(heads, chunks, app_id="other").write("/hub/agents/foreign", "secret")
    assert await store.list("/hub/agents") == ["nested/", "one.json"]
    await store.delete("/hub/agents/nested")
    assert await store.list("/hub/agents") == ["one.json"]
    assert await store.exists("/hub/agents/nested") is False
    assert await _store(heads, chunks, app_id="other").read("/hub/agents/foreign") == "secret"


@pytest.mark.asyncio
async def test_mongo_ag2_knowledge_store_append_returns_utf8_byte_offset(monkeypatch: Any) -> None:
    monkeypatch.setattr(module, "_CHUNK_BYTES", 3)
    heads, chunks = _Collection(heads=True), _Collection()
    store = _store(heads, chunks)
    assert await store.append("/wal", "caf\u00e9") == 0
    assert await store.append("/wal", "\U0001f9f0\nnext") == 5
    assert await store.read("/wal") == "caf\u00e9\U0001f9f0\nnext"
    assert await store.read_range("/wal", 3, 9) == "\u00e9\U0001f9f0"
    assert await store.read_range("/wal", 9) == "\nnext"
    with pytest.raises(UnicodeDecodeError):
        await store.read_range("/wal", 4, 9)
    assert await store.read_range("/missing", 0) == ""
    assert await store.read_range("/wal", 1000) == ""


@pytest.mark.asyncio
async def test_inline_content_is_migrated_only_by_successful_mutation() -> None:
    heads, chunks = _Collection(heads=True), _Collection()
    heads.docs["inline"] = {"_id": "inline", "app_id": "app-1", "chat_id": "chat-1", "path": "/wal", "content": "caf\u00e9"}
    store = _store(heads, chunks)
    assert await store.read_range("/wal", 3) == "\u00e9"
    assert heads.mutations == chunks.mutations == []
    assert await store.append("/wal", "\nnext") == 5
    assert "content" not in heads.docs["inline"]
    assert await _store(heads, chunks).read("/wal") == "caf\u00e9\nnext"
    await store.write("/wal", "replacement")
    assert await store.read("/wal") == "replacement"
    await store.write("/empty", "")
    assert await store.append("/empty", "") == 0
    assert await store.read("/empty") == ""


@pytest.mark.asyncio
async def test_mongo_ag2_knowledge_store_preserves_concurrent_metadata_write_order() -> None:
    class DelayedChunks(_Collection):
        def __init__(self) -> None:
            super().__init__()
            self.pending_started, self.release_pending = asyncio.Event(), asyncio.Event()

        async def insert_one(self, doc: dict[str, Any]) -> None:
            if doc["data"] == b"pending":
                self.pending_started.set()
                await self.release_pending.wait()
            await super().insert_one(doc)

    heads, chunks = _Collection(heads=True), DelayedChunks()
    store = _store(heads, chunks)
    pending = asyncio.create_task(store.write("/metadata", "pending"))
    await chunks.pending_started.wait()
    active = asyncio.create_task(store.write("/metadata", "active"))
    await asyncio.sleep(0)
    chunks.release_pending.set()
    await asyncio.gather(pending, active)
    assert await store.read("/metadata") == "active"


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement", [False, True])
async def test_reader_snapshot_survives_delete_or_replace(replacement: bool) -> None:
    class DelayedRead(_Collection):
        def __init__(self) -> None:
            super().__init__()
            self.started, self.release = asyncio.Event(), asyncio.Event()
            self.pause = True

        async def find_one(self, query: Any, *args: Any) -> Any:
            if self.pause:
                self.pause = False
                self.started.set()
                await self.release.wait()
            return await super().find_one(query, *args)

    heads, chunks = _Collection(heads=True), DelayedRead()
    store = _store(heads, chunks)
    await store.write("/wal", "old bytes")
    read = asyncio.create_task(store.read("/wal"))
    await chunks.started.wait()
    if replacement:
        await store.write("/wal", "new bytes")
    else:
        await store.delete("/wal")
    chunks.release.set()
    assert await read == "old bytes"
    assert await store.read("/wal") == ("new bytes" if replacement else None)


@pytest.mark.asyncio
async def test_failed_chunk_staging_never_publishes_partial_bytes(monkeypatch: Any) -> None:
    monkeypatch.setattr(module, "_CHUNK_BYTES", 4)
    class FailingChunks(_Collection):
        fail = False

        async def insert_one(self, doc: dict[str, Any]) -> None:
            if self.fail and doc["start"] >= 8:
                raise OSError("chunk write failed")
            await super().insert_one(doc)

    heads, chunks = _Collection(heads=True), FailingChunks()
    store = _store(heads, chunks)
    await store.write("/wal", "base")
    chunks.fail = True
    with pytest.raises(OSError, match="chunk write failed"):
        await store.append("/wal", "long append")
    assert await store.read("/wal") == "base"


@pytest.mark.asyncio
async def test_negative_cas_retries_and_removes_only_unpublished_operation() -> None:
    class LosingHeads(_Collection):
        lose = True

        async def update_one(self, query: Any, update: Any, **kwargs: Any) -> Any:
            if self.lose:
                self.lose = False
                return SimpleNamespace(matched_count=0)
            return await super().update_one(query, update, **kwargs)

    heads, chunks = LosingHeads(heads=True), _Collection()
    store = _store(heads, chunks)
    await store.write("/wal", "base")
    assert await store.append("/wal", "next") == 4
    assert await store.read("/wal") == "basenext"
    assert len(chunks.docs) == 2


@pytest.mark.asyncio
async def test_ambiguous_cas_ack_does_not_retry_or_delete_published_bytes() -> None:
    class UncertainHeads(_Collection):
        async def update_one(self, query: Any, update: Any, **kwargs: Any) -> Any:
            await super().update_one(query, update, **kwargs)
            raise AutoReconnect("acknowledgement lost")

    heads, chunks = UncertainHeads(heads=True), _Collection()
    store = _store(heads, chunks)
    await store.write("/wal", "base")
    with pytest.raises(AutoReconnect):
        await store.append("/wal", "next")
    assert await store.read("/wal") == "basenext"
    assert len(chunks.docs) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["missing", "gap", "cross_tenant", "cycle", "unknown_format"])
async def test_invalid_committed_storage_fails_closed(damage: str, monkeypatch: Any) -> None:
    monkeypatch.setattr(module, "_CHUNK_BYTES", 3)
    heads, chunks = _Collection(heads=True), _Collection()
    store = _store(heads, chunks)
    await store.write("/wal", "abcdef")
    head = next(iter(heads.docs.values()))
    tail = chunks.docs[head["tail"]]
    if damage == "missing":
        chunks.docs.pop(head["tail"])
    elif damage == "gap":
        tail["start"] = 4
    elif damage == "cross_tenant":
        tail["app_id"] = "other"
    elif damage == "cycle":
        tail["previous"] = head["tail"]
    else:
        head["storage_format"] = "unknown"
    with pytest.raises(ValueError):
        await store.read("/wal")


@pytest.mark.asyncio
async def test_mongo_ag2_knowledge_store_returns_noop_subscription() -> None:
    subscription = await _store(_Collection(heads=True), _Collection()).on_change("/channels", lambda _path: None)
    await subscription.close()


@pytest.mark.asyncio
async def test_mongo_ag2_knowledge_store_refuses_mutation_after_lease_loss() -> None:
    resource = dl.chat_lock_resource("app-1", "chat-1")
    lease = dl.ChatLease(resource=resource, holder_id="stale-holder", mode=dl.ChatLockMode.REQUIRED, collection=None, ttl_seconds=60)
    lease._mark_lost("test")
    dl._process_leases[resource] = lease
    heads, chunks = _Collection(heads=True), _Collection()
    store = _store(heads, chunks)
    try:
        for operation in (store.write("/hub/state", "state"), store.append("/wal", "entry"), store.delete("/hub")):
            with pytest.raises(dl.ChatLeaseLostError):
                await operation
        assert heads.mutations == chunks.mutations == []
    finally:
        dl.reset_chat_lock_state()


@pytest.mark.asyncio
async def test_lease_loss_after_staging_blocks_publication(monkeypatch: Any) -> None:
    heads, chunks = _Collection(heads=True), _Collection()
    store = _store(heads, chunks)
    await store.write("/wal", "base")
    original_insert = chunks.insert_one
    lost = False

    async def lose_after_insert(doc: Any) -> None:
        nonlocal lost
        await original_insert(doc)
        lost = True

    def guard(**kwargs: Any) -> None:
        if lost:
            raise dl.ChatLeaseLostError("test")

    monkeypatch.setattr(chunks, "insert_one", lose_after_insert)
    monkeypatch.setattr(module, "assert_chat_mutable", guard)
    with pytest.raises(dl.ChatLeaseLostError):
        await store.append("/wal", "unpublished")
    assert await store.read("/wal") == "base"


@pytest.mark.asyncio
async def test_ambiguous_initial_insert_keeps_published_chunks() -> None:
    class UncertainHeads(_Collection):
        async def insert_one(self, doc: Any) -> None:
            await super().insert_one(doc)
            raise AutoReconnect("insert acknowledgement lost")

    heads, chunks = UncertainHeads(heads=True), _Collection()
    store = _store(heads, chunks)
    with pytest.raises(AutoReconnect):
        await store.append("/wal", "once")
    assert await store.read("/wal") == "once"
    assert len(chunks.docs) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('inline', [False, True])
@pytest.mark.parametrize(('start', 'end'), [(0, None), (0, -1), (-5, -1), (-3, 0), (-100, 3), (3, 3), (4, 99), (9, None), (0, 0), (-1, None)])
async def test_read_range_matches_native_ag2_boundary_semantics(inline: bool, start: int, end: int | None) -> None:
    heads, chunks = _Collection(heads=True), _Collection()
    store = _store(heads, chunks)
    native = MemoryKnowledgeStore()
    await native.write('/wal', 'abcdef')
    if inline:
        heads.docs['inline'] = {'_id': 'inline', 'app_id': 'app-1', 'chat_id': 'chat-1', 'path': '/wal', 'content': 'abcdef'}
    else:
        await store.write('/wal', 'abcdef')
    assert await store.read_range('/wal', start, end) == await native.read_range('/wal', start, end)
