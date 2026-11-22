# Profile Panel And Tab Contract

The profile contract lets modules declare account-scoped panels and profile
tabs they contribute to the user profile page. Profile is the signed-in
person's surface: identity, personal preferences, personal relationship
inventory, safe personal summaries, and social sections such as friends, posts,
activity, and messages. It is not an app/workspace management surface and it is
not the org/workspace home.

Billing, subscriptions, entitlements, app access, collaborators, deployments,
governance, build runs, and revenue participation belong in Admin Portal or
Studio / Workspace Shell. Do not use profile panels or tabs to continue app
builds or manage app/workspace operations.

The shared profile UI renders the host's authenticated profile and declared
sections. A failed profile or section request displays a retryable error; it
must never substitute a demo identity or role. Changing the signed-in account
or viewed username clears the previous identity, sections, and edit draft.
Display name and bio edits persist through `PUT /api/me`. Images are displayed
from server-provided profile URLs; the shared page does not offer local-only
uploads that appear to be saved to the account.

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

tabs:
  - id: friends
    label: Friends
    order: 10
    action: list_friends_of
    component: FriendListTab
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

### Profile Tabs

Use `tabs[]` for first-class profile sections that users expect to live on the
person profile rather than in shell navigation. This is the default for friends,
direct messages, user posts, and activity feed.

```yaml
schema_version: mozaiks.profile.v1

tabs:
  - id: messages
    label: Messages
    order: 40
    action: list_threads
    component: MessagingProfileTab
```

The tab component receives `{ tab, data }` props. `data` is the result of
calling `action` if one is declared; otherwise it is `null`.

Actions that render the viewed profile subject should declare an optional
`user_id` input in `module.yaml`. The platform passes the target profile user id
only to actions whose input schema declares `user_id`. Viewer-scoped surfaces,
such as a signed-in user's message inbox, should omit `user_id` and read
`ctx.user_id`.

---

## Runtime Discovery

### `GET /api/me/profile-panels`

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

### `GET /api/me/profile-tabs`

The platform discovers `tabs[]` from the same `contracts/profile.yaml` files.
For `/me`, actions hydrate with the signed-in user as the profile subject. For a
public profile route such as `/u/:username`, the shell calls
`/api/me/profile-tabs?username=<username>` so profile-subject actions can render
that user's public social sections while viewer-scoped actions continue to use
the signed-in viewer.

Response shape:

```json
{
  "tabs": [
    {
      "id": "friends",
      "label": "Friends",
      "order": 10,
      "module_id": "friends",
      "action": "list_friends_of",
      "component": "FriendListTab",
      "data": { "friends": [] },
      "error": null
    }
  ]
}
```

---

## Built-in Sections

The framework-owned identity and preferences sections are rendered directly by
`ProfilePage.jsx` and always appear regardless of module panels or tabs. They are not
declared in `profile.yaml` — they are platform guarantees.

| Section | Order | Editable |
|---------|-------|---------|
| Identity (avatar, display_name, bio, email, roles) | 0 | display_name, bio |
| *Account-scoped module panels and tabs inject here (order 1-998)* | — | — |
| App Preferences | 999 | settings dict |

---

## Where Code Lives

| Concern | File |
|---------|------|
| Contract models | `mozaiksai/core/runtime/app/module_loader.py` — `ModuleProfilePanel`, `ModuleProfileTab`, `ModuleProfileManifest` |
| Discovery | `mozaiksai/core/profile/discovery.py` — `load_profile_panels(app_root)`, `load_profile_tabs(app_root)` |
| API endpoint | `mozaiksai/hosts/platform.py` — `GET /api/me/profile-panels`, `GET /api/me/profile-tabs`, `GET /api/users/{username}` |
| UI renderer | `chat-ui/src/pages/ProfilePage.jsx` — profile tab renderer |

---

## Allowed Panel Scope

Use `contracts/profile.yaml` only when the panel answers a question about the
signed-in person.

Allowed examples:

- personal notification preferences
- personal invitations
- personal community memberships
- friends, direct messages, user posts, and activity feed as profile tabs
- personal votes or delegations
- personal usage summaries that do not manage an app/workspace

Forbidden examples:

- app build history or continue-build controls
- app access, roles, collaborators, or team settings
- billing plans, subscriptions, entitlement assignment, or revenue
  participation
- deployments, domains, hosting, health, incidents, or audit logs
- app/workspace integrations or provider configuration

