"""
Auth config loader — reads the ``auth`` section from app.json for Keycloak config.

This module provides a bridge between the declarative app.json (the single
source of app configuration) and the runtime AuthConfig used by the Python
backend.

Priority order:
    1. Environment variables (always win — deployment overrides)
    2. app.json ``auth`` section values (app-level defaults)
    3. Built-in Keycloak defaults (fallback)

Usage:
    from mozaiksai.runtime.auth.auth_config_loader import load_app_json, derive_auth_env

    # Load the raw JSON
    app_json = load_app_json()

    # Or derive OIDC env-style values for AuthConfig
    derived = derive_auth_env()
    # derived = {"authority": "http://localhost:8080/realms/mozaiks", ...}
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional
from functools import lru_cache

from logs.logging_config import get_core_logger

logger = get_core_logger("auth.config_loader")

# Default search paths for app.json (relative to project root)
_APP_JSON_SEARCH_PATHS = [
    "app/app.json",
]


def _find_app_json() -> Optional[Path]:
    """
    Locate app.json by searching known paths relative to project root.

    Returns:
        Path to app.json if found, None otherwise.
    """
    # Allow explicit override via env var
    explicit = os.getenv("MOZAIKS_APP_JSON_PATH")
    if explicit:
        p = Path(explicit)
        if p.is_file():
            return p
        logger.warning(f"MOZAIKS_APP_JSON_PATH set to '{explicit}' but file not found")
        return None

    # Search from project root (three levels up from this file:
    # mozaiksai/runtime/auth/auth_config_loader.py → project root)
    project_root = Path(__file__).resolve().parent.parent.parent.parent

    for rel_path in _APP_JSON_SEARCH_PATHS:
        candidate = project_root / rel_path
        if candidate.is_file():
            logger.debug(f"Found app.json at {candidate}")
            return candidate

    return None


@lru_cache(maxsize=1)
def load_app_json() -> Dict[str, Any]:
    """
    Load and parse app.json. Returns empty dict if not found.

    Cached after first load (call clear_app_json_cache() to invalidate).
    """
    path = _find_app_json()
    if path is None:
        logger.info("No app.json found — using env vars / built-in Keycloak defaults")
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        logger.info(f"Loaded app.json from {path}")
        return data
    except (json.JSONDecodeError, OSError) as exc:
        logger.error(f"Failed to load app.json from {path}: {exc}")
        return {}


# ── Backwards compatibility aliases ──────────────────────────────────────────
# Some callsites may still reference load_auth_json — redirect to app.json
load_auth_json = load_app_json


def clear_app_json_cache() -> None:
    """Clear cached app.json (useful for testing or hot-reload)."""
    load_app_json.cache_clear()


clear_auth_json_cache = clear_app_json_cache


def derive_auth_env(app_json: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Derive OIDC / auth config values from app.json's ``auth`` section.

    Converts the declarative app.json structure into the flat key-value
    pairs that AuthConfig expects. Environment variables always take
    precedence — this only fills in what's missing.

    Returns:
        Dict with keys: authority, realm, client_id, audience,
        discovery_url, roles_claim, user_id_claim, email_claim
    """
    if app_json is None:
        app_json = load_app_json()

    auth = app_json.get("auth", {})
    provider = auth.get("provider", "keycloak")
    kc = auth.get("keycloak", {})
    roles_cfg = auth.get("roles", {})

    # Build Keycloak authority (includes realm path)
    base_authority = kc.get("authority", "http://localhost:8080")
    realm = kc.get("realm", "mozaiks")
    realm_authority = f"{base_authority.rstrip('/')}/realms/{realm}"

    # OIDC discovery URL for Keycloak
    discovery_url = f"{realm_authority}/.well-known/openid-configuration"

    # Client ID (used as audience for Keycloak)
    client_id = kc.get("clientId", "mozaiks-app")

    # Role claim path — Keycloak puts roles in realm_access.roles
    # The JWT validator maps this via the roles_claim config
    roles_claim = "realm_access"
    if "claimPath" in roles_cfg:
        # If claimPath is "realm_access.roles", we use "realm_access"
        # (the validator digs into the .roles sub-field)
        parts = roles_cfg["claimPath"].split(".")
        roles_claim = parts[0] if parts else "realm_access"

    return {
        "provider": provider,
        "authority": realm_authority,
        "realm": realm,
        "base_authority": base_authority,
        "client_id": client_id,
        "audience": client_id,  # Keycloak uses client_id as audience
        "discovery_url": discovery_url,
        "roles_claim": roles_claim,
        "user_id_claim": "sub",
        "email_claim": "email",
        "required_scope": "openid",
    }


