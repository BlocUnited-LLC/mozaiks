"""Bounded Mongo alias operations for apps containing owner-scoped collections.

App-wide aliases retain their own query semantics. Their handles and cursors
cannot expose another collection or run a cross-collection aggregation.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any, cast

from .ownership import validate_owned_pipeline


class GuardedAliasCursor:
    """Cursor operations used by shipped app-owned alias repositories."""

    __slots__ = ("__cursor",)

    def __init__(self, cursor: Any) -> None:
        self.__cursor = cursor

    def sort(self, *args: Any, **kwargs: Any) -> GuardedAliasCursor:
        self.__cursor = self.__cursor.sort(*args, **kwargs)
        return self

    def limit(self, limit: int) -> GuardedAliasCursor:
        self.__cursor = self.__cursor.limit(limit)
        return self

    def skip(self, skip: int) -> GuardedAliasCursor:
        self.__cursor = self.__cursor.skip(skip)
        return self

    async def to_list(self, length: int | None = None) -> list[Any]:
        return cast(list[Any], await self.__cursor.to_list(length=length))

    def __aiter__(self) -> GuardedAliasCursor:
        return self

    async def __anext__(self) -> Any:
        return await self.__cursor.__anext__()


class GuardedAliasCollection:
    """Same-collection Mongo CRUD without access to other namespaces.

    There is deliberately no attribute forwarding: database handles, alternate
    collection constructors, cursor internals, and Mongo administration are absent.
    """

    __slots__ = ("__collection",)

    def __init__(self, collection: Any) -> None:
        self.__collection = collection

    def find(self, *args: Any, **kwargs: Any) -> GuardedAliasCursor:
        return GuardedAliasCursor(self.__collection.find(*args, **kwargs))

    async def find_one(self, *args: Any, **kwargs: Any) -> Any:
        return await self.__collection.find_one(*args, **kwargs)

    async def insert_one(self, *args: Any, **kwargs: Any) -> Any:
        return await self.__collection.insert_one(*args, **kwargs)

    async def insert_many(self, *args: Any, **kwargs: Any) -> Any:
        return await self.__collection.insert_many(*args, **kwargs)

    async def update_one(self, *args: Any, **kwargs: Any) -> Any:
        return await self.__collection.update_one(*args, **kwargs)

    async def update_many(self, *args: Any, **kwargs: Any) -> Any:
        return await self.__collection.update_many(*args, **kwargs)

    async def replace_one(self, *args: Any, **kwargs: Any) -> Any:
        return await self.__collection.replace_one(*args, **kwargs)

    async def delete_one(self, *args: Any, **kwargs: Any) -> Any:
        return await self.__collection.delete_one(*args, **kwargs)

    async def delete_many(self, *args: Any, **kwargs: Any) -> Any:
        return await self.__collection.delete_many(*args, **kwargs)

    async def count_documents(self, *args: Any, **kwargs: Any) -> Any:
        return await self.__collection.count_documents(*args, **kwargs)

    async def distinct(self, *args: Any, **kwargs: Any) -> Any:
        return await self.__collection.distinct(*args, **kwargs)

    async def find_one_and_update(self, *args: Any, **kwargs: Any) -> Any:
        return await self.__collection.find_one_and_update(*args, **kwargs)

    async def find_one_and_replace(self, *args: Any, **kwargs: Any) -> Any:
        return await self.__collection.find_one_and_replace(*args, **kwargs)

    async def find_one_and_delete(self, *args: Any, **kwargs: Any) -> Any:
        return await self.__collection.find_one_and_delete(*args, **kwargs)

    def aggregate(
        self, pipeline: Sequence[Mapping[str, Any]], *args: Any, **kwargs: Any,
    ) -> GuardedAliasCursor:
        # Motor executes lazily. Keep a detached validated pipeline so caller
        # mutation after aggregate() cannot add a lookup/union/output stage.
        stages = deepcopy(list(pipeline))
        validate_owned_pipeline(stages)
        return GuardedAliasCursor(self.__collection.aggregate(stages, *args, **kwargs))
