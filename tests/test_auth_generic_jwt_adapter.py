"""Unit tests for GenericJWTAdapter claim extraction and configuration.

Tests _extract_claims, _extract_scopes, is_enabled, and JWTAdapterConfig
without network calls or live JWKS endpoints.
The validate_token JWKS path is covered by integration/e2e tests.
"""
from __future__ import annotations

import asyncio
import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

import mozaiksai.core.auth.adapters.jwt_adapter as jwt_adapter_module
from mozaiksai.core.auth.adapters.base import AuthError
from mozaiksai.core.auth.adapters.jwt_adapter import GenericJWTAdapter, JWTAdapterConfig


def _adapter(**config_overrides: object) -> GenericJWTAdapter:
    defaults = {
        "jwks_url": "https://auth.example.com/.well-known/jwks.json",
        "issuer": "https://auth.example.com/",
        "audience": "my-api",
    }
    defaults.update(config_overrides)
    return GenericJWTAdapter(config=JWTAdapterConfig(**defaults))


def _base_claims(**overrides: object) -> dict:
    claims: dict = {
        "sub": "user-abc-123",
        "email": "bob@example.com",
        "name": "Bob Jones",
        "roles": ["user"],
        "scp": "openid read write",
        "tid": "tenant-xyz",
        "workspace_id": "workspace-xyz",
        "app_id": "app-xyz",
    }
    claims.update(overrides)
    return claims


# ---------------------------------------------------------------------------
# _extract_claims — default claim mappings
# ---------------------------------------------------------------------------


class TestExtractClaims:
    def test_user_id_from_sub(self):
        result = _adapter()._extract_claims(_base_claims())
        assert result.user_id == "user-abc-123"

    def test_email_extracted(self):
        result = _adapter()._extract_claims(_base_claims())
        assert result.email == "bob@example.com"

    def test_name_extracted(self):
        result = _adapter()._extract_claims(_base_claims())
        assert result.name == "Bob Jones"

    def test_email_none_when_absent(self):
        claims = _base_claims()
        del claims["email"]
        result = _adapter()._extract_claims(claims)
        assert result.email is None

    def test_name_none_when_absent(self):
        claims = _base_claims()
        del claims["name"]
        result = _adapter()._extract_claims(claims)
        assert result.name is None

    def test_provider_is_jwt(self):
        result = _adapter()._extract_claims(_base_claims())
        assert result.provider == "jwt"

    def test_tenant_id_from_tid(self):
        result = _adapter()._extract_claims(_base_claims())
        assert result.tenant_id == "tenant-xyz"

    def test_workspace_id_from_default_claim(self):
        result = _adapter()._extract_claims(_base_claims())
        assert result.workspace_id == "workspace-xyz"

    def test_app_id_from_default_claim(self):
        result = _adapter()._extract_claims(_base_claims())
        assert result.app_id == "app-xyz"

    def test_tenant_id_none_when_absent(self):
        claims = _base_claims()
        del claims["tid"]
        result = _adapter()._extract_claims(claims)
        assert result.tenant_id is None

    def test_raw_claims_preserved(self):
        claims = _base_claims(custom_claim="custom_value")
        result = _adapter()._extract_claims(claims)
        assert result.raw_claims["custom_claim"] == "custom_value"

    def test_missing_user_id_claim_raises_auth_error(self):
        claims = _base_claims()
        del claims["sub"]
        with pytest.raises(AuthError) as exc_info:
            _adapter()._extract_claims(claims)
        assert exc_info.value.status_code == 401

    def test_user_id_coerced_to_string(self):
        # Some providers emit sub as an integer
        claims = _base_claims(sub=42)
        result = _adapter()._extract_claims(claims)
        assert result.user_id == "42"

    def test_email_coerced_to_string(self):
        # Defensive: if email is somehow non-string but truthy
        claims = _base_claims(email="carol@example.com")
        result = _adapter()._extract_claims(claims)
        assert isinstance(result.email, str)

    def test_roles_list_extracted(self):
        claims = _base_claims(roles=["admin", "editor"])
        result = _adapter()._extract_claims(claims)
        assert "admin" in result.roles
        assert "editor" in result.roles

    def test_roles_string_wrapped_in_list(self):
        claims = _base_claims(roles="admin")
        result = _adapter()._extract_claims(claims)
        assert "admin" in result.roles

    def test_roles_non_list_non_string_yields_empty(self):
        claims = _base_claims(roles={"not": "a list"})
        result = _adapter()._extract_claims(claims)
        assert result.roles == []

    def test_roles_absent_yields_empty(self):
        claims = _base_claims()
        del claims["roles"]
        result = _adapter()._extract_claims(claims)
        assert result.roles == []


# ---------------------------------------------------------------------------
# _extract_claims — custom claim mappings
# ---------------------------------------------------------------------------


