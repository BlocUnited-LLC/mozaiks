"""Bounded Mongo alias operations for apps containing owner-scoped collections.

App-wide aliases retain their own query semantics. Their handles and cursors
cannot expose another collection or run a cross-collection aggregation.

A bounded handle forwards only the options Mongo defines for each operation.
The installed driver merges any leftover keyword it does not recognise into the
command document it sends to the server, and the server reads a handful of those
fields (the command verb, the target namespace, a replacement pipeline, the
database) as the collection or database to act on. A raw query command envelope in a
filter has the same effect for reads. So every method validates its arguments
against an explicit per-operation allow-list and refuses the rest before the
driver builds a command: an unexpected option, a raw query envelope, or a surplus
positional argument fails closed with ``PersistenceScopeError`` and never
reaches the server. The allow-lists cover every option a caller legitimately
needs (sessions, comments, projections, sorts, upserts, index hints, time
limits, and so on); none of them can name a different collection, database,
pipeline, or command.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any, cast

from .adapter import PersistenceScopeError
from .ownership import validate_owned_pipeline

# Execution options shared by most operations. None of these redirect an
# operation to another namespace: a session joins a transaction, a comment
# annotates the command for the profiler, a collation/hint/time-limit tune how
# the server runs the operation against this same collection.
_SHARED_OPTIONS = frozenset({"session", "comment"})
_READ_OPTIONS = _SHARED_OPTIONS | {"hint", "collation", "maxTimeMS", "let"}

_FIND_OPTIONS = _READ_OPTIONS | {
    "projection", "sort", "skip", "limit", "batch_size", "max_time_ms",
    "allow_disk_use", "no_cursor_timeout", "cursor_type", "allow_partial_results",
    "return_key", "show_record_id", "max", "min",
}
_COUNT_OPTIONS = _READ_OPTIONS | {"skip", "limit"}
_DISTINCT_OPTIONS = _READ_OPTIONS
_AGGREGATE_OPTIONS = _READ_OPTIONS | {"allowDiskUse", "batchSize", "bypassDocumentValidation"}
_FIND_AND_MODIFY_OPTIONS = _READ_OPTIONS | {
    "projection", "sort", "upsert", "return_document", "array_filters",
}
_FIND_AND_DELETE_OPTIONS = _READ_OPTIONS | {"projection", "sort"}
_INSERT_ONE_OPTIONS = _SHARED_OPTIONS | {"bypass_document_validation"}
_INSERT_MANY_OPTIONS = _SHARED_OPTIONS | {"ordered", "bypass_document_validation"}
_UPDATE_OPTIONS = _SHARED_OPTIONS | {
    "upsert", "bypass_document_validation", "collation", "array_filters", "hint", "let", "sort",
}
_REPLACE_OPTIONS = _SHARED_OPTIONS | {
    "upsert", "bypass_document_validation", "collation", "hint", "let", "sort",
}
_DELETE_OPTIONS = _SHARED_OPTIONS | {"collation", "hint", "let"}

# A filter is a match document, never a raw server command. The driver's find
# path treats a top-level ``$query`` key as the driver query command envelope and lifts
# its siblings into the command — a route to another namespace. No legitimate
# match filter uses it, so a bounded read refuses it.
_FILTER_COMMAND_ENVELOPE = frozenset({"$query"})


def _check_options(method: str, allowed: frozenset[str], kwargs: Mapping[str, Any]) -> None:
    refused = sorted(key for key in kwargs if key not in allowed)
    if refused:
        raise PersistenceScopeError(
            f"{method} does not accept option(s) {refused} on a bounded collection handle"
        )


def _check_positional(method: str, args: Sequence[Any], limit: int) -> None:
    if len(args) > limit:
        raise PersistenceScopeError(
            f"{method} accepts at most {limit} positional argument(s) on a bounded collection handle"
        )


def _check_filter_envelope(method: str, args: Sequence[Any], kwargs: Mapping[str, Any]) -> None:
    candidate = args[0] if args else kwargs.get("filter")
    if isinstance(candidate, Mapping) and any(key in candidate for key in _FILTER_COMMAND_ENVELOPE):
        raise PersistenceScopeError(
            f"{method} filter cannot carry a raw query command envelope on a bounded collection handle"
        )


class GuardedAliasCursor:
    """Cursor operations used by shipped app-owned alias repositories."""

    __slots__ = ("__cursor",)

    def __init__(self, cursor: Any) -> None:
        self.__cursor = cursor

    def sort(self, key_or_list: Any, direction: Any = None) -> GuardedAliasCursor:
        self.__cursor = self.__cursor.sort(key_or_list, direction)
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
    Every method validates its options and positional arity before forwarding,
    so a caller cannot smuggle a command field, a raw query envelope, or an extra
    positional through to the driver.
    """

    __slots__ = ("__collection",)

    def __init__(self, collection: Any) -> None:
        self.__collection = collection

    def find(self, *args: Any, **kwargs: Any) -> GuardedAliasCursor:
        _check_positional("find", args, 2)
        _check_options("find", _FIND_OPTIONS, kwargs)
        _check_filter_envelope("find", args, kwargs)
        return GuardedAliasCursor(self.__collection.find(*args, **kwargs))

    async def find_one(self, *args: Any, **kwargs: Any) -> Any:
        _check_positional("find_one", args, 2)
        _check_options("find_one", _FIND_OPTIONS, kwargs)
        _check_filter_envelope("find_one", args, kwargs)
        return await self.__collection.find_one(*args, **kwargs)

    async def insert_one(self, *args: Any, **kwargs: Any) -> Any:
        _check_positional("insert_one", args, 1)
        _check_options("insert_one", _INSERT_ONE_OPTIONS, kwargs)
        return await self.__collection.insert_one(*args, **kwargs)

    async def insert_many(self, *args: Any, **kwargs: Any) -> Any:
        _check_positional("insert_many", args, 1)
        _check_options("insert_many", _INSERT_MANY_OPTIONS, kwargs)
        return await self.__collection.insert_many(*args, **kwargs)

    async def update_one(self, *args: Any, **kwargs: Any) -> Any:
        _check_positional("update_one", args, 2)
        _check_options("update_one", _UPDATE_OPTIONS, kwargs)
        return await self.__collection.update_one(*args, **kwargs)

    async def update_many(self, *args: Any, **kwargs: Any) -> Any:
        _check_positional("update_many", args, 2)
        _check_options("update_many", _UPDATE_OPTIONS, kwargs)
        return await self.__collection.update_many(*args, **kwargs)

    async def replace_one(self, *args: Any, **kwargs: Any) -> Any:
        _check_positional("replace_one", args, 2)
        _check_options("replace_one", _REPLACE_OPTIONS, kwargs)
        return await self.__collection.replace_one(*args, **kwargs)

    async def delete_one(self, *args: Any, **kwargs: Any) -> Any:
        _check_positional("delete_one", args, 1)
        _check_options("delete_one", _DELETE_OPTIONS, kwargs)
        return await self.__collection.delete_one(*args, **kwargs)

    async def delete_many(self, *args: Any, **kwargs: Any) -> Any:
        _check_positional("delete_many", args, 1)
        _check_options("delete_many", _DELETE_OPTIONS, kwargs)
        return await self.__collection.delete_many(*args, **kwargs)

    async def count_documents(self, *args: Any, **kwargs: Any) -> Any:
        _check_positional("count_documents", args, 1)
        _check_options("count_documents", _COUNT_OPTIONS, kwargs)
        return await self.__collection.count_documents(*args, **kwargs)

    async def distinct(self, *args: Any, **kwargs: Any) -> Any:
        _check_positional("distinct", args, 2)
        _check_options("distinct", _DISTINCT_OPTIONS, kwargs)
        return await self.__collection.distinct(*args, **kwargs)

    async def find_one_and_update(self, *args: Any, **kwargs: Any) -> Any:
        _check_positional("find_one_and_update", args, 2)
        _check_options("find_one_and_update", _FIND_AND_MODIFY_OPTIONS, kwargs)
        return await self.__collection.find_one_and_update(*args, **kwargs)

    async def find_one_and_replace(self, *args: Any, **kwargs: Any) -> Any:
        _check_positional("find_one_and_replace", args, 2)
        _check_options("find_one_and_replace", _FIND_AND_MODIFY_OPTIONS, kwargs)
        return await self.__collection.find_one_and_replace(*args, **kwargs)

    async def find_one_and_delete(self, *args: Any, **kwargs: Any) -> Any:
        _check_positional("find_one_and_delete", args, 1)
        _check_options("find_one_and_delete", _FIND_AND_DELETE_OPTIONS, kwargs)
        return await self.__collection.find_one_and_delete(*args, **kwargs)

    def aggregate(
        self, pipeline: Sequence[Mapping[str, Any]], **kwargs: Any,
    ) -> GuardedAliasCursor:
        _check_options("aggregate", _AGGREGATE_OPTIONS, kwargs)
        # Motor executes lazily. Keep a detached validated pipeline so caller
        # mutation after aggregate() cannot add a lookup/union/output stage.
        stages = deepcopy(list(pipeline))
        validate_owned_pipeline(stages)
        return GuardedAliasCursor(self.__collection.aggregate(stages, **kwargs))
