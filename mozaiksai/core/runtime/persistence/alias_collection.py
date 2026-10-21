"""Bounded Mongo alias operations for apps containing owner-scoped collections.

App-wide aliases retain their own query semantics. Their handles and cursors
cannot expose another collection or run a cross-collection aggregation.

A bounded handle accepts only the arguments the installed driver defines for
each operation. The driver passes a keyword it does not recognise into the
server command it builds, so an unchecked keyword, or a raw query command
envelope in a read filter, could change what that command addresses. Every
method therefore binds its call against an explicit per-operation contract
before the driver builds a command:

- the driver's primary arguments (``filter``, ``update``, ``replacement``,
  ``document``, ``documents``, ``key``, ``pipeline``, and ``projection`` for
  reads) may be given positionally or by the driver's own keyword, never both;
- every other keyword must be on that operation's allow-list, which holds only
  options the driver defines for it (sessions, comments, sorts, upserts, index
  hints, time limits and the like); none of them can name a different
  collection, database, pipeline, or command;
- a read filter's top level and an aggregation pipeline are read once into
  plain containers, and that copy is both what is checked and what the driver
  receives, so the checked content and the sent content cannot differ.

Anything else (an unexpected option, a surplus positional argument, a primary
argument given twice, a raw query envelope) fails closed with
``PersistenceScopeError`` and never reaches the server.

This is least-privilege plumbing: it keeps a module's mistakes inside its own
collection. It is not a sandbox. Code running inside the host process can reach
the driver by other means, so code that is not trusted must not be loaded into
the host in the first place.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, cast

from .adapter import PersistenceScopeError
from .ownership import canonical_pipeline, validate_owned_pipeline

# Options below are the ones the installed driver defines for each operation: a
# named parameter of the driver method, or a keyword its documentation lists for
# a method that takes command options. None of them redirects an operation to
# another namespace: a session joins a transaction, a comment annotates the
# command for the profiler, and a collation, hint or time limit tunes how the
# server runs the operation against this same collection.
_SHARED = frozenset({"session", "comment"})
_FIND = _SHARED | {
    "sort", "skip", "limit", "batch_size", "max_time_ms", "allow_disk_use",
    "no_cursor_timeout", "cursor_type", "allow_partial_results", "return_key",
    "show_record_id", "max", "min", "hint", "collation", "let",
}
_WRITE = _SHARED | {"collation", "hint", "let"}
_UPDATE = _WRITE | {"upsert", "bypass_document_validation"}
_FIND_AND_MODIFY = _SHARED | {"projection", "sort", "hint", "let", "collation", "maxTimeMS"}

# operation -> (primary arguments in the driver's positional order, options).
_OPERATIONS: dict[str, tuple[tuple[str, ...], frozenset[str]]] = {
    "find": (("filter", "projection"), _FIND),
    "find_one": (("filter", "projection"), _FIND),
    "insert_one": (("document",), _SHARED | {"bypass_document_validation"}),
    "insert_many": (("documents",), _SHARED | {"ordered", "bypass_document_validation"}),
    "update_one": (("filter", "update"), _UPDATE | {"array_filters", "sort"}),
    "update_many": (("filter", "update"), _UPDATE | {"array_filters"}),
    "replace_one": (("filter", "replacement"), _UPDATE | {"sort"}),
    "delete_one": (("filter",), _WRITE),
    "delete_many": (("filter",), _WRITE),
    "count_documents": (("filter",), _SHARED | {"skip", "limit", "maxTimeMS", "collation", "hint"}),
    "distinct": (("key", "filter"), _SHARED | {"hint", "maxTimeMS", "collation"}),
    "find_one_and_update": (
        ("filter", "update"), _FIND_AND_MODIFY | {"upsert", "return_document", "array_filters"},
    ),
    "find_one_and_replace": (("filter", "replacement"), _FIND_AND_MODIFY | {"upsert", "return_document"}),
    "find_one_and_delete": (("filter",), _FIND_AND_MODIFY),
    "aggregate": (
        ("pipeline",),
        _SHARED | {"let", "allowDiskUse", "maxTimeMS", "batchSize", "collation", "bypassDocumentValidation"},
    ),
}

# A filter is a match document, never a raw server command. No legitimate match
# filter carries the driver's query command envelope key, so a bounded read
# refuses it.
_FILTER_COMMAND_ENVELOPE = frozenset({"$query"})


def _bind(method: str, args: tuple[Any, ...], kwargs: Mapping[str, Any]) -> tuple[list[Any], dict[str, Any]]:
    """Check one call against its operation contract and return the call to forward.

    The call keeps the shape the caller chose: a positional argument stays
    positional and a keyword stays a keyword.
    """
    primaries, options = _OPERATIONS[method]
    # Decide on a keyword's content, not on how a str subclass compares or hashes.
    named = {str.__str__(key): value for key, value in kwargs.items()}
    if len(args) > len(primaries):
        raise PersistenceScopeError(
            f"{method} accepts at most {len(primaries)} positional argument(s) on a bounded collection handle"
        )
    twice = sorted(name for name in primaries[:len(args)] if name in named)
    if twice:
        raise PersistenceScopeError(
            f"{method} received {twice} both positionally and by keyword on a bounded collection handle"
        )
    refused = sorted(key for key in named if key not in options and key not in primaries)
    if refused:
        raise PersistenceScopeError(
            f"{method} does not accept option(s) {refused} on a bounded collection handle"
        )
    return list(args), named


def _replace_primary(
    method: str, name: str, args: list[Any], named: dict[str, Any], checked: Callable[[Any], Any],
) -> bool:
    """Swap one primary argument, wherever the caller put it, for its checked copy."""
    index = _OPERATIONS[method][0].index(name)
    if index < len(args):
        args[index] = checked(args[index])
    elif name in named:
        named[name] = checked(named[name])
    else:
        return False
    return True


def _match_filter(method: str) -> Callable[[Any], Any]:
    def checked(candidate: Any) -> Any:
        if not isinstance(candidate, Mapping):
            return candidate
        # One read. A lazy cursor builds its command from the filter long after
        # this check, so the driver gets the copy that was checked, not an
        # object the caller can still change.
        detached = {
            str.__str__(key) if isinstance(key, str) else key: value
            for key, value in list(candidate.items())
        }
        if any(key in detached for key in _FILTER_COMMAND_ENVELOPE):
            raise PersistenceScopeError(
                f"{method} filter cannot carry a raw query command envelope on a bounded collection handle"
            )
        return detached

    return checked


def _owned_pipeline(pipeline: Any) -> list[dict[str, Any]]:
    stages = canonical_pipeline(pipeline)
    validate_owned_pipeline(stages)
    return stages


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
    Every method binds its call against its operation contract before forwarding,
    so a caller cannot pass a command field, a raw query envelope, or an extra
    positional through to the driver.
    """

    __slots__ = ("__collection",)

    def __init__(self, collection: Any) -> None:
        self.__collection = collection

    def find(self, *args: Any, **kwargs: Any) -> GuardedAliasCursor:
        call, named = _bind("find", args, kwargs)
        _replace_primary("find", "filter", call, named, _match_filter("find"))
        return GuardedAliasCursor(self.__collection.find(*call, **named))

    async def find_one(self, *args: Any, **kwargs: Any) -> Any:
        call, named = _bind("find_one", args, kwargs)
        _replace_primary("find_one", "filter", call, named, _match_filter("find_one"))
        return await self.__collection.find_one(*call, **named)

    async def insert_one(self, *args: Any, **kwargs: Any) -> Any:
        call, named = _bind("insert_one", args, kwargs)
        return await self.__collection.insert_one(*call, **named)

    async def insert_many(self, *args: Any, **kwargs: Any) -> Any:
        call, named = _bind("insert_many", args, kwargs)
        return await self.__collection.insert_many(*call, **named)

    async def update_one(self, *args: Any, **kwargs: Any) -> Any:
        call, named = _bind("update_one", args, kwargs)
        return await self.__collection.update_one(*call, **named)

    async def update_many(self, *args: Any, **kwargs: Any) -> Any:
        call, named = _bind("update_many", args, kwargs)
        return await self.__collection.update_many(*call, **named)

    async def replace_one(self, *args: Any, **kwargs: Any) -> Any:
        call, named = _bind("replace_one", args, kwargs)
        return await self.__collection.replace_one(*call, **named)

    async def delete_one(self, *args: Any, **kwargs: Any) -> Any:
        call, named = _bind("delete_one", args, kwargs)
        return await self.__collection.delete_one(*call, **named)

    async def delete_many(self, *args: Any, **kwargs: Any) -> Any:
        call, named = _bind("delete_many", args, kwargs)
        return await self.__collection.delete_many(*call, **named)

    async def count_documents(self, *args: Any, **kwargs: Any) -> Any:
        call, named = _bind("count_documents", args, kwargs)
        return await self.__collection.count_documents(*call, **named)

    async def distinct(self, *args: Any, **kwargs: Any) -> Any:
        call, named = _bind("distinct", args, kwargs)
        return await self.__collection.distinct(*call, **named)

    async def find_one_and_update(self, *args: Any, **kwargs: Any) -> Any:
        call, named = _bind("find_one_and_update", args, kwargs)
        return await self.__collection.find_one_and_update(*call, **named)

    async def find_one_and_replace(self, *args: Any, **kwargs: Any) -> Any:
        call, named = _bind("find_one_and_replace", args, kwargs)
        return await self.__collection.find_one_and_replace(*call, **named)

    async def find_one_and_delete(self, *args: Any, **kwargs: Any) -> Any:
        call, named = _bind("find_one_and_delete", args, kwargs)
        return await self.__collection.find_one_and_delete(*call, **named)

    def aggregate(self, *args: Any, **kwargs: Any) -> GuardedAliasCursor:
        call, named = _bind("aggregate", args, kwargs)
        # Motor executes lazily. The driver gets the plain copy that was
        # validated, so neither a later change by the caller nor a stage that
        # reads differently a second time can add a lookup/union/output stage.
        if not _replace_primary("aggregate", "pipeline", call, named, _owned_pipeline):
            raise PersistenceScopeError("aggregate requires a pipeline on a bounded collection handle")
        return GuardedAliasCursor(self.__collection.aggregate(*call, **named))
