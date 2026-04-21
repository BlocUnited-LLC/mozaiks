# Admin System

The Mozaiks admin system has two tiers that serve different purposes. Understanding the boundary between them is essential for generator agents producing correct output.

---

## Two-Tier Model

| | Platform Admin | App Admin |
|---|---|---|
| **What** | Runtime observability dashboard | App-level operator controls |
| **Who uses it** | The person who built/deployed the app | The same person, for app-specific settings |
| **Where** | `/admin` route — framework-provided | `/__mozaiks/admin/*` REST contract — generated |
| **Who builds it** | Framework (always present) | AppGenerator (when `admin_pack` selected) |
| **Config file** | `platform/config/admin.json` | `server/admin_surfaces.json` |
| **Auth** | Email allowlist + JWT role promotion | `X-Mozaiks-App-Admin-Key` server-to-server |

---

## Tier 1: Platform Admin Portal

The `/admin` portal is a **first-class framework component**, registered in `chat-ui/src/registry/coreComponents.js` as `AdminPortal` alongside `ChatPage`. It is available in every app automatically — no generation required.

### What it shows out of the box

- **System Stats** — active chats, total runs, agent turns, token usage, estimated cost
- **Active Runs** — live in-memory workflow snapshots (auto-refreshes every 10s)
- **Recent Sessions** — persisted chat sessions from MongoDB

### Configuration

The only file the generator writes for this tier is `platform/config/admin.json`:

```json
{
  "enabled": true,
  "admin_emails": ["builder@example.com"],
  "panels": ["stats", "runs", "sessions"],
  "roles": ["admin"],
  "features": {
    "user_management": false,
    "billing": false,
    "audit_log": false
  }
}
```

When this file exists and `enabled` is true, the shell-config endpoint automatically injects the `/admin` route into navigation. No `platform/extensions.js` wiring, no page schema, no React.

### How admin access works

Access follows three escalating checks in priority order:

1. **JWT role** — auth provider (Keycloak, Supabase) issued `"admin"` in the token
2. **Email allowlist** — user's email matches `admin_emails` in `admin.json`; role is promoted at request time
3. **Dev mode** — `AUTH_ENABLED=false` + `AUTH_ANON_ROLES=admin,user` in `.env`

The app builder sets up access by putting their email in `admin_emails` during `mozaiks init`. They log in with their normal email/password and see the Admin Portal link in the profile menu.

### Generator rules for Tier 1

- **DO** write `platform/config/admin.json` with the builder's email and desired panels
- **DO NOT** create an admin page schema (`AdminPage.yaml` or similar)
- **DO NOT** add `/admin` to `manifest.navigation` — the framework adds it
- **DO NOT** generate admin React components — `AdminPage.jsx` is framework-owned

---

## Tier 2: App Admin Surfaces

Generated apps can expose operator-configurable modules through a standardized REST contract. The Mozaiks platform calls these endpoints server-to-server to read/write app-level settings.

### Contract location

```
server/admin_surfaces.json        ← module manifest
server/mozaiks_admin_server.cjs   ← stub Node.js server (reference implementation)
server/mozaiks_admin/             ← module registry + MongoDB persistence helpers
```

### Endpoint contract

```
Base path: /__mozaiks/admin/
Auth header: X-Mozaiks-App-Admin-Key: <secret from MOZAIKS_APP_ADMIN_KEY env>

GET  /__mozaiks/admin/modules
GET  /__mozaiks/admin/modules/{moduleId}/settings
PUT  /__mozaiks/admin/modules/{moduleId}/settings
GET  /__mozaiks/admin/modules/{moduleId}/status
```

Settings are persisted to MongoDB collection `module_settings`.

### Module manifest shape

```json
{
  "version": "1",
  "modules": [
    {
      "moduleId": "agents.monitoring",
      "displayName": "Agent Monitoring",
      "category": "agents",
      "admin": { "enabled": true, "scope": "app" },
      "settingsSchema": {
        "type": "object",
        "properties": {
          "enabled": { "type": "boolean", "default": true },
          "pollIntervalSeconds": { "type": "integer", "minimum": 10, "default": 60 }
        }
      },
      "actions": [
        { "actionId": "runNow", "label": "Run now", "danger": false },
        { "actionId": "disable", "label": "Disable", "danger": true }
      ]
    }
  ]
}
```

### Generator rules for Tier 2

- **DO** generate `admin_surfaces.json` when the app has operator-configurable modules
- **DO** include a `task_type: admin_config` build task
- **DO NOT** implement the full admin server — generate the stub only; the reference implementation is provided
- **DO NOT** expose `MOZAIKS_APP_ADMIN_KEY` to the frontend

---

## What the Generator Produces for `admin_pack`

When `admin_pack` is in `capability_packs`, the AppGenerator produces exactly:

```
platform/config/admin.json      ← Tier 1 config (email, panels, features)
server/admin_surfaces.json      ← Tier 2 module manifest (if app has configurable modules)
```

Nothing else. No admin React pages. No admin routes in page/UI/workflow owner manifests. No custom admin login.

---

## Build Task Contract

```yaml
# Example build task in AppBuildPlan
- task_id: task_admin_config
  task_type: admin_config
  capability_pack_id: admin_pack
  execution_target: AppGenerator
  initial_agent: ConfigMiddlewareAgent
  description: "Generate admin.json config and admin_surfaces.json module manifest."
  initial_message: "Generate platform/config/admin.json with the builder's email and admin_surfaces.json with the app's configurable modules."
  owned_paths:
    - platform/config/admin.json
    - server/admin_surfaces.json
  depends_on: []
  acceptance_criteria:
    - admin_emails contains at least one email address
    - admin_surfaces.json is valid JSON with a modules array
```

---

## Summary

```
Generator writes:           Framework provides:
  admin.json             →    /admin portal (AdminPortal component)
  admin_surfaces.json    →    Calls /__mozaiks/admin/* to display settings
```

The generator never builds a UI. The framework never knows about app-specific module settings. The two tiers communicate through config files and a standardized REST contract.
