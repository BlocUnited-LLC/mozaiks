# Account, Admin, and Platform Services

This document defines the deterministic product services that every Mozaiks app
can rely on without authoring a workflow or generating a custom shell surface.

These concerns still matter if AI is turned off. That is the classification
test.

Account identity, relationship inventory, settings, notifications, and admin
are not workflow products. They are platform and app-backend responsibilities
with explicit UI, API, and module-contract boundaries. Billing, subscriptions,
entitlements, deployment, governance, build history, app access, and workspace
operations are app/workspace management concerns and belong in Admin Portal or
Studio, not in Profile.

## Canonical Rule

- Treat these concerns as first-class platform services.
- Do not model them as app-specific `capability_packs` in AppGenerator.
- Do not generate replacement `/profile` or `/admin` shells.
- Keep deterministic state, policies, and CRUD behind the app backend and
  module contracts.
- Add AI only as augmentation on top of these services, never as the source of
  truth.
- Use the [Platform Navigation Contract](platform-navigation-contract.md) for
  the canonical Profile/Admin/Studio/App Shell split.

AppGenerator already follows this rule. Its planning prompts explicitly mark
authentication, user profile, settings, notifications, user management, and the
admin portal as built-in platform features rather than
things to scaffold as product-specific packs.

## Three Contract Layers

### 1. Framework-Owned Surfaces

Mozaiks ships the visible shell surfaces for account and admin flows.

- `/profile` is rendered by the core `ProfilePage` component and labeled
  "Account" in shell navigation.
- `/profile` is only for the signed-in person's account data and personal
  preferences. It is not an app/workspace management surface.
- current-user relationship inventory is exposed through
  `GET /api/me/relationships` for My Apps, Portfolio, and My Resources
  surfaces.
- Generated app admin routes are rendered by the core `AdminPortal` component.
- Studio's first-party Admin Portal entry routes to `/apps`, not a standalone
  `/admin` page.
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
- `GET /api/me/relationships` owns current-user resource relationship
  aggregation across module-declared relationship providers.
- `GET /api/admin/config` owns host admin shell state, runtime panels, and
  module admin panel discovery.
- `GET {app_backend_url}/api/admin/config` and related
  `app_backend_url/api/admin/*` endpoints optionally own app-business admin
  panels that are embedded inside the unified generated-app admin shell.

The shell can render these surfaces only because deterministic backend
contracts exist. The shell is not the source of truth.

### 3. Module Declaratives And Hooks

Modules can extend these deterministic systems through explicit contracts.

- `modules/{module}/contracts/settings.yaml` declares module-local settings and
  feature flags.
- `modules/{module}/contracts/reactions.yaml` declares event reactions owned by
  the module.
- `modules/{module}/contracts/profile.yaml` declares account-scoped profile
  panels for user-owned personal data only.
- `modules/{module}/contracts/relationships.yaml` declares current-user
  resource relationship providers for My Apps, Portfolio, and My Resources
  surfaces.
- `modules/{module}/contracts/notifications.yaml` declares notification intents
  per event.
- `modules/{module}/contracts/admin.yaml` declares feature-owned admin panels
  rendered inside the unified generated-app admin shell.
- contract-declared custom admin components are materialized as frontend stubs
  and registered through the active app root's `ui/index.js` extension barrel.

Module backend files implement deterministic behavior behind those manifests.
Use the canonical `handler.py`, `service.py`, `repo.py`, `policy.py`, and
`schemas.py` split, plus explicit helper files when a module needs them.

## Surface Map

| Concern | Primary UX surface | Source of truth | Extension contract | Not owned by |
|---|---|---|---|---|
| Account/Profile | `/profile` via `ProfilePage` | `GET/PUT /api/me` | `modules/{module}/contracts/profile.yaml` for module panels | workflows, generated page bundles |
| Resource relationships | My Apps/Portfolio/My Resources surfaces | `GET /api/me/relationships` | `modules/{module}/contracts/relationships.yaml` | workflows, generated page bundles, admin shell |
| Preferences | `/profile` preferences section | `GET/PUT /api/me/preferences` | `modules/{module}/contracts/settings.yaml` when settings runtime support exists | workflow prompts |
| Notifications | shell notification surfaces and backend delivery rules | app backend plus module notification policy | `modules/{module}/contracts/notifications.yaml` | workflows as source of truth |
| Subscriptions and entitlements | Admin Portal billing/access views and gated capability behavior | app backend entitlement state | billing/subscription modules | Profile, capability-pack generation |
| Build continuation | Studio/App Studio build history | build/app records plus selected `chat_id` | Studio build metadata | Profile, shell Create action |
| Admin | generated-app admin route family via `AdminPortal`; Studio Admin Portal uses `/apps` | framework admin shell plus same-host admin APIs, `app/app.json` `admins`, and optional app-backend admin APIs | `modules/{module}/contracts/admin.yaml` | custom admin page generation, Profile |

