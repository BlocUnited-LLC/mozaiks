# AppGenerator Output Assembly Contract

**Status:** Canonical contract
**Purpose:** Define exactly how AppGenerator turns persistent UI intent into bundle artifacts.

---

## Owned Artifacts

AppGenerator emits deterministic app-bundle artifacts for persistent app UI:

- `app.json`
- `admin/admin_registry.yaml`
- `ui/pages/*.yaml`
- `brand/theme_config.json`
- `config/shell.json`
- `config/asset_manifest.json`

The artifact split is strict:

- `app.json` defines app identity, targets, auth intent, and startup behavior such as `startup.landing_spot`.
- `admin/admin_registry.yaml` declares all admin portal pages for the app's Studio management surface. Module panels reference these page ids via the `page` field in `modules/{module}/contracts/admin.yaml`.
- `ui/pages/*.yaml` define persistent page structure and route ownership.
- `brand/theme_config.json` defines visual tokens, shared primitives, and semantic `ui.chat` / `ui.shell` / `ui.page` styling.
- `config/shell.json` defines compact app-wide shell behavior: header logo/actions, canonical shortcuts, navigation policy, non-page-owned navigation items, and chrome mode defaults.
- `config/asset_manifest.json` defines reusable media inventory metadata for non-token assets (icons/images/video), including source/provenance and usage hints.

AppGenerator does not own the full build lifecycle, agent workflows, agent UI
tools, or workflow transition surfaces. AgentGenerator produces workflow/agent
artifacts, and the control plane governs AppContextVersion selection,
validation, review, and ArtifactVersion acceptance/promotion. Generated bundle
files become source of truth only through artifact acceptance and promotion.

When AppGenerator assembles module artifacts into the app workspace, companion
manifests stay under `modules/{module}/contracts/`. Canonical event files are
`contracts/events.yaml`, `contracts/reactions.yaml`, and
`contracts/notifications.yaml`; do not flatten them to the module root or emit
`contracts/subscriptions.yaml`. Persistent module backends use
`backend/schemas.py`, not `backend/models.py`.

---

## Upstream Inputs

AppGenerator should compile these inputs in priority order:

1. `captured_theme_config`
   - optional canonical ThemeCapture artifact
   - strongest visual source when present

2. `app_build_plan`
   - carries `theme_preferences`, `brand_intent`, optional `shell_preset_hint`, pages, entities, capability packs, auth, and integrations

3. `experience_spec_document` / `ui_design_document`
   - persistent page intent and layout guidance

4. `concept_blueprint` and related design docs
   - fallback context when no stronger artifact exists

---

## Compilation Flow

### 1. ThemeCapture

`ThemeCapture` produces canonical visual evidence only.

It emits:

- `theme`
- `identity`
- `assets`
- `primitives`
- `fonts`
- `colors`
- `shadows`
- `ui.chat`
- `ui.shell`
- `ui.page`

It does **not** emit shell content such as header actions, profile menu items, notification copy, or footer links.

### 2. AppSchemaAgent

`AppSchemaAgent` compiles persistent UI into one `AppSchemaOutput` with six payloads:

- `manifest`
- `pages`
- `custom_route_bundle`
- `theme_config_patch`
- `shell_config`
- `asset_manifest`

### Theme vs Shell Ownership

Two artifacts handle visual and behavioral customization:

- `theme_config_patch` → visual tokens only: colors, typography, spacing scale, shadows, density, `ui.chat` / `ui.shell` / `ui.page` semantic styling
- `shell_config` → shell behavior only: header logo/actions, canonical `shortcuts`, `navigation.policy`, non-page-owned `navigation.items`, and `chrome` mode overrides

`shell_preset_hint` is not an artifact. It is prompt-time AppGenerator guidance
from `factory_app/workflows/AppGenerator/tools/shell_presets.yaml`. AppSchemaAgent
uses it to choose page `navigation`, page `shell_mode`, and whether a compact
`shell_config` override is necessary. The preset id is never written into the
generated app bundle.

Do not mix them. Raw spacing/width/density tokens belong in `theme_config_patch`.
Header action labels and app-wide shell placement rules belong in `shell_config`.

Rules:

