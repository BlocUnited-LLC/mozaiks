"""
Auth adapter unit tests: UserClaims, AuthError, BaseAuthAdapter, NoAuthAdapter.

Covers:
  UserClaims:
    - has_role: present and absent
    - has_any_role: match and no match
    - has_scope: present and absent
    - get_claim: present, absent with default, absent without default
    - default fields
    - provider metadata fields

  AuthError:
    - message, status_code, provider stored
    - default status_code 401
    - __str__ includes provider and message

  BaseAuthAdapter._extract_bearer_token:
    - missing header raises AuthError 401
    - invalid format raises AuthError 401
    - valid Bearer token returns token part
    - case insensitive "bearer" prefix

  NoAuthAdapter:
    - validate_token returns anonymous UserClaims regardless of input
    - validate_token_sync same
    - is_enabled always True
    - default user_id "anonymous"
    - custom user_id via constructor
    - AUTH_ANON_USER_ID env var honored
    - AUTH_ANON_EMAIL env var honored
    - AUTH_ANON_ROLES env var parsed
    - claims always have access_as_user scope
    - provider always "none"
"""
from __future__ import annotations

import pytest

from mozaiksai.core.auth.adapters.base import (
    AuthError,
    BaseAuthAdapter,
    UserClaims,
)
from mozaiksai.core.auth.adapters.no_auth import NoAuthAdapter

# ---------------------------------------------------------------------------
# 1. UserClaims
# ---------------------------------------------------------------------------

class TestUserClaims:
    def test_has_role_present(self):
        claims = UserClaims(user_id="u1", roles=["admin", "user"])
        assert claims.has_role("admin") is True

    def test_has_role_absent(self):
        claims = UserClaims(user_id="u1", roles=["user"])
        assert claims.has_role("admin") is False

    def test_has_any_role_match(self):
        claims = UserClaims(user_id="u1", roles=["admin"])
        assert claims.has_any_role(["user", "admin"]) is True

    def test_has_any_role_no_match(self):
        claims = UserClaims(user_id="u1", roles=["viewer"])
        assert claims.has_any_role(["user", "admin"]) is False

    def test_has_any_role_empty_roles(self):
        claims = UserClaims(user_id="u1", roles=[])
        assert claims.has_any_role(["admin"]) is False

    def test_has_scope_present(self):
        claims = UserClaims(user_id="u1", scopes=["access_as_user"])
        assert claims.has_scope("access_as_user") is True

    def test_has_scope_absent(self):
        claims = UserClaims(user_id="u1", scopes=[])
        assert claims.has_scope("access_as_user") is False

    def test_get_claim_present(self):
        claims = UserClaims(user_id="u1", raw_claims={"custom_key": "value"})
        assert claims.get_claim("custom_key") == "value"

    def test_get_claim_absent_with_default(self):
        claims = UserClaims(user_id="u1", raw_claims={})
        assert claims.get_claim("missing", "fallback") == "fallback"

    def test_get_claim_absent_returns_none_by_default(self):
        claims = UserClaims(user_id="u1", raw_claims={})
        assert claims.get_claim("missing") is None

    def test_default_roles_empty_list(self):
        claims = UserClaims(user_id="u1")
        assert claims.roles == []

    def test_default_scopes_empty_list(self):
        claims = UserClaims(user_id="u1")
        assert claims.scopes == []

    def test_default_provider_unknown(self):
        claims = UserClaims(user_id="u1")
        assert claims.provider == "unknown"

    def test_provider_set(self):
        claims = UserClaims(user_id="u1", provider="keycloak")
        assert claims.provider == "keycloak"

    def test_optional_fields_none(self):
        claims = UserClaims(user_id="u1")
        assert claims.email is None
        assert claims.name is None
        assert claims.app_id is None
        assert claims.tenant_id is None


# ---------------------------------------------------------------------------
# 2. AuthError
# ---------------------------------------------------------------------------

