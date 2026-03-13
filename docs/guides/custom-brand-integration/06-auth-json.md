# Step 6 - Auth in app.json

> Guide: Customizing Your Frontend · Step 6

Mozaiks no longer uses a separate `brand/public/auth.json`.

Auth configuration now lives in `platform/app.json` under `auth`.

---

## Why this changed

Keeping auth in `platform/app.json` removes split-brain config:

1. Frontend auth adapter reads the same file.
2. Backend auth loader derives defaults from the same file.
3. Keycloak realm generation also uses the same source.

App-level chat and workflow boot defaults still remain separate in
`platform/config/ai.json`.

---

## Minimal auth config

```json
{
  "appName": "My App",
  "appId": "my-app",
  "apiUrl": "http://localhost:8000",
  "wsUrl": "ws://localhost:8000",
  "auth": {
    "provider": "keycloak",
    "keycloak": {
      "authority": "http://localhost:8080",
      "realm": "mozaiks",
      "clientId": "mozaiks-app",
      "themeName": "mozaiks"
    }
  }
}
```

For mobile, use the same manifest under `platforms.mobile.auth`:

```json
{
  "platforms": {
    "mobile": {
      "auth": {
        "provider": "keycloak-native",
        "redirectScheme": "myapp",
        "redirectPath": "oauthredirect",
        "scopes": ["openid", "profile", "email"]
      }
    }
  }
}
```

Simple rule:

- `Keycloak` is the product you run
- `OIDC` is the login protocol it speaks
- if your mobile app logs into Keycloak natively, choose `keycloak-native`
- if you want the app to open in Ask mode or default to `GreenRoom`, set that in `platform/config/ai.json`, not in `platform/app.json`

---

## Field reference

| Key | What it controls |
|---|---|
| `auth.provider` | Auth provider name (`keycloak` for OSS default) |
| `auth.keycloak.authority` | Keycloak base URL (no `/realms/...` suffix) |
| `auth.keycloak.realm` | Realm name |
| `auth.keycloak.clientId` | OIDC client ID (`AUTH_AUDIENCE` default) |
| `auth.keycloak.themeName` | Keycloak login theme folder name under `infra/keycloak/themes/` |
| `platforms.mobile.auth.provider` | Mobile auth mode: `token`, `external`, `oidc`, or `keycloak-native` |
| `platforms.mobile.auth.redirectScheme` | Native callback URL scheme synced into iOS/Android |
| `platforms.mobile.auth.redirectPath` | Callback path segment appended to the native redirect URI |
| `platforms.mobile.auth.scopes` | OIDC scopes requested by the built-in native login flow |

---

## After changing auth config

Regenerate derived artifacts:

```powershell
python -m mozaiksai.cli generate
python -m mozaiksai.cli doctor
npm --prefix clients/mobile run native:prepare
```

`generate` syncs:

1. `infra/keycloak/realm-export.json`
2. Keycloak login theme CSS under `infra/keycloak/themes/<themeName>/...`

---

## Runtime overrides

Environment variables still override `platform/app.json` when set, for deployment:

1. `MOZAIKS_OIDC_AUTHORITY`
2. `AUTH_AUDIENCE`
3. `AUTH_REQUIRED_SCOPE`
4. `AUTH_ROLES_CLAIM`

---

## Temporary auth bypass

For local emergency fallback only:

```dotenv
AUTH_ENABLED=false
```

Use this only when Keycloak is unavailable. Keep `AUTH_ENABLED=true` for production-parity testing.