class TestCustomClaimMappings:
    def test_custom_user_id_claim(self):
        adapter = _adapter(user_id_claim="oid")
        claims = _base_claims(oid="oid-value-999")
        result = adapter._extract_claims(claims)
        assert result.user_id == "oid-value-999"

    def test_custom_email_claim(self):
        adapter = _adapter(email_claim="upn")
        claims = _base_claims(upn="dan@corp.com")
        result = adapter._extract_claims(claims)
        assert result.email == "dan@corp.com"

    def test_custom_name_claim(self):
        adapter = _adapter(name_claim="display_name")
        claims = _base_claims(display_name="Dan Brown")
        result = adapter._extract_claims(claims)
        assert result.name == "Dan Brown"

    def test_custom_roles_claim(self):
        adapter = _adapter(roles_claim="app_roles")
        claims = _base_claims(app_roles=["super-admin"])
        result = adapter._extract_claims(claims)
        assert "super-admin" in result.roles

    def test_custom_tenant_and_workspace_claims(self):
        adapter = _adapter(tenant_id_claim="org_id", workspace_id_claim="team_id")
        claims = _base_claims(org_id="tenant-custom", team_id="workspace-custom")
        result = adapter._extract_claims(claims)
        assert result.tenant_id == "tenant-custom"
        assert result.workspace_id == "workspace-custom"

    def test_missing_required_custom_claim_raises(self):
        adapter = _adapter(user_id_claim="oid")
        # "oid" is absent — sub present but not used
        claims = _base_claims()
        del claims["sub"]
        with pytest.raises(AuthError) as exc_info:
            adapter._extract_claims(claims)
        assert exc_info.value.status_code == 401
        assert "oid" in exc_info.value.message.lower()


# ---------------------------------------------------------------------------
# _extract_scopes
# ---------------------------------------------------------------------------


class TestExtractScopes:
    def test_space_separated_string_parsed(self):
        adapter = _adapter(scopes_format="space", scopes_claim="scp")
        claims = _base_claims(scp="openid read write")
        scopes = adapter._extract_scopes(claims)
        assert "openid" in scopes
        assert "read" in scopes
        assert "write" in scopes

    def test_space_format_with_list_claim(self):
        # Provider emits list; adapter should still accept it
        adapter = _adapter(scopes_format="space", scopes_claim="scp")
        claims = _base_claims(scp=["openid", "read"])
        scopes = adapter._extract_scopes(claims)
        assert "openid" in scopes
        assert "read" in scopes

    def test_array_format_with_list_claim(self):
        adapter = _adapter(scopes_format="array", scopes_claim="scope")
        claims = _base_claims(scope=["openid", "email"])
        scopes = adapter._extract_scopes(claims)
        assert "openid" in scopes
        assert "email" in scopes

    def test_array_format_with_space_string_fallback(self):
        # Provider emits space string even though format is "array"
        adapter = _adapter(scopes_format="array", scopes_claim="scope")
        claims = _base_claims(scope="openid email")
        scopes = adapter._extract_scopes(claims)
        assert "openid" in scopes
        assert "email" in scopes

    def test_scope_absent_yields_empty(self):
        adapter = _adapter(scopes_claim="scp")
        claims = _base_claims()
        del claims["scp"]
        scopes = adapter._extract_scopes(claims)
        assert scopes == []

    def test_scope_custom_claim_name(self):
        adapter = _adapter(scopes_format="space", scopes_claim="scope")
        claims = _base_claims(scope="profile offline_access")
        scopes = adapter._extract_scopes(claims)
        assert "profile" in scopes
        assert "offline_access" in scopes

    def test_scope_non_string_non_list_yields_empty(self):
        adapter = _adapter(scopes_claim="scp")
        claims = _base_claims(scp=42)
        scopes = adapter._extract_scopes(claims)
        assert scopes == []

    def test_whitespace_only_scope_string_yields_empty(self):
        adapter = _adapter(scopes_claim="scp")
        claims = _base_claims(scp="   ")
        scopes = adapter._extract_scopes(claims)
        assert scopes == []


# ---------------------------------------------------------------------------
# is_enabled / configuration
# ---------------------------------------------------------------------------


