"""Runtime-safe context variables adapter.

Provides a unified container interface independent of AG2's internal implementation.
If the vendor ContextVariables class is missing methods or unavailable, falls back
to a simple dict-backed version with the same minimal API surface.

Public factory: create_context_container(initial: Optional[dict]) -> object
Returned object supports:
  .get(key, default=None)
  .set(key, value)
  .remove(key) -> bool
  .keys() -> Iterable[str]
  .contains(key) -> bool
  .data (property) -> raises AttributeError; removed. Use snapshot() for serialization.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from .authority import RUNTIME_SYSTEM_WRITER, ContextAuthorityPolicy, ContextWriterId
from .frozen import detach, freeze

logger = logging.getLogger(__name__)


class _RuntimeContextVariables:
    def __init__(
        self,
        initial: dict[str, Any] | None = None,
        chat_id: str | None = None,
        app_id: str | None = None,
        authority_policy: ContextAuthorityPolicy | None = None,
        writer_id: ContextWriterId = RUNTIME_SYSTEM_WRITER,
    ) -> None:
        self._data: dict[str, Any] = detach(initial or {})
        self._chat_id = chat_id
        self._app_id = app_id
        self._mozaiks_context_authority_policy = authority_policy
        self._mozaiks_context_writer_id = writer_id

    def get(self, key: str, default: Any | None = None) -> Any:
        return freeze(self._data.get(key, default))

    def set(self, key: str, value: Any) -> None:
        policy = getattr(self, "_mozaiks_context_authority_policy", None)
        writer_id = getattr(self, "_mozaiks_context_writer_id", RUNTIME_SYSTEM_WRITER)
        if policy is not None:
            policy.require_can_write(key, writer_id=writer_id, operation="set")
        self._data[key] = detach(value)
        
        # Auto-persist if context is bound to a session
        if self._chat_id and self._app_id:
            try:
                import asyncio

                from mozaiksai.core.data.persistence.persistence_manager import (
                    AG2PersistenceManager,
                )
                # Fire and forget persistence to avoid blocking the runtime call path.
                # Errors are logged at WARNING so operators can detect persistence drift
                # without crashing the active workflow.
                pm = AG2PersistenceManager()
                _task = asyncio.create_task(pm.persist_context_variables(
                    chat_id=self._chat_id,
                    app_id=self._app_id,
                    variables=self.snapshot(),
                ))
                _task.add_done_callback(
                    lambda t: logger.warning(
                        "CONTEXT_PERSIST_FAILED chat=%s app=%s: %s",
                        self._chat_id,
                        self._app_id,
                        t.exception(),
                    )
                    if not t.cancelled() and t.exception() is not None
                    else None
                )
            except Exception as exc:
                logger.warning(
                    "CONTEXT_PERSIST_TASK_CREATION_FAILED chat=%s app=%s: %s",
                    self._chat_id,
                    self._app_id,
                    exc,
                )

    def remove(self, key: str) -> bool:
        policy = getattr(self, "_mozaiks_context_authority_policy", None)
        writer_id = getattr(self, "_mozaiks_context_writer_id", RUNTIME_SYSTEM_WRITER)
        if policy is not None:
            policy.require_can_write(key, writer_id=writer_id, operation="delete")
        removed = self._data.pop(key, None)
        return removed is not None

    def keys(self) -> Iterable[str]:  # noqa: D401
        return self._data.keys()

    def contains(self, key: str) -> bool:
        return key in self._data

    def snapshot(self) -> dict[str, Any]:
        data = detach(self._data)
        return data if isinstance(data, dict) else {}

    def to_dict(self) -> dict[str, Any]:
        return self.snapshot()

    @property
    def data(self) -> dict[str, Any]:
        raise AttributeError(
            "_RuntimeContextVariables.data has been removed to prevent mutable-reference "
            "exposure. Use snapshot() for detached serialization."
        )


def create_context_container(
    initial: dict[str, Any] | None = None,
    chat_id: str | None = None,
    app_id: str | None = None,
    authority_policy: ContextAuthorityPolicy | None = None,
    writer_id: ContextWriterId = RUNTIME_SYSTEM_WRITER,
):
    return _RuntimeContextVariables(
        initial=initial,
        chat_id=chat_id,
        app_id=app_id,
        authority_policy=authority_policy,
        writer_id=writer_id,
    )

__all__ = ["create_context_container"]

