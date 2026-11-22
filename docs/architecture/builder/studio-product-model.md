# Studio Product Model

This note defines the current pre-launch product model for the first-party
Mozaiks management UX.

## Customer-Facing Terminology

Use these terms in visible product copy:

- `Mozaiks`
- `Studio`
- `Apps`
- `App Studio`
- `Overview`
- `Building`
- `Access`
- `Usage`
- `Integrations`

Hosted deployments may add provider-owned sections such as billing or hosting,
but those routes are not owned by the OSS first-party Studio.

Workflow-owned concepts like `Build` may still appear in lifecycle copy. The
canonical persistent surface is the `Building` portal, not a standalone
`/build` page.

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

### App Registry Ownership

Studio directory records have one immutable `owner_user_id`. HTTP routes derive
it from the authenticated principal, module actions from `ModuleContext`, and
local build lifecycle updates from the resolved session owner. It is not an
editable request field, and knowing an app or registry ID grants no access.

The App Registry repo includes ownership in every record read, lifecycle write,
and deletion filter. Reopening an app ID never transfers ownership. Concurrent
creation claims one record through the existing unique app-ID index; an
interrupted metadata write leaves an owned draft that the same owner can retry.
Missing owners fail validation. Other-owner and missing records are both absent
to reads and return `404` from Studio update/delete routes.

Removing a directory record does not purge runtime usage facts. A user-chosen
registry app ID is not authority to erase app-wide accounting. Account data
management and runtime retention remain separate owners.

Module creation without an explicit target app ID allocates a fresh app ID;
it must not reuse the host app ID from `ModuleContext`. This is shared Studio
behavior inherited by hosted products, not hosted commercial policy.

### Navigation

The account dropdown offers signed-in builders one management entry, `Studio`.
Its home is `My Apps` (`/apps`), the caller's portfolio. The workspace navigation
also offers `Performance` (`/performance`) across the caller's apps. Both use
the builder's existing owner scope.
During a workflow conversation, the dropdown resolves
`GET /api/studio/build/dashboard-link?chat_id=...`. The server reads the
caller's owned chat binding and verifies its App Registry target before returning
the enabled default app dashboard portal. URL app selectors cannot choose that
target. A missing conversation or an owned first-build chat without a binding
opens `/apps`. An inaccessible context returns `404`, and the menu returns to
the portfolio when resolution fails.

The stock dashboard uses the verified binding's target app ID. A host whose
dashboard uses its own registration IDs can supply the async FastAPI app-state
callback `resolve_studio_dashboard_app_id(*, host_app_id, owner_user_id, binding)`.
Studio calls it after verifying the owned chat and Factory registry record.
The host looks up the matching registration under that owner, host and Factory
binding, and returns its route ID or `None`. A missing or invalid ID yields
`404`. The destination dashboard still checks the caller's access.

Revenue and adapter management use the app selected in Studio. In stock Studio,
the dashboard ID is the owned Factory target. A host with distinct dashboard IDs
supplies `list_studio_app_bindings(*, host_app_id, owner_user_id, dashboard_app_id=None, build_registry_ids=None)`
on FastAPI app state. The async callback selects by one dashboard ID or a batch of
Factory registry IDs. It returns the owner's stored associations in that host,
each with `dashboard_app_id`, `build_registry_id`, and `target_app_id`. Studio
checks the Factory registration and execution host before it reads metrics or
changes adapters for the selected app.

App analytics and metric detail requests from a dashboard use
`reference=dashboard`. Direct analytics requests keep the existing Factory target
ID convention. Portfolio rows carry the measured `app_id` and a separate
`dashboard_app_id` for navigation. Older apps with no verified dashboard link
still contribute their owned metrics to Performance.

The shared navigation manifest is readable with a token bound to the loaded
Studio host. Explicit app selections retain registry checks.

App dashboard pages retain server-side registry ownership checks. Workspace
operations retain their existing role requirements. Generated app administration
continues through the app's declared Admin surface.

Workspace-level routes:

