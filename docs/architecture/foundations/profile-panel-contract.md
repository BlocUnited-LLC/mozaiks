# Profile Panel Contract

The profile panel contract lets modules declare sections they contribute to
the user profile page. A module with wallet balance data, a subscription tier
badge, or a notification preference section can inject that content into every
user's profile — with zero changes to the framework or `ProfilePage.jsx`.

## Design Goals

| Goal | How it is met |
|------|--------------|
| Not cookie-cutter across apps | Apps with more modules get richer profiles automatically |
| App-agnostic runtime | `ProfilePage` never imports wallet or billing — it renders what the API returns |
| Deterministic | Panels are declared contracts, not arbitrary React |
| Scalable | Simple apps: identity only. Complex apps: whatever modules declare |
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
  - id: wallet-balance          # unique id within this module
    title: Wallet               # section heading shown in the UI
    description: Your current balance and pending payouts.  # optional subtitle
    order: 20                   # sort position (identity=0, preferences=999)
    kind: metrics               # metrics | list | form | component
    action: get_wallet_summary  # module action name to hydrate the panel
    fields:                     # used by metrics and list kinds
      - id: balance
        label: Balance
        type: currency          # string | number | currency | date | boolean | status
      - id: pending
        label: Pending
        type: currency
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

For sections that can't be expressed as metrics/list (e.g. a transaction
history with charts), set `kind: component` and declare the registered
component name:

```yaml
  - id: billing-detail
    title: Billing
    kind: component
    component: BillingProfilePanel   # registered via registerComponent()
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
      "id": "wallet-balance",
      "title": "Wallet",
      "description": "Your current balance and pending payouts.",
      "order": 20,
      "kind": "metrics",
      "module_id": "wallet",
      "fields": [
        { "id": "balance", "label": "Balance", "type": "currency" },
        { "id": "pending", "label": "Pending", "type": "currency" }
      ],
      "data": { "balance": 42.50, "pending": 5.00 },
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
| *Module panels inject here (order 1–998)* | — | — |
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

## Example — wallet module

```yaml
# app/modules/wallet/contracts/profile.yaml
schema_version: mozaiks.profile.v1

panels:
  - id: wallet-summary
    title: Wallet
    description: Current balance and payout activity.
    order: 20
    kind: metrics
    action: get_wallet_summary
    fields:
      - { id: balance, label: Balance, type: currency }
      - { id: pending_payouts, label: Pending Payouts, type: currency }
      - { id: lifetime_earned, label: Lifetime Earned, type: currency }
```

The `wallet` module's handler must implement a `get_wallet_summary` action that
returns a dict matching those field ids. No changes anywhere else are needed —
the profile page picks it up automatically on next load.

---

## App-Level Profile Config — `config/profile.yaml` (optional)

Apps can suppress or reorder built-in sections:

```yaml
# app/config/profile.yaml
schema_version: mozaiks.profile.app.v1

sections:
  identity:
    enabled: true
    order: 0
  preferences:
    enabled: false   # hide raw JSON prefs for end-user-facing apps
```

This file is optional. When absent, all built-in sections are shown at default
order.

> **Not yet implemented.** The `config/profile.yaml` suppression mechanism is
> reserved for a future iteration. The current implementation always renders
> both built-in sections.