- `manifest.default_route` is persisted to `app.json -> startup.landing_spot`
- `custom_route_bundle` is a rare bounded escape hatch for persistent routes that cannot be expressed cleanly through shipped primitives
- `theme_config_patch` is a partial patch for `brand/theme_config.json`
- `shell_config` is a partial patch for `config/shell.json`
- `asset_manifest` is a partial patch for `config/asset_manifest.json`
- raw spacing, width, density, and sizing tokens belong in `theme_config_patch`, not `shell_config`
- generated `shell_config.shortcuts` may only contain `header`, `profile`, `mobile`, `footer`, and `footerHideOnMobile`
- do not emit custom shortcut catalogs or shell fields outside `AppShellConfigPatch`
- shell actions may use semantic `variants[].when` for context-aware label/target changes; do not emit path-prefix, wildcard, or query-param override rules
- page-owned shell navigation belongs on `ui/pages/*.yaml -> navigation`; `shell_config.navigation.policy` owns app-wide placement rules
- page-owned header/footer/bottom-bar intent belongs on `ui/pages/*.yaml -> shell_mode`; `shell_config.chrome` owns only app-wide mode-policy overrides
- reusable media inventory belongs in `asset_manifest`, not in `theme_config_patch` or `shell_config`
- custom routes must be owned exclusively by `custom_route_bundle` (`ui/route_manifest.json` + `ui/pages/custom/*.jsx`) and must not duplicate any `ui/pages/*.yaml` route
- every custom route manifest entry must have exactly one `page_files` entry whose `registry_key` matches the route `component`; `save_app_schema` synthesizes `ui/index.js` from that matched pair
- `app/ui/index.js` must register every component referenced by `ui/route_manifest.json`; missing registrations are export/download blockers, not runtime surprises
- `admin/admin_registry.yaml` declares admin page and panel metadata; it is not a route registry and must not own full-page custom route components
- hosted-pack pages must bind through an app-owned facade module endpoint such as `/api/modules/analytics_dashboard/get_metrics`, never directly to hosted-pack internals
- declarative pages may launch workflow sessions through typed page actions (`action_type: workflow`), but workflow-local React still belongs to AgentGenerator and `chat.tool_call`

### Hosted-pack facade binding

When a build has a host-provided pack, AppGenerator must keep the generated app
boundary explicit:

```text
hosted_pack
  -> app/services/integrations/{pack_id}_client.py
  -> app-owned facade module
  -> ui/pages bind to the facade module
```

Provider-neutral example:

```text
hosted_analytics
  -> app/services/integrations/hosted_analytics_client.py
  -> modules/analytics_dashboard/
  -> ui/pages/analytics.yaml
  -> /api/modules/analytics_dashboard/get_metrics
```

The pack descriptor may provide `surfaces`, `supported_domains`, `branding`,
`generation_rules`, `supersedes`, `adapter_template`, and `capability_source`.
Those fields are planning metadata. They do not authorize generated pages to
call hosted service internals directly.

### Route/component registration drift

For custom full-page React routes, the route contract is complete only when all
of these are true:

- `ui/route_manifest.json -> pages[].component` names the runtime component key
- `ui/pages/custom/*.jsx` contains exactly one default-exported page for that key
- `ui/index.js` registers the same key through `registerComponent`
- no declarative `ui/pages/*.yaml` route owns the same path
- no implicit file discovery or admin-registry fallback may substitute for the three files above

Generation should catch missing or mismatched component registrations before
download/export.

### 2b. AdminRegistryAgent

`AdminRegistryAgent` runs after `AppUIQualityAgent` passes and before `AssemblyAgent`.

It produces `AdminRegistryOutput` with two payloads:

- `admin_registry` — typed `AdminRegistry` object (page list with ids, labels, paths, icons, scope, order)
- `code_files` — serialized `admin/admin_registry.yaml`

Rules:

- Always emits `overview` and `settings` app-scope pages
- Includes additional **standard** pages (`users`, `billing`, `usage`, `activity`, `operations`, `integrations`, `support`) based on `app_build_plan.capability_packs` entity domains and `auth_strategy`
- Includes **hosted-only** pages (e.g. `hosting`) only when `available_hosted_packs` is non-empty and contains the relevant pack id — never in OSS or self-hosted contexts
- Uses `scope: app` for all generated app pages; workspace-scope pages only for hosted operator contexts
- Hosted global operator registries belong to hosted product workspaces and must not be emitted into standard generated app bundles
- Page ids must cover every entity domain that `ConfigMiddlewareAgent` assigns module admin panels to
- The module contract quality gate validates at generation time that every `admin.yaml` panel `page` field resolves to a declared page id

**Hosted-only pages:**

| Page id | Inclusion condition | Path |
|---|---|---|
| `hosting` | `available_hosted_packs` contains `hosting` or `deployment` | `/apps/:appId/hosting` |

Add new hosted-only pages here when a hosted capability pack introduces a new operator surface. Do not add them to the standard inclusion rules.

`AssemblyAgent` must include `admin/admin_registry.yaml` in the final bundle output.

### 3. save_app_schema

`save_app_schema` is the persistence tool for schema-driven app bundles.

It must:

- write generated artifacts under
  `$MOZAIKS_GENERATED_ARTIFACTS_PATH/apps/{app_id}/{build_id}/app/`
- write `app.json`
- write `ui/pages/{name}.yaml`
- write `ui/route_manifest.json` when `custom_route_bundle` exists
- write `ui/pages/custom/*.jsx` when `custom_route_bundle` exists
- synthesize `ui/index.js` from `custom_route_bundle.page_files` when custom routes exist
- reject or warn on custom route registry drift before assembly: missing page files, duplicate registry keys, `.js` route files, non-default-exported React, or route components that no page file registers
- deep-merge `theme_config_patch` into `brand/theme_config.json`
- deep-merge `shell_config` into `config/shell.json`
- deep-merge `asset_manifest` into `config/asset_manifest.json`
- store `app_manifest`, `app_pages`, `app_custom_route_bundle`, `app_theme_config_patch`, `app_shell_config`, `app_asset_manifest`, and `app_schema_ready` in workflow context