- `/apps` -> workspace app portfolio home
- `/performance` -> portfolio revenue and user performance
- `/usage` -> workspace workflow token usage and cost totals
- `/integrations` -> workspace provider setup and integration catalog
- `/support` -> apps with support chats, each linking to that app's inbox
- `/settings/ai-models` -> default and allowed AI models for builds; signed-in workspace administrators only

App-level routes:

- `/apps/:appId` -> redirects to `/apps/:appId/overview`
- `/apps/:appId/overview` -> App Studio overview
- `/apps/:appId/revenue` -> recurring-revenue analytics
- `/apps/:appId/audience` -> user analytics and the lifecycle funnel
- `/apps/:appId/building` -> manifest-backed Building portal for build requests, artifacts, approvals, and workflow launch
- `/apps/:appId/usage` -> app usage
- `/apps/:appId/health` -> what needs attention: latest build validation, failed runs, integrations missing a credential or failing their check
- `/apps/:appId/integrations` -> owner-managed connections and integration requirements for the selected app
- `/apps/:appId/support` -> app support follow-up, help desk notes, and stalled run triage
- `/apps/:appId/activity` -> app build review, artifact versions, validation state, review actions, and carry-forward audit detail, hidden from primary navigation

A Studio menu lists the routes in `factory_app/app/ui/route_manifest.json`.
Each one is also an enabled portal in `factory_app/app/dashboard/dashboard.yaml`,
and `tests/test_dashboard_manifest.py` fails when the two lists differ.

A page is mounted only when Studio has data for it. There is no workspace
Users page and no app Access page: Studio has no account directory to read, so
both could only show zeros. User counts are on Performance and on each app's
Users page.

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
| `/apps` | My Apps | Primary Studio workspace home and app portfolio |
| `/performance` | Performance | Portfolio revenue and user metrics, trend, and per-app comparison |
| `/usage` | Workspace Usage | Cross-app workflow input/output tokens, totals, and averages |
| `/integrations` | Workspace Integrations | Workspace provider setup, catalog coverage, safe credential-presence status, and the result of each saved connector's last check |
| `/support` | Workspace Support | Apps with support chats, each linking to that app's support inbox |
| `/settings/ai-models` | AI models | Default and allowed AI models for Factory builds and refinements |
| `/create` | Workflow entrypoint | Workflow-owned fresh create path; not part of the persistent Studio nav and never an implicit resume surface |
| `/apps/:appId` | App Studio | Redirects to app overview |
| `/apps/:appId/overview` | App Overview | App-scoped summary, next actions, connected services, build state, and activity |
| `/apps/:appId/revenue` | App Revenue | Recurring-revenue metrics, trend, and the MRR movement bridge |
| `/apps/:appId/audience` | App Users | Acquisition, engagement, conversion, and the app's lifecycle funnel |
| `/apps/:appId/building` | Building | Generic dashboard portal rendered from `dashboard/dashboard.yaml`; build requests, artifacts, approvals, and workflow launch |
| `/apps/:appId/health` | App Health | Specific things to fix, from the latest build's validation result, failed runs, and integrations missing a credential or failing their check |
| `/apps/:appId/usage` | App Usage | App-scoped input/output token usage, cost signals, totals, and averages |
| `/apps/:appId/integrations` | Integrations | Owner-scoped connection setup, write-only credentials, manual health checks for supported services, and build requirements |
| `/apps/:appId/support` | App Support | App-scoped help desk, escalations, stalled runs, and support diagnostics |
| `/apps/:appId/activity` | Build Review | Build artifact versions, validation state, staged output, review actions, and carry-forward audit detail; routable but hidden from primary navigation |

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
- building
- access
- usage
- support

App Studio data loads belong to the mounted app selection. Changing apps,
refreshing, or leaving a page aborts its pending registry and detail requests;
an abandoned registry response cannot start another detail batch. Development
StrictMode replay follows the same cancellation path. Explicit refresh still
loads fresh data, and the runtime rate limits remain in effect.

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
