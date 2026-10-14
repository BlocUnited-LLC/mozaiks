"""The app.json admins allowlist promotes only an identity-provider-verified email.

A signed access token whose ``email`` is on the allowlist but whose
``email_verified`` claim is false or missing must not become an admin: on a
provider with open self-registration (the packaged Keycloak realm allows it and
does not verify email), anyone can register an account with the admin's address
before the real admin does.

These tests drive the real admin router (``GET /api/admin/health``) through the
real auth dependency and the real generic JWT, Keycloak and Supabase adapters.
Token signature, issuer, audience and expiry are verified against in-memory
keys; nothing touches the network.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mozaiksai.core.auth import UserPrincipal, WebSocketUser
from mozaiksai.core.auth.adapters import registry as auth_registry
from mozaiksai.core.auth.adapters.base import UserClaims
from mozaiksai.core.auth.adapters.jwt_adapter import GenericJWTAdapter, JWTAdapterConfig
from mozaiksai.core.auth.config import clear_auth_config_cache

_ISSUER = "https://idp.example.invalid/realms/app"
_AUDIENCE = "app-api"
_KID = "kid-admin-email"
_SIGNING_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PUBLIC_JWK = {
    **json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(_SIGNING_KEY.public_key())),
    "kid": _KID,
    "alg": "RS256",
    "use": "sig",
}
_ADMIN_EMAIL = "owner@example.com"
_ABSENT = object()


class _StaticJWKSClient:
    async def get_signing_key(self, kid: str) -> dict[str, Any]:
        assert kid == _KID
        return _PUBLIC_JWK


@pytest.fixture
def admin_client(monkeypatch, tmp_path: Path) -> TestClient:
    """The real admin router on a host whose app.json lists one admin email."""
    app_root = tmp_path / "app"
    app_root.mkdir()
    (app_root / "app.json").write_text(
        json.dumps({"appName": "Allowlist App", "admins": [_ADMIN_EMAIL]}), encoding="utf-8"
    )
    monkeypatch.setenv("PLATFORM_PATH", str(app_root))

    for name in auth_registry._ALL_AUTH_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    for name, value in {
        "AUTH_PROVIDER": "jwt",
        "AUTH_JWKS_URL": "https://idp.example.invalid/jwks",
        "AUTH_ISSUER": _ISSUER,
        "AUTH_AUDIENCE": _AUDIENCE,
    }.items():
        monkeypatch.setenv(name, value)

    async def _offline_jwks(self: GenericJWTAdapter) -> _StaticJWKSClient:
        return _StaticJWKSClient()

    monkeypatch.setattr(GenericJWTAdapter, "_get_jwks_client_async", _offline_jwks)
    clear_auth_config_cache()
    auth_registry.reset_auth_adapter()

    from mozaiksai.core.admin.router import router

    app = FastAPI()
    app.include_router(router)
    yield TestClient(app)
    clear_auth_config_cache()
    auth_registry.reset_auth_adapter()


def _token(*, email: str | None = _ADMIN_EMAIL, email_verified: object = _ABSENT, **extra: object) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "sub": "user-1",
        "iss": _ISSUER,
        "aud": _AUDIENCE,
        "iat": now,
        "nbf": now - 1,
        "exp": now + 300,
        **extra,
    }
    if email is not None:
        claims["email"] = email
    if email_verified is not _ABSENT:
        claims["email_verified"] = email_verified
    return jwt.encode(claims, _SIGNING_KEY, algorithm="RS256", headers={"kid": _KID, "typ": "at+jwt"})


def _health(client: TestClient, token: str):
    return client.get("/api/admin/health", headers={"Authorization": f"Bearer {token}"})


# --- Reproduction: an unverified allowlisted email was promoted to admin ----------


def test_unverified_allowlisted_email_is_not_promoted(admin_client: TestClient) -> None:
    """A self-registered account with the admin's address, not yet verified."""
    response = _health(admin_client, _token(email_verified=False))

    assert response.status_code == 403
    assert response.json() == {"detail": "Admin access required"}


def test_allowlisted_email_without_a_verified_claim_is_not_promoted(admin_client: TestClient) -> None:
    """An issuer that never says whether the address is verified."""
    response = _health(admin_client, _token())

    assert response.status_code == 403


# --- Verified email, token roles, and refusals ----------------------------------------


def test_verified_allowlisted_email_is_promoted(admin_client: TestClient) -> None:
    assert _health(admin_client, _token(email_verified=True)).json() == {"status": "ok"}


def test_allowlist_match_ignores_email_case_when_verified(admin_client: TestClient) -> None:
    response = _health(admin_client, _token(email="Owner@Example.COM", email_verified=True))

    assert response.status_code == 200


@pytest.mark.parametrize("claim", ["true", "True", 1, "yes", None, {}])
def test_only_the_json_boolean_true_verifies_an_email(admin_client: TestClient, claim: object) -> None:
    assert _health(admin_client, _token(email_verified=claim)).status_code == 403


def test_verified_email_off_the_allowlist_is_refused(admin_client: TestClient) -> None:
    response = _health(admin_client, _token(email="someone@example.com", email_verified=True))

    assert response.status_code == 403


