# Profile Panel Contract

The profile panel contract lets modules declare account-scoped sections they
contribute to the user profile page. Profile is the signed-in person's surface:
identity, personal preferences, personal relationship inventory, and safe
personal summaries. It is not an app/workspace management surface.

Billing, subscriptions, entitlements, app access, collaborators, deployments,
governance, build runs, and revenue participation belong in Admin Portal or
Studio. Do not use profile panels to continue app builds or manage app/workspace
operations.

## Design Goals

| Goal | How it is met |
|------|--------------|
| Not cookie-cutter across apps | Apps with user-owned module data can expose personal summaries automatically |
| App-agnostic runtime | `ProfilePage` never imports app-specific modules — it renders what the API returns |
| Deterministic | Panels are declared contracts, not arbitrary React |
| Scalable | Simple apps: identity only. Complex apps: account-scoped module panels only |
| Consistent | Same discovery pattern as `contracts/admin.yaml` |

---

## Contract File — `contracts/profile.yaml`

Any module can create `contracts/profile.yaml` inside its module directory:

```
app/modules/{module_id}/
└── contracts/
    └── profile.yaml
```

### Schema

```yaml
schema_version: mozaiks.profile.v1

panels:
  - id: notification-summary    # unique id within this module
    title: Notifications        # section heading shown in the UI
    description: Personal delivery preferences and unread counts.  # optional subtitle
    order: 20                   # sort position (identity=0, preferences=999)
    kind: metrics               # metrics | list | form | component
    action: get_notification_profile_summary  # module action name to hydrate the panel
    fields:                     # used by metrics and list kinds
      - id: unread_count
        label: Unread
        type: number            # string | number | currency | date | boolean | status
      - id: digest_enabled
        label: Digest
        type: boolean
```

### `kind` values

| Kind | Behaviour |
|------|-----------|
| `metrics` | Renders a grid of labelled metric tiles from `fields` |
| `list` | Renders a key/value list from `fields` |
| `form` | **Reserved — not yet implemented.** Do not emit `kind: form` in profile.yaml. The validator rejects it at load time. |
| `component` | Renders an app-registered React component by `component` name |

### `type` values for `fields`

| Type | Rendered as |
|------|-------------|
| `string` | Plain text |
| `number` | Locale-formatted number |
| `currency` | `$0.00` formatted |
| `date` | `toLocaleDateString()` |
| `boolean` | `Yes` / `No` |
| `status` | `StatusPill` with tone derived from value |

### Component panels

For sections that can't be expressed as metrics/list, set `kind: component` and
declare the registered
component name:

```yaml
  - id: invitations
    title: Invitations
    kind: component
    component: InvitationsProfilePanel   # registered via registerComponent()
    order: 30
```

The component receives `{ panel, data }` props. `data` is the result of calling
`action` if one is declared; otherwise it is `null`.

---

## Runtime Discovery — `GET /api/me/profile-panels`

The platform walks `app_root/modules/*/contracts/profile.yaml` at request time
(same pattern as admin panel discovery in `mozaiksai/core/admin/router.py`).

For each panel that declares an `action`, the platform calls the module executor
and attaches the result as `data` on the panel. Panels without an `action` are
returned with `data: null`.

Response shape:

```json
{
      "panels": [
    {
      "id": "notification-summary",
      "title": "Notifications",
      "description": "Personal delivery preferences and unread counts.",
      "order": 20,
      "kind": "metrics",
      "module_id": "notifications",
      "fields": [
        { "id": "unread_count", "label": "Unread", "type": "number" },
        { "id": "digest_enabled", "label": "Digest", "type": "boolean" }
      ],
      "data": { "unread_count": 3, "digest_enabled": true },
      "error": null
    }
  ]
}
```

If the action call fails, `data` is `null` and `error` contains the error
message. The panel is still included so the UI can render a graceful empty
state rather than silently hiding it.

---

## Built-in Sections

The framework-owned identity and preferences sections are rendered directly by
`ProfilePage.jsx` and always appear regardless of module panels. They are not
declared in `profile.yaml` — they are platform guarantees.

| Section | Order | Editable |
|---------|-------|---------|
| Identity (avatar, display_name, email, roles) | 0 | display_name, avatar_url |
| *Account-scoped module panels inject here (order 1-998)* | — | — |
| App Preferences | 999 | settings dict |

---

## Where Code Lives

| Concern | File |
|---------|------|
| Contract models | `mozaiksai/core/runtime/app/module_loader.py` — `ModuleProfilePanel`, `ModuleProfileManifest` |
| Discovery | `mozaiksai/core/profile/discovery.py` — `load_profile_panels(app_root)` |
| API endpoint | `mozaiksai/hosts/platform.py` — `GET /api/me/profile-panels` |
| UI renderer | `chat-ui/src/pages/ProfilePage.jsx` — `ProfilePanelSection` |

---

## Allowed Panel Scope

Use `contracts/profile.yaml` only when the panel answers a question about the
signed-in person.

Allowed examples:

- personal notification preferences
- personal invitations
- personal community memberships
- personal votes or delegations
- personal usage summaries that do not manage an app/workspace

Forbidden examples:

- app build history or continue-build controls
- app access, roles, collaborators, or team settings
- billing plans, subscriptions, entitlement assignment, or revenue
  participation
- deployments, domains, hosting, health, incidents, or audit logs
- app/workspace integrations or provider configuration

Those belong in Admin Portal or Studio.

---

## Example — notifications module

```yaml
# app/modules/notifications/contracts/profile.yaml
schema_version: mozaiks.profile.v1

panels:
  - id: notification-summary
    title: Notifications
    description: Personal notification state.
    order: 20
    kind: metrics
    action: get_notification_profile_summary
    fields:
      - { id: unread_count, label: Unread, type: number }
      - { id: digest_enabled, label: Digest, type: boolean }
      - { id: last_delivery_at, label: Last Delivery, type: date }
```

The `notifications` module's handler must implement a
`get_notification_profile_summary` action that returns a dict matching those
field ids. No changes anywhere else are needed — the profile page picks it up
automatically on next load.
