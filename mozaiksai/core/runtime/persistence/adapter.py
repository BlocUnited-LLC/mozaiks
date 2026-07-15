from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

Document = Mapping[str, Any]
Query = Mapping[str, Any]
Projection = Mapping[str, Any]
SortSpec = Sequence[tuple[str, int]]
IndexSpec = Mapping[str, Any]


@runtime_checkable
class PersistenceCollection(Protocol):
    """Collection operations generated module repositories may depend on."""

    async def find_one(
        self,
        query: Query,
        projection: Projection | None = None,
    ) -> Document | None:
        ...

    async def find_many(
        self,
        query: Query,
        *,
        limit: int = 50,
        sort: SortSpec | None = None,
        projection: Projection | None = None,
    ) -> list[Document]:
        ...

    async def insert_one(self, document: Document) -> Any:
        ...

    async def update_one(
        self,
        query: Query,
        update: Mapping[str, Any],
        *,
        upsert: bool = False,
    ) -> Any:
        ...

    async def delete_one(self, query: Query) -> Any:
        ...

    async def delete_many(self, query: Query) -> Any:
        ...

    async def count(self, query: Query) -> int:
        ...

    async def aggregate(self, pipeline: Sequence[Mapping[str, Any]]) -> list[Document]:
        ...

    async def ensure_indexes(self, indexes: Sequence[IndexSpec]) -> None:
        ...


@runtime_checkable
class ModulePersistenceContext(Protocol):
    """App-scoped persistence access for a generated module."""

    @property
    def app_id(self) -> str:
        ...

    def collection(self, module_id: str, entity_name: str) -> PersistenceCollection:
        ...

    def scope_filter(self, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
        ...

    async def ensure_indexes(self) -> None:
        ...


__all__ = [
    "Document",
    "IndexSpec",
    "ModulePersistenceContext",
    "PersistenceCollection",
    "Projection",
    "Query",
    "SortSpec",
]
