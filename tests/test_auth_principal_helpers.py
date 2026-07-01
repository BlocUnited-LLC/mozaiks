"""
Auth principal pure helper unit tests.

Covers:
  UserPrincipal.has_role:
    - role present → True
    - role absent → False
    - empty roles list → False
    - case-sensitive check

  UserPrincipal.has_any_role:
    - one of the roles matches → True
    - none match → False
    - empty roles list → False
    - empty check list → False

  UserPrincipal.has_scope:
    - scope present → True
    - scope absent → False
    - empty scopes list → False

  UserPrincipal.validate_app_id:
    - no app_id on principal → True (unbound)
    - matching app_id → True
    - mismatching app_id → False
    - None principal app_id → True
    - numeric id coerced to str for comparison

  UserPrincipal.validate_chat_id:
    - no chat_id on principal → True (unbound)
    - matching chat_id → True
    - mismatching chat_id → False
    - None principal chat_id → True

  _extract_token:
    - authorization header present with credentials → returned
    - authorization None, access_token in query params → returned
    - authorization with credentials takes priority over query param
    - authorization present but empty credentials → query param fallback
    - both missing → None
    - query param present but empty string → None

  validate_user_id_against_principal:
    - anonymous with path_user_id → path_user_id returned
    - anonymous with body_user_id only → body_user_id returned
    - anonymous with neither → HTTPException 400
    - anonymous path_user_id preferred over body
    - authenticated matching path_user_id → jwt user_id returned
    - authenticated mismatching path_user_id → HTTPException 403
    - authenticated matching body_user_id → jwt user_id returned
    - authenticated mismatching body_user_id → HTTPException 403
    - authenticated with no path/body → jwt user_id returned
    - whitespace stripped before comparison
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from mozaiksai.core.auth.dependencies import (
    UserPrincipal,
    _extract_token,
    validate_user_id_against_principal,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _principal(**kw) -> UserPrincipal:
    defaults = {
        "user_id": "user-1",
        "email": "user@example.com",
        "name": "Test User",
        "roles": [],
        "scopes": [],
        "raw_claims": {},
    }
    return UserPrincipal(**{**defaults, **kw})


class _FakeAuth:
    """Minimal stub for HTTPAuthorizationCredentials."""
    def __init__(self, credentials: str):
        self.credentials = credentials


class _FakeRequest:
    """Minimal stub for fastapi.Request with query_params."""
    def __init__(self, params: dict | None = None):
        self.query_params = params or {}


# ---------------------------------------------------------------------------
# 1. UserPrincipal.has_role
# ---------------------------------------------------------------------------

class TestHasRole:
    def test_role_present_returns_true(self):
        p = _principal(roles=["admin", "editor"])
        assert p.has_role("admin") is True

    def test_role_absent_returns_false(self):
        p = _principal(roles=["editor"])
        assert p.has_role("admin") is False

    def test_empty_roles_returns_false(self):
        p = _principal(roles=[])
        assert p.has_role("admin") is False

    def test_case_sensitive_no_match(self):
        p = _principal(roles=["Admin"])
        assert p.has_role("admin") is False

    def test_exact_match_only(self):
        p = _principal(roles=["superadmin"])
        assert p.has_role("admin") is False


# ---------------------------------------------------------------------------
# 2. UserPrincipal.has_any_role
# ---------------------------------------------------------------------------

class TestHasAnyRole:
    def test_one_role_matches(self):
        p = _principal(roles=["editor"])
        assert p.has_any_role(["admin", "editor"]) is True

    def test_none_match(self):
        p = _principal(roles=["viewer"])
        assert p.has_any_role(["admin", "editor"]) is False

    def test_empty_roles_list_returns_false(self):
        p = _principal(roles=[])
        assert p.has_any_role(["admin"]) is False

    def test_empty_check_list_returns_false(self):
        p = _principal(roles=["admin"])
        assert p.has_any_role([]) is False

    def test_multiple_matches_returns_true(self):
        p = _principal(roles=["admin", "editor"])
        assert p.has_any_role(["admin", "editor"]) is True


# ---------------------------------------------------------------------------
# 3. UserPrincipal.has_scope
# ---------------------------------------------------------------------------

class TestHasScope:
    def test_scope_present_returns_true(self):
        p = _principal(scopes=["read", "write"])
        assert p.has_scope("read") is True

    def test_scope_absent_returns_false(self):
        p = _principal(scopes=["read"])
        assert p.has_scope("write") is False

    def test_empty_scopes_returns_false(self):
        p = _principal(scopes=[])
        assert p.has_scope("read") is False


# ---------------------------------------------------------------------------
# 4. UserPrincipal.validate_app_id
# ---------------------------------------------------------------------------

class TestValidateAppId:
    def test_no_app_id_on_principal_returns_true(self):
        p = _principal(app_id=None)
        assert p.validate_app_id("app-42") is True

    def test_matching_app_id_returns_true(self):
        p = _principal(app_id="app-42")
        assert p.validate_app_id("app-42") is True

    def test_mismatching_app_id_returns_false(self):
        p = _principal(app_id="app-1")
        assert p.validate_app_id("app-2") is False

    def test_str_coercion_for_comparison(self):
        # Both sides coerced to str
        p = _principal(app_id="42")
        assert p.validate_app_id("42") is True

    def test_empty_string_app_id_on_principal_falsy_returns_true(self):
        # Empty string is falsy → unbound → True
        p = _principal(app_id="")
        assert p.validate_app_id("app-1") is True


# ---------------------------------------------------------------------------
# 5. UserPrincipal.validate_chat_id
# ---------------------------------------------------------------------------

class TestValidateChatId:
    def test_no_chat_id_on_principal_returns_true(self):
        p = _principal(chat_id=None)
        assert p.validate_chat_id("chat-99") is True

    def test_matching_chat_id_returns_true(self):
        p = _principal(chat_id="chat-99")
        assert p.validate_chat_id("chat-99") is True

    def test_mismatching_chat_id_returns_false(self):
        p = _principal(chat_id="chat-1")
        assert p.validate_chat_id("chat-2") is False

    def test_empty_string_chat_id_on_principal_falsy_returns_true(self):
        p = _principal(chat_id="")
        assert p.validate_chat_id("chat-1") is True


class TestValidateTenantAndWorkspaceId:
    def test_no_tenant_or_workspace_on_principal_returns_true(self):
        p = _principal(tenant_id=None, workspace_id=None)
        assert p.validate_tenant_id("tenant-1") is True
        assert p.validate_workspace_id("workspace-1") is True

    def test_matching_tenant_and_workspace_return_true(self):
        p = _principal(tenant_id="tenant-1", workspace_id="workspace-1")
        assert p.validate_tenant_id("tenant-1") is True
        assert p.validate_workspace_id("workspace-1") is True

    def test_mismatching_tenant_and_workspace_return_false(self):
        p = _principal(tenant_id="tenant-1", workspace_id="workspace-1")
        assert p.validate_tenant_id("tenant-2") is False
        assert p.validate_workspace_id("workspace-2") is False


# ---------------------------------------------------------------------------
# 6. _extract_token
# ---------------------------------------------------------------------------

class TestExtractToken:
    def test_authorization_header_credentials_returned(self):
        auth = _FakeAuth("my-jwt-token")
        req = _FakeRequest()
        assert _extract_token(auth, req) == "my-jwt-token"

    def test_no_auth_falls_back_to_query_param(self):
        req = _FakeRequest({"access_token": "ws-token"})
        assert _extract_token(None, req) == "ws-token"

    def test_auth_header_takes_priority_over_query_param(self):
        auth = _FakeAuth("header-token")
        req = _FakeRequest({"access_token": "query-token"})
        assert _extract_token(auth, req) == "header-token"

    def test_both_missing_returns_none(self):
        req = _FakeRequest()
        assert _extract_token(None, req) is None

    def test_auth_with_empty_credentials_falls_back_to_query_param(self):
        auth = _FakeAuth("")  # falsy credentials
        req = _FakeRequest({"access_token": "ws-token"})
        assert _extract_token(auth, req) == "ws-token"

    def test_query_param_empty_string_returns_none(self):
        req = _FakeRequest({"access_token": ""})
        assert _extract_token(None, req) is None

    def test_auth_none_and_no_query_param_returns_none(self):
        req = _FakeRequest({"other_param": "value"})
        assert _extract_token(None, req) is None


# ---------------------------------------------------------------------------
# 7. validate_user_id_against_principal
# ---------------------------------------------------------------------------

class TestValidateUserIdAgainstPrincipal:
    def test_anonymous_with_path_user_id(self):
        p = _principal(user_id="anonymous")
        result = validate_user_id_against_principal(p, path_user_id="user-abc")
        assert result == "user-abc"

    def test_anonymous_with_body_user_id_only(self):
        p = _principal(user_id="anonymous")
        result = validate_user_id_against_principal(p, body_user_id="user-xyz")
        assert result == "user-xyz"

    def test_anonymous_path_preferred_over_body(self):
        p = _principal(user_id="anonymous")
        result = validate_user_id_against_principal(p, path_user_id="path-id", body_user_id="body-id")
        assert result == "path-id"

    def test_anonymous_with_neither_raises_400(self):
        p = _principal(user_id="anonymous")
        with pytest.raises(HTTPException) as exc_info:
            validate_user_id_against_principal(p)
        assert exc_info.value.status_code == 400

    def test_authenticated_returns_jwt_user_id(self):
        p = _principal(user_id="jwt-user-1")
        result = validate_user_id_against_principal(p, path_user_id="jwt-user-1")
        assert result == "jwt-user-1"

    def test_authenticated_mismatching_path_raises_403(self):
        p = _principal(user_id="jwt-user-1")
        with pytest.raises(HTTPException) as exc_info:
            validate_user_id_against_principal(p, path_user_id="other-user")
        assert exc_info.value.status_code == 403

    def test_authenticated_matching_body_user_id(self):
        p = _principal(user_id="jwt-user-1")
        result = validate_user_id_against_principal(p, body_user_id="jwt-user-1")
        assert result == "jwt-user-1"

    def test_authenticated_mismatching_body_raises_403(self):
        p = _principal(user_id="jwt-user-1")
        with pytest.raises(HTTPException) as exc_info:
            validate_user_id_against_principal(p, body_user_id="other-user")
        assert exc_info.value.status_code == 403

    def test_authenticated_no_path_or_body_returns_jwt_id(self):
        p = _principal(user_id="jwt-user-1")
        result = validate_user_id_against_principal(p)
        assert result == "jwt-user-1"

    def test_whitespace_stripped_before_comparison(self):
        p = _principal(user_id="jwt-user-1")
        # Matching with surrounding whitespace — stripped to match
        result = validate_user_id_against_principal(p, path_user_id="jwt-user-1 ")
        # "jwt-user-1 ".strip() == "jwt-user-1" → match
        assert result == "jwt-user-1"
