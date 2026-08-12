# AGENTS.md

Repository-level guidance for coding agents working in this repo.

## Working Path Constraint

**Always work on `C:\Repos\BlocUnitedRepo\mozaiks` (this repo) and `C:\Repos\BlocUnitedRepo\mozaiks-app`.**
Never read from or write to OneDrive paths (`C:\Users\...\OneDrive\...`). Those are stale copies, not the working repos.

Read [ARCHITECTURE.md](ARCHITECTURE.md) and [CLAUDE.md](CLAUDE.md) first.

## Required Pre-Edit Architecture Check

Before editing Mozaiks OSS:

- read [docs/architecture/ARCHITECTURE_QUICK_REFERENCE.md](docs/architecture/ARCHITECTURE_QUICK_REFERENCE.md)
- for generator, structured-output, YAML contract, taxonomy, route/action/component binding, or materialization work, also read
  [docs/architecture/CANONICAL_SCHEMA_GENERATION_POLICY.md](docs/architecture/CANONICAL_SCHEMA_GENERATION_POLICY.md)
- if the task changes, extends, replaces, or challenges architecture, also read
  [docs/architecture/MOZAIKS_OSS_SOFTWARE_DESIGN.md](docs/architecture/MOZAIKS_OSS_SOFTWARE_DESIGN.md)
- current source remains final authority
- if current source contradicts the frozen north star, stop and report the
  contradiction before editing
- before introducing a subsystem, identify the current canonical owner,
  determine whether AG2 already owns the primitive, determine whether Mozaiks
  already has the canonical implementation, and prefer extending or connecting
  the existing owner
- do not introduce a parallel subsystem without proving the canonical owner
  cannot satisfy the requirement
- architectural changes that contradict the frozen north star require an ADR or
  explicit architecture decision

## Deterministic Generation Rule

Generated-app reliability takes precedence over maximum schema expressiveness.

- Prefer small finite taxonomies, shallow typed schemas, and explicit canonical references.
- One runtime concept gets one canonical name. Do not preserve overlapping aliases for pages, actions, modules, workflows, capabilities, routes, events, or UI concepts.
- Every runtime-affecting structured-output value must resolve to a known canonical contract before promotion. Unknown taxonomy values and unresolved route/component/module/action/workflow/capability references fail early rather than becoming runtime `404`, `501`, missing-action, or fallback behavior.
- YAML and structured outputs own architecture, topology, identity, and contracts. Python and JS/React are bounded customization escape hatches behind those contracts; do not expand canonical YAML into an unbounded programming language.
- When a schema/taxonomy changes, update the structured-output model, Factory prompts/hooks, deterministic materializer/templates, runtime loader/consumer, validation, docs, fixtures, and acceptance tests together.
- `factory_app` and/or generated-app acceptance must dogfood generic schema/taxonomy changes where applicable.

This repo is pre-1.0 and not in production. **Do not add backward-compatibility logic for obsolete internal contracts by default.** Replace stale shapes and remove aliases, shims, fallback branches, dual-read/dual-write behavior, normalization of retired names, obsolete prompt guidance, and legacy tests in the same migration. Preserve compatibility only when an explicit current external contract or user-approved migration requirement proves it is necessary.

The detailed implementation policy is
[Canonical Schema Generation Policy](docs/architecture/CANONICAL_SCHEMA_GENERATION_POLICY.md).

This repo uses layered FastAPI hosts as the canonical OSS server composition:
- `mozaiksai.hosts.runtime`
- `mozaiksai.hosts.platform`
- `mozaiksai.hosts.studio`

`mozaiksai.hosts.runtime` is the execution substrate. `mozaiksai.hosts.platform`
is the headless app host. `mozaiksai.hosts.studio` is the Studio management
interface host — the shared management layer for both local and hosted
deployments. Hosted product repos compose their own app-local hosts on top of
Studio; this OSS repo does not own a hosted-product FastAPI host.

Start via the CLI:

```
mozaiks serve ./my-app                  # platform host (no factory dependency)
mozaiks serve ./my-app --host studio    # Studio management host (requires factory_app)
```


