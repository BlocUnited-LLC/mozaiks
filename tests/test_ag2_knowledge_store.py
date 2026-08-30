from __future__ import annotations

from typing import Any

import pytest

from mozaiksai.core.adapters.ag2_knowledge_store import MongoAG2KnowledgeStore


class _Cursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self.docs = docs

    async def to_list(self, *, length: int) -> list[dict[str, Any]]:
        return self.docs[:length]


class _Collection:
    def __init__(self) -> None:
        self.find_one_result: dict[str, Any] | None = None
        self.find_docs: list[dict[str, Any]] = []
        self.find_one_calls: list[tuple[Any, ...]] = []
        self.update_calls: list[tuple[Any, ...]] = []
        self.append_calls: list[tuple[Any, ...]] = []
        self.delete_calls: list[dict[str, Any]] = []
        self.index_calls: list[tuple[Any, ...]] = []

    async def create_index(self, *args: Any, **kwargs: Any) -> None:
        self.index_calls.append((args, kwargs))

    async def find_one(self, *args: Any) -> dict[str, Any] | None:
        self.find_one_calls.append(args)
        return self.find_one_result

    async def update_one(self, *args: Any, **kwargs: Any) -> None:
        self.update_calls.append((*args, kwargs))

    def find(self, *args: Any) -> _Cursor:
        return _Cursor(self.find_docs)

    async def delete_many(self, query: dict[str, Any]) -> None:
        self.delete_calls.append(query)

    async def find_one_and_update(self, *args: Any, **kwargs: Any) -> dict[str, Any] | None:
        self.append_calls.append((*args, kwargs))
        return self.find_one_result


@pytest.mark.asyncio
async def test_mongo_ag2_knowledge_store_scopes_paths_to_app_and_chat() -> None:
    collection = _Collection()
    collection.find_one_result = {"content": "hydrated"}
    store = MongoAG2KnowledgeStore(
        app_id="app-1",
        chat_id="chat-1",
        collection=collection,
    )

    assert await store.read("hub/agents/a.json") == "hydrated"
    query = collection.find_one_calls[0][0]
    assert query["path"] == "/hub/agents/a.json"
    assert query["chat_id"] == "chat-1"
    assert query["app_id"] == "app-1"
    assert collection.index_calls[0][1]["unique"] is True


@pytest.mark.asyncio
async def test_mongo_ag2_knowledge_store_lists_virtual_children() -> None:
    collection = _Collection()
    collection.find_docs = [
        {"path": "/hub/agents/one.json"},
        {"path": "/hub/agents/nested/two.json"},
        {"path": "/hub/agents/nested/three.json"},
    ]
    store = MongoAG2KnowledgeStore(
        app_id="app-1",
        chat_id="chat-1",
        collection=collection,
    )

    assert await store.list("/hub/agents") == ["nested/", "one.json"]


@pytest.mark.asyncio
async def test_mongo_ag2_knowledge_store_append_returns_utf8_byte_offset() -> None:
    collection = _Collection()
    collection.find_one_result = {"content": "café"}
    store = MongoAG2KnowledgeStore(
        app_id="app-1",
        chat_id="chat-1",
        collection=collection,
    )

    assert await store.append("/channels/wal", "\nnext") == 5
    query, update_pipeline, options = collection.append_calls[0]
    assert query["path"] == "/channels/wal"
    assert update_pipeline[0]["$set"]["content"]["$concat"][1] == "\nnext"
    assert options["upsert"] is True


@pytest.mark.asyncio
async def test_mongo_ag2_knowledge_store_returns_noop_subscription() -> None:
    collection = _Collection()
    store = MongoAG2KnowledgeStore(
        app_id="app-1",
        chat_id="chat-1",
        collection=collection,
    )

    subscription = await store.on_change("/channels", lambda _path: None)
    await subscription.close()
