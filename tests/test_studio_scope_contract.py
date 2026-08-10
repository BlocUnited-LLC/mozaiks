from __future__ import annotations

import pytest
from fastapi import HTTPException

from mozaiksai.core.auth import UserPrincipal
from mozaiksai.core.studio import StudioScope, resolve_studio_scope


def _principal(**overrides) -> UserPrincipal:  # noqa: ANN001
    values = {
        "user_id": "user-1",
        "email": None,
        "name": None,
        "roles": [],
        "scopes": [],
        "raw_claims": {},
        "provider": "test",
        "app_id": None,
    }
    values.update(overrides)
    return UserPrincipal(**values)


def test_resolve_studio_scope_returns_public_value_object() -> None:
    scope = resolve_studio_scope(_principal(app_id="app-1"))

    assert scope == StudioScope(app_id="app-1", user_id="user-1")


def test_resolve_studio_scope_allows_explicit_app_when_principal_is_not_app_bound() -> None:
    scope = resolve_studio_scope(_principal(), app_id="app-2")

    assert scope.app_id == "app-2"
    assert scope.user_id == "user-1"


def test_resolve_studio_scope_rejects_app_mismatch() -> None:
    with pytest.raises(HTTPException) as exc:
        resolve_studio_scope(_principal(app_id="app-1"), app_id="app-2")

    assert exc.value.status_code == 403


def test_resolve_studio_scope_uses_default_user_for_anonymous_principal() -> None:
    scope = resolve_studio_scope(
        _principal(user_id="anonymous"),
        app_id="app-1",
        default_user_id="demo-user",
    )

    assert scope == StudioScope(app_id="app-1", user_id="demo-user")