## Account And Profile

`ProfilePage` is a framework-owned surface registered in the core component
registry.

Current behavior:

- it renders the current user's account view at `/profile`
- it loads and updates profile data from `/api/me`
- it loads app/user preference data from `/api/me/preferences`
- it loads module profile panels from `/api/me/profile-panels`
- it uses the host API adapter rather than a custom page-local backend contract

Important boundary:

- `/profile` is not a generated page bundle
- `/profile` is not a workflow artifact
- `contracts/profile.yaml` is only for module-contributed account/profile
  panels; it does not replace identity, preferences, My Apps, Portfolio, build
  history, billing, app access, deployment, governance, or admin operations

Do not overload `settings.yaml`, `reactions.yaml`, `admin.yaml`, or
`relationships.yaml` as a proxy for account identity UI composition.

## Current-User Relationships

Relationship inventory is the framework-owned answer to "what resources is
this user connected to?" It powers My Apps, Portfolio, My Communities, and
other account-adjacent resource lists without turning those resources into
profile fields or admin panels.

Current behavior:

- modules declare providers in `modules/{module}/contracts/relationships.yaml`
- the platform hydrates providers through module actions at
  `GET /api/me/relationships`
- the endpoint returns normalized rows with `resource_type`, `resource_id`,
  `resource_label`, `relationship_type`, `status`, `capabilities`,
  `primary_route`, optional `secondary_routes`, and optional `metadata`

Important boundary:

- relationships are not app admin operations
- relationships do not grant payment, ownership, entitlement, or governance
  rights by themselves
- product-specific semantics stay in the owning module's service/policy layer
  and are exposed only as safe relationship metadata when useful for UI routing

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

Compact shell behavior belongs in `app/config/shell.json`.
Examples include the logo, sparse header actions, canonical shortcuts,
navigation placement policy, and chrome modes. Profile menus, notification
summaries, and footer links are derived from shortcuts and platform defaults
unless a first-party host explicitly owns a manual shell configuration.

Do not collapse these three settings layers into one contract.

## Subscriptions And Notifications

Subscriptions and notifications are platform services with module-level
extension points, not standalone app packs that every product must reinvent.

### Subscriptions And Entitlements

- entitlement state stays deterministic and backend-owned
- user-visible tier/status belongs in Admin Portal billing/access views unless
  the product is explicitly individual-account billing with no app/workspace
  administration surface
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

Generated app hosts have one visible admin route family rendered through the
`AdminPortal` shell surface. Studio does not expose a standalone `/admin` page;
its Admin Portal entry is the Apps surface at `/apps`.

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

- `profile`, `settings`, `notifications`, `auth`, and `user_management` are
  built-in platform capabilities, not app-specific packs
- subscriptions, billing, revenue participation, governance, deployment, and
  build continuation are app/workspace management concerns; generate module
  admin contracts or Studio metadata for them, not profile panels
- My Apps, Portfolio, and My Resources lists should use
  `contracts/relationships.yaml` plus `GET /api/me/relationships`, not custom
  generated profile/admin pages
- the generated app may wire config and module manifests around these systems
- the generated app should not scaffold replacement `/profile` or `/admin`
  surfaces

This keeps the deterministic product foundation stable while still allowing app-
specific business modules and AI workflows to sit on top.

## Cross References

- [Admin System](admin-system.md)
- [Core, Product, and App Bundle Boundary](../foundations/core-product-app-bundle-boundary.md)
- [Relationship Provider Contract](../foundations/relationship-provider-contract.md)
- [UI Surface and Layout](ui-surface-and-layout-architecture.md)
- [Workflow Architecture](../workflows/workflow-architecture.md)