class TestGenericJWTAdapterConfig:
    def test_is_enabled_with_url_and_issuer(self):
        adapter = _adapter()
        assert adapter.is_enabled() is True

    def test_not_enabled_without_jwks_url(self):
        adapter = _adapter(jwks_url="")
        assert adapter.is_enabled() is False

    def test_not_enabled_without_issuer(self):
        adapter = _adapter(issuer="")
        assert adapter.is_enabled() is False

    def test_not_enabled_with_neither(self):
        adapter = GenericJWTAdapter(config=JWTAdapterConfig())
        assert adapter.is_enabled() is False

    def test_is_enabled_with_oidc_authority(self):
        adapter = GenericJWTAdapter(config=JWTAdapterConfig(oidc_authority="https://auth.example.com"))
        assert adapter.is_enabled() is True

    def test_is_enabled_with_oidc_discovery_url(self):
        adapter = GenericJWTAdapter(
            config=JWTAdapterConfig(
                oidc_discovery_url="https://auth.example.com/.well-known/openid-configuration",
            )
        )
        assert adapter.is_enabled() is True

    def test_name_is_jwt(self):
        assert GenericJWTAdapter.name == "jwt"

    def test_validate_token_empty_raises_auth_error(self):
        adapter = _adapter()
        with pytest.raises(AuthError) as exc_info:
            asyncio.run(adapter.validate_token(""))
        assert exc_info.value.status_code == 401

    def test_validate_token_whitespace_raises_auth_error(self):
        adapter = _adapter()
        with pytest.raises(AuthError) as exc_info:
            asyncio.run(adapter.validate_token("   "))
        assert exc_info.value.status_code == 401

    def test_get_jwks_client_raises_when_url_empty(self):
        adapter = GenericJWTAdapter(config=JWTAdapterConfig(issuer="https://auth.example.com/"))
        with pytest.raises(AuthError) as exc_info:
            adapter._get_jwks_client()
        assert exc_info.value.status_code == 500

    def test_default_algorithms(self):
        adapter = GenericJWTAdapter(config=JWTAdapterConfig())
        assert "RS256" in adapter._config.algorithms

    def test_config_from_env_defaults(self, monkeypatch):
        # Clear relevant env vars to test defaults
        for var in (
            "AUTH_JWKS_URL",
            "AUTH_ISSUER",
            "AUTH_AUDIENCE",
            "AUTH_SCOPES_FORMAT",
            "MOZAIKS_OIDC_AUTHORITY",
            "MOZAIKS_OIDC_TENANT_ID",
            "MOZAIKS_OIDC_DISCOVERY_URL",
        ):
            monkeypatch.delenv(var, raising=False)
        config = JWTAdapterConfig.from_env()
        assert config.jwks_url == ""
        assert config.issuer == ""
        assert config.oidc_authority == ""
        assert config.oidc_tenant_id == ""
        assert config.oidc_discovery_url == ""
        assert config.scopes_format == "space"
        assert config.user_id_claim == "sub"

    def test_config_from_env_reads_vars(self, monkeypatch):
        monkeypatch.setenv("AUTH_JWKS_URL", "https://auth.test/.well-known/jwks.json")
        monkeypatch.setenv("AUTH_ISSUER", "https://auth.test/")
        monkeypatch.setenv("MOZAIKS_OIDC_AUTHORITY", "https://login.example.com")
        monkeypatch.setenv("MOZAIKS_OIDC_TENANT_ID", "tenant-1")
        monkeypatch.setenv("MOZAIKS_OIDC_DISCOVERY_URL", "https://login.example.com/.well-known/openid-configuration")
        monkeypatch.setenv("AUTH_SCOPES_FORMAT", "array")
        config = JWTAdapterConfig.from_env()
        assert config.jwks_url == "https://auth.test/.well-known/jwks.json"
        assert config.issuer == "https://auth.test/"
        assert config.oidc_authority == "https://login.example.com"
        assert config.oidc_tenant_id == "tenant-1"
        assert config.oidc_discovery_url == "https://login.example.com/.well-known/openid-configuration"
        assert config.scopes_format == "array"


class TestGenericJWTAdapterValidation:
    @pytest.mark.asyncio
    async def test_validate_token_uses_oidc_discovery_for_jwks_and_issuer(self, monkeypatch):
        issuer = "https://issuer.example.com"
        jwks_url = "https://issuer.example.com/.well-known/jwks.json"
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
        public_jwk.update({"kid": "kid-1", "alg": "RS256", "use": "sig"})

        token = jwt.encode(
            {
                **_base_claims(),
                "iss": issuer,
                "aud": "my-api",
                "iat": int(time.time()),
                "nbf": int(time.time()) - 1,
                "exp": int(time.time()) + 300,
            },
            key,
            algorithm="RS256",
            headers={"kid": "kid-1"},
        )

        class _FakeDiscoveryClient:
            async def get_jwks_uri(self):
                return jwks_url

            async def get_issuer(self):
                return issuer

        class _FakeJWKSClient:
            def __init__(self, *, jwks_url: str, use_discovery: bool):
                self.jwks_url = jwks_url
                self.use_discovery = use_discovery

            async def get_signing_key(self, kid: str):
                assert kid == "kid-1"
                assert self.jwks_url == jwks_url
                assert self.use_discovery is False
                return public_jwk

        monkeypatch.setattr(jwt_adapter_module, "JWKSClient", _FakeJWKSClient)
        adapter = GenericJWTAdapter(
            config=JWTAdapterConfig(
                audience="my-api",
                oidc_authority="https://issuer.example.com",
            )
        )
        monkeypatch.setattr(adapter, "_get_discovery_client", lambda: _FakeDiscoveryClient())

        claims = await adapter.validate_token(token)

        assert claims.user_id == "user-abc-123"
        assert claims.tenant_id == "tenant-xyz"
        assert claims.workspace_id == "workspace-xyz"
        assert claims.app_id == "app-xyz"
