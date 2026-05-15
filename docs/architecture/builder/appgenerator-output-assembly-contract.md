# AppGenerator Output Assembly Contract

**Status:** Canonical contract
**Purpose:** Define exactly how AppGenerator turns persistent UI intent into bundle artifacts.

---

## Owned Artifacts

AppGenerator owns the deterministic product bundle artifacts for persistent app UI:

- `app.json`
- `admin/admin_registry.yaml`
- `ui/pages/*.yaml`
- `brand/theme_config.json`
- `config/shell.json`
- `config/asset_manifest.json`

The ownership split is strict:

- `app.json` defines app identity, targets, auth intent, and startup behavior such as `startup.landing_spot`.
- `admin/admin_registry.yaml` declares all admin portal pages for the app's operator console. Module panels reference these page ids via the `page` field in `modules/{module}/contracts/admin.yaml`.
- `ui/pages/*.yaml` define persistent page structure and route ownership.
- `brand/theme_config.json` defines visual tokens, shared primitives, and semantic `ui.chat` / `ui.shell` / `ui.page` styling.
- `config/shell.json` defines shell content and behavior such as header actions, profile controls, notifications, and footer links.
- `config/asset_manifest.json` defines reusable media inventory metadata for non-token assets (icons/images/video), including source/provenance and usage hints.

AppGenerator does not own agent workflows, agent UI tools, or workflow transition surfaces. Those belong to AgentGenerator and the workflow UI contracts.

---

## Upstream Inputs

AppGenerator should compile these inputs in priority order:

1. `captured_theme_config`
   - optional canonical ThemeCapture artifact
   - strongest visual source when present

2. `app_build_plan`
   - carries `theme_preferences`, `brand_intent`, pages, entities, capability packs, auth, and integrations

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
- `shell_config` → shell content and behavior only: header actions, profile menu, notification copy, footer links, `navigation.policy`, `chrome` mode overrides

Do not mix them. Raw spacing/width/density tokens belong in `theme_config_patch`.
Header actions, nav labels, and footer links belong in `shell_config`.

Rules:

- `manifest.default_route` is persisted to `app.json -> startup.landing_spot`
- `custom_route_bundle` is a rare bounded escape hatch for persistent routes that cannot be expressed cleanly through shipped primitives
- `theme_config_patch` is a partial patch for `brand/theme_config.json`
- `shell_config` is a partial patch for `config/shell.json`
- `asset_manifest` is a partial patch for `config/asset_manifest.json`
- raw spacing, width, density, and sizing tokens belong in `theme_config_patch`, not `shell_config`
- header/profile/notifications/footer content belongs in `shell_config`, not `theme_config_patch`
- page-owned shell navigation belongs on `ui/pages/*.yaml -> navigation`; `shell_config.navigation.policy` owns app-wide placement rules
- page-owned header/footer/bottom-bar intent belongs on `ui/pages/*.yaml -> shell_mode`; `shell_config.chrome` owns only app-wide mode-policy overrides
- reusable media inventory belongs in `asset_manifest`, not in `theme_config_patch` or `shell_config`
- custom routes must be owned exclusively by `custom_route_bundle` (`ui/route_manifest.json` + `ui/pages/custom/*.jsx`) and must not duplicate any `ui/pages/*.yaml` route
- declarative pages may launch workflow sessions through typed page actions (`action_type: workflow`), but workflow-local React still belongs to AgentGenerator and `chat.tool_call`

### 2b. AdminRegistryAgent

`AdminRegistryAgent` runs after `AppUIQualityAgent` passes and before `AssemblyAgent`.

It produces `AdminRegistryOutput` with two payloads:

- `admin_registry` — typed `AdminRegistry` object (page list with ids, labels, paths, icons, scope, order)
- `code_files` — serialized `admin/admin_registry.yaml`

Rules:

- Always emits `overview` and `settings` app-scope pages
- Includes additional pages (`users`, `billing`, `usage`, `activity`, `operations`, `integrations`, `support`) based on `app_build_plan.capability_packs` entity domains
- Uses `scope: app` for all standard generated app pages; workspace-scope pages only for hosted operator contexts
- Page ids must cover every entity domain that `ConfigMiddlewareAgent` assigns module admin panels to

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

When `app_schema_ready == false`, `AssemblyAgent` should use MFJ fan-in via `assemble_app_tasks` and must still preserve the page contract.

### 4b. Raw Frontend Path Removed

AppGenerator no longer carries a secondary raw frontend page/component generation lane.

Rules:

- ordinary persistent pages still compile through `AppSchemaAgent`
- raw React page/component tasks do not belong in AppGenerator build plans outside the explicit `custom_route_bundle` contract
- shell content compiles through `shell_config`, not through a separate frontend shell agent
- if the primitive system is insufficient, the platform should add a primitive, pattern, or page capability rather than reviving a second frontend codegen path

### 5. generate_and_download

`generate_and_download` is the bundling tool.

It does not reason about artifact ownership. It packages the materialized file set
into the downloadable app bundle.

### 6. AppValidation Strategy

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
  integration checks to pass.

Materialization rule:

- typed agent outputs such as `app_backend_admin_config`, `python_files`, and
  `js_files` are the source of truth for their owned lanes
- the same applies to `database_files`, `model_files`, and `backend_foundation_bundle.files`
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
