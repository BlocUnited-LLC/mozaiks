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

    @pytest.mark.parametrize("environment", ["", "development", "dev", "test", "local"])
    def test_recognized_local_environments_follow_dev_contract(self, monkeypatch, environment):
        """Only the finite recognized local/dev/test allowlist (plus absent)."""
        from mozaiksai.core.auth.adapters.registry import resolve_auth_config

        env = {"AUTH_ENABLED": "false"}
        if environment:
            env["ENV"] = environment
        _set_matrix_env(monkeypatch, env)
        config = resolve_auth_config()
        assert config.provider == "none"
        assert config.explicitly_disabled is True

    @pytest.mark.parametrize(
        "environment",
        [
            "staging-us",
            "prod-us",
            "production-east",
            "preview",
            "qa",
            "qa-lab",
            "customer-prod",
            "sandbox",
            "uat",
            "integration",
            "dev-cluster",
            "testing",
            "локальный",
        ],
    )
    @pytest.mark.parametrize(
        "env",
        [
            {"AUTH_ENABLED": "false"},
            {"AUTH_PROVIDER": "none"},
            {},
        ],
    )
    def test_unknown_environments_never_inherit_no_auth(self, monkeypatch, environment, env):
        """An unknown explicit environment must NOT silently become development.

        Regional/custom production-looking names and arbitrary values alike are
        denied no-auth privilege — the allowlist is finite and fail-closed.
        """
        from mozaiksai.core.auth.adapters.registry import resolve_auth_config

        _set_matrix_env(monkeypatch, {**env, "ENV": environment})
        with pytest.raises(AuthError, match="not permitted"):
            resolve_auth_config()

    @pytest.mark.parametrize(
        "environment", ["staging-us", "prod-us", "preview", "qa", "customer-prod"]
    )
    def test_unknown_environments_boot_with_configured_auth(self, monkeypatch, environment):
        """Unknown environments may still boot — they just cannot run no-auth."""
        from mozaiksai.core.auth.adapters.registry import resolve_auth_config

        _set_matrix_env(
            monkeypatch,
            {
                "AUTH_ENABLED": "true",
                "SUPABASE_URL": "https://x.supabase.co",
                "ENV": environment,
            },
        )
        config = resolve_auth_config()
        assert config.provider == "supabase"
        assert config.enabled is True

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


# ---------------------------------------------------------------------------
# ENV / ENVIRONMENT canonical resolution (D2)
# ---------------------------------------------------------------------------


class TestEnvironmentResolution:
    """Both inputs resolve canonically: trimmed, alias-normalized, blank means
    absent, a blank primary never masks a non-blank fallback, and genuine
    conflicts fail configuration instead of silently picking one."""

    @pytest.fixture(autouse=True)
    def _clean(self, monkeypatch):
        monkeypatch.delenv("ENV", raising=False)
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        yield

    def _resolve(self, monkeypatch, env=None, environment=None):
        from mozaiksai.core.environment import resolve_environment

        monkeypatch.delenv("ENV", raising=False)
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        if env is not None:
            monkeypatch.setenv("ENV", env)
        if environment is not None:
            monkeypatch.setenv("ENVIRONMENT", environment)
        return resolve_environment()

    # -- blank primary must not mask the fallback ---------------------------

    @pytest.mark.parametrize("blank", ["", "   ", "\t"])
    @pytest.mark.parametrize("deployed", ["production", "staging"])
    def test_blank_env_does_not_mask_deployed_environment(self, monkeypatch, blank, deployed):
        resolved = self._resolve(monkeypatch, env=blank, environment=deployed)
        assert resolved.name == deployed
        assert resolved.permits_no_auth is False

    def test_blank_env_does_not_mask_unknown_environment(self, monkeypatch):
        resolved = self._resolve(monkeypatch, env="  ", environment="prod-us")
        assert resolved.name == "prod-us"
        assert resolved.classification == "unknown_deployment"
        assert resolved.permits_no_auth is False

    # -- conflicts ----------------------------------------------------------

    @pytest.mark.parametrize(
        ("env", "environment"),
        [
            ("development", "production"),
            ("development", "staging"),
            ("local", "production"),
            ("test", "prod"),
            ("production", "development"),
            ("staging", "test"),
            ("prod-us", "production"),
            ("foo", "bar"),
        ],
    )
    def test_conflicting_declarations_fail(self, monkeypatch, env, environment):
        from mozaiksai.core.environment import EnvironmentConfigError

        with pytest.raises(EnvironmentConfigError, match="Conflicting"):
            self._resolve(monkeypatch, env=env, environment=environment)

    @pytest.mark.parametrize(
        ("env", "environment"),
        [
            ("test", "development"),
            ("development", "test"),
            ("local", "development"),
            ("test", "local"),
            ("dev", "test"),
        ],
    )
    def test_same_class_local_declarations_are_accepted(self, monkeypatch, env, environment):
        """CI (ENV=test) beside a developer .env (ENVIRONMENT=development) is
        not a conflict: both are recognized local environments, so they agree
        on the only security-relevant question."""
        resolved = self._resolve(monkeypatch, env=env, environment=environment)
        assert resolved.permits_no_auth is True
        assert resolved.classification == "local_development"
        # ENV keeps precedence for the resolved name.
        assert resolved.name == env.replace("dev", "development") if env == "dev" else True

    @pytest.mark.parametrize(
        ("env", "environment"),
        [
            ("production", "staging"),
            ("staging", "production"),
            ("prod-us", "staging-eu"),
        ],
    )
    def test_different_deployed_environments_still_conflict(self, monkeypatch, env, environment):
        """Two distinct deployments are never silently resolved to one."""
        from mozaiksai.core.environment import EnvironmentConfigError

        with pytest.raises(EnvironmentConfigError, match="Conflicting"):
            self._resolve(monkeypatch, env=env, environment=environment)

    @pytest.mark.parametrize(
        ("env", "environment", "expected"),
        [
            ("prod", "production", "production"),
            ("stage", "staging", "staging"),
            ("dev", "development", "development"),
            ("production", "production", "production"),
            ("PRODUCTION", " production ", "production"),
            ("test", "test", "test"),
            ("prod-us", "prod-us", "prod-us"),
        ],
    )
    def test_agreeing_declarations_accepted(self, monkeypatch, env, environment, expected):
        resolved = self._resolve(monkeypatch, env=env, environment=environment)
        assert resolved.name == expected

    # -- classification -----------------------------------------------------

    @pytest.mark.parametrize("value", ["development", "dev", "local", "test", "  TEST  "])
    def test_recognized_local_values_permit_no_auth(self, monkeypatch, value):
        resolved = self._resolve(monkeypatch, env=value)
        assert resolved.permits_no_auth is True
        assert resolved.classification == "local_development"

    @pytest.mark.parametrize("value", ["production", "prod", "staging", "stage"])
    def test_known_deployed_values_reject_no_auth(self, monkeypatch, value):
        resolved = self._resolve(monkeypatch, env=value)
        assert resolved.permits_no_auth is False
        assert resolved.classification == "protected_deployment"

    @pytest.mark.parametrize(
        "value",
        ["staging-us", "prod-us", "production-east", "preview", "qa", "customer-prod", "xyzzy"],
    )
    def test_unknown_values_reject_no_auth(self, monkeypatch, value):
        resolved = self._resolve(monkeypatch, env=value)
        assert resolved.permits_no_auth is False
        assert resolved.classification == "unknown_deployment"

    def test_both_absent_keeps_documented_local_default(self, monkeypatch):
        resolved = self._resolve(monkeypatch)
        assert resolved.classification == "absent"
        assert resolved.permits_no_auth is True

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_whitespace_only_both_is_absence(self, monkeypatch, blank):
        resolved = self._resolve(monkeypatch, env=blank, environment=blank)
        assert resolved.classification == "absent"
        assert resolved.permits_no_auth is True

    def test_unknown_env_with_absent_fallback_rejects_no_auth(self, monkeypatch):
        resolved = self._resolve(monkeypatch, env="preview")
        assert resolved.permits_no_auth is False

    def test_environment_conflict_surfaces_as_auth_error(self, monkeypatch):
        """resolve_auth_config translates the environment conflict into the
        canonical auth failure so every auth surface fails closed uniformly."""
        from mozaiksai.core.auth.adapters.registry import resolve_auth_config

        _set_matrix_env(monkeypatch, {"ENV": "development", "ENVIRONMENT": "production"})
        with pytest.raises(AuthError, match="Conflicting"):
            resolve_auth_config()


# ---------------------------------------------------------------------------
# Provider-complete adapter configuration identity (D5)
# ---------------------------------------------------------------------------


