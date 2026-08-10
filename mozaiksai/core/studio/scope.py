from __future__ import annotations

from dataclasses import dataclass

from mozaiksai.core.auth import UserPrincipal
from mozaiksai.core.auth.dependencies import resolve_scope_from_principal


@dataclass(frozen=True)
class StudioScope:
    """Resolved app/user scope for Studio-compatible management endpoints."""

    app_id: str
    user_id: str


def resolve_studio_scope(
    principal: UserPrincipal,
    *,
    app_id: str | None = None,
    user_id: str | None = None,
    default_user_id: str | None = None,
    default_app_id: str = "default",
) -> StudioScope:
    """Resolve and validate the Studio app/user scope.

    This is the public counterpart to the historical private helper in
    ``mozaiksai.hosts.studio``. It exposes only the app/user scope tuple that
    app-local hosts need, while delegating validation to the existing auth
    principal scope rules.
    """

    resolved_app_id, resolved_user_id = resolve_scope_from_principal(
        principal,
        app_id=app_id,
        user_id=user_id,
        default_user_id=default_user_id,
        default_app_id=default_app_id,
    )
    return StudioScope(app_id=resolved_app_id, user_id=resolved_user_id)
