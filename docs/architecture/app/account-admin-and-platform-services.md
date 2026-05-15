# Account, Admin, and Platform Services

This document defines the deterministic product services that every Mozaiks app
can rely on without authoring a workflow or generating a custom shell surface.

These concerns still matter if AI is turned off. That is the classification
test.

Profile, settings, notifications, subscriptions, and admin are not workflow
products. They are platform and app-backend responsibilities with explicit UI,
API, and module-contract boundaries.

## Canonical Rule

- Treat these concerns as first-class platform services.
- Do not model them as app-specific `capability_packs` in AppGenerator.
- Do not generate replacement `/profile` or `/admin` shells.
- Keep deterministic state, policies, and CRUD behind the app backend and
  module contracts.
- Add AI only as augmentation on top of these services, never as the source of
  truth.

AppGenerator already follows this rule. Its planning prompts explicitly mark
authentication, user profile, settings, notifications, subscriptions, user
management, and the admin portal as built-in platform features rather than
things to scaffold as product-specific packs.

## Three Contract Layers

### 1. Framework-Owned Surfaces

Mozaiks ships the visible shell surfaces for account and admin flows.

- `/profile` is rendered by the core `ProfilePage` component.
- `/admin` and its section routes are rendered by the core `AdminPortal`
  component.
- These surfaces are first-class shell components, not app-authored page
  bundles.

This means app generation should not create replacement profile or admin pages
under `app/ui/pages/` just to make these features exist.

### 2. Host And App Backend APIs

The platform host owns the universal account/admin APIs. A connected app
backend may extend them with app-business admin data.

- `GET/PUT /api/me` owns the current user's core account/profile data.
- `GET/PUT /api/me/preferences` owns the current user's generic preference
  payload.
- `GET /api/admin/config` owns host admin shell state, runtime panels, and
  module admin panel discovery.
- `GET {app_backend_url}/api/admin/config` and related
  `app_backend_url/api/admin/*` endpoints optionally own app-business admin
  panels that are embedded inside the unified `/admin` shell.

The shell can render these surfaces only because deterministic backend
contracts exist. The shell is not the source of truth.

### 3. Module Declaratives And Hooks

Modules can extend these deterministic systems through explicit contracts.

- `modules/{module}/contracts/settings.yaml` declares module-local settings and
  feature flags.
- `modules/{module}/contracts/reactions.yaml` declares event reactions owned by
  the module.
- `modules/{module}/contracts/entitlements.yaml` declares plan, role, or usage
  gates when entitlement runtime support exists.
- `modules/{module}/contracts/notifications.yaml` declares notification intents
  per event.
- `modules/{module}/contracts/admin.yaml` declares feature-owned admin panels
  rendered inside the unified `/admin` shell.
- contract-declared custom admin components are materialized as frontend stubs
  and registered through the active app root's `ui/index.js` extension barrel.

Module backend files implement deterministic behavior behind those manifests.
Use the canonical `handler.py`, `service.py`, `repo.py`, `policy.py`, and
`schemas.py` split, plus explicit helper files when a module needs them.

## Surface Map

| Concern | Primary UX surface | Source of truth | Extension contract | Not owned by |
|---|---|---|---|---|
| Profile | `/profile` via `ProfilePage` | `GET/PUT /api/me` | none today | workflows, generated page bundles |
| Preferences | `/profile` preferences section | `GET/PUT /api/me/preferences` | `modules/{module}/contracts/settings.yaml` when settings runtime support exists | workflow prompts |
| Notifications | shell notification surfaces and backend delivery rules | app backend plus module notification policy | `modules/{module}/contracts/notifications.yaml` | workflows as source of truth |
| Subscriptions and entitlements | profile badges, billing/admin views, and gated capability behavior | app backend entitlement state | billing/subscription modules plus `modules/{module}/contracts/entitlements.yaml` | capability-pack generation |
| Admin | `/admin` route family via `AdminPortal` | framework admin shell plus same-host admin APIs, `app/app.json` `admins`, and optional app-backend admin APIs | `modules/{module}/contracts/admin.yaml` | custom admin page generation |

