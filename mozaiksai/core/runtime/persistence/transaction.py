"""Transactions for declared AppData aliases, without exposing driver sessions."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any, TypeVar

from pymongo import ReadPreference
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from .adapter import PersistenceScopeError
from .alias_collection import _OPERATIONS, GuardedAliasCollection
from .app_data import AppData, app_data_from_context
from .mongo import MongoPersistenceContext

_Result = TypeVar("_Result")
_active_transaction: ContextVar[bool] = ContextVar("app_data_transaction", default=False)


class _Attempt:
    def __init__(self, session: Any) -> None:
        self.session = session
        self.task = asyncio.current_task()
        self.active = True

    def check(self) -> None:
        if not self.active:
            raise PersistenceScopeError("Transaction handles are unavailable outside their callback attempt")
        if asyncio.current_task() is not self.task:
            raise PersistenceScopeError("Transaction operations must run sequentially in the callback task")

    def options(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        self.check()
        options = {str.__str__(key): value for key, value in kwargs.items()}
        if "session" in options:
            raise PersistenceScopeError("Transaction sessions are runtime-owned")
        return {**options, "session": self.session}


class _TransactionCursor:
    __slots__ = ("__cursor", "__attempt")

    def __init__(self, cursor: Any, attempt: _Attempt) -> None:
        self.__cursor = cursor
        self.__attempt = attempt

    def sort(self, key_or_list: Any, direction: Any = None) -> _TransactionCursor:
        self.__attempt.check()
        self.__cursor = self.__cursor.sort(key_or_list, direction)
        return self

    def limit(self, limit: int) -> _TransactionCursor:
        self.__attempt.check()
        self.__cursor = self.__cursor.limit(limit)
        return self

    def skip(self, skip: int) -> _TransactionCursor:
        self.__attempt.check()
        self.__cursor = self.__cursor.skip(skip)
        return self

    async def to_list(self, length: int | None = None) -> list[Any]:
        self.__attempt.check()
        rows = await self.__cursor.to_list(length=length)
        self.__attempt.check()
        return rows  # type: ignore[no-any-return]

    def __aiter__(self) -> _TransactionCursor:
        self.__attempt.check()
        return self

    async def __anext__(self) -> Any:
        self.__attempt.check()
        row = await self.__cursor.__anext__()
        self.__attempt.check()
        return row


class _TransactionCollection:
    __slots__ = ("__collection", "__attempt")

    def __init__(self, collection: Any, attempt: _Attempt) -> None:
        # Keep the canonical argument/pipeline boundary even when an older app's
        # literal_collection returns a raw alias. The original resolver still
        # owns namespace and read/write permission.
        self.__collection = GuardedAliasCollection(collection)
        self.__attempt = attempt

    def __getattr__(self, name: str) -> Any:
        if name not in _OPERATIONS:
            raise AttributeError(name)
        self.__attempt.check()
        method = getattr(self.__collection, name)
        if name in {"find", "aggregate"}:
            def cursor_operation(*args: Any, **kwargs: Any) -> _TransactionCursor:
                cursor = method(*args, **self.__attempt.options(kwargs))
                return _TransactionCursor(cursor, self.__attempt)

            return cursor_operation

        async def operation(*args: Any, **kwargs: Any) -> Any:
            result = await method(*args, **self.__attempt.options(kwargs))
            self.__attempt.check()
            return result

        return operation


async def run_app_data_transaction(
    ctx: Any,
    callback: Callable[[AppData], Awaitable[_Result]],
) -> _Result:
    """Run repository operations over declared aliases in one Mongo transaction.

    ``ctx`` must carry the runtime's injected ``MongoPersistenceContext``. The
    callback receives a new AppData view on every attempt, with the same alias
    snapshot and namespace/ownership restrictions. It receives no driver client,
    database or session. The original context and its cached AppData are unchanged.

    All database operations must use that callback view, sequentially in the
    callback task. Collection handles and cursors expire when the attempt ends.
    Existing alias repositories remain responsible for explicit app/domain query
    scope; a transaction does not grant access or infer authorization.

    Mongo requires a replica set or sharded cluster. Unsupported topology and
    database failures propagate; this helper never falls back to separate writes.
    Retry behavior is delegated to Motor's ``with_transaction``; an uncertain
    commit can propagate immediately (including with Motor 3.7.1), without a
    commit retry. The callback may run again: allocate stable IDs beforehand,
    perform no external side effects, and emit events only after this call returns.
    An exception is not proof that an uncertain commit did not happen; reconcile
    by the stable operation ID. This API does not transact generated module
    ``ctx.persistence.collection`` operations or automatically replace ``ctx``.
    """
    if _active_transaction.get():
        raise PersistenceScopeError("Nested app-data transactions are not supported")
    persistence = getattr(ctx, "persistence", None)
    if not isinstance(persistence, MongoPersistenceContext):
        raise PersistenceScopeError("Transactions require runtime-injected Mongo persistence")
    if str(getattr(ctx, "app_id", "") or "").strip() != persistence.app_id:
        raise PersistenceScopeError("Transaction app identity must match the persistence context")
    source = app_data_from_context(ctx)
    if not isinstance(source, AppData) or not source.aliases:
        raise PersistenceScopeError("Transactions require declared AppData aliases")
    aliases = source.aliases
    # Client access stays inside the owning runtime; callers cannot select a
    # client/session or obtain a broader collection through this API.
    client = persistence._client_handle()
    active_token = _active_transaction.set(True)
    try:
        async with await client.start_session() as session:
            async def execute(attempt_session: Any) -> _Result:
                attempt = _Attempt(attempt_session)

                def resolve(name: str) -> _TransactionCollection:
                    attempt.check()
                    return _TransactionCollection(persistence.literal_collection(name), attempt)

                view = AppData(aliases=aliases, collection_resolver=resolve)
                try:
                    return await callback(view)
                finally:
                    attempt.active = False

            return await session.with_transaction(
                execute,
                read_concern=ReadConcern("snapshot"),
                write_concern=WriteConcern("majority"),
                read_preference=ReadPreference.PRIMARY,
                max_commit_time_ms=10_000,
            )  # type: ignore[no-any-return]
    finally:
        _active_transaction.reset(active_token)


__all__ = ["run_app_data_transaction"]
