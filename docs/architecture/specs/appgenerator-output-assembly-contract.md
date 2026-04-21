# AppGenerator Output Assembly Contract

**Status:** Canonical contract
**Purpose:** Define exactly how AppGenerator turns persistent UI intent into bundle artifacts.

---

## Owned Artifacts

AppGenerator owns the deterministic product bundle artifacts for persistent app UI:

- `app.json`
- `pages/*.yaml`
- `brand/theme_config.json`
- `config/shell.json`
- `config/asset_manifest.json`

The ownership split is strict:

- `app.json` defines app identity, targets, auth intent, and startup behavior such as `startup.landing_spot`.
- `pages/*.yaml` define persistent page structure and route ownership.
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

`AppSchemaAgent` compiles persistent UI into one `AppSchemaOutput` with five payloads:

- `manifest`
- `pages`
- `theme_config_patch`
- `shell_config`
- `asset_manifest`

Rules:

- `manifest.default_route` is persisted to `app.json -> startup.landing_spot`
- `theme_config_patch` is a partial patch for `brand/theme_config.json`
- `shell_config` is a partial patch for `config/shell.json`
- `asset_manifest` is a partial patch for `config/asset_manifest.json`
- raw spacing, width, density, and sizing tokens belong in `theme_config_patch`, not `shell_config`
- header/profile/notifications/footer content belongs in `shell_config`, not `theme_config_patch`
- reusable media inventory belongs in `asset_manifest`, not in `theme_config_patch` or `shell_config`

### 3. save_app_schema

`save_app_schema` is the persistence tool for schema-driven app bundles.

It must:

- write `app.json`
- write `pages/{name}.yaml`
- deep-merge `theme_config_patch` into `brand/theme_config.json`
- deep-merge `shell_config` into `config/shell.json`
- deep-merge `asset_manifest` into `config/asset_manifest.json`
- store `app_manifest`, `app_pages`, `app_theme_config_patch`, `app_shell_config`, `app_asset_manifest`, and `app_schema_ready` in workflow context

### 4. AssemblyAgent

When `app_schema_ready == true`, `AssemblyAgent` must emit those artifacts back out as `code_files` so downstream download/export tools can bundle them.

Required schema-driven outputs:

- `app.json`
- `pages/{name}.yaml`
- `brand/theme_config.json` when `app_theme_config_patch` exists
- `config/shell.json` when `app_shell_config` exists
- `config/asset_manifest.json` when `app_asset_manifest` exists

When `app_schema_ready == false`, `AssemblyAgent` should use MFJ fan-in via `assemble_app_tasks` and must still preserve the page contract.

### 4b. Raw Frontend Path Removed

AppGenerator no longer carries a secondary raw frontend page/component generation lane.

Rules:

- ordinary persistent pages still compile through `AppSchemaAgent`
- raw React page/component tasks do not belong in AppGenerator build plans
- shell content compiles through `shell_config`, not through a separate frontend shell agent
- if the primitive system is insufficient, the platform should add a primitive, pattern, or page capability rather than reviving a second frontend codegen path

### 5. generate_and_download

`generate_and_download` is the bundling tool.

It does not reason about artifact ownership. It simply packages the emitted `code_files` into the downloadable app bundle.

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