## Profile

`ProfilePage` is a framework-owned surface registered in the core component
registry.

Current behavior:

- it renders the current user's account view at `/profile`
- it loads and updates profile data from `/api/me`
- it loads app/user preference data from `/api/me/preferences`
- it uses the host API adapter rather than a custom page-local backend contract

Important boundary:

- `/profile` is not a generated page bundle
- `/profile` is not a workflow artifact
- there is no current `profile.yaml` or module-level profile contribution
  contract

If Mozaiks later supports module-contributed profile sections, that should be a
new explicit contract. Do not overload `settings.yaml`, `reactions.yaml`, or
`admin.yaml` as a proxy for profile UI composition.

## Settings

"Settings" is not one thing in Mozaiks. Keep the categories separate.

### Account Preferences

User-scoped preferences belong behind the account API surface, currently exposed
through `/api/me/preferences` and rendered inside `/profile`.

### Module Settings

Feature-specific settings and feature flags belong in
`modules/{module}/contracts/settings.yaml`, with deterministic validation in
canonical backend service/helper code when runtime support exists.

### Shell Configuration

Shell behavior and shell content belong in `app/config/shell.json`.
Examples include header actions, profile menu items, notification text, and
footer links.

Do not collapse these three settings layers into one contract.

## Subscriptions And Notifications

Subscriptions and notifications are platform services with module-level
extension points, not standalone app packs that every product must reinvent.

### Subscriptions And Entitlements

- entitlement state stays deterministic and backend-owned
- user-visible tier/status may appear in profile or admin views
- module contracts use `contracts/entitlements.yaml` to declare feature-level
  gates when entitlement runtime support exists
- billing/subscription behavior belongs in deterministic billing/subscription
  modules and hosted capabilities, not in event reaction contracts

AI may inspect entitlement state or react to resulting events, but it does not
define the canonical subscription model.

### Notifications

- delivery policy and notification intent belong in deterministic contracts
- module contracts use `contracts/notifications.yaml` to declare what should
  happen when named events occur
- optional Python hooks determine audiences, rendering, or delivery details

AI can personalize content on top of a notification system. It should not be
the thing that makes the notification system exist.

## Admin

Mozaiks has one visible admin route family. The framework injects `/admin` and
its section routes through the `AdminPortal` shell surface.

Authority is separated by panel source:

- app-business panels may come from `app_backend_url/api/admin/config` and
  related `app_backend_url/api/admin/*` endpoints
- feature-owned admin panels come from `modules/{module}/contracts/admin.yaml`
- runtime/operator panels come from same-host admin APIs and the framework
  admin shell contract

Access is granted through the normal auth role model plus `app/app.json`
`admins` for bootstrap email allowlisting.

Important boundaries:

- admin is one unified shell surface, not a generated page family
- Studio and Build are separate product routes, not admin sections
- generated apps should produce module admin manifests and `app/app.json`
  `admins`, not a separate admin React shell

For the admin-only deep dive, see [Admin System](admin-system.md).
The optional connected app-backend panel contract remains repo-internal
planning material for now.

## What This Means For Generation

When AppGenerator is planning an app:

- `profile`, `settings`, `notifications`, `subscriptions`, `auth`, and
  `user_management` are built-in platform capabilities, not app-specific packs
- the generated app may wire config and module manifests around these systems
- the generated app should not scaffold replacement `/profile` or `/admin`
  surfaces

This keeps the deterministic product foundation stable while still allowing app-
specific business modules and AI workflows to sit on top.

## Cross References

- [Admin System](admin-system.md)
- [Core, Product, and App Bundle Boundary](../foundations/core-product-app-bundle-boundary.md)
- [UI Surface and Layout](ui-surface-and-layout-architecture.md)
- [Workflow Architecture](../workflows/workflow-architecture.md)