class TestAuthError:
    def test_message_stored(self):
        err = AuthError("Token expired")
        assert err.message == "Token expired"

    def test_default_status_code_401(self):
        err = AuthError("Bad token")
        assert err.status_code == 401

    def test_custom_status_code(self):
        err = AuthError("Not found", status_code=403)
        assert err.status_code == 403

    def test_provider_stored(self):
        err = AuthError("Bad token", provider="keycloak")
        assert err.provider == "keycloak"

    def test_default_provider_unknown(self):
        err = AuthError("Bad token")
        assert err.provider == "unknown"

    def test_str_includes_provider_and_message(self):
        err = AuthError("Token expired", provider="auth0")
        assert "auth0" in str(err)
        assert "Token expired" in str(err)

    def test_is_exception(self):
        err = AuthError("Bad")
        with pytest.raises(AuthError):
            raise err


# ---------------------------------------------------------------------------
# 3. BaseAuthAdapter._extract_bearer_token
# ---------------------------------------------------------------------------

class TestExtractBearerToken:
    def _adapter(self) -> BaseAuthAdapter:
        return BaseAuthAdapter()

    def test_missing_header_raises_auth_error(self):
        adapter = self._adapter()
        with pytest.raises(AuthError) as exc_info:
            adapter._extract_bearer_token("")
        assert exc_info.value.status_code == 401

    def test_none_header_raises_auth_error(self):
        adapter = self._adapter()
        with pytest.raises(AuthError):
            adapter._extract_bearer_token(None)  # type: ignore[arg-type]

    def test_invalid_format_raises_auth_error(self):
        adapter = self._adapter()
        with pytest.raises(AuthError) as exc_info:
            adapter._extract_bearer_token("NotBearerFormat")
        assert exc_info.value.status_code == 401

    def test_missing_token_after_bearer_raises(self):
        adapter = self._adapter()
        with pytest.raises(AuthError):
            adapter._extract_bearer_token("Bearer")

    def test_valid_bearer_returns_token(self):
        adapter = self._adapter()
        result = adapter._extract_bearer_token("Bearer eyJtoken123")
        assert result == "eyJtoken123"

    def test_case_insensitive_bearer(self):
        adapter = self._adapter()
        result = adapter._extract_bearer_token("bearer eyJtoken123")
        assert result == "eyJtoken123"

    def test_too_many_parts_raises(self):
        adapter = self._adapter()
        with pytest.raises(AuthError):
            adapter._extract_bearer_token("Bearer tok extra")


# ---------------------------------------------------------------------------
# 4. NoAuthAdapter
# ---------------------------------------------------------------------------

class TestNoAuthAdapter:
    @pytest.mark.asyncio
    async def test_validate_token_returns_user_claims(self):
        adapter = NoAuthAdapter()
        claims = await adapter.validate_token("any-token")
        assert isinstance(claims, UserClaims)

    @pytest.mark.asyncio
    async def test_validate_token_default_user_id_anonymous(self, monkeypatch):
        monkeypatch.delenv("AUTH_ANON_USER_ID", raising=False)
        adapter = NoAuthAdapter()
        claims = await adapter.validate_token("ignored")
        assert claims.user_id == "anonymous"

    @pytest.mark.asyncio
    async def test_validate_token_custom_user_id(self, monkeypatch):
        monkeypatch.delenv("AUTH_ANON_USER_ID", raising=False)
        adapter = NoAuthAdapter(default_user_id="dev-user")
        claims = await adapter.validate_token("ignored")
        assert claims.user_id == "dev-user"

    @pytest.mark.asyncio
    async def test_validate_token_env_user_id(self, monkeypatch):
        monkeypatch.setenv("AUTH_ANON_USER_ID", "env-user")
        adapter = NoAuthAdapter()
        claims = await adapter.validate_token("ignored")
        assert claims.user_id == "env-user"

    @pytest.mark.asyncio
    async def test_validate_token_env_email(self, monkeypatch):
        monkeypatch.setenv("AUTH_ANON_EMAIL", "dev@example.com")
        adapter = NoAuthAdapter()
        claims = await adapter.validate_token("ignored")
        assert claims.email == "dev@example.com"

    @pytest.mark.asyncio
    async def test_validate_token_has_access_as_user_scope(self):
        adapter = NoAuthAdapter()
        claims = await adapter.validate_token("ignored")
        assert "access_as_user" in claims.scopes

    @pytest.mark.asyncio
    async def test_validate_token_provider_is_none(self):
        adapter = NoAuthAdapter()
        claims = await adapter.validate_token("ignored")
        assert claims.provider == "none"

    @pytest.mark.asyncio
    async def test_validate_token_env_roles_parsed(self, monkeypatch):
        monkeypatch.setenv("AUTH_ANON_ROLES", "admin,user")
        adapter = NoAuthAdapter()
        claims = await adapter.validate_token("ignored")
        assert "admin" in claims.roles
        assert "user" in claims.roles

    @pytest.mark.asyncio
    async def test_validate_token_custom_roles(self, monkeypatch):
        monkeypatch.delenv("AUTH_ANON_ROLES", raising=False)
        adapter = NoAuthAdapter(default_roles=["superadmin"])
        claims = await adapter.validate_token("ignored")
        assert "superadmin" in claims.roles

    def test_validate_token_sync_returns_user_claims(self):
        adapter = NoAuthAdapter()
        claims = adapter.validate_token_sync("any-token")
        assert isinstance(claims, UserClaims)
        assert claims.provider == "none"

    def test_is_enabled_always_true(self):
        adapter = NoAuthAdapter()
        assert adapter.is_enabled() is True

    def test_name_is_none(self):
        assert NoAuthAdapter.name == "none"

    @pytest.mark.asyncio
    async def test_any_token_accepted(self):
        adapter = NoAuthAdapter()
        for token in ["", "garbage", "Bearer eyJ...", "123"]:
            claims = await adapter.validate_token(token)
            assert isinstance(claims, UserClaims)


