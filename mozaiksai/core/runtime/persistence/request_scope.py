"""Operation-time identity for persistence handles cached by module instances."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from .adapter import PersistencePrincipal


@dataclass
class _DispatchScope:
    app_id: str
    principal: PersistencePrincipal | None
    active: bool = True


_current_scope: ContextVar[_DispatchScope | None] = ContextVar("module_persistence_scope", default=None)


@contextmanager
def bind_persistence_principal(app_id: str, principal: PersistencePrincipal | None) -> Iterator[None]:
    """Bind one execution and revoke authority in inherited tasks when it ends."""
    scope = _DispatchScope(app_id=app_id, principal=principal)
    token = _current_scope.set(scope)
    try:
        yield
    finally:
        scope.active = False
        _current_scope.reset(token)


def current_persistence_principal(app_id: str) -> PersistencePrincipal | None:
    scope = _current_scope.get()
    if scope is None or not scope.active or scope.app_id != app_id:
        return None
    return scope.principal