@pytest.mark.parametrize("email_verified", [_ABSENT, False])
def test_admin_role_from_the_token_needs_no_verified_email(
    admin_client: TestClient, email_verified: object
) -> None:
    token = _token(email="ops@example.com", email_verified=email_verified, roles=["admin"])

    assert _health(admin_client, token).status_code == 200


def test_refusing_an_unverified_allowlisted_email_is_logged(
    admin_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        response = _health(admin_client, _token(email_verified=False))

    assert response.status_code == 403
    refusal = [record for record in caplog.records if "ADMIN_EMAIL_UNVERIFIED" in record.getMessage()]
    assert len(refusal) == 1
    message = refusal[0].getMessage()
    assert "user_id='user-1'" in message and "provider='jwt'" in message
    # The log names the principal, not the address it claimed.
    assert _ADMIN_EMAIL not in message


def test_a_configured_verified_email_claim_is_honoured(admin_client: TestClient, monkeypatch) -> None:
    """AUTH_EMAIL_VERIFIED_CLAIM names an issuer-specific verified-email claim."""
    monkeypatch.setenv("AUTH_EMAIL_VERIFIED_CLAIM", "xms_edov")
    clear_auth_config_cache()
    auth_registry.reset_auth_adapter()

    assert _health(admin_client, _token(xms_edov=True)).status_code == 200
    # The default claim no longer counts once another one is configured.
    assert _health(admin_client, _token(email_verified=True)).status_code == 403


# --- Authentication off --------------------------------------------------------------


def _authentication_off(monkeypatch, **values: str) -> None:
    for name in auth_registry._ALL_AUTH_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AUTH_ENABLED", "false")
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    clear_auth_config_cache()
    auth_registry.reset_auth_adapter()


def _peer(client: TestClient, host: str) -> TestClient:
    return TestClient(client.app, client=(host, 50000), base_url="http://localhost:8000")


def test_local_development_admin_comes_from_auth_anon_roles(admin_client: TestClient, monkeypatch) -> None:
    _authentication_off(monkeypatch, AUTH_ANON_ROLES="admin,user")

    assert _peer(admin_client, "127.0.0.1").get("/api/admin/health").status_code == 200


def test_local_development_identity_keeps_the_allowlist(admin_client: TestClient, monkeypatch) -> None:
    """Development access may already assume any role, so its allowlisted email still counts."""
    _authentication_off(monkeypatch, AUTH_ANON_EMAIL=_ADMIN_EMAIL)
    assert _peer(admin_client, "127.0.0.1").get("/api/admin/health").status_code == 200

    # A public visitor carries no email, so the allowlist never applies to it.
    _authentication_off(monkeypatch, AUTH_ANON_ACCESS="public", AUTH_ANON_EMAIL=_ADMIN_EMAIL)
    assert _peer(admin_client, "192.168.1.20").get("/api/admin/health").status_code == 403


# --- Keycloak and Supabase providers through the same gate ---------------------------

_KC_URL = "https://kc.example.invalid"
_KC_REALM = "app"
_KC_CLIENT = "app-api"


def _kc_token(**claims: object) -> str:
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": "kc-user",
        "typ": "Bearer",
        "iss": f"{_KC_URL}/realms/{_KC_REALM}",
        "aud": _KC_CLIENT,
        "iat": now,
        "exp": now + 300,
        "email": _ADMIN_EMAIL,
        **claims,
    }
    return jwt.encode(payload, _SIGNING_KEY, algorithm="RS256", headers={"kid": _KID})


@pytest.mark.parametrize(("email_verified", "status"), [(True, 200), (False, 403), (_ABSENT, 403)])
def test_keycloak_promotes_only_a_verified_email(
    admin_client: TestClient, monkeypatch, email_verified: object, status: int
) -> None:
    from mozaiksai.core.auth.adapters.keycloak import KeycloakAuthAdapter

    for name in auth_registry._ALL_AUTH_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AUTH_PROVIDER", "keycloak")
    monkeypatch.setenv("KEYCLOAK_URL", _KC_URL)
    monkeypatch.setenv("KEYCLOAK_REALM", _KC_REALM)
    monkeypatch.setenv("KEYCLOAK_CLIENT_ID", _KC_CLIENT)
    signing_key = SimpleNamespace(key=_SIGNING_KEY.public_key())
    jwks = SimpleNamespace(get_signing_key_from_jwt=lambda _token: signing_key)
    monkeypatch.setattr(KeycloakAuthAdapter, "_get_jwks_client", lambda self: jwks)
    clear_auth_config_cache()
    auth_registry.reset_auth_adapter()

    claims = {} if email_verified is _ABSENT else {"email_verified": email_verified}
    assert _health(admin_client, _kc_token(**claims)).status_code == status