# ---------------------------------------------------------------------------
# Provider detection fail-closed contract
# ---------------------------------------------------------------------------


class TestProviderDetectionFailClosed:
    """AUTH_ENABLED=true must never silently resolve to the trusted-bypass
    'none' provider; explicit disablement is the only unauthenticated mode."""

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        from mozaiksai.core.auth.adapters.registry import reset_auth_adapter

        for var in (
            "AUTH_ENABLED",
            "AUTH_PROVIDER",
            "SUPABASE_URL",
            "KEYCLOAK_URL",
            "KEYCLOAK_REALM",
            "AUTH_JWKS_URL",
            "AUTH_ISSUER",
            "MOZAIKS_OIDC_AUTHORITY",
            "MOZAIKS_OIDC_DISCOVERY_URL",
        ):
            monkeypatch.delenv(var, raising=False)
        reset_auth_adapter()
        yield
        reset_auth_adapter()

    def test_auth_enabled_true_without_provider_raises(self, monkeypatch):
        from mozaiksai.core.auth.adapters.registry import resolve_auth_config

        monkeypatch.setenv("AUTH_ENABLED", "true")
        with pytest.raises(AuthError, match="no authentication provider"):
            resolve_auth_config()

    def test_is_auth_enabled_fails_closed_when_misconfigured(self, monkeypatch):
        """is_auth_enabled must raise (fail closed), never return False, when
        AUTH_ENABLED=true has no resolvable provider."""
        from mozaiksai.core.auth.adapters.registry import is_auth_enabled

        monkeypatch.setenv("AUTH_ENABLED", "true")
        with pytest.raises(AuthError):
            is_auth_enabled()

    def test_auth_enabled_true_with_provider_none_conflicts(self, monkeypatch):
        from mozaiksai.core.auth.adapters.registry import resolve_auth_config

        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("AUTH_PROVIDER", "none")
        with pytest.raises(AuthError, match="conflicts"):
            resolve_auth_config()

    def test_unrecognized_auth_enabled_value_raises(self, monkeypatch):
        from mozaiksai.core.auth.adapters.registry import resolve_auth_config

        monkeypatch.setenv("AUTH_ENABLED", "tru")
        with pytest.raises(AuthError, match="Unrecognized AUTH_ENABLED"):
            resolve_auth_config()

    def test_auth_enabled_true_with_supabase_url_resolves(self, monkeypatch):
        from mozaiksai.core.auth.adapters.registry import resolve_auth_config

        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
        assert resolve_auth_config().provider == "supabase"

    def test_auth_enabled_false_resolves_none(self, monkeypatch):
        from mozaiksai.core.auth.adapters.registry import resolve_auth_config

        monkeypatch.setenv("AUTH_ENABLED", "false")
        assert resolve_auth_config().provider == "none"

    def test_nothing_configured_resolves_demo_none(self):
        from mozaiksai.core.auth.adapters.registry import resolve_auth_config

        assert resolve_auth_config().provider == "none"

    def test_explicitly_disabled_only_for_explicit_declarations(self, monkeypatch):
        from mozaiksai.core.auth.adapters.registry import is_auth_explicitly_disabled

        # Implicit demo mode is NOT explicit disablement.
        assert is_auth_explicitly_disabled() is False

        monkeypatch.setenv("AUTH_ENABLED", "false")
        assert is_auth_explicitly_disabled() is True

        monkeypatch.delenv("AUTH_ENABLED", raising=False)
        monkeypatch.setenv("AUTH_PROVIDER", "none")
        assert is_auth_explicitly_disabled() is True

        # Enabled deployments are never "explicitly disabled".
        monkeypatch.delenv("AUTH_PROVIDER", raising=False)
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
        assert is_auth_explicitly_disabled() is False

    def test_get_adapter_raises_for_enabled_but_unconfigured_provider(self, monkeypatch):
        from mozaiksai.core.auth.adapters.registry import get_auth_adapter
        from mozaiksai.core.auth.config import clear_auth_config_cache

        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("AUTH_PROVIDER", "jwt")
        clear_auth_config_cache()
        try:
            with pytest.raises(AuthError, match="not fully configured"):
                get_auth_adapter(force_provider="jwt")
        finally:
            clear_auth_config_cache()


