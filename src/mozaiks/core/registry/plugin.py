"""Plugin registry interface and in-memory implementation."""

from __future__ import annotations

from collections.abc import Callable
from threading import Lock, RLock
from typing import Any, Protocol, TypeVar, runtime_checkable

PluginValue = Any
PluginValueT = TypeVar("PluginValueT")


class PluginNotFoundError(KeyError):
    """Raised when plugin name is not found."""


class PluginAlreadyRegisteredError(KeyError):
    """Raised when plugin name conflicts with existing registration."""


@runtime_checkable
class PluginRegistry(Protocol):
    """Interface for plugin registration and lookup."""

    def register(self, name: str, value: PluginValue, *, replace: bool = False) -> PluginValue: ...

    def get(self, name: str) -> PluginValue: ...

    def list(self) -> list[str]: ...


class InMemoryPluginRegistry:
    """Thread-safe in-memory plugin registry."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._items: dict[str, PluginValue] = {}

    def register(self, name: str, value: PluginValue, *, replace: bool = False) -> PluginValue:
        if not name:
            raise ValueError("Plugin name is required.")

        with self._lock:
            if not replace and name in self._items:
                raise PluginAlreadyRegisteredError(name)
            self._items[name] = value
            return value

    def get(self, name: str) -> PluginValue:
        with self._lock:
            try:
                return self._items[name]
            except KeyError as exc:
                raise PluginNotFoundError(name) from exc

    def list(self) -> list[str]:
        with self._lock:
            return sorted(self._items.keys())


_GLOBAL_REGISTRY: InMemoryPluginRegistry | None = None
_GLOBAL_LOCK = Lock()


def get_plugin_registry() -> InMemoryPluginRegistry:
    """Return singleton plugin registry."""

    global _GLOBAL_REGISTRY
    if _GLOBAL_REGISTRY is None:
        with _GLOBAL_LOCK:
            if _GLOBAL_REGISTRY is None:
                _GLOBAL_REGISTRY = InMemoryPluginRegistry()
    return _GLOBAL_REGISTRY


def reset_plugin_registry() -> None:
    """Reset singleton plugin registry (primarily for tests)."""

    global _GLOBAL_REGISTRY
    with _GLOBAL_LOCK:
        _GLOBAL_REGISTRY = InMemoryPluginRegistry()


def plugin(
    name: str | None = None,
    *,
    registry: PluginRegistry | None = None,
) -> Callable[[PluginValueT], PluginValueT]:
    """Decorator that registers plugin classes/functions by name."""

    def decorator(value: PluginValueT) -> PluginValueT:
        resolved_name = name or getattr(value, "__name__", value.__class__.__name__)
        target_registry = registry or get_plugin_registry()
        target_registry.register(resolved_name, value)
        setattr(value, "__plugin_name__", resolved_name)
        return value

    return decorator


__all__ = [
    "InMemoryPluginRegistry",
    "PluginAlreadyRegisteredError",
    "PluginNotFoundError",
    "PluginRegistry",
    "get_plugin_registry",
    "plugin",
    "reset_plugin_registry",
]
