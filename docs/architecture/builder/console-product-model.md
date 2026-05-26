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
- `Health`
- `Users`
- `Usage`
- `Integrations`

Hosted deployments may add provider-owned sections such as billing or hosting,
but those routes are not owned by the OSS first-party factory console.

Workflow-owned concepts like `Build` may still appear in lifecycle copy, but
they are not standalone console pages in the current production surface.

## Internal Terminology

These terms may remain in code, APIs, and host/runtime composition, but should
not appear as primary customer-facing product language:

- `factory_app`
- `Hub`
- `Studio host`
- `Control Plane`
- `workflow_sequence`
- `extension_registry`

## Console, Studio Host, And CLI

The **Mozaiks Console** is the browser product. It owns app creation, build
continuation, artifact review, app workspace status, usage, health, users, and
integrations.

The **Studio host** is the internal FastAPI composition layer that serves the
Console and mounts the factory builder/control-plane capabilities. Keep
`studio` in host names, environment values, and architecture docs where it
describes runtime composition.

The **CLI** is a local developer interface. It creates workspaces, starts host
processes, prints diagnostics, and opens the Console. It must not grow separate
product workflows for app creation, artifact review, promotion, run history, or
build lifecycle management.

## Route Model

Workspace-level routes:

- `/apps` -> workspace app portfolio home
- `/usage` -> workspace workflow token usage and cost totals
- `/health` -> workspace cross-app health summary

App-level routes:

- `/apps/:appId` -> redirects to `/apps/:appId/overview`
- `/apps/:appId/overview` -> App Console overview
- `/apps/:appId/health` -> app health
- `/apps/:appId/users` -> app users
- `/apps/:appId/usage` -> app usage
- `/apps/:appId/integrations` -> app integrations

Primary app navigation is:

- Overview
- Health
- Users
- Integrations
- Usage

The Console route model is canonical. Do not add route aliases for
retired customer-facing terms such as `Hub`, `Studio`, `Treasury`, `Adapters`,
`Deploy`, `Operations`, `Settings`, or `Admin`.

Provider-owned billing or hosting pages belong in hosted app workspaces through
hosted packs, custom routes, or generated app-owned facades backed by explicit
host capability metadata. The OSS factory console must not hardcode hosted
product billing routes.

## Route Map

| Route | Surface | Notes |
| --- | --- | --- |
| `/apps` | Workspace Apps | Primary workspace home and app portfolio |
| `/usage` | Workspace Usage | Cross-app workflow input/output tokens, totals, and averages |
| `/health` | Workspace Health | Cross-app health posture and app-level risk visibility |
| `/create` | Workflow entrypoint | Workflow-owned create path; not part of the persistent console nav |
| `/apps/:appId` | App Console | Redirects to app overview |
| `/apps/:appId/overview` | App Overview | App-scoped summary and next actions |
| `/apps/:appId/health` | App Health | Overall app health across runtime, workflows, hosting, and integrations |
| `/apps/:appId/users` | App Users | App-scoped users and customer activity |
| `/apps/:appId/usage` | App Usage | App-scoped input/output token usage, cost signals, totals, and averages |
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
- health

`App Console` is single-app scope:

- overview
- health
- users
- integrations
- usage

## Non-Canonical Terms

Replace these in customer-facing UX:

| Non-canonical term | Replacement | Customer-facing/internal-only | Notes |
| --- | --- | --- | --- |
| `Hub` | `Apps` | Customer-facing | No longer presented as a product or shell name |
| `Studio` | none in console IA | Customer-facing | Keep `studio` only for host/runtime internals |
| `Deploy` | `Hosting` | Customer-facing | Managed rollout and production posture now live under Hosting |
| `Operations` | none in console IA | Customer-facing | Do not present unfinished operations pages in the production console |
| `Settings` | none in console IA | Customer-facing | Do not present unfinished settings pages in the production console |
| `Admin Portal` | none in console IA | Customer-facing | App-admin routes are not part of the current production console |
| `Treasury` | provider-owned billing capability | Hosted/product-owned | Do not add OSS factory console billing routes for hosted billing products |
| `Adapters` | `Integrations` | Customer-facing | Use `Integrations` for visible app surfaces; workspace adapter routing is hidden |
| `Factory App` | `Mozaiks` / `Mozaiks Console` | Customer-facing | `factory_app` remains an internal package name |
| `Control Plane` | none | Internal-only | Do not expose as primary product copy |

Keep internal API and host names stable unless runtime work explicitly requires
deeper refactoring.
