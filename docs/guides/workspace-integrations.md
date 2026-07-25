# Workspace Integrations Implementation

**Status:** Current implementation reference
**Last updated:** 2026-07-24
**Scope:** Factory workflow, workspace module, Studio workspace page, app integrations page

For the user-facing guide, see
[Integrations](integrations/01-overview.md).

## Purpose

Workspace integrations give Mozaiks one shared place to understand external
services. The system separates what services exist, what the workspace has
configured, and what a specific app needs.

This prevents generated apps from repeatedly asking for the same credentials and
lets Studio show setup blockers only when an app actually depends on a service.
Internal conversations are separate from this system: the app `messages` module
owns threads and message records, while integrations can optionally deliver
those message events to external channels.

## Architecture

```text
Catalog
  defines supported services and setup metadata

Workspace connector
  stores workspace-scoped credential metadata and vault references

App integration declaration
  records services a generated app needs plus allowed setup lanes

App facade/module/client
  consumes those services without exposing provider internals to app UI
```

## Runtime Module

Module root:

```text
factory_app/app/modules/workspace_integrations/
```

Main actions:

| Action | Responsibility |
|--------|----------------|
| `list_integrations` | Return catalog entries, environment-derived status, notes, and app usage counts. |
| `get_integration` | Return one catalog entry with safe setup metadata. |
| `set_integration_note` | Save an operator note. |
| `save_workspace_connector` | Save a workspace-scoped connector and vault secret reference. |
| `list_workspace_connectors` | Return saved workspace connectors with health metadata. |
| `delete_workspace_connector` | Delete a saved workspace connector and vault secret. |
| `declare_app_integration_needs` | Persist build-produced app requirements. |
| `upsert_app_integration_need` | Create or update one app requirement. |
| `delete_app_integration_need` | Soft-delete a removable app requirement. |
| `list_app_integration_needs` | Return app requirements with live workspace setup overlay. |

No action returns raw secret values.

## Build Workflow

AppGenerator owns the build-time flow:

1. `check_workspace_integrations` lets planning inspect catalog status.
2. IntegrationReadinessAgent resolves required, optional, and custom services.
3. `save_integration_manifest` persists the app's integration declarations.
4. AppGenerator assembly materializes `app/config/integrations.yaml` and
   `app/config/targets.json` into generated app bundles.
5. Monetizable apps receive a removable Mozaiks Pay declaration unless the build
   explicitly declared a different payment path.

Important files:

```text
factory_app/workflows/AppGenerator/tools/check_workspace_integrations.py
factory_app/workflows/AppGenerator/tools/materialize_app_config_contracts.py
factory_app/workflows/AppGenerator/tools/save_integration_manifest.py
factory_app/workflows/AppGenerator/agents.yaml
factory_app/workflows/AppGenerator/tools.yaml
```

## Setup Lanes

Integration requirements are not API-key-first. Each declaration may expose one
or more setup lanes:

| Lane | Meaning |
|------|---------|
| `managed` | A hosted or managed provider can provision the setup for the app. |
| `connect_account` | The user connects an account through an OAuth/OIDC-style flow. |
| `bring_your_own_key` | The user supplies their own credential values through the connector UI. |
| `not_required` | The integration is declared for metadata or optional readiness only. |

Mozaiks Pay defaults to `managed` with `bring_your_own_key` as a supported
self-host fallback. The OSS catalog only declares those lanes. Hosted
provisioning, billing, marketplace, and provider mechanics remain outside OSS.

## Studio UI

Workspace page:

```text
/integrations
factory_app/app/admin/pages/WorkspaceIntegrationsPage.jsx
```

The page groups services by task state:

- **Needs attention**: used by apps and missing setup.
- **Connected**: ready from environment variables or a saved workspace connector.
- **Available**: supported but not currently blocking any app.

App page:

```text
/apps/:appId/integrations
factory_app/app/admin/pages/AppIntegrationsPage.jsx
```

The app page groups declarations by:

- **Required**
- **Optional**
- **App-specific**

## Status Rules

Catalog-backed services can be ready in either of two ways:

- all required environment variables are present, or
- a ready workspace connector exists for the service.

Custom services are not in the catalog, so they rely on connector status.

Removed optional defaults are soft-deleted so future builds do not immediately
re-add a user-removed default such as Mozaiks Pay.

## Catalog Ownership

The current catalog is duplicated:

```text
factory_app/build_context/integrations/catalog.yaml
factory_app/app/modules/workspace_integrations/backend/schemas.py
```

The YAML file is factory build context. The Python list is the runtime module's
catalog. Keep both synchronized until the runtime loads the YAML through a
strict typed catalog loader.

Both catalog copies must include setup-lane metadata so Factory workflows,
generated app config, and Studio integration screens agree on the same setup
contract.

## Testing

Targeted tests:

```powershell
python -m pytest tests/test_workspace_integrations_module.py tests/test_appgenerator_integration_manifest.py tests/test_check_workspace_integrations_tool.py -q --no-cov
npm --prefix web_shell run test:responsive-smoke -- --grep "workspace integrations|app integrations"
```

Also run the web shell build after UI changes:

```powershell
npm --prefix web_shell run build
```
