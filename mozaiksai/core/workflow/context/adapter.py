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
  .data (property) -> underlying dict (for logging only)
"""
from __future__ import annotations
from typing import Any, Iterable

try:  # pragma: no cover - vendor optional
    from autogen.agentchat.group import ContextVariables as VendorContextVariables  # type: ignore
except Exception:  # pragma: no cover
    VendorContextVariables = None  # type: ignore


class _RuntimeContextVariables:
    def __init__(self, initial: dict[str, Any] | None = None, chat_id: str | None = None, app_id: str | None = None) -> None:
        # Keep a shallow copy for local reads while optionally tracking the original
        self._data: dict[str, Any] = dict(initial or {})
        self._backing: dict[str, Any] | None = initial if isinstance(initial, dict) else None
        self._chat_id = chat_id
        self._app_id = app_id

    def get(self, key: str, default: Any | None = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        if self._backing is not None:
            self._backing[key] = value
        
        # Auto-persist if context is bound to a session
        if self._chat_id and self._app_id:
            try:
                import asyncio
                from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager
                # Fire and forget persistence to avoid blocking
                pm = AG2PersistenceManager()
                asyncio.create_task(pm.persist_context_variables(
                    chat_id=self._chat_id,
                    app_id=self._app_id,
                    variables=self._data
                ))
            except Exception:
                pass # Fail silently on persistence to not break runtime flow

    def remove(self, key: str) -> bool:
        removed = self._data.pop(key, None)
        if self._backing is not None:
            self._backing.pop(key, None)
        return removed is not None

    def keys(self) -> Iterable[str]:  # noqa: D401
        return self._data.keys()

    def contains(self, key: str) -> bool:
        return key in self._data

    @property
    def data(self) -> dict[str, Any]:  # for logging only
        return self._data


def _vendor_is_usable(obj: Any) -> bool:
    required = ("get", "set", "remove", "keys", "contains")
    return all(hasattr(obj, n) for n in required)


def create_context_container(initial: dict[str, Any] | None = None, chat_id: str | None = None, app_id: str | None = None):
    if VendorContextVariables:
        try:
            inst = VendorContextVariables(data=initial or {})  # type: ignore[call-arg]
            if _vendor_is_usable(inst):
                return inst
        except Exception:
            pass
    return _RuntimeContextVariables(initial=initial, chat_id=chat_id, app_id=app_id)

__all__ = ["create_context_container"]

