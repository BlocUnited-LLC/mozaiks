"""Operation-time identity for persistence handles cached by module instances."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .adapter import PersistencePrincipal

if TYPE_CHECKING:
    from .workflow_result import ResultRecordSubject


@dataclass
class _DispatchScope:
    app_id: str
    principal: PersistencePrincipal | None
    module_id: str | None = None
    action_id: str | None = None
    result_record_key: str | None = None
    insert_attempts: int = 0
    inserted_subject: ResultRecordSubject | None = None
    subject_issued: bool = False
    active: bool = True


_current_scope: ContextVar[_DispatchScope | None] = ContextVar("module_persistence_scope", default=None)


@contextmanager
def bind_persistence_principal(
    app_id: str, principal: PersistencePrincipal | None, *,
    result_record_key: str | None = None, module_id: str | None = None, action_id: str | None = None,
) -> Iterator[None]:
    """Bind one execution and revoke authority in inherited tasks when it ends."""
    scope = _DispatchScope(
        app_id=app_id, principal=principal, result_record_key=result_record_key, module_id=module_id, action_id=action_id,
    )
    token = _current_scope.set(scope)
    try:
        yield
    finally:
        scope.active = False
        _current_scope.reset(token)


def _active_scope(app_id: str) -> _DispatchScope | None:
    scope = _current_scope.get()
    if scope is None or not scope.active or scope.app_id != app_id:
        return None
    return scope


def current_persistence_principal(app_id: str) -> PersistencePrincipal | None:
    scope = _active_scope(app_id)
    return scope.principal if scope is not None else None
