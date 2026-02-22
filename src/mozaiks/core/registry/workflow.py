"""Workflow registry interface and in-memory implementation."""

from __future__ import annotations

from collections.abc import Callable
from threading import Lock, RLock
from typing import Any, Protocol, TypeVar, runtime_checkable

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


_GLOBAL_REGISTRY: InMemoryWorkflowRegistry | None = None
_GLOBAL_LOCK = Lock()


def get_workflow_registry() -> InMemoryWorkflowRegistry:
    """Return a singleton in-memory workflow registry."""

    global _GLOBAL_REGISTRY
    if _GLOBAL_REGISTRY is None:
        with _GLOBAL_LOCK:
            if _GLOBAL_REGISTRY is None:
                _GLOBAL_REGISTRY = InMemoryWorkflowRegistry()
    return _GLOBAL_REGISTRY


def reset_workflow_registry() -> None:
    """Reset singleton workflow registry (primarily for tests)."""

    global _GLOBAL_REGISTRY
    with _GLOBAL_LOCK:
        _GLOBAL_REGISTRY = InMemoryWorkflowRegistry()


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


__all__ = [
    "InMemoryWorkflowRegistry",
    "WorkflowAlreadyRegisteredError",
    "WorkflowNotFoundError",
    "WorkflowRegistry",
    "get_workflow_registry",
    "reset_workflow_registry",
    "workflow",
]
