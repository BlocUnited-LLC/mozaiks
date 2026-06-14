"""Unit and integration tests for FastAPI auth dependencies.

Tests UserPrincipal helpers, validate_user_id_against_principal,
validate_path_app_id/validate_path_chat_id, and FastAPI dependency
integration using TestClient with a mocked auth adapter.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from mozaiksai.core.auth.adapters.base import AuthError, UserClaims
from mozaiksai.core.auth.dependencies import (
    UserPrincipal,
    _extract_token,
    require_role,
    require_user,
    validate_path_app_id,
    validate_path_chat_id,
    validate_user_id_against_principal,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _principal(**overrides: Any) -> UserPrincipal:
    defaults: dict[str, Any] = {
        "user_id": "user-123",
        "email": "alice@example.com",
        "name": "Alice",
        "roles": ["user"],
        "scopes": ["openid", "read"],
        "raw_claims": {},
        "provider": "keycloak",
        "app_id": None,
        "chat_id": None,
        "tenant_id": None,
    }
    defaults.update(overrides)
    return UserPrincipal(**defaults)


def _claims(**overrides: Any) -> UserClaims:
    defaults: dict[str, Any] = {
        "user_id": "user-123",
        "email": "alice@example.com",
        "name": "Alice",
        "roles": ["user"],
        "scopes": ["openid", "read"],
        "raw_claims": {"sub": "user-123"},
        "provider": "keycloak",
        "app_id": "app-abc",
        "chat_id": "chat-xyz",
        "tenant_id": "tenant-1",
    }
    defaults.update(overrides)
    return UserClaims(**defaults)


# ---------------------------------------------------------------------------
# UserPrincipal — role helpers
# ---------------------------------------------------------------------------


class TestUserPrincipalRoles:
    def test_has_role_true(self):
        p = _principal(roles=["admin", "user"])
        assert p.has_role("admin") is True

    def test_has_role_false(self):
        p = _principal(roles=["user"])
        assert p.has_role("admin") is False

    def test_has_any_role_matches_one(self):
        p = _principal(roles=["moderator"])
        assert p.has_any_role(["admin", "moderator"]) is True

    def test_has_any_role_no_match(self):
        p = _principal(roles=["user"])
        assert p.has_any_role(["admin", "moderator"]) is False

    def test_has_any_role_empty_input(self):
        p = _principal(roles=["admin"])
        assert p.has_any_role([]) is False

    def test_has_scope_true(self):
        p = _principal(scopes=["openid", "read"])
        assert p.has_scope("read") is True

    def test_has_scope_false(self):
        p = _principal(scopes=["openid"])
        assert p.has_scope("write") is False


# ---------------------------------------------------------------------------
# UserPrincipal — validate_app_id / validate_chat_id
# ---------------------------------------------------------------------------


class TestUserPrincipalValidation:
    def test_validate_app_id_matches(self):
        p = _principal(app_id="app-abc")
        assert p.validate_app_id("app-abc") is True

    def test_validate_app_id_mismatch(self):
        p = _principal(app_id="app-abc")
        assert p.validate_app_id("app-other") is False

    def test_validate_app_id_unbound_token_always_passes(self):
        p = _principal(app_id=None)
        assert p.validate_app_id("any-app") is True

    def test_validate_chat_id_matches(self):
        p = _principal(chat_id="chat-xyz")
        assert p.validate_chat_id("chat-xyz") is True

    def test_validate_chat_id_mismatch(self):
        p = _principal(chat_id="chat-xyz")
        assert p.validate_chat_id("chat-other") is False

    def test_validate_chat_id_unbound_always_passes(self):
        p = _principal(chat_id=None)
        assert p.validate_chat_id("any-chat") is True


# ---------------------------------------------------------------------------
# UserPrincipal.from_claims
# ---------------------------------------------------------------------------


class TestUserPrincipalFromClaims:
    def test_from_claims_maps_all_fields(self):
        claims = _claims()
        p = UserPrincipal.from_claims(claims)
        assert p.user_id == "user-123"
        assert p.email == "alice@example.com"
        assert p.name == "Alice"
        assert p.roles == ["user"]
        assert p.scopes == ["openid", "read"]
        assert p.provider == "keycloak"
        assert p.app_id == "app-abc"
        assert p.chat_id == "chat-xyz"
        assert p.tenant_id == "tenant-1"

    def test_from_claims_raw_claims_preserved(self):
        claims = _claims(raw_claims={"custom": "value", "sub": "user-123"})
        p = UserPrincipal.from_claims(claims)
        assert p.raw_claims["custom"] == "value"

    def test_from_claims_optional_fields_can_be_none(self):
        claims = UserClaims(user_id="u1", email=None, name=None, provider="jwt")
        p = UserPrincipal.from_claims(claims)
        assert p.email is None
        assert p.name is None
        assert p.app_id is None


# ---------------------------------------------------------------------------
# validate_user_id_against_principal
# ---------------------------------------------------------------------------


class TestValidateUserIdAgainstPrincipal:
    def test_auth_enabled_path_id_matches(self):
        p = _principal(user_id="user-123")
        result = validate_user_id_against_principal(p, path_user_id="user-123")
        assert result == "user-123"

    def test_auth_enabled_path_id_mismatch_raises_403(self):
        from fastapi import HTTPException

        p = _principal(user_id="user-123")
        with pytest.raises(HTTPException) as exc_info:
            validate_user_id_against_principal(p, path_user_id="other-user")
        assert exc_info.value.status_code == 403

    def test_auth_enabled_body_id_matches(self):
        p = _principal(user_id="user-123")
        result = validate_user_id_against_principal(p, body_user_id="user-123")
        assert result == "user-123"

    def test_auth_enabled_body_id_mismatch_raises_403(self):
        from fastapi import HTTPException

        p = _principal(user_id="user-123")
        with pytest.raises(HTTPException) as exc_info:
            validate_user_id_against_principal(p, body_user_id="other-user")
        assert exc_info.value.status_code == 403

    def test_auth_enabled_no_id_provided_returns_principal_id(self):
        p = _principal(user_id="user-123")
        result = validate_user_id_against_principal(p)
        assert result == "user-123"

    def test_anonymous_falls_back_to_path_id(self):
        p = _principal(user_id="anonymous")
        result = validate_user_id_against_principal(p, path_user_id="supplied-id")
        assert result == "supplied-id"

    def test_anonymous_falls_back_to_body_id(self):
        p = _principal(user_id="anonymous")
        result = validate_user_id_against_principal(p, body_user_id="body-id")
        assert result == "body-id"

    def test_anonymous_no_id_raises_400(self):
        from fastapi import HTTPException

        p = _principal(user_id="anonymous")
        with pytest.raises(HTTPException) as exc_info:
            validate_user_id_against_principal(p)
        assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# validate_path_app_id / validate_path_chat_id
# ---------------------------------------------------------------------------


class TestValidatePathHelpers:
    def test_path_app_id_mismatch_raises_403_when_auth_enabled(self):
        from fastapi import HTTPException

        p = _principal(app_id="app-abc")
        with patch("mozaiksai.core.auth.dependencies.is_auth_enabled", return_value=True):
            with pytest.raises(HTTPException) as exc_info:
                validate_path_app_id(p, "wrong-app")
        assert exc_info.value.status_code == 403

    def test_path_app_id_match_does_not_raise(self):
        p = _principal(app_id="app-abc")
        with patch("mozaiksai.core.auth.dependencies.is_auth_enabled", return_value=True):
            validate_path_app_id(p, "app-abc")  # should not raise

    def test_path_app_id_skipped_when_auth_disabled(self):
        p = _principal(app_id="app-abc")
        with patch("mozaiksai.core.auth.dependencies.is_auth_enabled", return_value=False):
            validate_path_app_id(p, "wrong-app")  # should not raise

    def test_path_chat_id_mismatch_raises_403_when_auth_enabled(self):
        from fastapi import HTTPException

        p = _principal(chat_id="chat-xyz")
        with patch("mozaiksai.core.auth.dependencies.is_auth_enabled", return_value=True):
            with pytest.raises(HTTPException) as exc_info:
                validate_path_chat_id(p, "wrong-chat")
        assert exc_info.value.status_code == 403

    def test_path_chat_id_match_does_not_raise(self):
        p = _principal(chat_id="chat-xyz")
        with patch("mozaiksai.core.auth.dependencies.is_auth_enabled", return_value=True):
            validate_path_chat_id(p, "chat-xyz")  # should not raise

    def test_path_chat_id_skipped_when_auth_disabled(self):
        p = _principal(chat_id="chat-xyz")
        with patch("mozaiksai.core.auth.dependencies.is_auth_enabled", return_value=False):
            validate_path_chat_id(p, "wrong-chat")  # should not raise


# ---------------------------------------------------------------------------
# FastAPI integration — require_user / require_role
# ---------------------------------------------------------------------------


class _MockAdapter:
    """Stub adapter that returns pre-configured claims."""

    name = "mock"

    def __init__(self, claims: UserClaims | None = None, raise_error: AuthError | None = None):
        self._claims = claims
        self._raise = raise_error

    async def validate_token(self, token: str) -> UserClaims:
        if self._raise is not None:
            raise self._raise
        assert self._claims is not None
        return self._claims

    def is_enabled(self) -> bool:
        return True


def _make_app(claims: UserClaims | None = None, raise_error: AuthError | None = None) -> FastAPI:
    """Build a minimal FastAPI app with protected routes."""
    app = FastAPI()
    adapter = _MockAdapter(claims=claims, raise_error=raise_error)

    @app.get("/protected")
    async def protected(user: UserPrincipal = Depends(require_user)):
        return {"user_id": user.user_id, "roles": user.roles}

    @app.get("/admin")
    async def admin_only(user: UserPrincipal = Depends(require_role("admin"))):
        return {"user_id": user.user_id}

    # Patch adapter inside the app scope
    app.state.mock_adapter = adapter
    return app


class TestRequireUserIntegration:
    def _client(self, claims: UserClaims | None = None, raise_error: AuthError | None = None) -> TestClient:
        app = _make_app(claims=claims, raise_error=raise_error)
        adapter = _MockAdapter(claims=claims, raise_error=raise_error)
        # We patch at the module level so require_user picks it up
        self._patch = patch("mozaiksai.core.auth.dependencies.get_auth_adapter", return_value=adapter)
        self._patch2 = patch("mozaiksai.core.auth.dependencies.is_auth_enabled", return_value=True)
        self._patch.start()
        self._patch2.start()
        return TestClient(app, raise_server_exceptions=False)

    def teardown_method(self, _):
        try:
            self._patch.stop()
            self._patch2.stop()
        except Exception:
            pass

    def test_valid_token_returns_200(self):
        claims = _claims(user_id="u1", roles=["user"])
        client = self._client(claims=claims)
        response = client.get("/protected", headers={"Authorization": "Bearer valid-token"})
        assert response.status_code == 200
        assert response.json()["user_id"] == "u1"

    def test_missing_token_returns_401(self):
        client = self._client(claims=_claims())
        response = client.get("/protected")
        assert response.status_code == 401

    def test_invalid_token_returns_401(self):
        client = self._client(raise_error=AuthError("bad token", 401, "mock"))
        response = client.get("/protected", headers={"Authorization": "Bearer bad"})
        assert response.status_code == 401

    def test_access_token_query_param_accepted(self):
        claims = _claims(user_id="u2")
        client = self._client(claims=claims)
        response = client.get("/protected?access_token=valid-token")
        assert response.status_code == 200
        assert response.json()["user_id"] == "u2"


class TestRequireRoleIntegration:
    def _client(self, claims: UserClaims) -> TestClient:
        app = _make_app(claims=claims)
        adapter = _MockAdapter(claims=claims)
        self._patch = patch("mozaiksai.core.auth.dependencies.get_auth_adapter", return_value=adapter)
        self._patch2 = patch("mozaiksai.core.auth.dependencies.is_auth_enabled", return_value=True)
        self._patch.start()
        self._patch2.start()
        return TestClient(app, raise_server_exceptions=False)

    def teardown_method(self, _):
        try:
            self._patch.stop()
            self._patch2.stop()
        except Exception:
            pass

    def test_admin_role_granted_access(self):
        claims = _claims(user_id="admin-user", roles=["admin"])
        client = self._client(claims=claims)
        response = client.get("/admin", headers={"Authorization": "Bearer tok"})
        assert response.status_code == 200
        assert response.json()["user_id"] == "admin-user"

    def test_non_admin_gets_403(self):
        claims = _claims(user_id="regular-user", roles=["user"])
        client = self._client(claims=claims)
        response = client.get("/admin", headers={"Authorization": "Bearer tok"})
        assert response.status_code == 403

    def test_no_roles_gets_403(self):
        claims = _claims(user_id="no-role-user", roles=[])
        client = self._client(claims=claims)
        response = client.get("/admin", headers={"Authorization": "Bearer tok"})
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Auth-disabled bypass
# ---------------------------------------------------------------------------


class TestAuthDisabledBypass:
    def test_require_user_returns_anonymous_when_auth_disabled(self):
        claims = _claims(user_id="any")
        app = _make_app(claims=claims)
        adapter = _MockAdapter(claims=UserClaims(user_id="anonymous", provider="none"))

        with patch("mozaiksai.core.auth.dependencies.get_auth_adapter", return_value=adapter), \
             patch("mozaiksai.core.auth.dependencies.is_auth_enabled", return_value=False):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/protected")
        assert response.status_code == 200
        assert response.json()["user_id"] == "anonymous"
