# Console Product Model

This note defines the current production-ready product model for the first-party
Mozaiks management UX.

## Customer-Facing Terminology

Use these terms in visible product copy:

- `Mozaiks`
- `Console`
- `Apps`
- `App Console`
- `Overview`
- `Users`
- `Usage`
- `Billing`
- `Hosting`
- `Integrations`

Workflow-owned concepts like `Build` may still appear in lifecycle copy, but
they are not standalone console pages in the current production surface.

## Internal Terminology

These terms may remain in code, APIs, and host/runtime composition, but should
not appear as primary customer-facing product language:

- `factory_app`
- `App Zero`
- `Hub`
- `Studio host`
- `Control Plane`
- `workflow_sequence`
- `extension_registry`

## Route Model

Workspace-level routes:

- `/apps` -> workspace app portfolio home
- `/usage` -> workspace cross-app usage
- `/billing` -> workspace billing summary
- `/hosting` -> workspace hosting summary

App-level routes:

- `/apps/:appId` -> redirects to `/apps/:appId/overview`
- `/apps/:appId/overview` -> App Console overview
- `/apps/:appId/users` -> app users
- `/apps/:appId/usage` -> app usage
- `/apps/:appId/billing` -> app billing
- `/apps/:appId/hosting` -> app hosting
- `/apps/:appId/integrations` -> app integrations

Primary app navigation is:

- Overview
- Users
- Integrations
- Usage
- Billing
- Hosting

The Console route model is canonical. Do not add compatibility aliases for
retired customer-facing terms such as `Hub`, `Studio`, `Treasury`, `Adapters`,
`Deploy`, `Operations`, `Settings`, or `Admin`.

## Route Map

| Route | Surface | Notes |
| --- | --- | --- |
| `/apps` | Workspace Apps | Primary workspace home and app portfolio |
| `/usage` | Workspace Usage | Cross-app usage aggregation |
| `/billing` | Workspace Billing | Revenue, recurring value, and finance readiness |
| `/hosting` | Workspace Hosting | Managed hosting posture, domains, and release readiness |
| `/create` | Workflow entrypoint | Workflow-owned create path; not part of the persistent console nav |
| `/apps/:appId` | App Console | Redirects to app overview |
| `/apps/:appId/overview` | App Overview | App-scoped summary and next actions |
| `/apps/:appId/users` | App Users | App-scoped users and customer activity |
| `/apps/:appId/usage` | App Usage | App-scoped usage and cost signals |
| `/apps/:appId/billing` | App Billing | App revenue, customer billing, and finance posture |
| `/apps/:appId/hosting` | App Hosting | Hosting readiness, domains, environments, and production posture |
| `/apps/:appId/integrations` | App Integrations | App-scoped connectors, credentials, and permissions |

## Lifecycle States

App records should exist immediately and use these customer-facing states:

- `Draft`
- `Building`
- `Review`
- `Configuring`
- `Deploying`
- `Active`
- `Needs Revision`
- `Archived`

Behavior expectations:

- Draft and in-progress apps appear in the Apps portfolio immediately.
- All apps route into `Open App Console` rather than a separate build page.
- App Console routes remain available before deployment.
- Pre-live sections show lifecycle-aware guidance instead of disappearing.

## Console Separation

`Mozaiks Console` is multi-app scope:

- apps
- usage
- billing
- hosting

`App Console` is single-app scope:

- overview
- users
- integrations
- usage
- billing
- hosting

## Deprecated Terms

Replace these in customer-facing UX:

| Deprecated term | Replacement | Customer-facing/internal-only | Notes |
| --- | --- | --- | --- |
| `Hub` | `Apps` | Customer-facing | No longer presented as a product or shell name |
| `Studio` | none in console IA | Customer-facing | Keep `studio` only for host/runtime internals |
| `Deploy` | `Hosting` | Customer-facing | Managed rollout and production posture now live under Hosting |
| `Operations` | none in console IA | Customer-facing | Do not present unfinished operations pages in the production console |
| `Settings` | none in console IA | Customer-facing | Do not present unfinished settings pages in the production console |
| `Admin Portal` | none in console IA | Customer-facing | App-admin routes are not part of the current production console |
| `Treasury` | `Billing` | Customer-facing | Revenue and recurring value stay under Billing |
| `Adapters` | `Integrations` | Customer-facing | Use `Integrations` for visible app surfaces; workspace adapter routing is hidden |
| `Factory App` | `Mozaiks` / `Mozaiks Console` | Customer-facing | `factory_app` remains an internal package name |
| `App Zero` | none | Internal-only | Internal bootstrap/reference term only |
| `Control Plane` | none | Internal-only | Do not expose as primary product copy |

Keep internal API and host names stable unless runtime work explicitly requires
deeper refactoring.
