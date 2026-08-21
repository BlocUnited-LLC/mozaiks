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
from collections.abc import Iterable, Mapping
from typing import Any

from .authority import (
    PERSISTED_REPLAY_WRITER,
    RUNTIME_SYSTEM_WRITER,
    ContextAuthorityError,
    ContextAuthorityPolicy,
    ContextWriterId,
)
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
        self.__data: dict[str, Any] = detach(initial or {})
        self._chat_id = chat_id
        self._app_id = app_id
        self._mozaiks_context_authority_policy = authority_policy
        self._mozaiks_context_writer_id = writer_id

    def get(self, key: str, default: Any | None = None) -> Any:
        return freeze(self.__data.get(key, default))

    def set(self, key: str, value: Any) -> None:
        policy = getattr(self, "_mozaiks_context_authority_policy", None)
        writer_id = getattr(self, "_mozaiks_context_writer_id", RUNTIME_SYSTEM_WRITER)
        if policy is not None:
            policy.require_can_write(key, writer_id=writer_id, operation="set")
        self.__data[key] = detach(value)

    def remove(self, key: str) -> bool:
        policy = getattr(self, "_mozaiks_context_authority_policy", None)
        writer_id = getattr(self, "_mozaiks_context_writer_id", RUNTIME_SYSTEM_WRITER)
        if policy is not None:
            policy.require_can_write(key, writer_id=writer_id, operation="delete")
        removed = self.__data.pop(key, None)
        return removed is not None

    def keys(self) -> Iterable[str]:  # noqa: D401
        return self.__data.keys()

    def contains(self, key: str) -> bool:
        return key in self.__data

    def snapshot(self) -> dict[str, Any]:
        data = detach(self.__data)
        return data if isinstance(data, dict) else {}

    def _hydrate_persisted_context(
        self,
        values: Mapping[str, Any],
    ) -> None:
        """Validate and atomically hydrate a persisted replay snapshot."""

        policy = self._mozaiks_context_authority_policy
        if not isinstance(policy, ContextAuthorityPolicy):
            raise ContextAuthorityError("context_authority.replay_policy_unavailable")

        diagnostics: list[str] = []
        accepted = policy.filter_for_replay(
            values,
            writer_id=PERSISTED_REPLAY_WRITER,
            diagnostics=diagnostics,
        )
        prepared = detach(accepted)
        if not isinstance(prepared, dict):  # pragma: no cover - detach preserves mappings
            raise ContextAuthorityError("context_authority.invalid_replay_snapshot")

        new_data = dict(self.__data)
        new_data.update(prepared)
        self.__data = new_data

        for diagnostic in diagnostics:
            logger.debug("[CONTEXT_REPLAY] %s", diagnostic)

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


def _hydrate_persisted_context(
    context: Any,
    values: Mapping[str, Any],
) -> None:
    """Trusted runtime entry point for persisted loader-context hydration."""

    if type(context) is not _RuntimeContextVariables:
        raise TypeError("persisted context hydration requires the canonical runtime context container")
    context._hydrate_persisted_context(values)

__all__ = ["create_context_container"]