It must not write directly into an active runtime-loaded app root such as a
workspace's `app/` bundle or `factory_app/app`. Activation requires an
explicit promotion step.

### 4. AssemblyAgent

When `app_schema_ready == true`, `AssemblyAgent` must emit those artifacts back out as `code_files` so downstream download/export tools can bundle them.

Required schema-driven outputs:

- `app.json`
- `admin/admin_registry.yaml`
- `ui/pages/{name}.yaml`
- `ui/route_manifest.json` when `app_custom_route_bundle` exists
- `ui/pages/custom/*.jsx` when `app_custom_route_bundle` exists
- `ui/index.js` when `app_custom_route_bundle` exists
- `brand/theme_config.json` when `app_theme_config_patch` exists
- `config/shell.json` when `app_shell_config` exists
- `config/asset_manifest.json` when `app_asset_manifest` exists

When `app_schema_ready == false`, `AssemblyAgent` should use task batch outputs
via `assemble_app_tasks` and must still preserve the page contract.

### 4b. Raw Frontend Path Removed

AppGenerator no longer carries a secondary raw frontend page/component generation lane.

Rules:

- ordinary persistent pages still compile through `AppSchemaAgent`
- raw React page/component tasks do not belong in AppGenerator build plans outside the explicit `custom_route_bundle` contract
- shell content compiles through `shell_config`, not through a separate frontend shell agent
- if the primitive system is insufficient, the platform should add a primitive, pattern, or page capability rather than reviving a second frontend codegen path

### 5. IntegrationReadinessAgent

`IntegrationReadinessAgent` runs after `AssemblyAgent` and before validation or
download. It is not a manual preflight step. It is the agentic aggregation point for
third-party connector needs discovered while planning or executing decomposed
build tasks.

Inputs:

- `app_build_plan.external_integrations`
- `capability_packs[].required_integrations`
- `build_tasks[].integration_needs`
- `app_task_batch_results` from task batch execution
- `integration_needs` recorded by task agents with `record_integration_need`

Rules:

- Reuse ready app-scoped connectors from the platform connector store.
- Prompt inline only for unresolved required credentials or required non-secret
  connector configuration.
- Emit structured `integration.required` requests with `integration_id`,
  provider/service id, `purpose`, `required_fields`, `secret_fields`,
  `non_secret_fields`, `permissions_required`, and resume context.
- Persist newly supplied credentials through the platform connector service so
  `/apps/{appId}/integrations` reflects the result.
- Persist only frontend-safe non-secret config in connector metadata. Secret
  fields are write-only and must not be returned to chat, generated code, or
  frontend read APIs.
- Do not generate app-owned API-key tables, credential collections, or custom
  credential forms.
- If required credentials are declined or unavailable, block validation/download
  and return to the user with the unresolved service list.

### 6. generate_and_download

`generate_and_download` is the bundling tool.

It does not reason about artifact ownership. It packages the materialized file set
into the downloadable app bundle.

### 7. AppValidation Strategy

`AppValidationAgent` must use an explicit validation strategy contract instead of
implicit E2B-only behavior.

Canonical strategy values:

- `e2b`
- `local`
- `skip`

Canonical status values:

- `passed`
- `failed`
- `skipped`

Rules:

- Studio/hosted environments may prefer `e2b` when sandbox credentials are available.
- CLI/local environments may resolve to `local` or explicit `skip`.
- generation/export must not be blocked solely because E2B is unavailable.
- `skip` is explicit and deterministic; it is not a hidden fallback and it is not
  reported as `passed`.
- export gating must allow only `passed` or explicit `skipped`, and still requires
  integration readiness and wiring checks to pass.

Materialization rule:

- typed agent outputs such as `app_backend_admin_config`, `python_files`, and
  `js_files` are the source of truth for their owned lanes
- the same applies to `database_files`, `model_files`, and `service_foundation_bundle.files`
- extraction may regenerate canonical file content from those typed fields before
  packaging
- raw `code_files` are the serialized mirror, not the authority, when a typed
  lane exists

---

## Bundle Rules

Do:

- keep persistent pages declarative
- keep shell content separate from shell styling
- reuse ThemeCapture output when available
- deep-merge generated theme/shell patches into canonical runtime files

Do not:

- generate raw React files for persistent pages by default
- generate any AppGenerator-managed raw React page/component files for persistent pages
- place header/footer action content in `theme_config.json`
- place spacing/padding/density tokens in `shell.json`
- place reusable media inventory in `theme_config.json` or `shell.json`
- route visual shell concerns through AgentGenerator

---

## Why This Exists

Without this split, AppGenerator either under-specifies visual/media control or mixes styling, shell behavior, and asset inventory.
The contract above keeps bundle generation deterministic, keeps ThemeCapture reusable, and gives the runtime a stable set of artifacts to consume.


