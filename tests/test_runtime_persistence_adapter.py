from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from mozaiksai.core.runtime.persistence import ModulePersistenceContext, PersistenceCollection
from mozaiksai.core.runtime.persistence.naming import scope_filter_for


class FakeCollection:
    async def find_one(self, query: Mapping[str, Any], projection: Mapping[str, Any] | None = None):
        return {"query": dict(query), "projection": projection}

    async def find_many(
        self,
        query: Mapping[str, Any],
        *,
        limit: int = 50,
        sort: Sequence[tuple[str, int]] | None = None,
        projection: Mapping[str, Any] | None = None,
    ):
        return [{"query": dict(query), "limit": limit, "sort": sort, "projection": projection}]

    async def insert_one(self, document: Mapping[str, Any]):
        return {"inserted_id": document.get("_id")}

    async def update_one(
        self,
        query: Mapping[str, Any],
        update: Mapping[str, Any],
        *,
        upsert: bool = False,
    ):
        return {"query": dict(query), "update": dict(update), "upsert": upsert}

    async def count(self, query: Mapping[str, Any]) -> int:
        return 1 if query else 0

    async def ensure_indexes(self, indexes: Sequence[Mapping[str, Any]]) -> None:
        self.indexes = list(indexes)


class FakePersistenceContext:
    def __init__(self, app_id: str) -> None:
        self._app_id = app_id
        self.collections: dict[tuple[str, str], FakeCollection] = {}
        self.indexes_ensured = False

    @property
    def app_id(self) -> str:
        return self._app_id

    def collection(self, module_id: str, entity_name: str) -> FakeCollection:
        key = (module_id, entity_name)
        if key not in self.collections:
            self.collections[key] = FakeCollection()
        return self.collections[key]

    def scope_filter(self, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return scope_filter_for(self.app_id, extra)

    async def ensure_indexes(self) -> None:
        self.indexes_ensured = True


def test_persistence_collection_protocol_can_be_implemented_by_fake_collection() -> None:
    collection = FakeCollection()

    assert isinstance(collection, PersistenceCollection)


def test_module_persistence_context_protocol_can_be_implemented_by_fake_adapter() -> None:
    context = FakePersistenceContext("app_1")

    assert isinstance(context, ModulePersistenceContext)
    assert context.collection("projects", "projects") is context.collection("projects", "projects")
    assert context.scope_filter({"status": "open"}) == {"app_id": "app_1", "status": "open"}
