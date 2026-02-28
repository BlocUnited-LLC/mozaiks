"""
Auth config file loader — reads auth.json from brand/public/ for Keycloak config.

This module provides a bridge between the declarative auth.json (served to the
frontend by Vite) and the runtime AuthConfig used by the Python backend.

Priority order:
    1. Environment variables (always win — deployment overrides)
    2. auth.json file values (app-level defaults)
    3. Built-in Keycloak defaults (fallback)

Usage:
    from mozaiksai.core.auth.auth_config_loader import load_auth_json, derive_auth_env

    # Load the raw JSON
    auth_json = load_auth_json()

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

# Default search paths for auth.json (relative to project root)
_AUTH_JSON_SEARCH_PATHS = [
    "app/brand/public/auth.json",
    "brand/public/auth.json",
]


def _find_auth_json() -> Optional[Path]:
    """
    Locate auth.json by searching known paths relative to project root.

    Returns:
        Path to auth.json if found, None otherwise.
    """
    # Allow explicit override via env var
    explicit = os.getenv("MOZAIKS_AUTH_JSON_PATH")
    if explicit:
        p = Path(explicit)
        if p.is_file():
            return p
        logger.warning(f"MOZAIKS_AUTH_JSON_PATH set to '{explicit}' but file not found")
        return None

    # Search from project root (two levels up from this file:
    # mozaiksai/core/auth/auth_config_loader.py → project root)
    project_root = Path(__file__).resolve().parent.parent.parent.parent

    for rel_path in _AUTH_JSON_SEARCH_PATHS:
        candidate = project_root / rel_path
        if candidate.is_file():
            logger.debug(f"Found auth.json at {candidate}")
            return candidate

    return None


@lru_cache(maxsize=1)
def load_auth_json() -> Dict[str, Any]:
    """
    Load and parse auth.json. Returns empty dict if not found.

    Cached after first load (call clear_auth_json_cache() to invalidate).
    """
    path = _find_auth_json()
    if path is None:
        logger.info("No auth.json found — using env vars / built-in Keycloak defaults")
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        logger.info(f"Loaded auth.json from {path}")
        return data
    except (json.JSONDecodeError, OSError) as exc:
        logger.error(f"Failed to load auth.json from {path}: {exc}")
        return {}


def clear_auth_json_cache() -> None:
    """Clear cached auth.json (useful for testing or hot-reload)."""
    load_auth_json.cache_clear()


def derive_auth_env(auth_json: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Derive OIDC / auth config values from auth.json.

    Converts the declarative auth.json structure into the flat key-value
    pairs that AuthConfig expects. Environment variables always take
    precedence — this only fills in what's missing.

    Returns:
        Dict with keys: authority, realm, client_id, audience,
        discovery_url, roles_claim, user_id_claim, email_claim
    """
    if auth_json is None:
        auth_json = load_auth_json()

    provider = auth_json.get("provider", "keycloak")
    kc = auth_json.get("keycloak", {})
    roles_cfg = auth_json.get("roles", {})

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


def get_keycloak_branding(auth_json: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Extract Keycloak login page branding config from auth.json.

    These values reference files in brand/public/assets/ and are used to:
    1. Generate Keycloak realm import JSON (for initial setup)
    2. Theme the Keycloak login pages via the Admin API

    Returns:
        Dict with branding keys: logo, favicon, backgroundImage, theme, etc.
    """
    if auth_json is None:
        auth_json = load_auth_json()

    branding = auth_json.get("branding", {})
    return {
        "loginTitle": branding.get("loginTitle", "Sign In"),
        "registerTitle": branding.get("registerTitle", "Create Account"),
        "logo": branding.get("logo", "mozaik_logo.svg"),
        "favicon": branding.get("favicon", "mozaik.png"),
        "backgroundImage": branding.get("backgroundImage"),
        "theme": branding.get("theme", "dark"),
        "customCss": branding.get("customCss"),
    }


def get_keycloak_realm_config(auth_json: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Generate a Keycloak realm import-compatible config from auth.json.

    Can be used by setup scripts to bootstrap the Keycloak realm
    with the correct client, roles, and branding.

    Returns:
        Dict suitable for Keycloak realm import JSON.
    """
    if auth_json is None:
        auth_json = load_auth_json()

    kc = auth_json.get("keycloak", {})
    features = auth_json.get("features", {})
    roles_cfg = auth_json.get("roles", {})
    session_cfg = auth_json.get("session", {})
    social = auth_json.get("socialProviders", [])

    realm = kc.get("realm", "mozaiks")
    client_id = kc.get("clientId", "mozaiks-app")

    # Build realm config
    realm_config = {
        "realm": realm,
        "enabled": True,
        "registrationAllowed": features.get("registration", True),
        "resetPasswordAllowed": features.get("passwordReset", True),
        "rememberMe": features.get("rememberMe", True),
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
                "name": client_id,
                "enabled": True,
                "publicClient": True,
                "standardFlowEnabled": True,
                "directAccessGrantsEnabled": False,
                "protocol": "openid-connect",
                "attributes": {
                    "pkce.code.challenge.method": "S256" if kc.get("pkce", True) else "",
                },
                "redirectUris": ["*"],
                "webOrigins": ["*"],
                "defaultClientScopes": kc.get("scopes", ["openid", "profile", "email"]),
            }
        ],

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