class TestProviderConfigIdentity:
    """Changing ANY meaning-bearing construction input for the resolved
    provider must change adapter identity and rebuild the adapter."""

    @pytest.fixture(autouse=True)
    def _fresh(self, monkeypatch):
        from mozaiksai.core.auth.adapters.registry import reset_auth_adapter
        from mozaiksai.core.auth.config import clear_auth_config_cache

        for var in _AUTH_MATRIX_VARS:
            monkeypatch.delenv(var, raising=False)
        for var in (
            "AUTH_NAME_CLAIM", "AUTH_SCOPES_CLAIM", "AUTH_SCOPES_FORMAT", "AUTH_APP_ID_CLAIM",
            "AUTH_CHAT_ID_CLAIM", "AUTH_TENANT_ID_CLAIM", "AUTH_WORKSPACE_ID_CLAIM",
            "AUTH_CLOCK_SKEW", "AUTH_ALGORITHMS", "AUTH_AUDIENCE", "AUTH_REQUIRED_SCOPE",
            "AUTH_USER_ID_CLAIM", "AUTH_EMAIL_CLAIM", "AUTH_ROLES_CLAIM",
            "AUTH_JWKS_CACHE_TTL", "AUTH_DISCOVERY_CACHE_TTL", "MOZAIKS_OIDC_TENANT_ID",
            "KEYCLOAK_CLIENT_ID", "KEYCLOAK_APP_ID_CLAIM", "KEYCLOAK_TENANT_ID_CLAIM",
            "KEYCLOAK_WORKSPACE_ID_CLAIM", "SUPABASE_JWT_SECRET",
            "AUTH_ANON_USER_ID", "AUTH_ANON_EMAIL", "AUTH_ANON_ROLES", "AUTH_ANON_SCOPES",
        ):
            monkeypatch.delenv(var, raising=False)
        clear_auth_config_cache()
        reset_auth_adapter()
        yield
        clear_auth_config_cache()
        reset_auth_adapter()

    def _jwt_base(self, monkeypatch) -> None:
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("AUTH_PROVIDER", "jwt")
        monkeypatch.setenv("AUTH_JWKS_URL", "https://a.example.com/.well-known/jwks.json")
        monkeypatch.setenv("AUTH_ISSUER", "https://a.example.com")

    @pytest.mark.parametrize(
        ("var", "value"),
        [
            ("AUTH_ISSUER", "https://b.example.com"),
            ("AUTH_JWKS_URL", "https://b.example.com/.well-known/jwks.json"),
            ("AUTH_AUDIENCE", "other-api"),
            ("AUTH_CLOCK_SKEW", "300"),
            ("AUTH_SCOPES_FORMAT", "array"),
            ("AUTH_SCOPES_CLAIM", "scope"),
            ("AUTH_NAME_CLAIM", "display_name"),
            ("AUTH_USER_ID_CLAIM", "oid"),
            ("AUTH_EMAIL_CLAIM", "upn"),
            ("AUTH_ROLES_CLAIM", "realm_access"),
            ("AUTH_APP_ID_CLAIM", "appid"),
            ("AUTH_CHAT_ID_CLAIM", "cid"),
            ("AUTH_TENANT_ID_CLAIM", "tenant"),
            ("AUTH_WORKSPACE_ID_CLAIM", "ws"),
            ("AUTH_ALGORITHMS", "RS256,ES256"),
            ("AUTH_REQUIRED_SCOPE", "access_as_user"),
            ("AUTH_JWKS_CACHE_TTL", "60"),
            ("AUTH_DISCOVERY_CACHE_TTL", "60"),
            ("MOZAIKS_OIDC_TENANT_ID", "tenant-b"),
        ],
    )
    def test_jwt_input_change_rebuilds_adapter(self, monkeypatch, var, value):
        from mozaiksai.core.auth.adapters.registry import get_auth_adapter

        self._jwt_base(monkeypatch)
        first = get_auth_adapter()
        monkeypatch.setenv(var, value)
        second = get_auth_adapter()
        assert second is not first, f"{var} change must rebuild the adapter"

    def test_jwt_rebuilt_adapter_reflects_new_config(self, monkeypatch):
        """The rebuilt adapter carries the NEW values, not the stale ones."""
        from mozaiksai.core.auth.adapters.registry import get_auth_adapter

        self._jwt_base(monkeypatch)
        first = get_auth_adapter()
        assert first._config.issuer == "https://a.example.com"
        assert first._config.clock_skew_seconds == 120

        monkeypatch.setenv("AUTH_ISSUER", "https://b.example.com")
        monkeypatch.setenv("AUTH_CLOCK_SKEW", "300")
        monkeypatch.setenv("AUTH_WORKSPACE_ID_CLAIM", "ws_b")
        second = get_auth_adapter()
        assert second._config.issuer == "https://b.example.com"
        assert second._config.clock_skew_seconds == 300
        assert second._config.workspace_id_claim == "ws_b"

    @pytest.mark.parametrize(
        ("var", "value"),
        [
            ("KEYCLOAK_CLIENT_ID", "other-client"),
            ("KEYCLOAK_APP_ID_CLAIM", "app"),
            ("KEYCLOAK_TENANT_ID_CLAIM", "tenant"),
            ("KEYCLOAK_WORKSPACE_ID_CLAIM", "ws_b"),
            ("KEYCLOAK_REALM", "realm-b"),
            ("KEYCLOAK_URL", "https://kc-b.example.com"),
        ],
    )
    def test_keycloak_input_change_rebuilds_adapter(self, monkeypatch, var, value):
        from mozaiksai.core.auth.adapters.registry import get_auth_adapter

        monkeypatch.setenv("KEYCLOAK_URL", "https://kc-a.example.com")
        monkeypatch.setenv("KEYCLOAK_REALM", "realm-a")
        first = get_auth_adapter()
        monkeypatch.setenv(var, value)
        second = get_auth_adapter()
        assert second is not first, f"{var} change must rebuild the adapter"

    def test_keycloak_rebuilt_adapter_reflects_new_claim_mapping(self, monkeypatch):
        from mozaiksai.core.auth.adapters.registry import get_auth_adapter

        monkeypatch.setenv("KEYCLOAK_URL", "https://kc-a.example.com")
        monkeypatch.setenv("KEYCLOAK_REALM", "realm-a")
        first = get_auth_adapter()
        assert first._workspace_id_claim == "workspace_id"

        monkeypatch.setenv("KEYCLOAK_WORKSPACE_ID_CLAIM", "ws_b")
        second = get_auth_adapter()
        assert second._workspace_id_claim == "ws_b"

    def test_supabase_secret_change_rebuilds_adapter(self, monkeypatch):
        from mozaiksai.core.auth.adapters.registry import get_auth_adapter

        monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
        first = get_auth_adapter()
        monkeypatch.setenv("SUPABASE_JWT_SECRET", "s3cret")
        second = get_auth_adapter()
        assert second is not first
        assert second._jwt_secret == "s3cret"

    @pytest.mark.parametrize(
        ("var", "value"),
        [
            ("AUTH_ANON_USER_ID", "dev_bob"),
            ("AUTH_ANON_EMAIL", "bob@example.com"),
            ("AUTH_ANON_ROLES", "admin,user"),
            ("AUTH_ANON_SCOPES", "access_as_user,billing.admin"),
        ],
    )
    def test_no_auth_input_change_rebuilds_adapter(self, monkeypatch, var, value):
        """Permitted local dev: changing the anonymous persona rebuilds."""
        from mozaiksai.core.auth.adapters.registry import get_auth_adapter

        monkeypatch.setenv("AUTH_ENABLED", "false")
        monkeypatch.setenv("ENV", "development")
        first = get_auth_adapter()
        monkeypatch.setenv(var, value)
        second = get_auth_adapter()
        assert second is not first, f"{var} change must rebuild the adapter"

    def test_no_auth_rebuilt_adapter_reflects_new_roles(self, monkeypatch):
        from mozaiksai.core.auth.adapters.registry import get_auth_adapter

        monkeypatch.setenv("AUTH_ENABLED", "false")
        monkeypatch.setenv("ENV", "development")
        first = get_auth_adapter()
        assert first._default_roles == []

        monkeypatch.setenv("AUTH_ANON_ROLES", "admin,user")
        second = get_auth_adapter()
        assert second._default_roles == ["admin", "user"]

    def test_environment_change_local_to_protected_fails_closed(self, monkeypatch):
        """local → protected must not keep serving the cached no-auth adapter."""
        from mozaiksai.core.auth.adapters.registry import get_auth_adapter

        monkeypatch.setenv("AUTH_ENABLED", "false")
        monkeypatch.setenv("ENV", "development")
        assert get_auth_adapter().name == "none"

        monkeypatch.setenv("ENV", "production")
        with pytest.raises(AuthError, match="not permitted"):
            get_auth_adapter()

    def test_unchanged_config_returns_same_instance(self, monkeypatch):
        from mozaiksai.core.auth.adapters.registry import get_auth_adapter

        self._jwt_base(monkeypatch)
        assert get_auth_adapter() is get_auth_adapter()


