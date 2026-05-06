from __future__ import annotations

import asyncio
import importlib

import pytest


data_entity_module = importlib.import_module("mozaiksai.core.workflow.context.data_entity")


class _FakeCollection:
    def __init__(self) -> None:
        self.indexes = []
        self.inserted = []
        self.updated = []

    async def create_index(self, keys, **kwargs):
        self.indexes.append((list(keys), dict(kwargs)))

    async def insert_one(self, doc):
        self.inserted.append(dict(doc))

    async def update_one(self, query, update, upsert=False):
        self.updated.append((dict(query), dict(update), bool(upsert)))


class _FakeDatabase:
    def __init__(self, collection) -> None:
        self._collection = collection

    def __getitem__(self, collection_name):
        return self._collection


class _FakeClient:
    def __init__(self, collection) -> None:
        self._database = _FakeDatabase(collection)

    def __getitem__(self, database_name):
        return self._database


def test_data_entity_manager_creates_indexes_and_applies_defaults(monkeypatch) -> None:
    collection = _FakeCollection()
    monkeypatch.setattr(data_entity_module, "get_mongo_client", lambda: _FakeClient(collection))

    manager = data_entity_module.DataEntityManager(
        database_name="mozaiksai",
        collection="users",
        schema={
            "fields": [
                {"name": "app_id", "type": "string", "required": True},
                {"name": "user_id", "type": "string", "required": True},
                {"name": "role", "type": "string", "required": False, "default": "member", "enum": ["member", "admin"]},
            ]
        },
        indexes=[{"keys": [["app_id", 1], ["user_id", 1]], "unique": True, "name": "users_app_user"}],
        search_by="user_id",
    )

    inserted = asyncio.run(manager.create({"app_id": "app_1", "user_id": "user_1"}))

    assert inserted["role"] == "member"
    assert collection.inserted[0]["role"] == "member"
    assert collection.indexes[0][0] == [("app_id", 1), ("user_id", 1)]
    assert collection.indexes[0][1]["unique"] is True
    assert collection.indexes[0][1]["name"] == "users_app_user"


def test_data_entity_manager_rejects_invalid_enum_and_type(monkeypatch) -> None:
    collection = _FakeCollection()
    monkeypatch.setattr(data_entity_module, "get_mongo_client", lambda: _FakeClient(collection))

    manager = data_entity_module.DataEntityManager(
        database_name="mozaiksai",
        collection="users",
        schema={
            "fields": [
                {"name": "user_id", "type": "string", "required": True},
                {"name": "attempts", "type": "integer", "required": False},
                {"name": "role", "type": "string", "required": False, "enum": ["member", "admin"]},
            ]
        },
        search_by="user_id",
    )

    with pytest.raises(ValueError, match="must be of type 'integer'"):
        asyncio.run(manager.update("user_1", {"attempts": "3"}))

    with pytest.raises(ValueError, match="must be one of"):
        asyncio.run(manager.create({"user_id": "user_1", "role": "owner"}))