def _supabase_token(secret: str, **claims: object) -> str:
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": "sb-user",
        "aud": "authenticated",
        "role": "authenticated",
        "iat": now,
        "exp": now + 300,
        "email": _ADMIN_EMAIL,
        **claims,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def test_supabase_ignores_the_user_writable_metadata_copy(admin_client: TestClient, monkeypatch) -> None:
    secret = secrets.token_hex(32)
    for name in auth_registry._ALL_AUTH_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AUTH_PROVIDER", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "https://project.supabase.invalid")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", secret)
    clear_auth_config_cache()
    auth_registry.reset_auth_adapter()

    # Supabase's default token: email verified only inside user_metadata,
    # which the user can rewrite through updateUser().
    default = _supabase_token(secret, user_metadata={"email": _ADMIN_EMAIL, "email_verified": True})
    assert _health(admin_client, default).status_code == 403
    # A top-level claim, which only the server (an access token hook) can set.
    hooked = _supabase_token(secret, email_verified=True)
    assert _health(admin_client, hooked).status_code == 200


# --- Each adapter maps the claim -------------------------------------------------------


def _jwt_claims(**claims: object) -> dict[str, Any]:
    return {"sub": "user-1", "email": _ADMIN_EMAIL, **claims}


@pytest.mark.parametrize(
    ("claims", "verified"),
    [
        (_jwt_claims(email_verified=True), True),
        (_jwt_claims(email_verified=False), False),
        (_jwt_claims(), False),
        (_jwt_claims(email_verified="true"), False),
        (_jwt_claims(email_verified=1), False),
        # Verification describes the email the principal carries; no email, nothing verified.
        ({"sub": "user-1", "email_verified": True}, False),
    ],
)
def test_generic_jwt_adapter_maps_email_verified(claims: dict[str, Any], verified: bool) -> None:
    adapter = GenericJWTAdapter(
        config=JWTAdapterConfig(jwks_url="https://j.invalid", issuer=_ISSUER, audience=_AUDIENCE)
    )

    assert adapter._extract_claims(claims).email_verified is verified


def test_generic_jwt_adapter_reads_the_configured_verified_claim() -> None:
    assert JWTAdapterConfig.from_env({}).email_verified_claim == "email_verified"
    adapter = GenericJWTAdapter(config=JWTAdapterConfig.from_env({"AUTH_EMAIL_VERIFIED_CLAIM": "xms_edov"}))

    assert adapter._extract_claims(_jwt_claims(xms_edov=True)).email_verified is True
    assert adapter._extract_claims(_jwt_claims(email_verified=True)).email_verified is False
    assert "AUTH_EMAIL_VERIFIED_CLAIM" in auth_registry._PROVIDER_CONFIG_ENV_VARS["jwt"]


@pytest.mark.parametrize(
    ("claims", "verified"),
    [({"email_verified": True}, True), ({"email_verified": False}, False), ({}, False)],
)
def test_keycloak_adapter_maps_email_verified(claims: dict[str, Any], verified: bool) -> None:
    from mozaiksai.core.auth.adapters.keycloak import KeycloakAuthAdapter

    adapter = KeycloakAuthAdapter(keycloak_url=_KC_URL, realm=_KC_REALM, client_id=_KC_CLIENT)
    extracted = adapter._extract_claims({"sub": "kc-user", "email": _ADMIN_EMAIL, **claims})

    assert extracted.email_verified is verified


@pytest.mark.parametrize(
    ("claims", "verified"),
    [
        ({"email_verified": True}, True),
        ({"user_metadata": {"email_verified": True}}, False),
        ({"app_metadata": {"email_verified": True}}, False),
        ({}, False),
    ],
)
def test_supabase_adapter_maps_only_the_top_level_claim(claims: dict[str, Any], verified: bool) -> None:
    from mozaiksai.core.auth.adapters.supabase import SupabaseAuthAdapter

    adapter = SupabaseAuthAdapter(supabase_url="https://project.supabase.invalid")
    extracted = adapter._extract_claims({"sub": "sb-user", "email": _ADMIN_EMAIL, **claims})

    assert extracted.email_verified is verified


def test_no_auth_adapter_never_verifies_its_email() -> None:
    from mozaiksai.core.auth.adapters.no_auth import NoAuthAdapter

    claims = NoAuthAdapter(default_email=_ADMIN_EMAIL, settings={}).validate_token_sync("")

    assert (claims.email, claims.email_verified) == (_ADMIN_EMAIL, False)


def test_principals_carry_the_adapter_fact() -> None:
    claims = UserClaims(user_id="user-1", email=_ADMIN_EMAIL, email_verified=True)

    assert UserPrincipal.from_claims(claims, auth_provenance="token_validated").email_verified is True
    assert WebSocketUser.from_claims(claims, auth_provenance="token_validated").email_verified is True
    assert UserPrincipal.from_claims(UserClaims(user_id="user-1", email=_ADMIN_EMAIL)).email_verified is False


def test_a_visitor_loses_any_verified_email_with_the_email() -> None:
    from mozaiksai.core.auth.anonymous_access import visitor_claims

    config = auth_registry.resolve_auth_config(environ={"AUTH_ENABLED": "false", "AUTH_ANON_ACCESS": "public"})
    claims = visitor_claims(UserClaims(user_id="anonymous", email=_ADMIN_EMAIL, email_verified=True), config)

    assert (claims.email, claims.email_verified) == (None, False)