CLI and Studio are **parallel interfaces** over shared system capabilities, not a
superset chain. Studio is not the CLI's UI. CLI owns developer tooling (filesystem,
scaffolding, process management). Studio owns the management interface (workspace
status, build lifecycle, artifacts, run history, config).

Profile stays person-scoped. Studio / Workspace Shell is the org/workspace home
base. App shells remain separate and brandable per app; do not collapse org
management into `/me`.

The current repo layout is transitional. The canonical target is documented in
[docs/architecture/foundations/distribution-and-workspace-model.md](docs/architecture/foundations/distribution-and-workspace-model.md).
Do not reintroduce a hybrid root that mixes the starter app bundle with shared
factory workflows.

## Repo Status

This codebase is **not in production**.

That means optimization goals are different from a typical enterprise codebase:

- Prefer the cleanest canonical implementation.
- Prefer replacement over preservation.
- Remove stale logic when a better contract or architecture is introduced.
- Do not keep shims, aliases, wrappers, fallback branches, or duplicate schemas unless explicitly requested.

## No Paid Infrastructure Until Launch

**Do not provision, recommend, or assume any paid cloud service is running.**

This project is pre-launch. All paid Azure infrastructure (Container Apps,
Redis Cache, Container Registry, Log Analytics, Front Door) has been stopped
or is not yet provisioned to avoid billing before the product is live.

Rules for agents:
- Do not add `REDIS_URL` to `.env.example` or any config as a required value.
  Redis is **optional** — the app runs fine without it using in-memory fallbacks.
  Wire it only when the product is actually live and multi-instance scaling is needed.
- Do not suggest provisioning Azure Container Apps, Redis, or any other
  paid Azure service as part of a task. Those decisions belong to the operator.
- Do not reference staging or production Azure URLs as live endpoints.
  The Front Door URL (`fd-mozaiks-endpoint-*.azurefd.net`) is a config
  placeholder — it is not currently provisioned.
- If a task requires a cloud service that costs money, note it as a
  pre-launch prerequisite and stop — do not provision it.

## Release Hold

Do **not** publish this repo yet.

- Do not create or push `v*` Git tags.
- Do not trigger `.github/workflows/release.yml`.
- Do not publish to PyPI or create a GitHub release.
- Do not treat trusted-publisher or GitHub environment setup as approval to
  release.
- Do not bump `mozaiksai/version.py` for a public release unless the user
  explicitly says the repo is production-ready and wants to publish.

Normal code pushes are fine. Public release actions are not.

## Replacement Policy

When adjusting behavior:

- Replace outdated logic instead of layering new logic on top of it.
- Delete obsolete prompt guidance, docs, tests, config fields, and dead code paths that no longer match the current contract.
- Do not leave temporary outdated branches behind.
- Do not preserve outdated shapes without an explicit current contract reason.

If a new contract is introduced, update all affected layers together:

- runtime behavior
- generator prompts/hooks
- declarative schemas
- validation
- docs
- tests

## Clean Code Standard

Avoid "AI slop":

- no speculative abstractions
- no duplicate helpers with overlapping purpose
- no verbose fallback code for non-production paths
- no stale comments describing removed behavior
- no split source of truth when one canonical source will do

Prefer:

- tight contracts
- explicit validation
- small, named abstractions with clear ownership
- removing drift at the source

## AG2 Ownership Boundary

Mozaiks uses AG2 as the long-term agentic backbone. Do not recreate an
agentic framework inside Mozaiks when AG2 already owns the primitive or is the
right upstream home for it. See
[docs/architecture/workflows/ag2-ownership-boundary.md](docs/architecture/workflows/ag2-ownership-boundary.md)
for the durable architecture contract and upgrade watchpoints.

AG2 should own agentic execution mechanics wherever practical:

- agent primitives, model calls, tools, middleware, and task execution
- multi-agent network behavior, hubs, agent clients, channels, adapters, and
  workflow state progression
- task observation, task lifecycle events, delegation mechanics, and agent
  runtime observability primitives