# ---------------------------------------------------------------------------
# Custom-provider cache invalidation contract (D5)
# ---------------------------------------------------------------------------


class TestCustomProviderCacheContract:
    """A custom provider's changed configuration may never silently reuse an
    adapter validated under an older configuration."""

    @pytest.fixture(autouse=True)
    def _fresh(self, monkeypatch):
        import mozaiksai.core.auth.adapters.registry as reg
        from mozaiksai.core.auth.config import clear_auth_config_cache

        for var in _AUTH_MATRIX_VARS:
            monkeypatch.delenv(var, raising=False)
        saved = dict(reg._adapter_registry)
        clear_auth_config_cache()
        reg.reset_auth_adapter()
        yield
        reg._adapter_registry.clear()
        reg._adapter_registry.update(saved)
        clear_auth_config_cache()
        reg.reset_auth_adapter()

    def _custom_adapter_class(self, revision_box):
        from mozaiksai.core.auth.adapters.base import BaseAuthAdapter, UserClaims

        class _CustomAdapter(BaseAuthAdapter):
            name = "custom"

            def __init__(self, settings=None):
                super().__init__(settings)
                self.revision = revision_box["value"]

            async def validate_token(self, token: str) -> UserClaims:
                return UserClaims(user_id="custom-user", provider=self.name)

            def is_enabled(self) -> bool:
                return True

        return _CustomAdapter

    def test_declared_config_identity_invalidates_on_revision_change(self, monkeypatch):
        from mozaiksai.core.auth.adapters.registry import get_auth_adapter, register_adapter

        box = {"value": "rev-a"}
        register_adapter(
            "custom",
            self._custom_adapter_class(box),
            config_identity=lambda: box["value"],
        )
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("AUTH_PROVIDER", "custom")

        first = get_auth_adapter()
        assert first.revision == "rev-a"
        assert get_auth_adapter() is first  # cached while identity is unchanged

        box["value"] = "rev-b"
        second = get_auth_adapter()
        assert second is not first
        assert second.revision == "rev-b"

    def test_adapter_without_config_identity_is_never_cached(self, monkeypatch):
        """Truthful bounded contract: no declared identity → no caching, so a
        changed configuration can never reuse a stale adapter."""
        from mozaiksai.core.auth.adapters.registry import get_auth_adapter, register_adapter

        box = {"value": "rev-a"}
        register_adapter("custom", self._custom_adapter_class(box))
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("AUTH_PROVIDER", "custom")

        first = get_auth_adapter()
        second = get_auth_adapter()
        assert second is not first  # rebuilt every resolution

        box["value"] = "rev-b"
        assert get_auth_adapter().revision == "rev-b"

    def test_reregistration_invalidates_cached_adapter(self, monkeypatch):
        from mozaiksai.core.auth.adapters.registry import get_auth_adapter, register_adapter

        box = {"value": "rev-a"}
        register_adapter("custom", self._custom_adapter_class(box), config_identity="static")
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("AUTH_PROVIDER", "custom")
        first = get_auth_adapter()

        box["value"] = "rev-b"
        register_adapter("custom", self._custom_adapter_class(box), config_identity="static")
        second = get_auth_adapter()
        assert second is not first
        assert second.revision == "rev-b"


# ---------------------------------------------------------------------------
# Cache record coherence (D5)
# ---------------------------------------------------------------------------


class TestCachePublicationCoherence:
    """Fingerprint, provider, and adapter are published as one immutable record."""

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

    def test_cache_entry_matches_resolved_config(self, monkeypatch):
        import mozaiksai.core.auth.adapters.registry as reg

        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("AUTH_PROVIDER", "jwt")
        monkeypatch.setenv("AUTH_JWKS_URL", "https://a.example.com/jwks.json")
        monkeypatch.setenv("AUTH_ISSUER", "https://a.example.com")

        adapter = reg.get_auth_adapter()
        entry = reg._adapter_cache
        config = reg.resolve_auth_config()

        assert entry is not None
        assert entry.adapter is adapter
        assert entry.provider == config.provider == "jwt"
        assert entry.fingerprint == config.fingerprint

    def test_startup_validation_binds_the_adapter_requests_use(self, monkeypatch):
        import mozaiksai.core.auth.adapters.registry as reg

        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")

        assert reg.validate_auth_provider_configuration() == "supabase"
        bound = reg._adapter_cache
        assert bound is not None
        assert reg.get_auth_adapter() is bound.adapter


# ---------------------------------------------------------------------------
# D5-A: lazy client chain is bound to the adapter's own snapshot
# ---------------------------------------------------------------------------


class TestLazyClientSnapshotBinding:
    """Once an adapter exists, later environment changes must not alter its
    behaviour — including through lazily created discovery/JWKS clients that
    had not been instantiated yet when the environment changed."""

    @pytest.fixture(autouse=True)
    def _fresh(self, monkeypatch):
        from mozaiksai.core.auth.adapters.registry import reset_auth_adapter
        from mozaiksai.core.auth.config import clear_auth_config_cache

        for var in (*_AUTH_MATRIX_VARS, "AUTH_JWKS_CACHE_TTL", "AUTH_DISCOVERY_CACHE_TTL",
                    "MOZAIKS_OIDC_TENANT_ID"):
            monkeypatch.delenv(var, raising=False)
        clear_auth_config_cache()
        reset_auth_adapter()
        yield
        clear_auth_config_cache()
        reset_auth_adapter()

    def _config_a(self, monkeypatch) -> None:
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("AUTH_PROVIDER", "jwt")
        monkeypatch.setenv("MOZAIKS_OIDC_DISCOVERY_URL", "https://a.example.com/.well-known")
        # An explicit JWKS URL keeps key resolution off the network while still
        # exercising the full lazy construction path.
        monkeypatch.setenv("AUTH_JWKS_URL", "https://a.example.com/jwks.json")
        monkeypatch.setenv("AUTH_ISSUER", "https://a.example.com")
        monkeypatch.setenv("AUTH_DISCOVERY_CACHE_TTL", "31")
        monkeypatch.setenv("AUTH_JWKS_CACHE_TTL", "31")

    def _mutate_to_config_b(self, monkeypatch) -> None:
        monkeypatch.setenv("MOZAIKS_OIDC_DISCOVERY_URL", "https://b.example.com/.well-known")
        monkeypatch.setenv("AUTH_JWKS_URL", "https://b.example.com/jwks.json")
        monkeypatch.setenv("AUTH_DISCOVERY_CACHE_TTL", "47")
        monkeypatch.setenv("AUTH_JWKS_CACHE_TTL", "47")

    @pytest.mark.asyncio
    async def test_old_adapter_lazy_clients_use_original_snapshot(self, monkeypatch):
        """The mandatory race proof: build under A, mutate the live env to B
        WITHOUT resolving a new adapter, then trigger the old adapter's lazy
        clients for the first time. They must still use A / TTL 31."""
        from mozaiksai.core.auth.adapters.registry import get_auth_adapter

        self._config_a(monkeypatch)
        adapter = get_auth_adapter()
        assert adapter.name == "jwt"

        # Lazy clients have NOT been created yet.
        assert adapter._discovery_client is None
        assert adapter._jwks_client is None

        self._mutate_to_config_b(monkeypatch)

        # First-ever instantiation of the lazy chain, after the mutation.
        discovery = adapter._get_discovery_client()
        assert discovery.discovery_url == "https://a.example.com/.well-known"
        assert discovery.cache_ttl_seconds == 31

        jwks = await adapter._get_jwks_client_async()
        assert jwks.cache_ttl_seconds == 31
        assert jwks._explicit_jwks_url == "https://a.example.com/jwks.json"
        # Key resolution routes through the adapter's own discovery client,
        # never the process-wide singleton.
        assert jwks._discovery_client is discovery

    @pytest.mark.asyncio
    async def test_new_resolution_after_change_uses_new_snapshot(self, monkeypatch):
        from mozaiksai.core.auth.adapters.registry import get_auth_adapter

        self._config_a(monkeypatch)
        old = get_auth_adapter()
        old_discovery = old._get_discovery_client()

        self._mutate_to_config_b(monkeypatch)
        new = get_auth_adapter()
        assert new is not old

        new_discovery = new._get_discovery_client()
        assert new_discovery.discovery_url == "https://b.example.com/.well-known"
        assert new_discovery.cache_ttl_seconds == 47
        new_jwks = await new._get_jwks_client_async()
        assert new_jwks.cache_ttl_seconds == 47

        # The old adapter is still internally bound to its old snapshot.
        assert old_discovery.discovery_url == "https://a.example.com/.well-known"
        assert old_discovery.cache_ttl_seconds == 31

    def test_discovery_client_cannot_be_repointed_by_environment(self, monkeypatch):
        from mozaiksai.core.auth.adapters.registry import get_auth_adapter

        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("AUTH_PROVIDER", "jwt")
        monkeypatch.setenv("MOZAIKS_OIDC_AUTHORITY", "https://authority-a.example.com")
        adapter = get_auth_adapter()

        monkeypatch.setenv("MOZAIKS_OIDC_DISCOVERY_URL", "https://attacker.example.com/.well-known")
        discovery = adapter._get_discovery_client()
        assert "attacker" not in (discovery.discovery_url or "")
        assert discovery.discovery_url.startswith("https://authority-a.example.com")

    @pytest.mark.asyncio
    async def test_jwks_client_cannot_be_repointed_by_global_config(self, monkeypatch):
        from mozaiksai.core.auth.adapters.registry import get_auth_adapter
        from mozaiksai.core.auth.config import clear_auth_config_cache

        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("AUTH_PROVIDER", "jwt")
        monkeypatch.setenv("AUTH_JWKS_URL", "https://a.example.com/jwks.json")
        monkeypatch.setenv("AUTH_ISSUER", "https://a.example.com")
        adapter = get_auth_adapter()

        # Repoint the global AuthConfig, then create the lazy client.
        monkeypatch.setenv("AUTH_JWKS_URL", "https://attacker.example.com/jwks.json")
        clear_auth_config_cache()
        jwks = await adapter._get_jwks_client_async()
        assert jwks._explicit_jwks_url == "https://a.example.com/jwks.json"

    def test_adapter_config_carries_snapshot_ttls(self, monkeypatch):
        from mozaiksai.core.auth.adapters.registry import get_auth_adapter

        self._config_a(monkeypatch)
        adapter = get_auth_adapter()
        assert adapter._config.discovery_cache_ttl_seconds == 31
        assert adapter._config.jwks_cache_ttl_seconds == 31

    def test_ttl_change_alone_rebuilds_adapter(self, monkeypatch):
        from mozaiksai.core.auth.adapters.registry import get_auth_adapter

        self._config_a(monkeypatch)
        first = get_auth_adapter()
        monkeypatch.setenv("AUTH_JWKS_CACHE_TTL", "47")
        second = get_auth_adapter()
        assert second is not first
        assert second._config.jwks_cache_ttl_seconds == 47