# ---------------------------------------------------------------------------
# Canonical resolved-auth state: contradiction + environment matrix (D2/D4)
# ---------------------------------------------------------------------------

_AUTH_MATRIX_VARS = (
    "AUTH_ENABLED",
    "AUTH_PROVIDER",
    "SUPABASE_URL",
    "SUPABASE_JWT_SECRET",
    "KEYCLOAK_URL",
    "KEYCLOAK_REALM",
    "AUTH_JWKS_URL",
    "AUTH_ISSUER",
    "MOZAIKS_OIDC_AUTHORITY",
    "MOZAIKS_OIDC_DISCOVERY_URL",
    "ENV",
    "ENVIRONMENT",
)


def _set_matrix_env(monkeypatch, env: dict) -> None:
    from mozaiksai.core.auth.adapters.registry import reset_auth_adapter
    from mozaiksai.core.auth.config import clear_auth_config_cache

    for var in _AUTH_MATRIX_VARS:
        monkeypatch.delenv(var, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    clear_auth_config_cache()
    reset_auth_adapter()


class TestResolvedAuthStateMatrix:
    """One canonical interpretation: predicates can never contradict, explicit
    contradictions reject, and protected environments refuse no-auth."""

    @pytest.fixture(autouse=True)
    def _teardown(self):
        from mozaiksai.core.auth.adapters.registry import reset_auth_adapter
        from mozaiksai.core.auth.config import clear_auth_config_cache

        yield
        clear_auth_config_cache()
        reset_auth_adapter()

    # -- contradiction matrix ------------------------------------------------

    @pytest.mark.parametrize(
        "env",
        [
            {"AUTH_ENABLED": "true", "AUTH_PROVIDER": "none"},
            {"AUTH_ENABLED": "false", "AUTH_PROVIDER": "jwt"},
            {"AUTH_ENABLED": "false", "AUTH_PROVIDER": "supabase", "SUPABASE_URL": "https://x.supabase.co"},
            {"AUTH_ENABLED": "tru"},
            {"AUTH_ENABLED": "enabled"},
        ],
    )
    def test_contradictory_or_malformed_declarations_reject(self, monkeypatch, env):
        from mozaiksai.core.auth.adapters.registry import resolve_auth_config

        _set_matrix_env(monkeypatch, env)
        with pytest.raises(AuthError):
            resolve_auth_config()

    def test_truthy_variant_without_provider_rejects(self, monkeypatch):
        from mozaiksai.core.auth.adapters.registry import resolve_auth_config

        _set_matrix_env(monkeypatch, {"AUTH_ENABLED": "1 "})
        with pytest.raises(AuthError, match="no authentication provider"):
            resolve_auth_config()

    def test_explicit_disable_beats_passive_provider_signals(self, monkeypatch):
        """A SUPABASE_URL left in a developer .env does not contradict an
        explicit AUTH_ENABLED=false: the explicit switch wins over passive
        presence (existing OSS dev contract)."""
        from mozaiksai.core.auth.adapters.registry import resolve_auth_config

        _set_matrix_env(
            monkeypatch,
            {"AUTH_ENABLED": "false", "SUPABASE_URL": "https://x.supabase.co"},
        )
        config = resolve_auth_config()
        assert config.provider == "none"
        assert config.explicitly_disabled is True
        assert config.enabled is False

    # -- predicate coherence -------------------------------------------------

    @pytest.mark.parametrize(
        "env",
        [
            {},
            {"AUTH_ENABLED": "false"},
            {"AUTH_PROVIDER": "none"},
            {"AUTH_ENABLED": "true", "SUPABASE_URL": "https://x.supabase.co"},
            {"AUTH_PROVIDER": "supabase", "SUPABASE_URL": "https://x.supabase.co"},
            {"KEYCLOAK_URL": "https://kc.example.com", "KEYCLOAK_REALM": "r"},
            {"AUTH_JWKS_URL": "https://x/jwks.json", "AUTH_ISSUER": "https://x"},
            {"MOZAIKS_OIDC_AUTHORITY": "https://login.example.com"},
        ],
    )
    def test_enabled_and_explicitly_disabled_never_both_true(self, monkeypatch, env):
        from mozaiksai.core.auth.adapters.registry import (
            is_auth_enabled,
            is_auth_explicitly_disabled,
        )

        _set_matrix_env(monkeypatch, env)
        assert not (is_auth_enabled() and is_auth_explicitly_disabled())

    def test_astra_d4_reproduction_now_rejects(self, monkeypatch):
        """The exact D4 contradiction (explicit provider + explicit disable)
        raises from BOTH predicates instead of answering inconsistently."""
        from mozaiksai.core.auth.adapters.registry import (
            is_auth_enabled,
            is_auth_explicitly_disabled,
        )

        _set_matrix_env(monkeypatch, {"AUTH_ENABLED": "false", "AUTH_PROVIDER": "jwt"})
        with pytest.raises(AuthError):
            is_auth_enabled()
        with pytest.raises(AuthError):
            is_auth_explicitly_disabled()

    # -- protected-environment policy (D2) -----------------------------------

    @pytest.mark.parametrize("environment", ["staging", "production", "stage", "prod"])
    @pytest.mark.parametrize(
        "env",
        [
            {"AUTH_ENABLED": "false"},
            {"AUTH_PROVIDER": "none"},
            {},
        ],
    )
    def test_protected_environments_reject_all_no_auth_operation(self, monkeypatch, environment, env):
        from mozaiksai.core.auth.adapters.registry import resolve_auth_config

        _set_matrix_env(monkeypatch, {**env, "ENV": environment})
        with pytest.raises(AuthError, match="not permitted"):
            resolve_auth_config()

    @pytest.mark.parametrize("environment", ["staging", "production"])
    def test_protected_environments_accept_configured_provider(self, monkeypatch, environment):
        from mozaiksai.core.auth.adapters.registry import resolve_auth_config

        _set_matrix_env(
            monkeypatch,
            {"AUTH_ENABLED": "true", "SUPABASE_URL": "https://x.supabase.co", "ENV": environment},
        )
        config = resolve_auth_config()
        assert config.provider == "supabase"
        assert config.enabled is True

    @pytest.mark.parametrize("environment", ["", "development", "dev", "test", "local", "qa-lab"])
    def test_unprotected_environments_follow_dev_contract(self, monkeypatch, environment):
        from mozaiksai.core.auth.adapters.registry import resolve_auth_config

        env = {"AUTH_ENABLED": "false"}
        if environment:
            env["ENV"] = environment
        _set_matrix_env(monkeypatch, env)
        config = resolve_auth_config()
        assert config.provider == "none"
        assert config.explicitly_disabled is True

    def test_environment_var_fallback_also_protected(self, monkeypatch):
        """ENVIRONMENT (without ENV) is the same canonical vocabulary."""
        from mozaiksai.core.auth.adapters.registry import resolve_auth_config

        _set_matrix_env(monkeypatch, {"AUTH_ENABLED": "false", "ENVIRONMENT": "staging"})
        with pytest.raises(AuthError, match="not permitted"):
            resolve_auth_config()


# ---------------------------------------------------------------------------
# Cache/config coherence transitions (D5)
# ---------------------------------------------------------------------------


class TestAdapterCacheCoherence:
    """The cached adapter is keyed by the resolved-config fingerprint: request
    resolution always matches the configuration startup validated, and no
    stale trusted-bypass adapter survives a configuration change."""

    @pytest.fixture(autouse=True)
    def _fresh(self, monkeypatch):
        from mozaiksai.core.auth.adapters.registry import reset_auth_adapter
        from mozaiksai.core.auth.config import clear_auth_config_cache

        for var in _AUTH_MATRIX_VARS:
            monkeypatch.delenv(var, raising=False)
        clear_auth_config_cache()
        reset_auth_adapter()
        yield
        clear_auth_config_cache()
        reset_auth_adapter()

    def _jwt_env(self, monkeypatch) -> None:
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("AUTH_PROVIDER", "jwt")
        monkeypatch.setenv("AUTH_JWKS_URL", "https://example.com/.well-known/jwks.json")
        monkeypatch.setenv("AUTH_ISSUER", "https://example.com")

    def test_astra_d5_sequence_none_then_jwt_startup_binds_jwt(self, monkeypatch):
        """demo 'none' resolved first -> config becomes valid JWT -> startup
        validation -> requests MUST use the JWT adapter, with no manual reset."""
        from mozaiksai.core.auth.adapters.registry import (
            get_auth_adapter,
            validate_auth_provider_configuration,
        )

        assert get_auth_adapter().name == "none"  # demo mode cached first

        self._jwt_env(monkeypatch)
        assert validate_auth_provider_configuration() == "jwt"
        assert get_auth_adapter().name == "jwt"

    def test_jwt_then_explicit_disable_in_dev_updates_consistently(self, monkeypatch):
        from mozaiksai.core.auth.adapters.registry import get_auth_adapter

        self._jwt_env(monkeypatch)
        assert get_auth_adapter().name == "jwt"

        for var in ("AUTH_PROVIDER", "AUTH_JWKS_URL", "AUTH_ISSUER"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("AUTH_ENABLED", "false")
        assert get_auth_adapter().name == "none"

    def test_provider_a_to_provider_b(self, monkeypatch):
        from mozaiksai.core.auth.adapters.registry import get_auth_adapter

        monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
        assert get_auth_adapter().name == "supabase"

        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.setenv("KEYCLOAK_URL", "https://kc.example.com")
        monkeypatch.setenv("KEYCLOAK_REALM", "realm")
        assert get_auth_adapter().name == "keycloak"

    def test_valid_provider_to_malformed_config_fails_closed(self, monkeypatch):
        from mozaiksai.core.auth.adapters.registry import get_auth_adapter

        monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
        assert get_auth_adapter().name == "supabase"

        monkeypatch.setenv("AUTH_ENABLED", "tru")
        with pytest.raises(AuthError, match="Unrecognized AUTH_ENABLED"):
            get_auth_adapter()

    def test_malformed_config_to_valid_provider_recovers(self, monkeypatch):
        from mozaiksai.core.auth.adapters.registry import get_auth_adapter

        monkeypatch.setenv("AUTH_ENABLED", "tru")
        with pytest.raises(AuthError):
            get_auth_adapter()

        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
        assert get_auth_adapter().name == "supabase"

    def test_repeated_initialization_is_deterministic_and_cached(self, monkeypatch):
        from mozaiksai.core.auth.adapters.registry import (
            get_auth_adapter,
            validate_auth_provider_configuration,
        )

        self._jwt_env(monkeypatch)
        assert validate_auth_provider_configuration() == "jwt"
        first = get_auth_adapter()
        assert validate_auth_provider_configuration() == "jwt"
        second = get_auth_adapter()
        assert first is second  # unchanged config -> same bound instance

    def test_stale_none_adapter_cannot_survive_authenticated_config(self, monkeypatch):
        """Even without startup validation, a plain request-time resolution
        after the config change must not return the stale 'none' adapter."""
        from mozaiksai.core.auth.adapters.registry import get_auth_adapter

        assert get_auth_adapter().name == "none"
        self._jwt_env(monkeypatch)
        assert get_auth_adapter().name == "jwt"