Mozaiks should own deterministic product and runtime contracts around AG2:

- declarative workflow files, structured-output contracts, and validation
- canonical generated app/workflow/module artifact shapes
- app/runtime persistence, transport integration, tenant/session boundaries,
  and Studio/platform lifecycle concerns
- factory refinement policies and deterministic decomposition contracts
  that describe what work must be done before AG2 agents execute it

When AG2 does not provide a required capability, first analyze AG2's current
APIs, docs, and source shape. Implement the smallest Mozaiks-owned layer that
fits inside AG2's framework, preferably behind `mozaiksai.core.adapters` or a
similarly narrow boundary. Document every intentional divergence from AG2 so it
can be revisited when AG2 updates, and raise upstream issues or proposals when
the missing capability belongs in AG2 rather than Mozaiks.

Do not introduce Mozaiks-owned replacements for AG2 hubs, agent clients,
network adapters, task observation streams, delegation engines, or generic
agent scheduling unless AG2 has no usable path and the custom boundary is
explicitly documented.

## Canonical Repo Boundary

Canonical ownership:

| Layer | Owns |
|-------|------|
| `mozaiksai.hosts.runtime` / `mozaiksai` | AI execution substrate, sessions, transport, persistence, workflow execution |
| `mozaiksai.hosts.platform` | Headless app host: modules, pages, shell config, admin, actions, routing |
| `mozaiksai.hosts.studio` | Studio management interface host — shared management layer (local and hosted) |
| `mozaiksai.hosts.bootstrap` | Repo-local path defaults (CWD-relative; no-ops when not in repo checkout) |
| `mozaiks_cli/` | CLI / developer interface — parallel to Studio, not a subset of it |
| `factory_app/workflows/` | Factory layer — shared builder/generator workflows (AppGenerator, AgentGenerator, DesignDocs, ValueEngine) |
| `factory_app/workflows/{WorkflowName}/*.yaml` | Factory layer — workflow-owned runtime YAML, prompts, agents, transitions, structured outputs, tool bindings, and middleware |
| `factory_app/workflows/_shared/` | Factory layer — shared builder implementation consumed by multiple factory workflows, including deterministic Python helpers and reusable workflow UI components |
| `factory_app/build_context/{context_name}/context.yaml` | Factory layer — named build-context registries for static catalogs, contracts, reusable packs, and templates |
| `factory_app/refinement_harness/` | Factory layer — declarative builder harness pack: checkpoints, classifier prompts, routing policies, context tools |
| `factory_app/app/` | Studio first-party app bundle — pages, modules, brand, config loaded by the Studio host; not a synonym for the Factory layer |
| `factory_app/app/ui/pages/custom/studio/` | Studio management UI components |
| `factory_app/app/admin/` | Admin portal layer — `admin_registry.yaml` declares pages, `admin/index.js` registers components, `admin/pages/` holds custom admin page React files |
| `factory_app/app/modules/factory_control_plane/` | Studio identity module only — no backend actions |
| `chat-ui/src/admin/` | Platform-management surfaces — registered by Studio, inherited by Mozaiks App |
| `platform/` | Repo-local infrastructure assets only — not an app workspace |
| `generated/` | Generator output awaiting validation/promotion |

Canonical target:

- generated/customer apps become standalone workspaces/repositories
- shared generation core lives outside any individual app workspace
- app workspaces keep app bundle files under `app/` and app-local workflows at
  the workspace root under `workflows/`
- app/product workflow registries may explicitly extend
  `mozaiks.default_workflow_registry`; such overlays declare only product deltas
  and `{id, remove: true}` tombstones, never copied factory workflow registry
  entries or copied factory workflow folders
- workspace build contexts, when present, live beside `app/` under
  `build_context/{context_name}/`; they are launch-time build input registries
  for operator/product-specific build input, not generated app runtime output
- app-owned service implementations live at `app/services/`
- hosted product workspaces should consume that same contract from their own repos

## Build Context Packs Rule