# ---------------------------------------------------------------------------
# D5-A: cache TTL validation happens at configuration resolution
# ---------------------------------------------------------------------------


class TestCacheTtlValidation:
    @pytest.fixture(autouse=True)
    def _fresh(self, monkeypatch):
        from mozaiksai.core.auth.adapters.registry import reset_auth_adapter
        from mozaiksai.core.auth.config import clear_auth_config_cache

        for var in (*_AUTH_MATRIX_VARS, "AUTH_JWKS_CACHE_TTL", "AUTH_DISCOVERY_CACHE_TTL"):
            monkeypatch.delenv(var, raising=False)
        clear_auth_config_cache()
        reset_auth_adapter()
        yield
        clear_auth_config_cache()
        reset_auth_adapter()

    @pytest.mark.parametrize("ttl_var", ["AUTH_JWKS_CACHE_TTL", "AUTH_DISCOVERY_CACHE_TTL"])
    @pytest.mark.parametrize("bad_value", ["abc", "1.5", "", "  ", "-1", "1e3", "0x10", "3,600"])
    def test_malformed_ttl_rejected_at_resolution(self, monkeypatch, ttl_var, bad_value):
        """Malformed TTLs fail during canonical resolution, long before a
        request can reach lazy client construction."""
        from mozaiksai.core.auth.adapters.registry import resolve_auth_config

        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("AUTH_PROVIDER", "jwt")
        monkeypatch.setenv("AUTH_JWKS_URL", "https://a.example.com/jwks.json")
        monkeypatch.setenv("AUTH_ISSUER", "https://a.example.com")
        monkeypatch.setenv(ttl_var, bad_value)

        if bad_value.strip() == "":
            # Blank falls back to the documented default rather than failing.
            assert resolve_auth_config().provider == "jwt"
            return
        with pytest.raises(AuthError, match=ttl_var):
            resolve_auth_config()

    @pytest.mark.parametrize("good_value", ["0", "1", "31", "3600", "86400"])
    def test_valid_ttl_accepted(self, monkeypatch, good_value):
        from mozaiksai.core.auth.adapters.registry import resolve_auth_config

        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("AUTH_PROVIDER", "jwt")
        monkeypatch.setenv("AUTH_JWKS_URL", "https://a.example.com/jwks.json")
        monkeypatch.setenv("AUTH_ISSUER", "https://a.example.com")
        monkeypatch.setenv("AUTH_JWKS_CACHE_TTL", good_value)
        assert resolve_auth_config().provider == "jwt"

    def test_parse_helper_domain(self):
        from mozaiksai.core.auth.cache_ttl import CacheTtlConfigError, parse_cache_ttl_seconds

        assert parse_cache_ttl_seconds("X", "60", default=1) == 60
        assert parse_cache_ttl_seconds("X", "0", default=1) == 0
        assert parse_cache_ttl_seconds("X", None, default=42) == 42
        assert parse_cache_ttl_seconds("X", "  ", default=42) == 42
        for bad in ("abc", "-1", "1.5", "1e3"):
            with pytest.raises(CacheTtlConfigError):
                parse_cache_ttl_seconds("X", bad, default=1)


# ---------------------------------------------------------------------------
# D5-B: constructor compatibility is decided before invocation
# ---------------------------------------------------------------------------


