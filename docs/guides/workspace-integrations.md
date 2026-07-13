# Workspace Integrations

**Status:** In Progress — Phases 1–4 complete  
**Last Updated:** 2026-07-11  
**Scope:** Factory workflow, workspace module, Studio UI (global management + app summary/deep detail)

---

## Overview

Workspace Integrations is a two-tier system that lets builders connect external services once at the workspace level, then have apps automatically detect, reuse, or request those connections during generation. The primary user path is global management at `/integrations` plus a compact connected-services summary on each app Overview.

The system solves three problems:

1. **Credential sprawl** — right now every generated app re-declares secrets independently with no shared visibility into what is already wired up
2. **Builder friction** — the factory agent has no way to know what third-party services are already available, so it can't auto-wire them or give specific setup guidance
3. **Ops opacity** — there is no workspace-level view of which integrations are healthy, missing, or broken

---

## Architecture

### Two-tier model

```
Workspace level (global)          App level (per-app)
─────────────────────────         ────────────────────────────
Integration catalog               App integration manifest
Credential health checks          Which integrations this app uses
Operator notes UI                 App-specific config per integration
Used by: Studio /integrations     Used by: app Overview + deep setup detail
```

### Integration catalog

The catalog lives in two places that must stay in sync:

- **`factory_app/build_context/integrations/catalog.yaml`** — YAML the factory agent reads during app planning (build context asset)
- **`factory_app/app/modules/workspace_integrations/backend/schemas.py`** — Python runtime list (`INTEGRATIONS_CATALOG`) the module service reads to derive live status

14 integrations are currently defined across 9 categories: `payments`, `email`, `sms`, `notifications`, `source_control`, `storage`, `database`, `cache`, `ai`, `auth`, `analytics`.

### Status derivation

Status is derived server-side from environment secrets — no secret values are ever returned:

| Status | Meaning |
|--------|---------|
| `configured` | All `required_secrets` are present and non-empty |
| `partial` | Some required secrets present, not all |
| `missing` | No required secrets present |
| `unknown` | `MOZAIKS_INTEGRATIONS_REGISTRY_MODE=catalog_only` — hosted multi-tenant mode where env vars cannot be read per-tenant |

The `secrets` field in API responses is always `[{"name": "SECRET_NAME", "present": bool}]` — values are never included.

---

## Build Phases

### Phase 1 — Integration catalog + workspace module ✓

**Goal:** Workspace knows what integrations exist and which are configured.

#### Deliverables

- [x] `factory_app/build_context/integrations/catalog.yaml` — integration catalog
- [x] `factory_app/build_context/integrations/context.yaml` — build context registry
- [x] `factory_app/app/modules/workspace_integrations/module.yaml`
- [x] `factory_app/app/modules/workspace_integrations/contracts/events.yaml`
- [x] `factory_app/app/modules/workspace_integrations/backend/__init__.py`
- [x] `factory_app/app/modules/workspace_integrations/backend/handler.py`
- [x] `factory_app/app/modules/workspace_integrations/backend/service.py`
- [x] `factory_app/app/modules/workspace_integrations/backend/repo.py`
- [x] `factory_app/app/modules/workspace_integrations/backend/policy.py`
- [x] `factory_app/app/modules/workspace_integrations/backend/schemas.py`

**Module actions:**

| Action | Kind | Description |
|--------|------|-------------|
| `list_integrations` | read | Full catalog with configured/partial/missing status and operator notes |
| `get_integration` | read | Single integration with setup steps and per-secret presence |
| `set_integration_note` | write | Operator annotation (rotation date, environment scope, owner) |

---

### Phase 2 — Factory workflow tool ✓

**Goal:** AppGenerator can check what integrations are available early in planning.

#### Deliverables

- [x] `factory_app/workflows/AppGenerator/tools/check_workspace_integrations.py`
- [x] `factory_app/workflows/AppGenerator/tools.yaml` — tool registered for `AppPlanAgent` (non-auto)
- [x] `factory_app/workflows/AppGenerator/agents.yaml` — AppPlanAgent step 9b instructs the agent to call `check_workspace_integrations`, populate `workspace_integration_status[]`, auto-wire configured integrations, and pass missing ones to `record_integration_need`
- [x] `factory_app/workflows/AppGenerator/structured_outputs.yaml` — `WorkspaceCatalogCheck` model added; `workspace_integration_status: optional_list[WorkspaceCatalogCheck]` added to `AppBuildPlan`

