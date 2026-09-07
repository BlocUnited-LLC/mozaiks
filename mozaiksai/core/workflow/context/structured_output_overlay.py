"""Turn-local read-only ``structured_output`` projection over a live context.

The documented public auto-tool contract is::

    data = context_variables.get("structured_output")

``structured_output`` is NOT ordinary application workflow state. It is a
runtime-owned TRANSIENT input projection carrying the exact validated
``structured_data`` for the current auto-tool invocation. Applications never
declare it in ``context_variables.yaml``, no writer may set or replace it, and
it must not survive the invocation as durable, replayable, or agent-visible
state.

:class:`StructuredOutputOverlay` is the smallest reusable context abstraction
that makes the documented contract true without seizing application context
authority: it wraps the live workflow context (a pattern context bridge or an
ephemeral runtime container), serves ``structured_output`` reads from the
runtime-held exact payload, fails every mutation of that key closed, and
delegates all other keys — reads, writes, snapshots, and persistence
extraction — to the underlying context unchanged. Snapshots and iteration
never include the projection, so persistence and replay cannot inherit it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, cast

from .authority import ContextAuthorityError
from .frozen import detach, freeze

#: The documented public auto-tool context key for validated agent output.
STRUCTURED_OUTPUT_KEY = "structured_output"


class StructuredOutputWriteError(ContextAuthorityError):
    """Raised when anything attempts to mutate the transient runtime projection."""


def _reject_write(operation: str) -> None:
    raise StructuredOutputWriteError(
        f"context_variables[{STRUCTURED_OUTPUT_KEY!r}] is a runtime-owned "
        f"read-only projection of the validated structured output; {operation} "
        "is not allowed. Save selected data under a declared application key "
        "instead."
    )


class StructuredOutputOverlay:
    """Live workflow context plus a read-only ``structured_output`` overlay."""

    def __init__(self, base: Any, structured_data: Mapping[str, Any]) -> None:
        if not isinstance(structured_data, Mapping):
            raise TypeError("structured_data must be a mapping of validated output")
        self._base = base
        self._structured_data: dict[str, Any] = detach(dict(structured_data))

    # -- reads ---------------------------------------------------------------

    def get(self, key: str, default: Any | None = None) -> Any:
        if key == STRUCTURED_OUTPUT_KEY:
            return freeze(self._structured_data)
        base_get = getattr(self._base, "get", None)
        if callable(base_get):
            return base_get(key, default)
        return default

    def contains(self, key: str) -> bool:
        if key == STRUCTURED_OUTPUT_KEY:
            return True
        base_contains = getattr(self._base, "contains", None)
        if callable(base_contains):
            return bool(base_contains(key))
        try:
            return key in self._base
        except TypeError:
            return False

    def __contains__(self, key: str) -> bool:
        return self.contains(key)

    def keys(self) -> Iterable[str]:
        """Underlying context keys only — the projection is not enumerable state."""
        base_keys = getattr(self._base, "keys", None)
        if callable(base_keys):
            return cast(Iterable[str], base_keys())
        return ()

    # -- writes --------------------------------------------------------------

    def set(self, key: str, value: Any) -> None:
        if key == STRUCTURED_OUTPUT_KEY:
            _reject_write("set")
        self._base.set(key, value)

    def remove(self, key: str) -> bool:
        if key == STRUCTURED_OUTPUT_KEY:
            _reject_write("delete")
        base_remove = getattr(self._base, "remove", None)
        if callable(base_remove):
            return bool(base_remove(key))
        return False

    def __setitem__(self, key: str, value: Any) -> None:
        self.set(key, value)

    def __delitem__(self, key: str) -> None:
        self.remove(key)

    # -- snapshots / persistence extraction ----------------------------------

    def _base_snapshot(self) -> dict[str, Any]:
        for method_name in ("snapshot", "to_dict"):
            method = getattr(self._base, method_name, None)
            if callable(method):
                data = method()
                if isinstance(data, dict):
                    return data
        if isinstance(self._base, Mapping):
            return dict(self._base)
        return {}

    def snapshot(self) -> dict[str, Any]:
        """Durable state only: the runtime projection is never part of a snapshot."""
        data = self._base_snapshot()
        data.pop(STRUCTURED_OUTPUT_KEY, None)
        return data

    def to_dict(self) -> dict[str, Any]:
        return self.snapshot()

    @property
    def data(self) -> dict[str, Any]:
        raise AttributeError(
            "StructuredOutputOverlay.data is not exposed; use snapshot() for "
            "detached serialization of the underlying context."
        )


__all__ = [
    "STRUCTURED_OUTPUT_KEY",
    "StructuredOutputOverlay",
    "StructuredOutputWriteError",
]
