"""Generate Keycloak realm-export.json from app.json.

Reads ``app/app.json`` and produces ``infra/keycloak/realm-export.json``
so the Keycloak realm stays in sync with the declarative app config.

Keycloak only imports realm-export.json on **first startup** (when the
realm doesn't exist yet).  To apply changes to a running Keycloak, delete
the database volume or use the Admin REST API.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from mozaiksai.cli.paths import find_project_root, load_json


# ── Public API ───────────────────────────────────────────────────────────────

def generate_realm_dict(app: dict) -> dict:
    """
    Build a Keycloak realm import dict from an app.json config.

    This is a pure function: dict in → dict out.
    Identical logic exists in ``auth_config_loader.get_keycloak_realm_config()``
    for runtime use.
    """
    auth = app.get("auth", {})
    dev = app.get("dev", {})
    kc = auth.get("keycloak", {})

    realm = kc.get("realm", "mozaiks")
    client_id = kc.get("clientId", "mozaiks-app")
    theme_name = kc.get("themeName", "mozaiks")

    # ── Dev users ────────────────────────────────────────────────────────
    users = []
    for u in dev.get("users", []):
        users.append({
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

    # ── Realm ────────────────────────────────────────────────────────────
    return {
        "realm": realm,
        "enabled": True,
        "registrationAllowed": True,
        "resetPasswordAllowed": True,
        "rememberMe": True,
        "loginWithEmailAllowed": True,
        "duplicateEmailsAllowed": False,
        "sslRequired": "none",
        "loginTheme": theme_name,
        "accessTokenLifespan": 300,
        "ssoSessionIdleTimeout": 1800,
        "ssoSessionMaxLifespan": 36000,
        "offlineSessionIdleTimeout": 2592000,

        "roles": {
            "realm": [
                {"name": "user", "description": "Default user role", "composite": False},
                {"name": "admin", "description": "Administrator role", "composite": False},
            ]
        },
        "defaultRoles": ["user"],

        "clients": [
            {
                "clientId": client_id,
                "name": "Mozaiks Application",
                "enabled": True,
                "publicClient": True,
                "standardFlowEnabled": True,
                "directAccessGrantsEnabled": bool(dev.get("autoLogin")),
                "implicitFlowEnabled": False,
                "serviceAccountsEnabled": False,
                "protocol": "openid-connect",
                "attributes": {
                    "pkce.code.challenge.method": "S256",
                    "post.logout.redirect.uris": (
                        "http://localhost:5173/*"
                        "##http://localhost:3000/*"
                        "##http://localhost:8000/*"
                    ),
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
                "defaultClientScopes": ["openid", "profile", "email", "roles"],
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

        "users": users,
    }


def run(*, root: Path | None = None, dry_run: bool = False) -> int:
    """
    Generate realm-export.json.

    Returns 0 on success, 1 on failure.
    """
    root = root or find_project_root()
    app_json = root / "app" / "app.json"
    realm_out = root / "infra" / "keycloak" / "realm-export.json"

    app = load_json(app_json, "app.json")
    realm = generate_realm_dict(app)
    output = json.dumps(realm, indent=2, ensure_ascii=False) + "\n"

    if dry_run:
        print(output)
        print(f"(dry run — would write to {realm_out})", file=sys.stderr)
        return 0

    realm_out.parent.mkdir(parents=True, exist_ok=True)
    realm_out.write_text(output, encoding="utf-8")

    print(f"OK: Generated {realm_out.relative_to(root)}")
    print(f"  Realm: {realm['realm']}")
    print(f"  Client: {realm['clients'][0]['clientId']}")
    print(f"  Theme: {realm.get('loginTheme', 'mozaiks')}")
    print(f"  Users: {[u['username'] for u in realm['users']]}")
    print(f"  Direct access grants: {realm['clients'][0]['directAccessGrantsEnabled']}")
    return 0