**Tool signature:**
```python
async def check_workspace_integrations(
    integration_ids: list[str] | None = None,
    context_variables: Any = None,
) -> dict:
    """
    Returns:
        {
            "available": [{"id": "mozaikspay", "name": "MozaiksPay", "status": "configured"}],
            "partial":   [{"id": "s3",     "name": "AWS S3", "status": "partial",
                           "missing_secrets": ["AWS_S3_BUCKET"]}],
            "missing":   [{"id": "twilio", "name": "Twilio", "status": "missing",
                           "missing_secrets": [...],
                           "setup_url": "/integrations/twilio"}],
            "unknown":   [],
            "not_in_catalog": ["my_custom_service"],
        }
    """
```

**Agent behavior contract (pending prompt update):**

The AppPlanAgent should:
1. Call `check_workspace_integrations` with the integration IDs its plan requires
2. For `available` integrations: wire them automatically in generated `secrets.yaml` and `services/integrations/`
3. For `missing` integrations: pass them to `record_integration_need` for the downstream `IntegrationReadinessAgent` to collect inline
4. For `partial` integrations: generate code but flag the incomplete secret in the build summary

---

### Phase 3 — Studio UI: global integrations page ✓

**Goal:** Operators can see all available integrations, their status, and add notes in one place.

**Route:** `/integrations`  
**Shell mode:** `workspace`

#### Deliverables

- [x] `factory_app/app/admin/pages/WorkspaceIntegrationsPage.jsx` — global catalog view
- [x] `factory_app/app/admin/admin_registry.yaml` — `workspace-integrations` entry at `/integrations`, order 20
- [x] `factory_app/app/admin/index.js` — `WorkspaceIntegrationsPage` lazy-imported and registered

**Implementation notes:**
- Page calls `/api/modules/workspace_integrations/list_integrations` (POST)
- Notes saved via `/api/modules/workspace_integrations/set_integration_note` (POST)
- Integrations grouped by category, sorted alphabetically
- Each card: status pill, per-secret presence rows, note preview, "Add/Edit note" button
- No hook extracted — the page fetches directly; extract `useWorkspaceIntegrations` if reuse is needed

---

### Phase 4 — App connected-services summary + setup detail ✓

**Goal:** Each app's Overview shows which integrations the build declared it needs, with live workspace status overlaid. A deep detail route is available when an operator needs to inspect app-specific requirements or setup gaps.

**Routes:** `/apps/{id}/overview` summary, `/apps/{id}/integrations` deep detail  
**Navigation:** the deep detail route is route-visible but hidden from the primary app sidebar

#### Deliverables

- [x] `factory_app/app/modules/workspace_integrations/backend/schemas.py` — `DECLARATIONS_ENTITY`, `build_declaration_document()`, `build_declaration_response()`
- [x] `factory_app/app/modules/workspace_integrations/backend/repo.py` — `IntegrationDeclarationsRepo` (uses `AG2PersistenceManager` directly, no `ModuleContext` needed)
- [x] `factory_app/app/modules/workspace_integrations/backend/service.py` — `declare_app_integration_needs()`, `list_app_integration_needs()` (live workspace status overlay)
- [x] `factory_app/app/modules/workspace_integrations/backend/handler.py` — dispatch for two new module actions
- [x] `factory_app/app/modules/workspace_integrations/module.yaml` — `declare_app_integration_needs`, `list_app_integration_needs` actions
- [x] `factory_app/workflows/AppGenerator/tools/save_integration_manifest.py` — factory tool, direct Python import, best-effort
- [x] `factory_app/workflows/AppGenerator/tools.yaml` — tool registered for `IntegrationReadinessAgent`
- [x] `factory_app/workflows/AppGenerator/agents.yaml` — step 5 added to `IntegrationReadinessAgent`
- [x] `factory_app/app/admin/pages/AppOverviewPage.jsx` — connected-services summary sourced from app declarations
- [x] `factory_app/app/admin/pages/AppIntegrationsPage.jsx` — deep app-specific setup detail + live workspace status
- [x] `factory_app/app/admin/admin_registry.yaml` — app integrations route remains enabled with `show_in_navigation: false`

**Module actions added:**

| Action | Kind | Description |
|--------|------|-------------|
| `declare_app_integration_needs` | write | Persist what a build declared the app needs (`AppIntegrationDeclarations` collection) |
| `list_app_integration_needs` | read | Return declarations with live workspace catalog status overlaid |

**Data flow:**

```
IntegrationReadinessAgent resolves needs
    → save_integration_manifest (factory tool, best-effort)
        → IntegrationDeclarationsRepo.upsert_declarations()
            → AppIntegrationDeclarations (MongoDB, SYSTEM_DATABASE)

AppOverviewPage.jsx / AppIntegrationsPage.jsx
    → POST /api/modules/workspace_integrations/list_app_integration_needs
        → service.list_app_integration_needs()
            → repo.get_for_app() + live derive_status() overlay
```

**Live workspace status overlay:** `list_app_integration_needs` re-derives `workspace_status` from current env vars at query time (not the snapshot taken at build time), so the app Overview summary and deep detail page always show the current workspace configuration state.

