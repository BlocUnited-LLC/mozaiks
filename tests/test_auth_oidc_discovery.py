"""
Tests for OSS auth OIDC discovery — provider-neutrality and configuration hygiene.

Covers:
  1. No proprietary CIAM URL/tenant ID in OSS defaults.
  2. Missing authority produces a clear RuntimeError, not silent CIAM fallback.
  3. Explicit MOZAIKS_OIDC_DISCOVERY_URL configures discovery correctly.
  4. MOZAIKS_OIDC_AUTHORITY + MOZAIKS_OIDC_TENANT_ID builds the right URL.
  5. MOZAIKS_OIDC_AUTHORITY alone (no tenant) uses bare well-known endpoint.
  6. AuthConfig defaults carry no proprietary authority, audience, or scope.
  7. Explicit AUTH_AUDIENCE and AUTH_REQUIRED_SCOPE still work.
  8. Auth adapter registry auto-detection behavior is unchanged.
  9. Hygiene scan: no proprietary strings appear in auth source files.
"""
from __future__ import annotations

import importlib
import inspect
import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_AUTH_ROOT = Path(__file__).resolve().parents[1] / "mozaiksai" / "core" / "auth"

_PROPRIETARY_STRINGS = [
    "mozaiks.ciamlogin.com",
    "9d0073d5-42e8-46f0-a325-5b4be7b1a38d",
    "api://mozaiks-auth",
]


