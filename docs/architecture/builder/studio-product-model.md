# Studio Product Model

This note defines the current production-ready product model for the first-party
Mozaiks management UX.

## Customer-Facing Terminology

Use these terms in visible product copy:

- `Mozaiks`
- `Studio`
- `Apps`
- `App Studio`
- `Overview`
- `Access`
- `Usage`
- `Integrations`

Hosted deployments may add provider-owned sections such as billing or hosting,
but those routes are not owned by the OSS first-party Studio.

Workflow-owned concepts like `Build` may still appear in lifecycle copy, but
they are not standalone Studio pages in the current production surface.

## Internal Terminology

These terms may remain in code, APIs, and host/runtime composition, but should
not appear as primary customer-facing product language:

- `factory_app`
- `Hub`
- `Studio host`
- `Refinement Engine`
- `workflow_sequence`
- `extension_registry`

## Studio, Host, And CLI

**Mozaiks Studio** is the browser product. It owns app creation, build
continuation, artifact review, app workspace status, usage, app health signals, access, and
integrations. Creation and continuation are separate intents: the shell Create
entrypoint always starts a fresh build journey, while continuation happens from
Studio/App Studio build history or an explicit chat/build record.

The **Studio host** is the internal FastAPI composition layer that serves the
browser Studio and mounts the factory builder/Refinement Engine capabilities. Keep
`studio` in host names, environment values, and architecture docs where it
describes runtime composition.

The **CLI** is a local developer interface. It creates workspaces, starts host
processes, prints diagnostics, and opens Studio. It must not grow separate
product workflows for app creation, artifact review, promotion, run history, or
build lifecycle management.

## Route Model

Workspace-level routes:

- `/apps` -> workspace app portfolio home
- `/usage` -> workspace workflow token usage and cost totals
- `/integrations` -> workspace provider setup and integration catalog

App-level routes:

- `/apps/:appId` -> redirects to `/apps/:appId/overview`
- `/apps/:appId/overview` -> App Studio overview with operational health summary
- `/apps/:appId/health` -> deep app health diagnostics, hidden from primary navigation
- `/apps/:appId/access` -> app access
- `/apps/:appId/usage` -> app usage
- `/apps/:appId/integrations` -> app integration setup detail, hidden from primary navigation
- `/apps/:appId/support` -> app support follow-up, help desk notes, and stalled run triage
- `/apps/:appId/activity` -> app build history, artifact versions, and carry-forward audit detail, hidden from primary navigation

Primary app navigation is:

- Overview
- Access
- Usage
- Support

The Studio route model is canonical. Do not add route aliases for
retired customer-facing terms such as `Hub`, `Treasury`, `Adapters`,
`Deploy`, `Operations`, `Settings`, or `Admin`.

Provider-owned billing or hosting pages belong in hosted app workspaces through
managed capabilities, custom routes, or generated app-owned facades backed by explicit
host capability metadata. The OSS factory Studio must not hardcode hosted
product billing routes.

## Route Map

| Route | Surface | Notes |
| --- | --- | --- |
| `/apps` | Workspace Apps | Primary workspace home and app portfolio |
| `/usage` | Workspace Usage | Cross-app workflow input/output tokens, totals, and averages |
| `/integrations` | Workspace Integrations | Workspace provider setup, catalog coverage, and safe credential-presence status |
| `/create` | Workflow entrypoint | Workflow-owned fresh create path; not part of the persistent Studio nav and never an implicit resume surface |
| `/apps/:appId` | App Studio | Redirects to app overview |
| `/apps/:appId/overview` | App Overview | App-scoped summary, next actions, operational health, connected services, build state, and activity |
| `/apps/:appId/health` | App Health Diagnostics | Deep diagnostics across runtime, workflows, hosting, and integrations; routable but hidden from primary navigation |
| `/apps/:appId/access` | App Access | App-scoped account access, plan assignment, and access blockers |
| `/apps/:appId/usage` | App Usage | App-scoped input/output token usage, cost signals, totals, and averages |
| `/apps/:appId/integrations` | App Integration Setup | App-declared integration needs with workspace provider status; routable but hidden from primary navigation |
| `/apps/:appId/support` | App Support | App-scoped help desk, escalations, stalled runs, and support diagnostics |
| `/apps/:appId/activity` | Build History | Build artifact versions, validation state, and carry-forward audit detail; routable but hidden from primary navigation |

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
- All apps route into `Open App Studio` rather than a separate build page.
- App Studio routes remain available before deployment.
- Pre-live sections show lifecycle-aware guidance instead of disappearing.
- Continue-build actions are launched from the app/build record, not from
  Profile or the shell Create button.

## Studio Separation

`Mozaiks Studio` is multi-app scope:

- apps
- usage
- integrations

`App Studio` is single-app scope:

- overview
- access
- usage
- support

## Non-Canonical Terms

Replace these in customer-facing UX:

| Non-canonical term | Replacement | Customer-facing/internal-only | Notes |
| --- | --- | --- | --- |
| `Hub` | `Apps` | Customer-facing | No longer presented as a product or shell name |
| `Deploy` | `Hosting` | Customer-facing | Managed rollout and production posture now live under Hosting |
| `Operations` | none in Studio IA | Customer-facing | Do not present unfinished operations pages in the production Studio |
| `Settings` | none in Studio IA | Customer-facing | Do not present unfinished settings pages in the production Studio |
| `Admin Portal` | none in Studio IA | Customer-facing | App-admin routes are not part of the current production Studio |
| `Treasury` | provider-owned billing capability | Hosted/product-owned | Do not add OSS factory Studio billing routes for hosted billing products |
| `Adapters` | `Integrations` | Customer-facing | Use `Integrations` for visible app surfaces; workspace adapter routing is hidden |
| `Factory App` | `Mozaiks` / `Mozaiks Studio` | Customer-facing | `factory_app` remains an internal package name |
| `Refinement Engine` | none | Internal-only | Do not expose as primary product copy |

Keep internal API and host names stable unless runtime work explicitly requires
deeper refactoring.