class TestConstructorContract:
    """A TypeError raised inside a constructor body is a real failure. It must
    never be mistaken for 'this constructor does not accept settings' and
    trigger a second, unconfigured construction."""

    @pytest.fixture(autouse=True)
    def _fresh(self, monkeypatch):
        import mozaiksai.core.auth.adapters.registry as reg
        from mozaiksai.core.auth.config import clear_auth_config_cache

        for var in _AUTH_MATRIX_VARS:
            monkeypatch.delenv(var, raising=False)
        saved = dict(reg._adapter_registry)
        clear_auth_config_cache()
        reg.reset_auth_adapter()
        yield
        reg._adapter_registry.clear()
        reg._adapter_registry.update(saved)
        clear_auth_config_cache()
        reg.reset_auth_adapter()

    def _select(self, monkeypatch, provider: str) -> None:
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("AUTH_PROVIDER", provider)

    def test_settings_aware_adapter_receives_snapshot(self, monkeypatch):
        from mozaiksai.core.auth.adapters.base import BaseAuthAdapter, UserClaims
        from mozaiksai.core.auth.adapters.registry import get_auth_adapter, register_adapter

        class _SettingsAware(BaseAuthAdapter):
            name = "settingsaware"

            def __init__(self, settings=None):
                super().__init__(settings)
                self.got_settings = settings

            async def validate_token(self, token):
                return UserClaims(user_id="u", provider=self.name)

            def is_enabled(self):
                return True

        register_adapter("settingsaware", _SettingsAware, config_identity="v1")
        self._select(monkeypatch, "settingsaware")
        adapter = get_auth_adapter()
        assert adapter.got_settings is not None
        assert "AUTH_PROVIDER" in adapter.got_settings

    def test_adapter_without_settings_still_constructs(self, monkeypatch):
        from mozaiksai.core.auth.adapters.base import BaseAuthAdapter, UserClaims
        from mozaiksai.core.auth.adapters.registry import get_auth_adapter, register_adapter

        class _NoSettingsAdapter(BaseAuthAdapter):
            name = "nosettingsadapter"

            def __init__(self):
                super().__init__()
                self.constructed = True

            async def validate_token(self, token):
                return UserClaims(user_id="u", provider=self.name)

            def is_enabled(self):
                return True

        register_adapter("nosettingsadapter", _NoSettingsAdapter, config_identity="v1")
        self._select(monkeypatch, "nosettingsadapter")
        assert get_auth_adapter().constructed is True

    def test_constructor_body_typeerror_never_retries_without_settings(self, monkeypatch):
        """The exact D5-B defect: a TypeError from inside the body must fail
        closed, not silently rebuild the adapter without its configuration."""
        from mozaiksai.core.auth.adapters.base import BaseAuthAdapter, UserClaims
        from mozaiksai.core.auth.adapters.registry import get_auth_adapter, register_adapter

        attempts: list[dict] = []

        class _BodyTypeError(BaseAuthAdapter):
            name = "bodytypeerror"

            def __init__(self, settings=None):
                super().__init__(settings)
                attempts.append({"settings": settings})
                raise TypeError("defective adapter body")

            async def validate_token(self, token):
                return UserClaims(user_id="u", provider=self.name)

            def is_enabled(self):
                return True

        register_adapter("bodytypeerror", _BodyTypeError, config_identity="v1")
        self._select(monkeypatch, "bodytypeerror")

        with pytest.raises(AuthError, match="Failed to configure"):
            get_auth_adapter()
        assert len(attempts) == 1, "must not retry construction without settings"
        assert attempts[0]["settings"] is not None

    def test_constructor_valueerror_fails_closed(self, monkeypatch):
        from mozaiksai.core.auth.adapters.base import BaseAuthAdapter, UserClaims
        from mozaiksai.core.auth.adapters.registry import get_auth_adapter, register_adapter

        attempts: list[int] = []

        class _BodyValueError(BaseAuthAdapter):
            name = "bodyvalueerror"

            def __init__(self, settings=None):
                super().__init__(settings)
                attempts.append(1)
                raise ValueError("bad configuration")

            async def validate_token(self, token):
                return UserClaims(user_id="u", provider=self.name)

            def is_enabled(self):
                return True

        register_adapter("bodyvalueerror", _BodyValueError, config_identity="v1")
        self._select(monkeypatch, "bodyvalueerror")

        with pytest.raises(AuthError, match="Failed to configure"):
            get_auth_adapter()
        assert len(attempts) == 1

    def test_malformed_signature_adapter_rejected_at_registration(self):
        """A constructor requiring an argument the runtime cannot supply is
        rejected when it registers — the contract cannot be established, so it
        is never classified as a zero-settings constructor."""
        from mozaiksai.core.auth.adapters.base import BaseAuthAdapter, UserClaims
        from mozaiksai.core.auth.adapters.registry import register_adapter

        class _NeedsUnknownArg(BaseAuthAdapter):
            name = "needsunknownarg"

            def __init__(self, required_thing):
                super().__init__()
                self.required_thing = required_thing

            async def validate_token(self, token):
                return UserClaims(user_id="u", provider=self.name)

            def is_enabled(self):
                return True

        with pytest.raises(AuthError, match="requires constructor argument"):
            register_adapter("needsunknownarg", _NeedsUnknownArg, config_identity="v1")

    def test_kwargs_constructor_is_treated_as_settings_aware(self, monkeypatch):
        from mozaiksai.core.auth.adapters.base import BaseAuthAdapter, UserClaims
        from mozaiksai.core.auth.adapters.registry import get_auth_adapter, register_adapter

        class _Kwargs(BaseAuthAdapter):
            name = "kwargsadapter"

            def __init__(self, **kwargs):
                super().__init__(kwargs.get("settings"))
                self.kwargs = kwargs

            async def validate_token(self, token):
                return UserClaims(user_id="u", provider=self.name)

            def is_enabled(self):
                return True

        register_adapter("kwargsadapter", _Kwargs, config_identity="v1")
        self._select(monkeypatch, "kwargsadapter")
        assert get_auth_adapter().kwargs.get("settings") is not None

    @pytest.mark.parametrize("provider", ["none", "jwt", "supabase", "keycloak"])
    def test_builtin_adapters_are_settings_aware(self, provider):
        import mozaiksai.core.auth.adapters.registry as reg

        reg._ensure_builtin_adapters()
        assert reg._adapter_registry[provider].accepts_settings is True


# ---------------------------------------------------------------------------
# D5-C: custom config identity contract is strict
# ---------------------------------------------------------------------------


class TestConfigIdentityContract:
    @pytest.fixture(autouse=True)
    def _fresh(self, monkeypatch):
        import mozaiksai.core.auth.adapters.registry as reg
        from mozaiksai.core.auth.config import clear_auth_config_cache

        for var in _AUTH_MATRIX_VARS:
            monkeypatch.delenv(var, raising=False)
        saved = dict(reg._adapter_registry)
        clear_auth_config_cache()
        reg.reset_auth_adapter()
        yield
        reg._adapter_registry.clear()
        reg._adapter_registry.update(saved)
        clear_auth_config_cache()
        reg.reset_auth_adapter()

    def _adapter_class(self):
        from mozaiksai.core.auth.adapters.base import BaseAuthAdapter, UserClaims

        class _Custom(BaseAuthAdapter):
            name = "identityprobe"

            def __init__(self, settings=None):
                super().__init__(settings)

            async def validate_token(self, token):
                return UserClaims(user_id="u", provider=self.name)

            def is_enabled(self):
                return True

        return _Custom

    @pytest.mark.parametrize(
        "bad_identity",
        [123, 1.5, True, ["rev-a"], {"rev": "a"}, ("rev",), object()],
    )
    def test_malformed_literal_identity_rejected_at_registration(self, bad_identity):
        from mozaiksai.core.auth.adapters.registry import register_adapter

        with pytest.raises(AuthError, match="must be a string"):
            register_adapter("identityprobe", self._adapter_class(), config_identity=bad_identity)

    @pytest.mark.parametrize("blank_identity", ["", "   ", "\t", "\n"])
    def test_blank_literal_identity_rejected(self, blank_identity):
        from mozaiksai.core.auth.adapters.registry import register_adapter

        with pytest.raises(AuthError, match="non-blank"):
            register_adapter("identityprobe", self._adapter_class(), config_identity=blank_identity)

    @pytest.mark.parametrize("bad_return", [None, 123, ["rev"], {"rev": "a"}])
    def test_callable_returning_non_string_rejected(self, monkeypatch, bad_return):
        from mozaiksai.core.auth.adapters.registry import get_auth_adapter, register_adapter

        register_adapter(
            "identityprobe", self._adapter_class(), config_identity=lambda: bad_return
        )
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("AUTH_PROVIDER", "identityprobe")
        with pytest.raises(AuthError, match="must be a string"):
            get_auth_adapter()

    @pytest.mark.parametrize("blank_return", ["", "  "])
    def test_callable_returning_blank_rejected(self, monkeypatch, blank_return):
        from mozaiksai.core.auth.adapters.registry import get_auth_adapter, register_adapter

        register_adapter(
            "identityprobe", self._adapter_class(), config_identity=lambda: blank_return
        )
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("AUTH_PROVIDER", "identityprobe")
        with pytest.raises(AuthError, match="non-blank"):
            get_auth_adapter()

    def test_callable_raising_fails_closed(self, monkeypatch):
        from mozaiksai.core.auth.adapters.registry import get_auth_adapter, register_adapter

        def _boom():
            raise RuntimeError("identity source unavailable")

        register_adapter("identityprobe", self._adapter_class(), config_identity=_boom)
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("AUTH_PROVIDER", "identityprobe")
        with pytest.raises(AuthError, match="config_identity callable raised"):
            get_auth_adapter()

    def test_valid_identity_accepted_and_cached(self, monkeypatch):
        from mozaiksai.core.auth.adapters.registry import get_auth_adapter, register_adapter

        register_adapter("identityprobe", self._adapter_class(), config_identity="rev-a")
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("AUTH_PROVIDER", "identityprobe")
        assert get_auth_adapter() is get_auth_adapter()

    def test_no_identity_remains_uncached_not_an_error(self, monkeypatch):
        from mozaiksai.core.auth.adapters.registry import get_auth_adapter, register_adapter

        register_adapter("identityprobe", self._adapter_class())
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("AUTH_PROVIDER", "identityprobe")
        assert get_auth_adapter() is not get_auth_adapter()


# ---------------------------------------------------------------------------
# Built-in snapshot-consumption census
# ---------------------------------------------------------------------------