**`setup_url`** is emitted only when `workspace_status == "missing"` and `catalog_id` is set, pointing to `/integrations/{catalog_id}` for direct setup flow.

**Uncatalogued services** (custom/internal APIs not in the 14-entry catalog) show connector vault status only — no `setup_url` or workspace env-var status.

---

### Phase 5 — Enterprise hardening

- [ ] **Audit log** — note updates logged to audit trail
- [ ] **RBAC gate** — `workspace_integrations.read` and `workspace_integrations.manage` permissions enforced at dispatch
- [ ] **Multi-tenant isolation** — note storage scoped by tenant in hosted mode
- [ ] **Health check endpoint** — `GET /api/admin/integrations/health` for monitoring
- [ ] **Catalog versioning validation** — loader rejects catalogs with wrong `schema_version`
- [x] **Zero secrets in transit** — `build_integration_response` returns `{"name": s, "present": bool}` only, never values
- [x] **`catalog_only` mode** — `MOZAIKS_INTEGRATIONS_REGISTRY_MODE=catalog_only` returns `unknown` for all entries without env reads

---

## File Checklist

### Factory / Build Context
- [x] `factory_app/build_context/integrations/catalog.yaml`
- [x] `factory_app/build_context/integrations/context.yaml`
- [x] `factory_app/workflows/AppGenerator/tools/check_workspace_integrations.py`
- [x] `factory_app/workflows/AppGenerator/tools/save_integration_manifest.py`
- [x] `factory_app/workflows/AppGenerator/tools.yaml` — both tools registered
- [x] `factory_app/workflows/AppGenerator/agents.yaml` — `IntegrationReadinessAgent` step 5
- [ ] `factory_app/workflows/AppGenerator/structured_outputs.yaml` — add `IntegrationRequirement`

### Module
- [x] `factory_app/app/modules/workspace_integrations/module.yaml`
- [x] `factory_app/app/modules/workspace_integrations/contracts/events.yaml`
- [x] `factory_app/app/modules/workspace_integrations/backend/__init__.py`
- [x] `factory_app/app/modules/workspace_integrations/backend/handler.py`
- [x] `factory_app/app/modules/workspace_integrations/backend/service.py`
- [x] `factory_app/app/modules/workspace_integrations/backend/repo.py`
- [x] `factory_app/app/modules/workspace_integrations/backend/policy.py`
- [x] `factory_app/app/modules/workspace_integrations/backend/schemas.py`

### Studio UI
- [x] `factory_app/app/admin/pages/WorkspaceIntegrationsPage.jsx` — global page
- [x] `factory_app/app/admin/pages/AppOverviewPage.jsx` — connected-services summary
- [x] `factory_app/app/admin/pages/AppIntegrationsPage.jsx` — app-specific setup detail page
- [x] `factory_app/app/admin/admin_registry.yaml` — workspace-integrations entry
- [x] `factory_app/app/admin/index.js` — component registered

### Tests
- [x] `tests/test_workspace_integrations_module.py` — catalog, policy, service, declarations (31 tests)
- [x] `tests/test_check_workspace_integrations_tool.py` — factory tool (8 tests)
- [ ] `tests/test_integrations_catalog_loader.py` — catalog YAML schema validation

### Docs
- [x] This file updated as phases complete

---

## UX Principles

**Don't overwhelm.** The global integrations page shows 14 integration types grouped by category. Configured items appear with green status pills; missing items still show but without alarming the operator — the catalog is informational unless an app actually depends on it.

**Be specific about what's missing.** Each card shows per-secret presence rows with the exact env var name so the operator knows what to copy where.

**Bridge the gap.** When the factory agent surfaces a missing integration during app creation, give the user a direct path via `setup_url` (`/integrations/{id}`) — not just "you need Twilio" but a link to the workspace integrations page for Twilio specifically.

**Keep app dashboards focused.** App Overview shows a concise "Connected services" summary for services that app actually declared. The app-level integration route is a deep setup/detail page and is hidden from the primary sidebar so global provider management does not compete with core app management.

**Status must be honest.** In hosted multi-tenant mode where secret presence cannot be verified server-side, show `unknown` with a clear explanation — not a false `configured` or `missing`.

---

## Open Questions

- [ ] Should `catalog.yaml` be extensible by app workspaces (workspace-local integration definitions)? Or OSS-only?
- [ ] Do we want OAuth-based connections (for example GitHub OAuth App) in addition to API key integrations, or key-only for v1?
- [ ] Should app integration requirements be inferred from `secrets.yaml` only, or also from `services/integrations/` file presence?
- [ ] Multi-tenant: should workspace operators be able to mark an integration as "managed by platform" so app builders don't see setup steps?