def get_keycloak_branding(app_json: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Extract Keycloak login page branding config from brand.json.

    These values reference files in brand/public/assets/ and are used to:
    1. Generate a custom Keycloak theme (for branded login pages)
    2. Theme the Keycloak login pages via the Admin API

    Returns:
        Dict with branding keys: logo, favicon, backgroundImage, theme, etc.
    """
    # Branding lives in brand.json (not app.json) — load it directly
    project_root = Path(__file__).resolve().parent.parent.parent.parent
    brand_path = project_root / "app" / "brand" / "public" / "brand.json"
    brand = {}
    if brand_path.is_file():
        try:
            with open(brand_path, "r", encoding="utf-8") as f:
                brand = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    assets = brand.get("assets", {})
    return {
        "loginTitle": "Sign In",
        "registerTitle": "Create Account",
        "logo": assets.get("logo", "mozaik_logo.svg"),
        "favicon": assets.get("favicon", "mozaik.png"),
        "backgroundImage": assets.get("backgroundImage"),
        "theme": "dark",
        "customCss": None,
    }


def get_keycloak_realm_config(app_json: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Generate a Keycloak realm import-compatible config from app.json.

    Reads the ``auth`` and ``dev`` sections to produce a complete realm
    definition including client, roles, and dev users.

    Can be used by setup scripts to bootstrap the Keycloak realm
    with the correct client, roles, users, and branding.

    Returns:
        Dict suitable for Keycloak realm import JSON.
    """
    if app_json is None:
        app_json = load_app_json()

    auth = app_json.get("auth", {})
    dev = app_json.get("dev", {})
    kc = auth.get("keycloak", {})
    roles_cfg = auth.get("roles", {})
    session_cfg = auth.get("session", {})
    social = auth.get("socialProviders", [])

    realm = kc.get("realm", "mozaiks")
    client_id = kc.get("clientId", "mozaiks-app")

    # Dev users from app.json → Keycloak user seed
    dev_users = dev.get("users", [])
    keycloak_users = []
    for u in dev_users:
        keycloak_users.append({
            "username": u["username"],
            "email": u.get("email", f"{u['username']}@mozaiks.local"),
            "enabled": True,
            "emailVerified": True,
            "firstName": u.get("firstName", u["username"].title()),
            "lastName": u.get("lastName", "User"),
            "credentials": [
                {
                    "type": "password",
                    "value": u["password"],
                    "temporary": False,
                }
            ],
            "realmRoles": u.get("roles", ["user"]),
        })

    # Build realm config
    realm_config = {
        "realm": realm,
        "enabled": True,
        "registrationAllowed": True,
        "resetPasswordAllowed": True,
        "rememberMe": True,
        "loginWithEmailAllowed": True,
        "duplicateEmailsAllowed": False,

        # Session lifespans (converted to seconds)
        "accessTokenLifespan": session_cfg.get("accessTokenLifespanMinutes", 5) * 60,
        "ssoSessionIdleTimeout": session_cfg.get("ssoSessionIdleMinutes", 30) * 60,
        "ssoSessionMaxLifespan": session_cfg.get("ssoSessionMaxMinutes", 600) * 60,

        # Default roles
        "defaultRoles": [roles_cfg.get("default", "user")],

        # Roles
        "roles": {
            "realm": [
                {"name": "user", "description": "Default authenticated user"},
                {"name": roles_cfg.get("admin", "admin"), "description": "Application administrator"},
            ]
        },

        # Client (public SPA client with PKCE)
        "clients": [
            {
                "clientId": client_id,
                "name": "Mozaiks Application",
                "enabled": True,
                "publicClient": True,
                "standardFlowEnabled": True,
                # Direct access grants needed for dev auto-login
                "directAccessGrantsEnabled": bool(dev.get("autoLogin")),
                "implicitFlowEnabled": False,
                "serviceAccountsEnabled": False,
                "protocol": "openid-connect",
                "attributes": {
                    "pkce.code.challenge.method": "S256" if kc.get("pkce", True) else "",
                    "post.logout.redirect.uris": "http://localhost:5173/*",
                },
                "redirectUris": [
                    "http://localhost:5173/*",
                    "http://localhost:3000/*",
                    "http://localhost:8000/*",
                ],
                "webOrigins": [
                    "http://localhost:5173",
                    "http://localhost:3000",
                    "http://localhost:8000",
                ],
                "defaultClientScopes": kc.get("scopes", ["openid", "profile", "email", "roles"]),
                "protocolMappers": [
                    {
                        "name": "realm-roles",
                        "protocol": "openid-connect",
                        "protocolMapper": "oidc-usermodel-realm-role-mapper",
                        "consentRequired": False,
                        "config": {
                            "multivalued": "true",
                            "id.token.claim": "true",
                            "access.token.claim": "true",
                            "claim.name": "realm_access.roles",
                            "jsonType.label": "String",
                            "userinfo.token.claim": "true",
                        },
                    },
                    {
                        "name": f"{client_id}-audience",
                        "protocol": "openid-connect",
                        "protocolMapper": "oidc-audience-mapper",
                        "consentRequired": False,
                        "config": {
                            "included.client.audience": client_id,
                            "access.token.claim": "true",
                            "id.token.claim": "false",
                        },
                    },
                ],
            }
        ],

        # Dev users
        "users": keycloak_users,

        # Identity providers (social login)
        "identityProviders": [
            {
                "alias": sp.get("provider", sp.get("alias", "")),
                "providerId": sp.get("provider", ""),
                "enabled": True,
                "config": {
                    k: v for k, v in sp.items()
                    if k not in ("provider", "alias")
                },
            }
            for sp in social
            if sp.get("provider")
        ],
    }

    return realm_config
