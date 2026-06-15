"""Unit tests for KeycloakAuthAdapter claim extraction.

Tests _extract_claims and _extract_roles without needing a live Keycloak
instance or network calls — only the claim parsing logic is exercised here.
The JWKS validation path is gated behind validate_token() which requires
a live JWKS endpoint; that path is covered by integration/e2e tests.
"""
from __future__ import annotations

import pytest

from mozaiksai.core.auth.adapters.base import AuthError
from mozaiksai.core.auth.adapters.keycloak import KeycloakAuthAdapter


def _adapter(
    keycloak_url: str = "https://kc.example.com",
    realm: str = "myrealm",
    client_id: str = "my-app",
) -> KeycloakAuthAdapter:
    return KeycloakAuthAdapter(
        keycloak_url=keycloak_url,
        realm=realm,
        client_id=client_id,
    )


def _base_claims(**overrides: object) -> dict:
    claims: dict = {
        "sub": "user-uuid-123",
        "email": "alice@example.com",
        "name": "Alice Smith",
        "preferred_username": "alice",
        "realm_access": {"roles": ["user"]},
        "resource_access": {"my-app": {"roles": ["app-user"]}},
        "scope": "openid email profile",
        "azp": "my-app",
    }
    claims.update(overrides)
    return claims


# ---------------------------------------------------------------------------
# _extract_claims
# ---------------------------------------------------------------------------


class TestExtractClaims:
    def test_user_id_from_sub(self):
        adapter = _adapter()
        result = adapter._extract_claims(_base_claims())
        assert result.user_id == "user-uuid-123"

    def test_email_extracted(self):
        adapter = _adapter()
        result = adapter._extract_claims(_base_claims())
        assert result.email == "alice@example.com"

    def test_name_extracted(self):
        adapter = _adapter()
        result = adapter._extract_claims(_base_claims())
        assert result.name == "Alice Smith"

    def test_name_falls_back_to_preferred_username(self):
        adapter = _adapter()
        claims = _base_claims()
        del claims["name"]
        result = adapter._extract_claims(claims)
        assert result.name == "alice"

    def test_email_none_when_absent(self):
        adapter = _adapter()
        claims = _base_claims()
        del claims["email"]
        result = adapter._extract_claims(claims)
        assert result.email is None

    def test_name_none_when_absent(self):
        adapter = _adapter()
        claims = _base_claims()
        claims.pop("name", None)
        claims.pop("preferred_username", None)
        result = adapter._extract_claims(claims)
        assert result.name is None

    def test_provider_is_keycloak(self):
        adapter = _adapter()
        result = adapter._extract_claims(_base_claims())
        assert result.provider == "keycloak"

    def test_tenant_id_from_azp(self):
        adapter = _adapter()
        result = adapter._extract_claims(_base_claims())
        assert result.tenant_id == "my-app"

    def test_scopes_parsed_from_scope_string(self):
        adapter = _adapter()
        result = adapter._extract_claims(_base_claims(scope="openid email profile"))
        assert "openid" in result.scopes
        assert "email" in result.scopes
        assert "profile" in result.scopes

    def test_scopes_empty_when_scope_absent(self):
        adapter = _adapter()
        claims = _base_claims()
        del claims["scope"]
        result = adapter._extract_claims(claims)
        assert result.scopes == []

    def test_raw_claims_preserved(self):
        adapter = _adapter()
        claims = _base_claims(custom_field="custom_value")
        result = adapter._extract_claims(claims)
        assert result.raw_claims["custom_field"] == "custom_value"

    def test_missing_sub_raises_auth_error(self):
        adapter = _adapter()
        claims = _base_claims()
        del claims["sub"]
        with pytest.raises(AuthError) as exc_info:
            adapter._extract_claims(claims)
        assert exc_info.value.status_code == 401
        assert "sub" in exc_info.value.message.lower() or "user id" in exc_info.value.message.lower()

    def test_sub_as_uuid_string(self):
        adapter = _adapter()
        claims = _base_claims(sub="550e8400-e29b-41d4-a716-446655440000")
        result = adapter._extract_claims(claims)
        assert result.user_id == "550e8400-e29b-41d4-a716-446655440000"


# ---------------------------------------------------------------------------
# _extract_roles
# ---------------------------------------------------------------------------