Build-time intelligence uses named build contexts. The OSS factory, hosted
products, and customer workspaces use the same shape:
`build_context/{context_name}/context.yaml` plus files explicitly declared in
`context.yaml` `assets[]`.

- `context.yaml` is a registry. It declares `context_id`,
  `applies_to_workflows`, `assets[]`, optional `pack:` metadata, and optional
  `projections.context_variables`.
- Build-context files are consumed only when declared in `assets[]`; folder
  names are organization, not policy. Factory catalog YAMLs should sit directly
  beside `context.yaml` unless a real grouping need exists.
- Factory catalogs live under `factory_app/build_context/{context_name}/` and
  are declared as `assets[]` with `kind: catalog`, not beside runtime workflow
  YAML.
- Workflow hooks/tools stay under `factory_app/workflows/.../tools/` and read
  factory catalogs through
  `factory_app.workflows._shared.hook_utils.workflow_context_path()`.
- Do not recreate `factory_app/workflows/_shared/catalogs/`. Catalog contents
  belong in `factory_app/build_context/{context_name}/`; workflow
  tools may contain workflow-specific renderers over those catalogs.
- Shared workflow React components that are reused by multiple factory workflows
  belong under `factory_app/workflows/_shared/ui/`. They are not auto-registered:
  each consuming workflow must re-export/register them from its own
  `factory_app/workflows/{WorkflowName}/ui/index.js` so UI ownership remains
  workflow-scoped and deterministic.
- Do not import UI from a sibling workflow folder. Move genuinely shared
  workflow UI to `_shared/ui/`, or keep a workflow-specific wrapper in the
  owning workflow's `ui/` folder.
- `context_variables.yaml` declares runtime/session state. Large static prompt
  catalogs are injected by deterministic hooks; do not stuff them into context
  variables.
- Reusable OSS build packs are named build contexts. `context.yaml` owns the
  pack descriptor through a `pack:` section plus capabilities and facades. It
  should be useful LLM/build context, not a placeholder
  manifest or generated build plan.
- Pack-specific agent instructions are `assets[]` with `kind: contract`. Keep
  contract YAML typed: use rule lists such as `selection_rules`,
  `required_outputs`, `forbidden_outputs`, `runtime_boundaries`, and `facades`;
  do not use narrative top-level prose.
- Deterministic generated files are `assets[]` with `kind: templates`. The
  template directory mirrors the canonical generated app tree. Store generated
  module templates under
  `templates/modules/...`, service/client templates under
  `templates/services/...`, page templates under `templates/ui/...`, and config
  templates under `templates/config/...`.
  YAML files there are generated app declaratives, not build-context contracts.
- App or hosted-product build contexts live at workspace root `build_context/`,
  beside `app/` and `workflows/`, using the same named-context shape.
- Runtime launch context may be enriched through
  `MOZAIKS_LAUNCH_CONTEXT_PROVIDER=mozaiksai.core.session.build_context:merge_build_context`.
- Context `context.yaml` must project only declared workflow
  `context_variables`; it is not routing authority, secret storage, generated
  app runtime code, Python resolver code, or a place to hide product-specific
  logic in OSS.

Examples:

- `factory_app/build_context/AppGenerator/file_contracts.yaml`
- `factory_app/build_context/AgentGenerator/ag2_network_patterns.yaml`
- `factory_app/build_context/Communications/context.yaml`
- `workspace/build_context/mozaikspay/context.yaml`
- `workspace/build_context/mozaikspay/contract.yaml`

## Multi-Agent Coordination

Multiple coding agents (Claude Code, Codex) operate autonomously and
simultaneously across `mozaiks` and `mozaiks-app`. Follow these rules to avoid
stomping on each other.

**Before starting any task:**
```bash
git fetch origin
gh pr list --state open           # see what other agents have in flight
git log origin/main --oneline -5  # see what recently landed
```

Never branch off another open PR's still-unmerged branch, even as a "hard
dependency" — you inherit its bugs and every later fix has to be re-propagated
into your branch too. Wait for it to merge (green CI, actually in `main`) and
branch from