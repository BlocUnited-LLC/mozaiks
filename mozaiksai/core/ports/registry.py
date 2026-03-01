"""Workflow and plugin registry interfaces."""

from __future__ import annotations

from collections.abc import Callable
from threading import Lock, RLock
from typing import Any, Protocol, TypeVar, runtime_checkable

# ─── Workflow Registry ─────────────────────────────────────────────

WorkflowHandler = Callable[..., Any]
WorkflowHandlerT = TypeVar("WorkflowHandlerT", bound=WorkflowHandler)


class WorkflowNotFoundError(KeyError):
    """Raised when a workflow name is not registered."""


class WorkflowAlreadyRegisteredError(KeyError):
    """Raised when workflow registration conflicts with existing name."""


@runtime_checkable
class WorkflowRegistry(Protocol):
    """Interface for workflow registration and lookup."""

    def register(
        self,
        name: str,
        handler: WorkflowHandler,
        *,
        replace: bool = False,
    ) -> WorkflowHandler: ...

    def get(self, name: str) -> WorkflowHandler: ...

    def list(self) -> list[str]: ...


class InMemoryWorkflowRegistry:
    """Thread-safe in-memory workflow registry."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._items: dict[str, WorkflowHandler] = {}

    def register(
        self,
        name: str,
        handler: WorkflowHandler,
        *,
        replace: bool = False,
    ) -> WorkflowHandler:
        if not name:
            raise ValueError("Workflow name is required.")

        with self._lock:
            if not replace and name in self._items:
                raise WorkflowAlreadyRegisteredError(name)
            self._items[name] = handler
            return handler

    def get(self, name: str) -> WorkflowHandler:
        with self._lock:
            try:
                return self._items[name]
            except KeyError as exc:
                raise WorkflowNotFoundError(name) from exc

    def list(self) -> list[str]:
        with self._lock:
            return sorted(self._items.keys())


_WORKFLOW_REGISTRY: InMemoryWorkflowRegistry | None = None
_WORKFLOW_LOCK = Lock()


def get_workflow_registry() -> InMemoryWorkflowRegistry:
    """Return a singleton in-memory workflow registry."""
    global _WORKFLOW_REGISTRY
    if _WORKFLOW_REGISTRY is None:
        with _WORKFLOW_LOCK:
            if _WORKFLOW_REGISTRY is None:
                _WORKFLOW_REGISTRY = InMemoryWorkflowRegistry()
    return _WORKFLOW_REGISTRY


def reset_workflow_registry() -> None:
    """Reset singleton workflow registry (primarily for tests)."""
    global _WORKFLOW_REGISTRY
    with _WORKFLOW_LOCK:
        _WORKFLOW_REGISTRY = InMemoryWorkflowRegistry()


def workflow(
    name: str | None = None,
    *,
    registry: WorkflowRegistry | None = None,
) -> Callable[[WorkflowHandlerT], WorkflowHandlerT]:
    """Decorator that registers workflow handlers deterministically."""

    def decorator(handler: WorkflowHandlerT) -> WorkflowHandlerT:
        resolved_name = name or handler.__name__
        target_registry = registry or get_workflow_registry()
        target_registry.register(resolved_name, handler)
        setattr(handler, "__workflow_name__", resolved_name)
        return handler

    return decorator


# ─── Plugin Registry ───────────────────────────────────────────────

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


_PLUGIN_REGISTRY: InMemoryPluginRegistry | None = None
_PLUGIN_LOCK = Lock()


def get_plugin_registry() -> InMemoryPluginRegistry:
    """Return singleton plugin registry."""
    global _PLUGIN_REGISTRY
    if _PLUGIN_REGISTRY is None:
        with _PLUGIN_LOCK:
            if _PLUGIN_REGISTRY is None:
                _PLUGIN_REGISTRY = InMemoryPluginRegistry()
    return _PLUGIN_REGISTRY


def reset_plugin_registry() -> None:
    """Reset singleton plugin registry (primarily for tests)."""
    global _PLUGIN_REGISTRY
    with _PLUGIN_LOCK:
        _PLUGIN_REGISTRY = InMemoryPluginRegistry()


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
    # Workflow
    "InMemoryWorkflowRegistry",
    "WorkflowAlreadyRegisteredError",
    "WorkflowNotFoundError",
    "WorkflowRegistry",
    "get_workflow_registry",
    "reset_workflow_registry",
    "workflow",
    # Plugin
    "InMemoryPluginRegistry",
    "PluginAlreadyRegisteredError",
    "PluginNotFoundError",
    "PluginRegistry",
    "get_plugin_registry",
    "plugin",
    "reset_plugin_registry",
]
