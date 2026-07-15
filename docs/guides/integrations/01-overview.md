# Integrations

Integrations let a Mozaiks workspace connect external services once and reuse
those connections across generated apps. The user-facing model is intentionally
small:

- **Workspace Integrations** shows which services are connected, which services
  need setup for apps that use them, and which services are simply available.
- **App Integrations** shows the services a specific app needs.
- **Advanced setup details** show environment names, setup steps, and operator
  notes only when the user opens an integration.

The UI should not expose module names, adapter details, provider internals, or
raw secrets.

## Core Objects

| Object | Purpose | User-facing? |
|--------|---------|--------------|
| Catalog entry | Supported integration type such as OpenAI, Resend, or Mozaiks Pay. No secrets. | Yes, as an available service |
| Workspace connector | Saved workspace credential/config metadata for a catalog service. Secret values stay in the backend vault. | Yes, as a connected service |
| App integration declaration | Per-app record saying "this app needs this service." Produced by the build workflow or edited by the operator. | Yes, on the app page |
| Facade module | App-owned API that exposes product actions without binding UI directly to provider internals. | No |
| Provider adapter | Low-level SDK/API mechanics when an app truly owns a provider integration. | No |
| Runtime extension | Callback, webhook, startup poller, or long-running integration boundary. | No, except through status |
| Internal messaging | App-owned threads, messages, read state, and in-app notification events. | Yes, through app/support UI |

## Setup Flow

1. The factory catalog is loaded from
   `factory_app/build_context/integrations/catalog.yaml`.
2. During planning, AppGenerator can call `check_workspace_integrations` to see
   which services are already configured.
3. IntegrationReadinessAgent resolves the app's required and optional services.
4. `save_integration_manifest` persists those requirements as app integration
   declarations.
5. Studio overlays live workspace setup status when the app page loads.
6. Users can configure shared services through workspace-level environment
   variables or saved workspace connectors.
7. Apps read the resulting declarations/status and show whether each required
   service is ready, needs setup, or is app-specific.

Mozaiks Pay is the default removable payment integration for monetizable apps.
It is represented as a Mozaiks-managed service in the catalog and generated app
metadata.

Internal app messaging is not configured as a workspace integration. Support
desks, social replies, and app-to-user conversations use the app's `messages`
module first. Email, SMS, push, and workspace chat providers are optional
delivery adapters on top of message events.

## What The User Sees

### Workspace `/integrations`

The page is task-oriented:

- **Needs attention**: services used by apps but missing setup.
- **Connected**: services ready for apps.
- **Available**: supported services that no app currently depends on.

Opening a service shows status, usage, credential source, notes, and advanced
setup details. Deleting is only available for saved workspace connectors. Catalog
entries and environment variables are not deleted from this page.

### App `/apps/:appId/integrations`

The page is app-oriented:

- **Required**: services this app expects before related features are usable.
- **Optional**: removable defaults or enhancements.
- **App-specific**: services outside the workspace catalog.

If a required catalog service is not ready, the app points the user back to the
workspace Integrations page. If a service is app-specific, setup belongs in the
app environment or a dedicated app module.

## How Apps Know What Is Ready

App pages do not guess from UI state. They call
`workspace_integrations.list_app_integration_needs`.

That action combines:

- the app's persisted integration declarations,
- live environment-secret presence from the catalog,
- saved workspace connector inventory,
- removed optional defaults.

A catalog-backed service is considered ready when either its required
environment variables are present or a ready workspace connector exists. Custom
services use connector status because they are not in the workspace catalog.

## Where To Add Integrations

Add new supported integration types to the factory catalog and keep runtime
validation aligned:

- `factory_app/build_context/integrations/catalog.yaml`
- `factory_app/app/modules/workspace_integrations/backend/schemas.py`
- `tests/test_workspace_integrations_module.py`

The current implementation keeps catalog data in both YAML and Python. Keep
them synchronized until the catalog is moved behind a single typed loader.

Generated apps should not call provider internals directly when a Mozaiks facade
exists. They should bind pages and workflows to app-owned facade modules or
managed-capability clients, with provider mechanics hidden behind services or
hosted product boundaries.

For support-heavy apps, the generated app should bind support views to the
internal `messages` module and use integrations only for external delivery.

## Connector And Secret Rules

- Secret values are never returned to the frontend.
- API responses may include secret names and boolean presence only.
- Workspace connectors are scoped by workspace and can be reused by apps.
- App-scoped connectors override workspace connectors when a generated app
  explicitly needs its own credential.
- Runtime extensions are reserved for OAuth callbacks, webhooks, long-running
  pollers, or external event pipelines.
