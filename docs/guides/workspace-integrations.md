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
| `list_integrations` | Return catalog entries, environment-derived status, notes, and usage counts for apps owned by the caller. |
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

Workspace connector actions act only on the caller's verified workspace: the
workspace the validated token is bound to (`AUTH_WORKSPACE_ID_CLAIM`, default
`workspace_id`), or a membership the host's `module_scope_resolver` hook
asserted with `verified_workspace_id`. The optional `workspace_id` argument must
name that workspace; any other workspace is refused, and a workspace or tenant
selected by the request never chooses one. A signed-in caller with no verified
workspace cannot use the connector actions, and `list_app_integration_needs`
then returns declarations without a connector overlay. With authentication off
and development access, the action uses the requested workspace, then the
dispatch workspace or tenant, then `demo-workspace`.

Workspace actions refuse a dispatch workspace or tenant that conflicts with the
verified principal before storage or message events. App declaration actions
require the selected app to have an app registry record owned by the caller;
the app ID in the action must match the dispatch app. Catalog usage counts are
limited to the caller's registry-owned apps. The AppGenerator tool writes
declarations through the service directly under its build authorization.

The connector vault uses the same explicit app or workspace scope as the
metadata record. A workspace connector is never selected by an app ID alone.
App-level overrides and workspace defaults remain distinct even when their
identifiers and service names happen to match.

### Existing vault records before 0.2.0

The scoped vault contract does not read old unqualified secrets. From a
private operator session with read access to the intended environment, run a
dry-run inventory:

```powershell
python scripts/inventory_connector_vault.py --provider mongo --report <private-new-report-path>
```

For Azure Key Vault, use `--provider azure_key_vault`. The script projects
only connector metadata and vault properties, including version properties;
it never retrieves, decrypts, copies, or prints a credential. It writes
counts, opaque record references, and a fingerprint to the private report.
Azure inventory selects names in the configured connector prefix namespace;
other platform secrets in a shared vault are outside this cleanup scope even
when tagged `managed_by=mozaiks`. Never select cleanup targets by that tag
alone. Azure connector writes and deletes require permission to inspect all
version properties and refuse a name with any unowned or unknown version.
Prevent other principals from writing connector names during inspection and
mutation; Azure has no atomic version-history lock across those calls.
Deployments sharing one Mongo instance must not rely on different secret
prefixes for isolation: the connector collection uses a fixed system database
and its key omits the prefix. Use separate Mongo instances.

Re-run with
`--expect-fingerprint <reviewed-fingerprint>` to detect changed metadata.
There is no automatic apply operation. A report with unqualified, missing,
duplicate, or mismatched entries blocks readiness. A single old record still
needs operator review; a collision cannot establish which scope owns its
remaining value.

For each environment with existing connector secrets:

1. Record the current vault provider and prefix, take a private backup under the
   operator's normal secret-handling policy, and review the first inventory.
   Freeze connector writes and connector-dependent operations for the cutover.
2. Run the scoped runtime in a restricted maintenance window. Verify each
   connector's app or workspace owner from independent account records.
   Re-enter credentials through that owner's scoped Studio UI, including every
   ambiguous connector. Do not copy one ambiguous old value into multiple
   scopes. If duplicate old Mongo rows prevent creation of the scoped unique
   index, stop. Keep operations frozen, resolve only the exact duplicate rows
   through separately approved operator tooling and the protected backup, then
   re-run inventory before retrying. Verify scoped reads and provider health
   before enabling usage.
3. Take a new inventory after re-entry. A retained unqualified record still produces
   a finding and keeps `ready: false`, even if its scoped replacement is ready.
   Keep it only for the reviewed rollback window. Before cleanup, freeze writes
   again and re-run the reviewed snapshot with `--expect-fingerprint`.
4. Separately approve and perform exact unqualified-record cleanup using the
   operator's vault/database tooling and private metadata. Confirm the exact
   connector name and owner for each target; exclude other platform secrets
   in a shared vault. This inventory script cannot delete, decrypt, or migrate
   a secret. If cleanup is deferred,
   keep the connector-dependent release gate closed.
5. Write a fresh final inventory to a new private path. Require `ready: true`
   and repeat scoped read/provider checks before opening connector-dependent
   traffic. After cleanup, rollback needs the protected backup or credential
   re-entry; the old unqualified records are no longer a live fallback.

## Build Workflow

AppGenerator owns the build-time flow:

1. `check_workspace_integrations` lets planning inspect catalog status.
2. IntegrationReadinessAgent resolves required, optional, and custom services,
   blocking only on required `build_time` or `validation_time` setup. Runtime-only
   needs remain recorded as deployment requirements without blocking generation.
3. `save_integration_manifest` runs automatically after readiness and persists
   all app integration declarations, including runtime-only requirements.
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

Optional defaults that a user turns off stay turned off for later builds of the
same app.

## Connector Vault Scope Migration Prerequisite

Connector metadata distinguishes app and workspace scope, but the current vault
secret identifier does not include the scope kind. A future scope-aware
identifier requires a one-time, read-only dry run over existing connector
records and vault entries before any rename. The dry run should report unique,
missing, and ambiguous mappings without exposing secret values. Ambiguous
records need owner review or credential re-entry; metadata alone cannot recover
an overwritten value.

The eventual change must update vault storage, connector readers and writers,
health checks, and generated clients together. Stage and verify migrated
secrets before switching reads, retain a rollback path, and remove previous
entries only after verification. This note does not migrate existing data.

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