class TestBuiltinSnapshotConsumption:
    """Captured values must be the values actually consumed all the way down,
    for every built-in provider."""

    @pytest.fixture(autouse=True)
    def _fresh(self, monkeypatch):
        from mozaiksai.core.auth.adapters.registry import reset_auth_adapter
        from mozaiksai.core.auth.config import clear_auth_config_cache

        for var in (*_AUTH_MATRIX_VARS, "AUTH_JWKS_CACHE_TTL", "AUTH_DISCOVERY_CACHE_TTL",
                    "KEYCLOAK_CLIENT_ID", "KEYCLOAK_APP_ID_CLAIM", "KEYCLOAK_TENANT_ID_CLAIM",
                    "KEYCLOAK_WORKSPACE_ID_CLAIM", "SUPABASE_JWT_SECRET",
                    "AUTH_ANON_USER_ID", "AUTH_ANON_ROLES", "AUTH_ANON_SCOPES",
                    "AUTH_ANON_EMAIL", "AUTH_WORKSPACE_ID_CLAIM", "AUTH_CLOCK_SKEW"):
            monkeypatch.delenv(var, raising=False)
        clear_auth_config_cache()
        reset_auth_adapter()
        yield
        clear_auth_config_cache()
        reset_auth_adapter()

    def test_jwt_consumes_captured_values(self, monkeypatch):
        from mozaiksai.core.auth.adapters.registry import get_auth_adapter

        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("AUTH_PROVIDER", "jwt")
        monkeypatch.setenv("AUTH_JWKS_URL", "https://a.example.com/jwks.json")
        monkeypatch.setenv("AUTH_ISSUER", "https://a.example.com")
        monkeypatch.setenv("AUTH_AUDIENCE", "aud-a")
        monkeypatch.setenv("AUTH_WORKSPACE_ID_CLAIM", "ws_a")
        monkeypatch.setenv("AUTH_CLOCK_SKEW", "77")
        monkeypatch.setenv("AUTH_JWKS_CACHE_TTL", "31")
        monkeypatch.setenv("AUTH_DISCOVERY_CACHE_TTL", "33")

        a = get_auth_adapter()
        assert a._config.jwks_url == "https://a.example.com/jwks.json"
        assert a._config.issuer == "https://a.example.com"
        assert a._config.audience == "aud-a"
        assert a._config.workspace_id_claim == "ws_a"
        assert a._config.clock_skew_seconds == 77
        assert a._config.jwks_cache_ttl_seconds == 31
        assert a._config.discovery_cache_ttl_seconds == 33

    def test_keycloak_consumes_captured_values(self, monkeypatch):
        """Programmatic only — no admin console or browser login involved."""
        from mozaiksai.core.auth.adapters.registry import get_auth_adapter

        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("AUTH_PROVIDER", "keycloak")
        monkeypatch.setenv("KEYCLOAK_URL", "https://kc-a.example.com/")
        monkeypatch.setenv("KEYCLOAK_REALM", "realm-a")
        monkeypatch.setenv("KEYCLOAK_CLIENT_ID", "client-a")
        monkeypatch.setenv("KEYCLOAK_APP_ID_CLAIM", "app_a")
        monkeypatch.setenv("KEYCLOAK_TENANT_ID_CLAIM", "tenant_a")
        monkeypatch.setenv("KEYCLOAK_WORKSPACE_ID_CLAIM", "ws_a")

        a = get_auth_adapter()
        assert a._keycloak_url == "https://kc-a.example.com"
        assert a._realm == "realm-a"
        assert a._client_id == "client-a"
        assert a._app_id_claim == "app_a"
        assert a._tenant_id_claim == "tenant_a"
        assert a._workspace_id_claim == "ws_a"
        # Derived URLs follow the captured values.
        assert a._issuer == "https://kc-a.example.com/realms/realm-a"
        assert a._jwks_url.startswith("https://kc-a.example.com/realms/realm-a")

        # Mutating the environment does not mutate the existing adapter.
        monkeypatch.setenv("KEYCLOAK_REALM", "realm-b")
        assert a._realm == "realm-a"
        assert a._issuer == "https://kc-a.example.com/realms/realm-a"

    def test_supabase_consumes_captured_values(self, monkeypatch):
        from mozaiksai.core.auth.adapters.registry import get_auth_adapter

        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("SUPABASE_URL", "https://proj-a.supabase.co/")
        monkeypatch.setenv("SUPABASE_JWT_SECRET", "secret-a")

        a = get_auth_adapter()
        assert a._supabase_url == "https://proj-a.supabase.co"
        assert a._jwt_secret == "secret-a"
        assert a._issuer == "https://proj-a.supabase.co/auth/v1"

        monkeypatch.setenv("SUPABASE_JWT_SECRET", "secret-b")
        assert a._jwt_secret == "secret-a"

    def test_no_auth_consumes_captured_values(self, monkeypatch):
        from mozaiksai.core.auth.adapters.registry import get_auth_adapter

        monkeypatch.setenv("AUTH_ENABLED", "false")
        monkeypatch.setenv("ENV", "development")
        monkeypatch.setenv("AUTH_ANON_USER_ID", "dev_alice")
        monkeypatch.setenv("AUTH_ANON_EMAIL", "alice@example.com")
        monkeypatch.setenv("AUTH_ANON_ROLES", "admin,user")
        monkeypatch.setenv("AUTH_ANON_SCOPES", "access_as_user,extra.scope")

        a = get_auth_adapter()
        assert a._default_user_id == "dev_alice"
        assert a._default_email == "alice@example.com"
        assert a._default_roles == ["admin", "user"]
        assert a._default_scopes == ["access_as_user", "extra.scope"]

        monkeypatch.setenv("AUTH_ANON_USER_ID", "dev_bob")
        assert a._default_user_id == "dev_alice"


# ---------------------------------------------------------------------------
# D5-1: constructor compatibility must be POSITIVELY established
# ---------------------------------------------------------------------------


