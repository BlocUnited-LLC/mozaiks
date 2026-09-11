"""App auth intent, effective runtime mode, and public shell safety."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from mozaiksai.core.auth.adapters.base import AuthError
from mozaiksai.core.auth.adapters.registry import reset_auth_adapter
from mozaiksai.core.runtime.app.auth_contract import (
    AppAuthContractError,
    build_app_auth_projection,
    load_app_auth_contract,
    validate_app_auth_contract,
)
from mozaiksai.core.runtime.app.loader import AppLoader, AppLoadError

FACTORY_APP = Path(__file__).resolve().parents[1] / "factory_app" / "app"


@pytest.fixture
def auth_document():
    return yaml.safe_load((FACTORY_APP / "config/auth.yaml").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def isolated_auth(monkeypatch):
    for name in tuple(os.environ):
        if name.startswith(("AUTH_", "MOZAIKS_OIDC_", "VITE_OIDC_", "SUPABASE_", "KEYCLOAK_")):
            monkeypatch.delenv(name)
    monkeypatch.setenv("ENV", "test")
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    reset_auth_adapter()
    yield
    reset_auth_adapter()


def write_app(root, document=None, *, required=True):
    root.mkdir(parents=True, exist_ok=True)
    (root / "app.json").write_text(json.dumps({"appName": "Auth proof", "authRequired": required}), encoding="utf-8")
    if document is not None:
        (root / "config").mkdir(exist_ok=True)
        (root / "config/auth.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")


def test_factory_declares_oidc_without_weakening_app_intent(auth_document):
    manifest = json.loads((FACTORY_APP / "app.json").read_text(encoding="utf-8"))
    contract = load_app_auth_contract(FACTORY_APP, auth_required=manifest["authRequired"])
    assert manifest["authRequired"] is True
    assert contract.auth_required is True
    assert contract.strategy == "oidc"
    assert contract.routes.post_login_default == "/apps"
    assert validate_app_auth_contract(auth_document) == contract


@pytest.mark.parametrize("change", [
    {"strategy": "public"},
    {"auth_required": False},
    {"auth_required": 1},
    {"signup_enabled": "false"},
    {"mode": "direct_provider_sdk"},
    {"client_secret": "unpublishable-value"},
    {"frontend": {"adapter": "oidc_pkce", "authority_env": "OPENAI_API_KEY"}},
    {"routes": {"login": "//foreign.invalid", "callback": "/callback", "logout": "/", "post_login_default": "/"}},
    {"identity_providers": [{"id": "broker", "label": "https://provider.invalid"}]},
    {"login_methods": [{"id": "broker", "kind": "oidc_redirect", "label": "Sign in", "provider_id": "undeclared"}]},
])
def test_invalid_declarations_fail_without_echoing_values(auth_document, change):
    document = {**auth_document, **change}
    with pytest.raises(AppAuthContractError) as caught:
        validate_app_auth_contract(document)
    assert "unpublishable-value" not in str(caught.value)
    assert "https://provider.invalid" not in str(caught.value)


@pytest.mark.asyncio
async def test_loader_requires_declared_auth_contract(tmp_path):
    write_app(tmp_path)
    with pytest.raises(AppLoadError, match="requires config/auth.yaml"):
        await AppLoader.load(str(tmp_path))


@pytest.mark.asyncio
async def test_loader_exposes_validated_contract(tmp_path, auth_document):
    write_app(tmp_path, auth_document)
    loaded = await AppLoader.load(str(tmp_path))
    assert loaded.auth_contract == validate_app_auth_contract(auth_document)


@pytest.mark.asyncio
async def test_loader_rejects_present_invalid_contract_for_public_app(tmp_path, auth_document):
    document = deepcopy(auth_document)
    document["runtime"]["enabled_env"] = "CUSTOM_AUTH_BYPASS"
    write_app(tmp_path, document, required=False)
    with pytest.raises(AppLoadError, match="runtime.enabled_env"):
        await AppLoader.load(str(tmp_path))


def test_public_app_can_omit_auth_contract(tmp_path):
    write_app(tmp_path, required=False)
    assert load_app_auth_contract(tmp_path) is None


@pytest.mark.parametrize("route", ["//foreign.invalid", "/%2fforeign.invalid", "/%5cforeign.invalid", "/a/%2e%2e/login", "/log in", "/login%0d%0aLocation:evil", "login", "%2flogin"])
def test_auth_routes_reject_unsafe_redirect_paths(auth_document, route):
    auth_document["routes"]["login"] = route
    with pytest.raises(AppAuthContractError, match="app-local route"):
        validate_app_auth_contract(auth_document)


def test_app_owned_login_visual_reference_remains_declared_intent(auth_document):
    auth_document["customization"]["login_theme_source"] = "app/ui/pages/custom/LoginPage.jsx"
    contract = validate_app_auth_contract(auth_document)
    assert contract.customization.login_theme_source == "app/ui/pages/custom/LoginPage.jsx"


@pytest.mark.parametrize("reference", ["../LoginPage.jsx", "%2e%2e/LoginPage.jsx", "/absolute/LoginPage.jsx", "C:/LoginPage.jsx", "ui\\LoginPage.jsx", "https://foreign.invalid/LoginPage.jsx"])
def test_login_visual_reference_cannot_escape_app(auth_document, reference):
    auth_document["customization"]["login_theme_source"] = reference
    with pytest.raises(AppAuthContractError, match="relative visual reference"):
        validate_app_auth_contract(auth_document)


def test_auth_loader_sanitizes_invalid_encoding(tmp_path):
    write_app(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config/auth.yaml").write_bytes(b"private-input-\xff")
    with pytest.raises(AppAuthContractError, match="valid YAML") as caught:
        load_app_auth_contract(tmp_path, auth_required=True)
    assert "private-input" not in str(caught.value)


@pytest.mark.asyncio
async def test_explicit_local_mode_uses_runtime_identity_without_token(monkeypatch, auth_document):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("AUTH_ANON_USER_ID", "local-operator")
    monkeypatch.setenv("AUTH_ANON_ROLES", "developer")
    projection = await build_app_auth_projection(validate_app_auth_contract(auth_document))
    assert projection["contract"]["auth_required"] is True
    assert projection["required"] is True
    runtime = projection["runtime"]
    assert runtime["enabled"] is False
    assert runtime["provider"] == "none"
    assert runtime["local_development"] is True
    assert runtime["user"]["id"] == "local-operator"
    assert runtime["user"]["roles"] == ["developer"]
    assert "token" not in json.dumps(runtime)


@pytest.mark.asyncio
async def test_implicit_demo_default_does_not_grant_frontend_local_mode(auth_document):
    projection = await build_app_auth_projection(validate_app_auth_contract(auth_document))
    assert projection["runtime"] == {"enabled": False, "provider": "none", "local_development": False, "user": None}


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
async def test_public_app_projects_explicit_anonymous_intent_without_changing_runtime_auth(monkeypatch, enabled):
    if enabled:
        monkeypatch.setenv("ENV", "production")
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("AUTH_PROVIDER", "jwt")
        monkeypatch.setenv("AUTH_ISSUER", "https://backend-issuer.invalid")
        monkeypatch.setenv("AUTH_JWKS_URL", "https://backend-issuer.invalid/jwks")
    projection = await build_app_auth_projection(None)
    assert projection["required"] is False
    assert projection["contract"] is None and projection["frontend"] is None
    assert projection["runtime"]["enabled"] is enabled
    assert projection["runtime"]["local_development"] is False
    assert projection["runtime"]["user"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("environment", ["staging", "production", "preview-eu"])
async def test_deployed_disable_still_fails_closed(monkeypatch, auth_document, environment):
    monkeypatch.setenv("ENV", environment)
    monkeypatch.setenv("AUTH_ENABLED", "false")
    with pytest.raises(AuthError, match="not permitted"):
        await build_app_auth_projection(validate_app_auth_contract(auth_document))


@pytest.mark.asyncio
async def test_public_projection_exposes_only_declared_browser_config(monkeypatch, auth_document):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_PROVIDER", "jwt")
    monkeypatch.setenv("AUTH_ISSUER", "https://backend-issuer.invalid")
    monkeypatch.setenv("AUTH_JWKS_URL", "https://backend-issuer.invalid/jwks")
    monkeypatch.setenv("VITE_OIDC_AUTHORITY", "https://browser-issuer.invalid")
    monkeypatch.setenv("VITE_OIDC_CLIENT_ID", "factory-browser")
    monkeypatch.setenv("OPENAI_API_KEY", "must-stay-private-model-key")
    monkeypatch.setenv("AUTH_CLIENT_SECRET", "must-stay-private-client-secret")
    projection = await build_app_auth_projection(validate_app_auth_contract(auth_document))
    assert projection["runtime"] == {"enabled": True, "provider": "jwt", "local_development": False, "user": None}
    assert projection["frontend"] == {
        "authority": "https://browser-issuer.invalid", "discovery_url": "", "client_id": "factory-browser",
        "redirect_uri": "", "scope": "",
    }
    serialized = json.dumps(projection)
    assert "must-stay-private" not in serialized
    assert "backend-issuer.invalid" not in serialized


@pytest.mark.asyncio
async def test_missing_browser_env_never_disables_backend_auth(monkeypatch, auth_document):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_PROVIDER", "jwt")
    monkeypatch.setenv("AUTH_ISSUER", "https://backend-issuer.invalid")
    monkeypatch.setenv("AUTH_JWKS_URL", "https://backend-issuer.invalid/jwks")
    contract = validate_app_auth_contract(auth_document)
    projection = await build_app_auth_projection(contract)
    assert projection["frontend"] == dict.fromkeys(
        ("authority", "discovery_url", "client_id", "redirect_uri", "scope"), ""
    )
    assert projection["contract"]["frontend"]["default_scopes"] == ["openid", "profile", "email"]
    assert projection["runtime"] == {"enabled": True, "provider": "jwt", "local_development": False, "user": None}
    monkeypatch.setenv("VITE_OIDC_SCOPE", "openid profile email project:read")
    configured = await build_app_auth_projection(contract)
    assert configured["frontend"]["scope"] == "openid profile email project:read"
    assert configured["runtime"] == projection["runtime"]


@pytest.mark.asyncio
async def test_shell_projects_auth_contract_and_effective_local_mode(monkeypatch):
    from mozaiksai.hosts import platform

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setattr(platform, "resolve_app_root", lambda: FACTORY_APP)
    result = await platform.build_shell_config(surface="studio")
    assert result["auth"]["contract"]["routes"]["post_login_default"] == "/apps"
    assert result["auth"]["runtime"]["local_development"] is True
    assert result["auth"]["runtime"]["user"]["id"] == "anonymous"
