"""Real Mongo proof of the AG2 virtual-file storage boundary.

Uses a fresh test database and skips only when MONGO_URI is absent/unreachable.
No generated app code or provider calls run in this suite.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any
from uuid import uuid4

import pytest
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import AutoReconnect

from mozaiksai.core.adapters.ag2_knowledge_store import MongoAG2KnowledgeStore
from mozaiksai.core.runtime.persistence import distributed_lock as dl


@pytest.fixture
async def mongo(monkeypatch: Any):
    uri = os.getenv("MONGO_URI")
    required = os.getenv("MOZAIKS_REQUIRE_REAL_MONGO") == "1"
    if not uri:
        if required:
            pytest.fail("MOZAIKS_REQUIRE_REAL_MONGO=1 requires MONGO_URI")
        pytest.skip("requires a reachable MongoDB via MONGO_URI")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=2000)
    try:
        await client.admin.command("ping")
    except Exception:
        client.close()
        if required:
            pytest.fail("MOZAIKS_REQUIRE_REAL_MONGO=1 requires reachable MongoDB")
        pytest.skip("requires a reachable MongoDB via MONGO_URI")
    database = client["ag2_knowledge_test_" + uuid4().hex]
    dl.reset_chat_lock_state()
    dl.configure_chat_lock(dl.ChatLockMode.REQUIRED)
    monkeypatch.setattr(dl, "_get_lock_collection", lambda: database.Locks)
    monkeypatch.setenv("DISTRIBUTED_LOCK_MAX_RETRIES", "0")
    try:
        yield database
    finally:
        dl.reset_chat_lock_state()
        await client.drop_database(database.name)
        client.close()


def _store(database: Any, *, app_id: str = "app", chat_id: str = "chat", heads: Any = None, chunks: Any = None) -> MongoAG2KnowledgeStore:
    return MongoAG2KnowledgeStore(
        app_id=app_id, chat_id=chat_id,
        collection=database.Heads if heads is None else heads,
        chunks_collection=database.Chunks if chunks is None else chunks,
    )


@pytest.mark.asyncio
async def test_large_append_restart_and_utf8_range(mongo: Any) -> None:
    store = _store(mongo)
    original = "prefix:" + "\u00e9" * (9 * 1024 * 1024)
    assert len(original.encode("utf-8")) > 16 * 1024 * 1024
    # A single append also exceeds BSON's document limit.
    assert await store.append("/channels/run/wal.jsonl", original) == 0
    offset = await store.append("/channels/run/wal.jsonl", "\nfinal\U0001f9f0")
    assert offset == len(original.encode("utf-8"))
    restarted = _store(mongo)
    assert await restarted.read("/channels/run/wal.jsonl") == original + "\nfinal\U0001f9f0"
    assert await restarted.read_range("/channels/run/wal.jsonl", offset - 4, offset + 6) == "\u00e9\u00e9\nfinal"
    assert await restarted.read_range("/channels/run/wal.jsonl", offset + 6) == "\U0001f9f0"
    head = await mongo.Heads.find_one({"path": "/channels/run/wal.jsonl"})
    assert "content" not in head
    assert await mongo.Chunks.count_documents({}) >= 19


@pytest.mark.asyncio
async def test_large_write_replaces_visible_content_beyond_bson_limit(mongo: Any) -> None:
    store = _store(mongo)
    await store.write("/state", "old")
    content = "\u00e9" * (9 * 1024 * 1024)
    await store.write("/state", content)
    assert await _store(mongo).read("/state") == content
    assert await store.append("/state", "tail") == len(content.encode("utf-8"))
    assert await store.read_range("/state", len(content.encode("utf-8"))) == "tail"
    await store.write("/state", "")
    assert await store.read("/state") == ""


@pytest.mark.asyncio
async def test_independent_appenders_publish_contiguous_offsets(mongo: Any) -> None:
    stores = [_store(mongo) for _ in range(12)]
    payloads = [f"{index}:" + "\u00e9" * (index + 1) for index in range(len(stores))]
    ready = asyncio.Event()

    async def append(store: Any, text: str) -> tuple[int, str]:
        await ready.wait()
        return await store.append("/wal", text), text

    tasks = [asyncio.create_task(append(store, text)) for store, text in zip(stores, payloads, strict=True)]
    ready.set()
    results = sorted(await asyncio.gather(*tasks))
    offset = 0
    for actual, payload in results:
        assert actual == offset
        offset += len(payload.encode("utf-8"))
    assert await _store(mongo).read("/wal") == "".join(payload for _, payload in results)
    assert await mongo.Chunks.count_documents({}) == len(payloads)


class _Proxy:
    def __init__(self, collection: Any) -> None:
        self.collection = collection

    def __getattr__(self, name: str) -> Any:
        return getattr(self.collection, name)


@pytest.mark.asyncio
async def test_failed_chunk_or_cas_does_not_expose_partial_bytes(mongo: Any) -> None:
    await _store(mongo).write("/wal", "base")

    class FailedChunks(_Proxy):
        count = 0

        async def insert_one(self, doc: Any) -> Any:
            self.count += 1
            if self.count == 2:
                raise OSError("injected chunk failure")
            return await self.collection.insert_one(doc)

    with pytest.raises(OSError, match="injected chunk failure"):
        await _store(mongo, chunks=FailedChunks(mongo.Chunks)).append("/wal", "a" * (2 * 1024 * 1024))
    assert await _store(mongo).read("/wal") == "base"

    class FailedHead(_Proxy):
        async def update_one(self, *args: Any, **kwargs: Any) -> Any:
            raise AutoReconnect("injected head failure")

    with pytest.raises(AutoReconnect):
        await _store(mongo, heads=FailedHead(mongo.Heads)).append("/wal", "unpublished")
    assert await _store(mongo).read("/wal") == "base"

    class UncertainHead(_Proxy):
        async def update_one(self, *args: Any, **kwargs: Any) -> Any:
            await self.collection.update_one(*args, **kwargs)
            raise AutoReconnect("injected lost acknowledgement")

    with pytest.raises(AutoReconnect):
        await _store(mongo, heads=UncertainHead(mongo.Heads)).append("/wal", "committed")
    assert await _store(mongo).read("/wal") == "basecommitted"


@pytest.mark.asyncio
async def test_existing_inline_history_migrates_atomically(mongo: Any) -> None:
    await mongo.Heads.insert_one({"app_id": "app", "chat_id": "chat", "path": "/wal", "content": "\u00e9" * (7 * 1024 * 1024)})
    store = _store(mongo)
    original = await store.read("/wal")
    assert await mongo.Chunks.count_documents({}) == 0
    assert await store.append("/wal", "x" * (4 * 1024 * 1024)) == len(original.encode("utf-8"))
    assert await _store(mongo).read("/wal") == original + "x" * (4 * 1024 * 1024)
    assert "content" not in await mongo.Heads.find_one({"path": "/wal"})


@pytest.mark.asyncio
async def test_snapshot_reader_survives_replacement_and_delete_recreate(mongo: Any) -> None:
    await _store(mongo).write("/wal", "old")
    old_head = await mongo.Heads.find_one({"path": "/wal"})
    await _store(mongo).write("/wal", "replacement")
    await _store(mongo).delete("/wal")
    await _store(mongo).write("/wal", "new")
    # This is the exact already-captured-head path exercised by public read().
    assert await _store(mongo)._read_bytes("/wal", old_head, 0, None) == b"old"
    assert await _store(mongo).read("/wal") == "new"
    assert old_head["_id"] != (await mongo.Heads.find_one({"path": "/wal"}))["_id"]


@pytest.mark.asyncio
async def test_tenant_chat_isolation_and_explicit_quiescent_purge(mongo: Any) -> None:
    stores = [_store(mongo), _store(mongo, app_id="other"), _store(mongo, chat_id="other")]
    for index, store in enumerate(stores):
        await store.write("/wal", f"secret{index}")
        await store.write("/wal", f"current{index}")
    await stores[0].delete("/")
    assert await stores[0].read("/wal") is None
    assert await mongo.Chunks.count_documents({"app_id": "app", "chat_id": "chat"}) == 2
    # Existing chat lease prevents cleanup while the namespace is executing.
    async with dl.chat_execution_lease(app_id="app", chat_id="chat"):
        with pytest.raises(dl.LockAcquisitionError):
            await stores[0].purge_namespace()
    await stores[0].purge_namespace()
    assert await mongo.Heads.count_documents({"app_id": "app", "chat_id": "chat"}) == 0
    assert await mongo.Chunks.count_documents({"app_id": "app", "chat_id": "chat"}) == 0
    assert await stores[1].read("/wal") == "current1"
    assert await stores[2].read("/wal") == "current2"


@pytest.mark.asyncio
async def test_deleted_recreated_head_fences_stale_append(mongo: Any) -> None:
    store = _store(mongo)
    await store.write("/wal", "old")

    class RecreatedHead(_Proxy):
        first = True

        async def update_one(self, *args: Any, **kwargs: Any) -> Any:
            if self.first:
                self.first = False
                await store.delete("/wal")
                await store.write("/wal", "replacement")
            return await self.collection.update_one(*args, **kwargs)

    pending = _store(mongo, heads=RecreatedHead(mongo.Heads))
    assert await pending.append("/wal", "next") == len("replacement")
    assert await store.read("/wal") == "replacementnext"


@pytest.mark.asyncio
async def test_purge_failure_is_scoped_and_retryable(mongo: Any) -> None:
    store = _store(mongo)
    foreign = _store(mongo, chat_id="foreign")
    await store.write("/wal", "retire")
    await foreign.write("/wal", "retain")

    class FailedDelete(_Proxy):
        async def delete_many(self, *args: Any, **kwargs: Any) -> Any:
            raise OSError("injected cleanup failure")

    with pytest.raises(OSError, match="injected cleanup failure"):
        await _store(mongo, chunks=FailedDelete(mongo.Chunks)).purge_namespace()
    assert await store.read("/wal") is None
    assert await mongo.Chunks.count_documents({"chat_id": "chat"}) == 1
    await store.purge_namespace()
    assert await mongo.Chunks.count_documents({"chat_id": "chat"}) == 0
    assert await foreign.read("/wal") == "retain"


@pytest.mark.asyncio
async def test_purge_refuses_process_local_lease(mongo: Any) -> None:
    store = _store(mongo)
    await store.write('/wal', 'retain')
    dl.configure_chat_lock(dl.ChatLockMode.LOCAL)
    with pytest.raises(ValueError, match='distributed chat lease'):
        await store.purge_namespace()
    assert await store.read('/wal') == 'retain'