class TestExtractRoles:
    def test_realm_roles_included(self):
        adapter = _adapter()
        claims = _base_claims(realm_access={"roles": ["user", "moderator"]})
        roles = adapter._extract_roles(claims)
        assert "user" in roles
        assert "moderator" in roles

    def test_client_roles_included_when_client_id_set(self):
        adapter = _adapter(client_id="my-app")
        claims = _base_claims(
            realm_access={"roles": ["user"]},
            resource_access={"my-app": {"roles": ["app-admin"]}},
        )
        roles = adapter._extract_roles(claims)
        assert "user" in roles
        assert "app-admin" in roles

    def test_client_roles_excluded_when_no_client_id(self):
        adapter = _adapter(client_id="")
        claims = _base_claims(
            realm_access={"roles": ["user"]},
            resource_access={"my-app": {"roles": ["app-admin"]}},
        )
        roles = adapter._extract_roles(claims)
        assert "user" in roles
        assert "app-admin" not in roles

    def test_other_client_roles_not_included(self):
        adapter = _adapter(client_id="my-app")
        claims = _base_claims(
            realm_access={"roles": []},
            resource_access={
                "my-app": {"roles": ["app-user"]},
                "other-app": {"roles": ["other-admin"]},
            },
        )
        roles = adapter._extract_roles(claims)
        assert "app-user" in roles
        assert "other-admin" not in roles

    def test_empty_realm_roles(self):
        adapter = _adapter()
        claims = _base_claims(realm_access={"roles": []})
        roles = adapter._extract_roles(claims)
        # Should not raise; may include client roles
        assert isinstance(roles, list)

    def test_missing_realm_access(self):
        adapter = _adapter()
        claims = _base_claims()
        del claims["realm_access"]
        roles = adapter._extract_roles(claims)
        assert isinstance(roles, list)

    def test_roles_deduplication_not_required_but_no_crash(self):
        adapter = _adapter(client_id="my-app")
        claims = _base_claims(
            realm_access={"roles": ["user"]},
            resource_access={"my-app": {"roles": ["user"]}},
        )
        roles = adapter._extract_roles(claims)
        # "user" may appear twice; caller is responsible for dedup if needed
        assert "user" in roles

    def test_roles_non_list_realm_roles_ignored(self):
        adapter = _adapter()
        claims = _base_claims(realm_access={"roles": "not-a-list"})
        roles = adapter._extract_roles(claims)
        # Non-list realm roles should be silently ignored
        assert isinstance(roles, list)
        assert "not-a-list" not in roles


# ---------------------------------------------------------------------------
# Configuration / is_enabled
# ---------------------------------------------------------------------------


class TestKeycloakAdapterConfig:
    def test_is_enabled_when_url_and_realm_set(self):
        adapter = _adapter()
        assert adapter.is_enabled() is True

    def test_is_not_enabled_when_url_missing(self):
        adapter = KeycloakAuthAdapter(keycloak_url="", realm="myrealm")
        assert adapter.is_enabled() is False

    def test_is_not_enabled_when_realm_missing(self):
        adapter = KeycloakAuthAdapter(keycloak_url="https://kc.example.com", realm="")
        assert adapter.is_enabled() is False

    def test_jwks_url_construction(self):
        adapter = KeycloakAuthAdapter(
            keycloak_url="https://kc.example.com",
            realm="prod",
        )
        assert adapter._jwks_url == (
            "https://kc.example.com/realms/prod/protocol/openid-connect/certs"
        )

    def test_issuer_construction(self):
        adapter = KeycloakAuthAdapter(
            keycloak_url="https://kc.example.com",
            realm="prod",
        )
        assert adapter._issuer == "https://kc.example.com/realms/prod"

    def test_trailing_slash_stripped_from_url(self):
        adapter = KeycloakAuthAdapter(
            keycloak_url="https://kc.example.com/",
            realm="prod",
        )
        assert adapter._keycloak_url == "https://kc.example.com"

    def test_validate_token_empty_raises_auth_error(self):
        adapter = _adapter()
        import asyncio
        with pytest.raises(AuthError) as exc_info:
            asyncio.run(adapter.validate_token(""))
        assert exc_info.value.status_code == 401

    def test_validate_token_whitespace_only_raises_auth_error(self):
        adapter = _adapter()
        import asyncio
        with pytest.raises(AuthError) as exc_info:
            asyncio.run(adapter.validate_token("   "))
        assert exc_info.value.status_code == 401

    def test_get_jwks_client_raises_when_not_configured(self):
        adapter = KeycloakAuthAdapter(keycloak_url="", realm="")
        with pytest.raises(AuthError) as exc_info:
            adapter._get_jwks_client()
        assert exc_info.value.status_code == 500

    def test_name_is_keycloak(self):
        assert KeycloakAuthAdapter.name == "keycloak"