def _read_auth_sources() -> str:
    """Concatenate all .py source files under mozaiksai/core/auth/ for scanning."""
    parts: list[str] = []
    for path in sorted(_AUTH_ROOT.rglob("*.py")):
        try:
            parts.append(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# 1. Hygiene: no proprietary strings in OSS auth sources
# ---------------------------------------------------------------------------

class TestNoProprietaryStringsInOssAuthSources:
    def test_ciam_authority_not_in_source(self) -> None:
        source = _read_auth_sources()
        assert "mozaiks.ciamlogin.com" not in source, (
            "Proprietary CIAM authority URL found in OSS auth sources"
        )

    def test_ciam_tenant_id_not_in_source(self) -> None:
        source = _read_auth_sources()
        assert "9d0073d5-42e8-46f0-a325-5b4be7b1a38d" not in source, (
            "Proprietary CIAM tenant ID found in OSS auth sources"
        )

    def test_ciam_audience_not_in_source(self) -> None:
        source = _read_auth_sources()
        assert "api://mozaiks-auth" not in source, (
            "Proprietary audience string 'api://mozaiks-auth' found in OSS auth sources"
        )


# ---------------------------------------------------------------------------
# 2 & 3. OIDCDiscoveryClient — missing authority raises, does not fall back
# ---------------------------------------------------------------------------

class TestDiscoveryClientWithNoAuthority:
    def _make_client(self, env: dict[str, str] | None = None) -> Any:
        """Import discovery fresh (avoiding module-level singleton state)."""
        # Patch env before import to control what the constructor sees.
        clean_env = {
            k: v for k, v in os.environ.items()
            if not k.startswith(("MOZAIKS_OIDC", "AUTH_DISCOVERY"))
        }
        if env:
            clean_env.update(env)

        import mozaiksai.core.auth.discovery as mod
        mod.reset_discovery_client()
        with patch.dict(os.environ, clean_env, clear=True):
            from mozaiksai.core.auth.discovery import OIDCDiscoveryClient
            return OIDCDiscoveryClient()

    def test_no_authority_sets_discovery_url_to_none(self) -> None:
        import mozaiksai.core.auth.discovery as mod
        mod.reset_discovery_client()

        clean = {
            k: v for k, v in os.environ.items()
            if not k.startswith(("MOZAIKS_OIDC", "AUTH_DISCOVERY"))
        }
        with patch.dict(os.environ, clean, clear=True):
            from mozaiksai.core.auth.discovery import OIDCDiscoveryClient
            client = OIDCDiscoveryClient()

        assert client.discovery_url is None

    @pytest.mark.asyncio
    async def test_no_authority_get_discovery_raises_runtime_error(self) -> None:
        import mozaiksai.core.auth.discovery as mod
        mod.reset_discovery_client()

        clean = {
            k: v for k, v in os.environ.items()
            if not k.startswith(("MOZAIKS_OIDC", "AUTH_DISCOVERY"))
        }
        with patch.dict(os.environ, clean, clear=True):
            from mozaiksai.core.auth.discovery import OIDCDiscoveryClient
            client = OIDCDiscoveryClient()

        with pytest.raises(RuntimeError, match="OIDC discovery is not configured"):
            await client.get_discovery()

    @pytest.mark.asyncio
    async def test_error_message_names_env_vars(self) -> None:
        import mozaiksai.core.auth.discovery as mod
        mod.reset_discovery_client()

        clean = {
            k: v for k, v in os.environ.items()
            if not k.startswith(("MOZAIKS_OIDC", "AUTH_DISCOVERY"))
        }
        with patch.dict(os.environ, clean, clear=True):
            from mozaiksai.core.auth.discovery import OIDCDiscoveryClient
            client = OIDCDiscoveryClient()

        with pytest.raises(RuntimeError) as exc_info:
            await client.get_discovery()

        msg = str(exc_info.value)
        assert "MOZAIKS_OIDC_AUTHORITY" in msg or "MOZAIKS_OIDC_DISCOVERY_URL" in msg


# ---------------------------------------------------------------------------
# 4. Explicit MOZAIKS_OIDC_DISCOVERY_URL configures the client correctly
# ---------------------------------------------------------------------------

class TestExplicitDiscoveryUrl:
    def test_explicit_env_url_is_used(self) -> None:
        import mozaiksai.core.auth.discovery as mod
        mod.reset_discovery_client()

        env = {"MOZAIKS_OIDC_DISCOVERY_URL": "https://idp.example.com/.well-known/openid-configuration"}
        with patch.dict(os.environ, env):
            from mozaiksai.core.auth.discovery import OIDCDiscoveryClient
            client = OIDCDiscoveryClient()

        assert client.discovery_url == "https://idp.example.com/.well-known/openid-configuration"

    def test_explicit_constructor_url_is_used(self) -> None:
        import mozaiksai.core.auth.discovery as mod
        mod.reset_discovery_client()

        clean = {k: v for k, v in os.environ.items() if not k.startswith("MOZAIKS_OIDC")}
        with patch.dict(os.environ, clean, clear=True):
            from mozaiksai.core.auth.discovery import OIDCDiscoveryClient
            client = OIDCDiscoveryClient(
                discovery_url="https://auth.example.org/openid-configuration"
            )

        assert client.discovery_url == "https://auth.example.org/openid-configuration"

    def test_env_url_takes_priority_over_constructor_url(self) -> None:
        import mozaiksai.core.auth.discovery as mod
        mod.reset_discovery_client()

        env = {"MOZAIKS_OIDC_DISCOVERY_URL": "https://env-override.example.com/.well-known"}
        with patch.dict(os.environ, env):
            from mozaiksai.core.auth.discovery import OIDCDiscoveryClient
            client = OIDCDiscoveryClient(
                discovery_url="https://constructor-arg.example.com/.well-known"
            )

        assert client.discovery_url == "https://env-override.example.com/.well-known"


# ---------------------------------------------------------------------------
# 5. MOZAIKS_OIDC_AUTHORITY + MOZAIKS_OIDC_TENANT_ID builds the right URL
# ---------------------------------------------------------------------------

class TestAuthorityAndTenantDiscoveryUrl:
    def test_authority_and_tenant_compose_discovery_url(self) -> None:
        import mozaiksai.core.auth.discovery as mod
        mod.reset_discovery_client()

        env = {
            "MOZAIKS_OIDC_AUTHORITY": "https://login.example.com",
            "MOZAIKS_OIDC_TENANT_ID": "tenant-abc-123",
        }
        clean = {
            k: v for k, v in os.environ.items()
            if not k.startswith(("MOZAIKS_OIDC", "AUTH_DISCOVERY"))
        }
        clean.update(env)
        with patch.dict(os.environ, clean, clear=True):
            from mozaiksai.core.auth.discovery import OIDCDiscoveryClient
            client = OIDCDiscoveryClient()

        assert client.discovery_url == (
            "https://login.example.com/tenant-abc-123/v2.0/.well-known/openid-configuration"
        )

    def test_authority_without_tenant_uses_bare_well_known(self) -> None:
        import mozaiksai.core.auth.discovery as mod
        mod.reset_discovery_client()

        env = {"MOZAIKS_OIDC_AUTHORITY": "https://auth.example.org"}
        clean = {
            k: v for k, v in os.environ.items()
            if not k.startswith(("MOZAIKS_OIDC", "AUTH_DISCOVERY"))
        }
        clean.update(env)
        with patch.dict(os.environ, clean, clear=True):
            from mozaiksai.core.auth.discovery import OIDCDiscoveryClient
            client = OIDCDiscoveryClient()

        assert client.discovery_url == "https://auth.example.org/.well-known/openid-configuration"

    def test_trailing_slash_on_authority_is_stripped(self) -> None:
        import mozaiksai.core.auth.discovery as mod
        mod.reset_discovery_client()

        env = {
            "MOZAIKS_OIDC_AUTHORITY": "https://login.example.com/",
            "MOZAIKS_OIDC_TENANT_ID": "t1",
        }
        clean = {
            k: v for k, v in os.environ.items()
            if not k.startswith(("MOZAIKS_OIDC", "AUTH_DISCOVERY"))
        }
        clean.update(env)
        with patch.dict(os.environ, clean, clear=True):
            from mozaiksai.core.auth.discovery import OIDCDiscoveryClient
            client = OIDCDiscoveryClient()

        assert "//" not in client.discovery_url.replace("https://", ""), (
            "Double-slash in discovery URL from trailing slash on authority"
        )


# ---------------------------------------------------------------------------
# 6. AuthConfig defaults carry no proprietary authority, audience, or scope
# ---------------------------------------------------------------------------

class TestAuthConfigProviderNeutralDefaults:
    def _load_config(self) -> Any:
        import mozaiksai.core.auth.config as mod
        mod.clear_auth_config_cache()
        clean = {
            k: v for k, v in os.environ.items()
            if not k.startswith((
                "MOZAIKS_OIDC", "AUTH_AUDIENCE", "AUTH_REQUIRED_SCOPE",
                "AUTH_ISSUER", "AUTH_JWKS_URL",
            ))
        }
        with patch.dict(os.environ, clean, clear=True):
            from mozaiksai.core.auth.config import get_auth_config
            config = get_auth_config()
        mod.clear_auth_config_cache()
        return config

    def test_default_oidc_authority_is_empty(self) -> None:
        config = self._load_config()
        assert config.oidc_authority == "", (
            f"Default oidc_authority must be empty, got: {config.oidc_authority!r}"
        )

    def test_default_oidc_tenant_id_is_empty(self) -> None:
        config = self._load_config()
        assert config.oidc_tenant_id == ""

    def test_default_audience_is_empty(self) -> None:
        config = self._load_config()
        assert config.audience == "", (
            f"Default audience must be empty (no Mozaiks-specific audience), got: {config.audience!r}"
        )

    def test_default_required_scope_is_empty(self) -> None:
        config = self._load_config()
        assert config.required_scope == "", (
            f"Default required_scope must be empty, got: {config.required_scope!r}"
        )

    def test_no_proprietary_string_in_defaults(self) -> None:
        config = self._load_config()
        all_values = (
            config.oidc_authority
            + config.oidc_tenant_id
            + config.oidc_discovery_url
            + config.audience
            + config.required_scope
        )
        for s in _PROPRIETARY_STRINGS:
            assert s not in all_values, (
                f"Proprietary string {s!r} found in AuthConfig defaults"
            )


# ---------------------------------------------------------------------------
# 7. Explicit AUTH_AUDIENCE and AUTH_REQUIRED_SCOPE still work
# ---------------------------------------------------------------------------

class TestAuthConfigExplicitValues:
    def test_explicit_audience_is_loaded(self) -> None:
        import mozaiksai.core.auth.config as mod
        mod.clear_auth_config_cache()
        with patch.dict(os.environ, {"AUTH_AUDIENCE": "api://my-app"}):
            from mozaiksai.core.auth.config import get_auth_config
            config = get_auth_config()
        mod.clear_auth_config_cache()
        assert config.audience == "api://my-app"

    def test_explicit_required_scope_is_loaded(self) -> None:
        import mozaiksai.core.auth.config as mod
        mod.clear_auth_config_cache()
        with patch.dict(os.environ, {"AUTH_REQUIRED_SCOPE": "read:data"}):
            from mozaiksai.core.auth.config import get_auth_config
            config = get_auth_config()
        mod.clear_auth_config_cache()
        assert config.required_scope == "read:data"

    def test_explicit_oidc_authority_is_loaded(self) -> None:
        import mozaiksai.core.auth.config as mod
        mod.clear_auth_config_cache()
        with patch.dict(os.environ, {"MOZAIKS_OIDC_AUTHORITY": "https://idp.myorg.com"}):
            from mozaiksai.core.auth.config import get_auth_config
            config = get_auth_config()
        mod.clear_auth_config_cache()
        assert config.oidc_authority == "https://idp.myorg.com"


# ---------------------------------------------------------------------------
# 8. Auth adapter registry auto-detection behavior is unchanged
# ---------------------------------------------------------------------------

class TestAdapterRegistryAutoDetection:
    def _detect(self, env_overrides: dict[str, str]) -> str:
        """Run auto-detection with specific env overrides, return provider name."""
        clean = {
            k: v for k, v in os.environ.items()
            if k not in {
                "AUTH_PROVIDER", "AUTH_ENABLED",
                "SUPABASE_URL", "KEYCLOAK_URL", "KEYCLOAK_REALM",
                "AUTH_JWKS_URL", "AUTH_ISSUER",
            }
        }
        clean.update(env_overrides)
        import mozaiksai.core.auth.adapters.registry as reg
        with patch.dict(os.environ, clean, clear=True):
            return reg._auto_detect_provider()

    def test_explicit_auth_provider_wins(self) -> None:
        assert self._detect({"AUTH_PROVIDER": "keycloak"}) == "keycloak"

    def test_auth_enabled_false_returns_none(self) -> None:
        assert self._detect({"AUTH_ENABLED": "false"}) == "none"

    def test_supabase_url_selects_supabase(self) -> None:
        assert self._detect({"SUPABASE_URL": "https://xyz.supabase.co"}) == "supabase"

    def test_keycloak_url_and_realm_select_keycloak(self) -> None:
        assert self._detect({
            "KEYCLOAK_URL": "https://kc.example.com",
            "KEYCLOAK_REALM": "myrealm",
        }) == "keycloak"

    def test_jwks_url_and_issuer_select_jwt(self) -> None:
        assert self._detect({
            "AUTH_JWKS_URL": "https://idp.example.com/.well-known/jwks.json",
            "AUTH_ISSUER": "https://idp.example.com",
        }) == "jwt"

    def test_no_config_defaults_to_none(self) -> None:
        assert self._detect({}) == "none"