class TestConstructorClassification:
    """An adapter may be built without the snapshot only when inspection
    proves it takes no settings argument at all. "Could not
    inspect" and "no settings keyword" are never evidence of that."""

    @pytest.fixture(autouse=True)
    def _fresh(self, monkeypatch):
        import mozaiksai.core.auth.adapters.registry as reg
        from mozaiksai.core.auth.config import clear_auth_config_cache

        for var in _AUTH_MATRIX_VARS:
            monkeypatch.delenv(var, raising=False)
        saved = dict(reg._adapter_registry)
        clear_auth_config_cache()
        reg.reset_auth_adapter()
        yield
        reg._adapter_registry.clear()
        reg._adapter_registry.update(saved)
        clear_auth_config_cache()
        reg.reset_auth_adapter()

    def _select(self, monkeypatch, provider: str) -> None:
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("AUTH_PROVIDER", provider)

    def _claims(self):
        from mozaiksai.core.auth.adapters.base import UserClaims

        return UserClaims(user_id="u", provider="probe")

    # -- 1/2/3: settings positively supplied --------------------------------

    def test_positional_or_keyword_settings_receives_snapshot(self, monkeypatch):
        from mozaiksai.core.auth.adapters.base import BaseAuthAdapter
        from mozaiksai.core.auth.adapters.registry import get_auth_adapter, register_adapter

        outer = self

        class _Adapter(BaseAuthAdapter):
            name = "poskw"

            def __init__(self, settings=None):
                super().__init__(settings)
                self.got = settings

            async def validate_token(self, token):
                return outer._claims()

            def is_enabled(self):
                return True

        register_adapter("poskw", _Adapter, config_identity="v1")
        self._select(monkeypatch, "poskw")
        assert get_auth_adapter().got is not None

    def test_keyword_only_settings_receives_snapshot(self, monkeypatch):
        from mozaiksai.core.auth.adapters.base import BaseAuthAdapter
        from mozaiksai.core.auth.adapters.registry import get_auth_adapter, register_adapter

        outer = self

        class _Adapter(BaseAuthAdapter):
            name = "kwonly"

            def __init__(self, *, settings=None):
                super().__init__(settings)
                self.got = settings

            async def validate_token(self, token):
                return outer._claims()

            def is_enabled(self):
                return True

        register_adapter("kwonly", _Adapter, config_identity="v1")
        self._select(monkeypatch, "kwonly")
        assert get_auth_adapter().got is not None

    def test_var_keyword_adapter_receives_snapshot(self, monkeypatch):
        from mozaiksai.core.auth.adapters.base import BaseAuthAdapter
        from mozaiksai.core.auth.adapters.registry import get_auth_adapter, register_adapter

        outer = self

        class _Adapter(BaseAuthAdapter):
            name = "varkw"

            def __init__(self, **kwargs):
                super().__init__(kwargs.get("settings"))
                self.got = kwargs.get("settings")

            async def validate_token(self, token):
                return outer._claims()

            def is_enabled(self):
                return True

        register_adapter("varkw", _Adapter, config_identity="v1")
        self._select(monkeypatch, "varkw")
        assert get_auth_adapter().got is not None

    # -- 4: real zero-argument constructor ---------------------------

    def test_true_zero_argument_constructor_allowed(self, monkeypatch):
        import mozaiksai.core.auth.adapters.registry as reg
        from mozaiksai.core.auth.adapters.base import BaseAuthAdapter
        from mozaiksai.core.auth.adapters.registry import get_auth_adapter, register_adapter

        outer = self

        class _NoSettings(BaseAuthAdapter):
            name = "truenosettings"

            def __init__(self):
                super().__init__()
                self.constructed = True

            async def validate_token(self, token):
                return outer._claims()

            def is_enabled(self):
                return True

        register_adapter("truenosettings", _NoSettings, config_identity="v1")
        assert reg._adapter_registry["truenosettings"].constructor_mode == "no_settings"
        self._select(monkeypatch, "truenosettings")
        assert get_auth_adapter().constructed is True

    def test_optional_non_settings_args_are_no_settings(self):
        import mozaiksai.core.auth.adapters.registry as reg
        from mozaiksai.core.auth.adapters.base import BaseAuthAdapter
        from mozaiksai.core.auth.adapters.registry import register_adapter

        outer = self

        class _OptionalArgs(BaseAuthAdapter):
            name = "optargs"

            def __init__(self, url=None, secret=None):
                super().__init__()

            async def validate_token(self, token):
                return outer._claims()

            def is_enabled(self):
                return True

        register_adapter("optargs", _OptionalArgs, config_identity="v1")
        assert reg._adapter_registry["optargs"].constructor_mode == "no_settings"

    # -- 5: positional-only settings ----------------------------------------

    def test_positional_only_settings_is_explicitly_rejected(self):
        """Positional-only configuration is not part of the plugin contract, so
        it is rejected rather than silently discarded as zero-argument."""
        from mozaiksai.core.auth.adapters.base import BaseAuthAdapter
        from mozaiksai.core.auth.adapters.registry import register_adapter

        outer = self

        class _PosOnly(BaseAuthAdapter):
            name = "posonly"

            def __init__(self, settings=None, /):
                super().__init__(settings)

            async def validate_token(self, token):
                return outer._claims()

            def is_enabled(self):
                return True

        with pytest.raises(AuthError, match="positional-only"):
            register_adapter("posonly", _PosOnly, config_identity="v1")

    # -- 6: uninspectable constructor ---------------------------------------

    def test_uninspectable_constructor_rejected_without_declaration(self):
        from mozaiksai.core.auth.adapters.registry import register_adapter

        class _Uninspectable:
            __init__ = print  # builtin: signature() cannot be established

        with pytest.raises(AuthError, match="could not be inspected"):
            register_adapter("uninspectable", _Uninspectable, config_identity="v1")

    def test_uninspectable_constructor_accepted_with_explicit_mode(self, monkeypatch):
        import mozaiksai.core.auth.adapters.registry as reg
        from mozaiksai.core.auth.adapters.registry import get_auth_adapter, register_adapter

        constructed: list[dict] = []

        class _Meta(type):
            def __call__(cls, *args, **kwargs):  # defeats signature inspection
                constructed.append(kwargs)
                return super().__call__()

        class _Exotic(metaclass=_Meta):
            name = "exotic"

            def __init__(self):
                self._settings = None

            async def validate_token(self, token):
                from mozaiksai.core.auth.adapters.base import UserClaims

                return UserClaims(user_id="u", provider=self.name)

            def is_enabled(self):
                return True

        try:
            reg._classify_constructor(_Exotic, declared_mode=None)
            inspectable = True
        except AuthError:
            inspectable = False

        register_adapter(
            "exotic", _Exotic, config_identity="v1", constructor_mode="no_settings"
        )
        assert reg._adapter_registry["exotic"].constructor_mode == "no_settings"
        self._select(monkeypatch, "exotic")
        adapter = get_auth_adapter()
        assert adapter.name == "exotic"
        # Whether or not this particular metaclass defeats inspection, the
        # explicit declaration is what decided the contract.
        assert inspectable in (True, False)

    def test_unknown_declared_mode_rejected(self):
        from mozaiksai.core.auth.adapters.base import BaseAuthAdapter
        from mozaiksai.core.auth.adapters.registry import register_adapter

        outer = self

        class _Adapter(BaseAuthAdapter):
            name = "badmode"

            def __init__(self, settings=None):
                super().__init__(settings)

            async def validate_token(self, token):
                return outer._claims()

            def is_enabled(self):
                return True

        with pytest.raises(AuthError, match="Unknown constructor_mode"):
            register_adapter("badmode", _Adapter, constructor_mode="whatever")  # type: ignore[arg-type]

    # -- 10: built-ins classify correctly -----------------------------------

    @pytest.mark.parametrize("provider", ["none", "jwt", "supabase", "keycloak"])
    def test_builtin_adapters_classify_as_settings_keyword(self, provider):
        import mozaiksai.core.auth.adapters.registry as reg

        reg._ensure_builtin_adapters()
        assert reg._adapter_registry[provider].constructor_mode == "settings_keyword"

    def test_custom_registration_does_not_suppress_builtins(self, monkeypatch):
        """Registering a custom adapter into an empty registry must not stop
        the built-in providers from being registered on first resolution."""
        import mozaiksai.core.auth.adapters.registry as reg
        from mozaiksai.core.auth.adapters.base import BaseAuthAdapter
        from mozaiksai.core.auth.adapters.registry import get_auth_adapter, register_adapter

        outer = self

        class _Custom(BaseAuthAdapter):
            name = "earlycustom"

            def __init__(self, settings=None):
                super().__init__(settings)

            async def validate_token(self, token):
                return outer._claims()

            def is_enabled(self):
                return True

        reg._adapter_registry.clear()  # simulate a fresh process
        register_adapter("earlycustom", _Custom, config_identity="v1")

        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("AUTH_PROVIDER", "jwt")
        monkeypatch.setenv("AUTH_JWKS_URL", "https://a.example.com/jwks.json")
        monkeypatch.setenv("AUTH_ISSUER", "https://a.example.com")
        assert get_auth_adapter().name == "jwt"
        assert reg.BUILTIN_PROVIDERS.issubset(reg._adapter_registry.keys())

    def test_builtin_override_is_preserved(self, monkeypatch):
        """An intentional override of a built-in name is not clobbered."""
        import mozaiksai.core.auth.adapters.registry as reg
        from mozaiksai.core.auth.adapters.base import BaseAuthAdapter
        from mozaiksai.core.auth.adapters.registry import get_auth_adapter, register_adapter

        outer = self

        class _MyJwt(BaseAuthAdapter):
            name = "jwt"

            def __init__(self, settings=None):
                super().__init__(settings)
                self.mine = True

            async def validate_token(self, token):
                return outer._claims()

            def is_enabled(self):
                return True

        reg._adapter_registry.clear()
        register_adapter("jwt", _MyJwt, config_identity="v1")
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("AUTH_PROVIDER", "jwt")
        adapter = get_auth_adapter()
        assert getattr(adapter, "mine", False) is True


# ---------------------------------------------------------------------------
# D5-2: every accepted TTL stays valid during real cache use
# ---------------------------------------------------------------------------

_EXTREME_TTLS = [0, 1, 31, 3600, 86400, 2**63, 10**30, 10**400]