Those belong in Admin Portal or Studio / Workspace Shell.

Do not add profile-owned social surfaces to `config/shell.json` shortcuts or
global navigation by default. Generate a separate `/feed`, `/friends`,
`/posts`, or global `/messages` route only when the product explicitly needs an
app-wide discovery or conversation surface outside the profile.

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


## Profile images and owner access

The shared profile page offers Change photo and Change banner on your own profile.
`PUT /api/me/media/{kind}` accepts a JPEG, PNG, or WebP request body, with
`kind` equal to `avatar` or `cover`. The platform bounds input at 5 MB and
16 million pixels, decodes it, removes metadata, resizes it, and stores a JPEG
of at most 2 MB through the existing media content store. The app-scoped
UserProfiles record atomically receives its content reference and display URL
after storage completes. The configured local, GridFS, or external media backend
owns the bytes. Replaced content remains available for operator retention cleanup.

Authenticated profile projections contain `avatar_url` and `cover_url`.
`GET /api/users/{user_id}/media/{kind}/{digest}` resolves the exact image within
the authenticated app scope. Reads require authentication and send private,
no-store cache headers. The shared profile UI fetches images using its auth
adapter and revokes temporary object URLs when the image or account changes.
Image bytes are excluded from profile JSON responses.

Factory's app workbench displays the admin email allowlist from the generated
app manifest. The configured identity provider owns registration, password setup,
and recovery. A verified identity matching that allowlist gains app admin access.
The account menu uses effective backend roles. Operators must configure that
provider and deliver its account setup or recovery flow before customer launch.

The Social pack stores one authoritative request/connection record for each
unordered pair of users. Acceptance, decline, withdrawal, and removal use atomic
conditional state transitions. Both users' friendship views derive from accepted
records. Request IDs change on a new invitation, fencing delayed responses from
an earlier invitation. Older Social workspaces require a fresh development
lineage or an explicit reviewed migration before adopting this pre-production
storage contract.


The Social Friends tab accepts a username, resolves the existing public profile,
and sends a request to that authenticated app's user identity. Owners can accept,
decline, and remove connections. The Messaging tab links to the pack's conversation
creation page and retains unsent text when the service refuses a message.

`tests/test_social_messaging_journey.py` materializes the packs, applies their data
migration, and runs signed API and Playwright journeys against an owned ephemeral
Mongo container. It requires local Docker with the test's pinned Mongo image,
Node, and installed web_shell dependencies. The fixture stops its own container
and HTTP server and removes its temporary synthetic bearer-token file. Other
accounts, databases, and services are outside that fixture's scope.


Acceptance stores its event identity and acceptance timestamp in the same atomic
transition. Retrying the acceptance replays that identity and payload through
`ModuleContext.emit`, including after a fresh service/router starts. Both first
acceptance and retry return the accepting user's friendship view. Recovery after
a stopped process leaves a persisted confirmation-pending flag. The accepting
user can reopen their Friends tab and select Send confirmation to retry the
existing acceptance action. Load more friends makes older confirmations reachable
beyond the first page. Friend cursors include the timestamp and request identity
so equal timestamps retain every connection. Successful dispatch clears that flag. The acceptance notification
uses the existing declared notification reaction and durable idempotency ledger.


## Profile messaging and support

The Messaging pack registers one workspace at `/me?tab=messages`. Conversation
creation resolves usernames through authenticated public-profile lookup and uses
the existing messages module. It emits no standalone Messages route.

The Support pack registers an owner-only `support-tickets` profile page and a
`support-inbox` Admin panel. Requests start in the shared widget. The shell
projects the installed support module so the widget submits to that app's service.
The app Admin registry must declare the `support` page referenced by the panel.
App authorization grants `support.submit` to members, owners, and support teams.
Owners and support teams also receive `support.read` and `support.manage`.
The backend enforces ticket access within the app-bound support collection.
The inbox includes older app-owned tickets whose subject annotation is `default`.

A profile hydration action can return `profile_visible: false` to hide an empty
page contribution. Missing values keep the page visible. Hydration errors also
keep the page visible so users retain an error/retry path. Support uses this
projection only until the requester has submitted a ticket, including resolved
or archived tickets.

Existing workspaces adopting these pack versions must remove old `/messages`
and `/support` routes and register the updated profile components. The existing
conversation and ticket collections continue through the same module actions.