class TestCacheTtlArithmetic:
    @pytest.mark.parametrize("ttl", _EXTREME_TTLS)
    def test_discovery_cache_expiry_never_overflows(self, ttl):
        import time

        from mozaiksai.core.auth.discovery import CachedDiscovery

        entry = CachedDiscovery(document={"issuer": "x"}, fetched_at=time.time(), ttl_seconds=ttl)
        assert entry.is_expired() is (ttl == 0)

    @pytest.mark.parametrize("ttl", _EXTREME_TTLS)
    def test_jwks_cache_expiry_never_overflows(self, ttl):
        import time

        from mozaiksai.core.auth.jwks import CachedJWKS

        entry = CachedJWKS(keys={}, fetched_at=time.time(), ttl_seconds=ttl, source_url="u")
        assert entry.is_expired() is (ttl == 0)

    @pytest.mark.parametrize("ttl", [0, 1, 31, 3600, 86400])
    def test_helper_matches_original_boundary_semantics(self, ttl):
        """For representable TTLs, reproduce `now > fetched_at + ttl` exactly."""
        from mozaiksai.core.auth.cache_ttl import cache_entry_is_expired

        assert cache_entry_is_expired(1000.0, ttl, now=1000.0 + ttl) is False
        assert cache_entry_is_expired(1000.0, ttl, now=1000.0 + ttl + 1) is True

    @pytest.mark.parametrize("ttl", [2**63, 10**30, 10**400])
    def test_helper_handles_very_large_ttls(self, ttl):
        """Very large TTLs must not raise and are never expired at any
        realistic clock value."""
        from mozaiksai.core.auth.cache_ttl import cache_entry_is_expired

        assert cache_entry_is_expired(1000.0, ttl, now=1000.0) is False
        assert cache_entry_is_expired(1000.0, ttl, now=1e18) is False

    def test_regression_witness_old_formulation_overflowed(self):
        """The exact D5-2 defect: the previous `fetched_at + ttl` formulation
        raises OverflowError for a TTL the parser accepts, while the elapsed
        comparison used now does not."""
        from mozaiksai.core.auth.cache_ttl import cache_entry_is_expired, resolve_cache_ttl_setting

        huge = resolve_cache_ttl_setting("AUTH_JWKS_CACHE_TTL", str(10**400))
        with pytest.raises(OverflowError):
            float(huge)  # the old formulation converted the TTL through float
        assert cache_entry_is_expired(1000.0, huge, now=1000.0) is False

    @pytest.mark.parametrize("raw", ["0", "1", "31", "3600", "86400", str(2**63), str(10**400)])
    def test_accepted_values_survive_real_cache_use(self, monkeypatch, raw):
        """Anything configuration resolution accepts must work in the cache."""
        import time

        from mozaiksai.core.auth.adapters.registry import resolve_auth_config
        from mozaiksai.core.auth.discovery import CachedDiscovery

        _set_matrix_env(
            monkeypatch,
            {
                "AUTH_ENABLED": "true",
                "AUTH_PROVIDER": "jwt",
                "AUTH_JWKS_URL": "https://a.example.com/jwks.json",
                "AUTH_ISSUER": "https://a.example.com",
            },
        )
        monkeypatch.setenv("AUTH_DISCOVERY_CACHE_TTL", raw)
        monkeypatch.setenv("AUTH_JWKS_CACHE_TTL", raw)
        resolve_auth_config()  # accepted at startup
        entry = CachedDiscovery(document={}, fetched_at=time.time(), ttl_seconds=int(raw))
        entry.is_expired()  # must not raise

    def test_zero_ttl_stays_zero_not_default(self, monkeypatch):
        """0 means always refetch and must never be truthiness-coerced away."""
        from mozaiksai.core.auth.adapters.jwt_adapter import JWTAdapterConfig
        from mozaiksai.core.auth.cache_ttl import resolve_cache_ttl_setting
        from mozaiksai.core.auth.config import clear_auth_config_cache, get_auth_config
        from mozaiksai.core.auth.discovery import OIDCDiscoveryClient
        from mozaiksai.core.auth.jwks import JWKSClient

        assert resolve_cache_ttl_setting("AUTH_JWKS_CACHE_TTL", "0") == 0
        assert resolve_cache_ttl_setting("AUTH_DISCOVERY_CACHE_TTL", "0") == 0

        config = JWTAdapterConfig.from_env(
            {"AUTH_JWKS_CACHE_TTL": "0", "AUTH_DISCOVERY_CACHE_TTL": "0"}
        )
        assert config.jwks_cache_ttl_seconds == 0
        assert config.discovery_cache_ttl_seconds == 0

        assert OIDCDiscoveryClient(cache_ttl=0, consult_environment=False).cache_ttl_seconds == 0
        assert JWKSClient(jwks_url="https://x/j", cache_ttl=0, consult_environment=False).cache_ttl_seconds == 0

        monkeypatch.setenv("AUTH_JWKS_CACHE_TTL", "0")
        monkeypatch.setenv("AUTH_DISCOVERY_CACHE_TTL", "0")
        clear_auth_config_cache()
        try:
            assert get_auth_config().jwks_cache_ttl_seconds == 0
            assert get_auth_config().discovery_cache_ttl_seconds == 0
        finally:
            clear_auth_config_cache()


# ---------------------------------------------------------------------------
# D5-3: absent / empty / whitespace normalize identically, in every layer
# ---------------------------------------------------------------------------

_BLANK_FORMS = [None, "", " ", "   ", "\t", "\n", " \t\n "]
_TTL_ENV_NAMES = ["AUTH_JWKS_CACHE_TTL", "AUTH_DISCOVERY_CACHE_TTL"]


class TestCacheTtlNormalization:
    @pytest.fixture(autouse=True)
    def _fresh(self, monkeypatch):
        from mozaiksai.core.auth.adapters.registry import reset_auth_adapter
        from mozaiksai.core.auth.config import clear_auth_config_cache

        for var in (*_AUTH_MATRIX_VARS, *_TTL_ENV_NAMES):
            monkeypatch.delenv(var, raising=False)
        clear_auth_config_cache()
        reset_auth_adapter()
        yield
        clear_auth_config_cache()
        reset_auth_adapter()

    def _expected_default(self, name: str) -> int:
        from mozaiksai.core.auth.cache_ttl import CACHE_TTL_DEFAULTS

        return CACHE_TTL_DEFAULTS[name]

    @pytest.mark.parametrize("name", _TTL_ENV_NAMES)
    @pytest.mark.parametrize("blank", _BLANK_FORMS)
    def test_blank_forms_all_yield_the_canonical_default(self, name, blank):
        from mozaiksai.core.auth.cache_ttl import resolve_cache_ttl_setting

        assert resolve_cache_ttl_setting(name, blank) == self._expected_default(name)

    @pytest.mark.parametrize("blank", _BLANK_FORMS)
    def test_resolution_and_adapter_construction_agree_on_blanks(self, monkeypatch, blank):
        """The exact D5-3 defect: canonical resolution and JWTAdapterConfig must
        not disagree about whitespace."""
        from mozaiksai.core.auth.adapters.jwt_adapter import JWTAdapterConfig
        from mozaiksai.core.auth.adapters.registry import get_auth_adapter, resolve_auth_config

        env = {
            "AUTH_ENABLED": "true",
            "AUTH_PROVIDER": "jwt",
            "AUTH_JWKS_URL": "https://a.example.com/jwks.json",
            "AUTH_ISSUER": "https://a.example.com",
        }
        _set_matrix_env(monkeypatch, env)
        if blank is not None:
            monkeypatch.setenv("AUTH_JWKS_CACHE_TTL", blank)
            monkeypatch.setenv("AUTH_DISCOVERY_CACHE_TTL", blank)

        config = resolve_auth_config()  # must not raise
        adapter = get_auth_adapter()  # must not raise
        direct = JWTAdapterConfig.from_env(config.settings)

        assert adapter._config.jwks_cache_ttl_seconds == self._expected_default(
            "AUTH_JWKS_CACHE_TTL"
        )
        assert adapter._config.discovery_cache_ttl_seconds == self._expected_default(
            "AUTH_DISCOVERY_CACHE_TTL"
        )
        assert direct.jwks_cache_ttl_seconds == adapter._config.jwks_cache_ttl_seconds
        assert direct.discovery_cache_ttl_seconds == adapter._config.discovery_cache_ttl_seconds

    @pytest.mark.parametrize("name", _TTL_ENV_NAMES)
    @pytest.mark.parametrize("bad", ["-1", "1.5", "abc", "1e3", "0x10", "3,600", " -5 "])
    def test_invalid_values_rejected_by_every_layer(self, monkeypatch, name, bad):
        from mozaiksai.core.auth.adapters.jwt_adapter import JWTAdapterConfig
        from mozaiksai.core.auth.adapters.registry import resolve_auth_config
        from mozaiksai.core.auth.cache_ttl import CacheTtlConfigError

        _set_matrix_env(
            monkeypatch,
            {
                "AUTH_ENABLED": "true",
                "AUTH_PROVIDER": "jwt",
                "AUTH_JWKS_URL": "https://a.example.com/jwks.json",
                "AUTH_ISSUER": "https://a.example.com",
            },
        )
        monkeypatch.setenv(name, bad)
        with pytest.raises(AuthError, match=name):
            resolve_auth_config()
        with pytest.raises(CacheTtlConfigError):
            JWTAdapterConfig.from_env({name: bad})

    @pytest.mark.parametrize("name", _TTL_ENV_NAMES)
    @pytest.mark.parametrize("good", ["0", "1", "31", "3600", "86400", " 42 "])
    def test_valid_values_agree_across_layers(self, monkeypatch, name, good):
        from mozaiksai.core.auth.adapters.jwt_adapter import JWTAdapterConfig
        from mozaiksai.core.auth.cache_ttl import resolve_cache_ttl_setting

        expected = int(good.strip())
        assert resolve_cache_ttl_setting(name, good) == expected
        config = JWTAdapterConfig.from_env({name: good})
        attribute = (
            "jwks_cache_ttl_seconds"
            if name == "AUTH_JWKS_CACHE_TTL"
            else "discovery_cache_ttl_seconds"
        )
        assert getattr(config, attribute) == expected

    def test_canonical_defaults_match_documented_cache_semantics(self):
        """Defaults are declared once; discovery is long-lived, keys rotate."""
        from mozaiksai.core.auth.cache_ttl import (
            DEFAULT_DISCOVERY_CACHE_TTL_SECONDS,
            DEFAULT_JWKS_CACHE_TTL_SECONDS,
        )
        from mozaiksai.core.auth.config import clear_auth_config_cache, get_auth_config

        assert DEFAULT_JWKS_CACHE_TTL_SECONDS == 3600
        assert DEFAULT_DISCOVERY_CACHE_TTL_SECONDS == 86400
        clear_auth_config_cache()
        try:
            config = get_auth_config()
            assert config.jwks_cache_ttl_seconds == DEFAULT_JWKS_CACHE_TTL_SECONDS
            assert config.discovery_cache_ttl_seconds == DEFAULT_DISCOVERY_CACHE_TTL_SECONDS
        finally:
            clear_auth_config_cache()
