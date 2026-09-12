# AGENTS.md

Repository-level guidance for coding agents working in this repo.

## Shared Engineering Contract

The rules below are canonical for every agent and live in one place:
[docs/agent-engineering-contract.md](docs/agent-engineering-contract.md). Read it before
changing code. Do not restate its rules here — a second copy drifts, and two agents then
follow two different contracts.

It covers: working path constraint, ADR authoring context, AG2 ownership boundary, release
hold, contributor guidance operating system, generated deployment artifact contract,
generated persistence contract, structured-output-first contract rule, contract-declared
customization rule, and decision rules.
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

This repo is pre-1.0 and not in production. **Replace obsolete internal contracts directly by default.** Remove stale shapes, aliases, shims, fallback branches, dual-read/dual-write behavior, normalization of retired names, obsolete prompt guidance, and retired tests in the same migration. Preserve an older shape only when an explicit current external contract or user-approved migration requirement proves it is necessary.

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
  variables. `structured_output` is reserved runtime vocabulary — auto tools
  receive it as a transient read-only projection; declaring it as a context
  variable fails workflow validation.
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
branch from fresh `origin/main` instead. Always work in an isolated worktree
(`git worktree add .local/worktrees/<task-name> origin/main -b cc/<desc>`),
never directly in the shared main checkout — other agents run git commands
there concurrently and will switch branches or sweep in unrelated edits.

**Branch workflow — always use feature branches:**
```bash
git checkout main && git reset --hard origin/main
git checkout -b cc/<description>  # cc/ = Claude Code, codex/ = Codex
# ... work, commit -s ...
git push -u origin cc/<description>
gh pr create --title "..." --body "..."
gh pr merge <number> --squash --delete-branch --auto   # auto-merge is enabled repo-wide; request it right away, don't wait on CI
```

Before opening the PR, run `ruff check .` and `pytest -q --no-cov` locally in
the worktree. If a check still fails, confirm via `git show origin/main:<path>`
whether it's pre-existing on `main` before assuming it's your bug — and if a
check fails identically across multiple unrelated PRs, it's a repo-wide `main`
regression blocking everyone; fix it first with a small isolated hotfix PR.

**Every commit must carry a DCO sign-off — use `git commit -s`, never plain
`-m`.** See [DCO.md](DCO.md). A commit without a `Signed-off-by:` trailer
fails the `dco` check, and that check is *not* in `main`'s required-checks
list — `--auto` can merge right past a failing `dco` check, so a green
auto-merge is not proof you signed off. Forgot on the last commit? Fix it
before pushing with `git commit --amend -s --no-edit`; for several unsigned
commits, `git rebase --signoff origin/main`.

Primary repo ownership (avoids overlap by default):

| Repo | Primary agent |
|------|--------------|
| `mozaiks` (OSS) | Claude Code |
| `mozaiks-app` (hosted product) | Codex |

See `.claude/rules/multi-agent-coordination.md` for full rules.

## Module Contract Rule

When working in or generating modules:

- Only `module.yaml` and `backend/handler.py` are required. All companion manifests
  live under `contracts/` and are optional — include only what the module needs.
- Canonical module shape:
  ```
  modules/{module_id}/
  ├── module.yaml                     ← required: identity, actions, capabilities
  ├── contracts/                      ← optional companion manifests
  │   ├── events.yaml                 ← domain events this module may publish
  │   ├── reactions.yaml              ← event reactions owned by this module
  │   ├── notifications.yaml          ← notification rules per event
  │   ├── settings.yaml               ← user/app settings schema
  │   ├── admin.yaml                  ← admin panels mounted into /admin/*
  │   └── profile.yaml                ← user profile page panels (optional)
  ├── runtime_extensions.yaml         ← optional: api_router / startup_service
  └── backend/
      ├── handler.py                  ← required: thin dispatch, one method per action
      ├── service.py                  ← recommended: business logic and event emission
      ├── repo.py                     ← recommended: MongoDB access layer, no logic
      ├── policy.py                   ← recommended: query scoping for multi-tenancy
      ├── schemas.py                  ← recommended: typed request/response + document shapes
      ├── {helper_files}.py           ← optional: declared, justified, module-local support
      ├── settings.py                 ← optional: settings hooks
      └── admin.py                    ← optional: admin panel hooks
  ```
- `backend/handler.py` is thin dispatch only — one method per declared action, no
  business logic, no `ctx.db`, no `ctx.emit`.
- `backend/service.py`, `backend/repo.py`, `backend/policy.py`, and
  `backend/schemas.py` are the canonical support files for any module with database access.
- Backend helper files are allowed only when declared before generation, kept
  module-local, justified by a specific purpose, and imported by a canonical
  layer or referenced by `runtime_extensions.yaml`.
- `runtime_extensions.yaml` is optional. Use `api_router` only for module-local
  external callback routes, and `startup_service` only for process-lifetime
  module services such as audit/event subscribers or background pollers.
  Two `startup_service` patterns: (1) persistent connection workers (WebSocket,
  broker); (2) background pollers for the `event_pipeline` archetype — use when
  the module needs to detect external state changes (DNS propagation, certificate
  issuance, payment confirmation) and advance a multi-step pipeline automatically.
  Pollers must use `AsyncIOMotorClient` directly (not `app_data_from_context`),
  resolve adapters lazily, and be accompanied by a stub adapter so the pipeline
  runs locally without external infrastructure.
  Internal actions that are only triggered by event reactions must use
  `api_surface: internal` and `permissions: []` — the event bus is the
  authorization boundary.
  Do not use runtime extensions for generic business logic, persistence,
  auth/scope helpers, transport infrastructure, or workflow orchestration.
- App modules publish `domain.*` events. Hosted product modules use `hosted.*`.
  Workflow starts/resumes are resolved by runtime/platform trigger contracts, not by
  hardcoded workflow names in module code.
- Factory workflows such as `AppGenerator` produce these files through
  structured output models, and contributors may author them directly. Keep the
  canonical shapes aligned with runtime loaders, docs, and tests.

## Service Contract Rule

When working in or generating app services:

- `app/services/` is optional app-owned support code. It is not a module system,
  product service layer, persistence plane, security plane, or entitlement
  authority.
- Canonical service shape:
  ```
  services/
  ├── __init__.py                         ← optional Python package marker
  ├── config.py                           ← optional app-owned support config, no secrets
  ├── integrations/                       ← thin clients for external or hosted APIs
  │   ├── __init__.py
  │   └── {service}_client.py
  ├── adapters/                           ← provider-specific implementation mechanics
  │   ├── __init__.py
  │   └── {area}/
  │       ├── __init__.py
  │       └── {provider}.py
  └── routes/                             ← explicit app-level routes only when required
      ├── __init__.py
      └── {route}.py
  ```
- Common adapter areas are `auth/`, `source_control/`, `deployment/`, `dns/`,
  `registrar/`, `cloud/`, `storage/`, `search/`, `email/`, `database/`,
  `secrets/`, and `payments/` when the app itself directly owns that provider
  integration.
- `services/integrations/{pack_id}_client.py` is the lane for managed-capability API
  clients. Pages and app actions should bind to app-owned facade modules, not
  directly to managed-capability internals.
- Authenticated apps declare provider-neutral auth behavior in
  `app/config/auth.yaml`. App identity and coarse `authRequired` stay in
  `app/app.json`; visual login styling stays in `app/brand/theme_config.json`;
  provider mechanics stay in `app/services/adapters/auth/` only when the app
  directly owns that provider integration.
- `services/adapters/{area}/{provider}.py` owns provider mechanics such as SDK
  calls, protocol translation, signing, retries, and response normalization.
  It must not own durable app facts, lifecycle transitions, user-facing actions,
  permissions, emitted events, or persistence authority.
- Do not generate hosted platform provider adapters into customer app bundles.
  Mozaiks-hosted deployment, DNS/domain, billing, wallet, and platform
  operations are consumed through hosted API clients/facade modules and
  host-owned records; provider adapters stay in the hosted product.
- `services/routes/` is only for app-level routes required by a host contract or
  explicit integration boundary. Module-local callback routes should normally
  be declared through that module's `runtime_extensions.yaml`.
- Modules may call service files as implementation details. Service files should
  not import `app.modules`, use `ctx.persistence`, emit events, or dispatch
  module actions.
- Do not generate `app/services/data/` or `app/services/security/`. Data
  contracts live under `app/data/`; secret policy lives at
  `app/security/secrets.yaml`; provider-neutral secret resolution belongs in the
  OSS `mozaiksai.core.secrets` runtime primitive.
- Do not create entitlement grant adapters under services. SaaS plans live in
  `app/config/subscriptions.yaml`, assignment state lives in the configured app
  data alias, and runtime enforcement is handled by the OSS
  `ConfiguredEntitlementAdapter`.

## Generated Secret Contract

App-owned runtime secret output is names-first, not value-first:

- `security/secrets.yaml` is the optional canonical generated contract for
  app-owned secret provider/vault policy, env handles, and secret names.
- It must never contain raw API keys, tokens, passwords, connection strings,
  private keys, webhook secrets, or other credential values.
- Secret policy belongs in `app/security/`; provider-neutral resolution and
  supported secret manager mechanics belong in the OSS `mozaiksai.core.secrets`
  runtime primitive.
- Connector/API-key collection during workflows must store raw credential values
  only through the configured secret backend. App artifacts should carry safe
  metadata and secret references, not raw values.

## Platform Shell Constraints

Do not generate or suggest entries for the following — these are injected by the
platform runtime and must not be declared manually:

- `admin-portal` shortcuts or navigation items in `shell.json` or `route_manifest.json`
- `admin-portal` entries in `extension_registry.json` entrypoints
- Manual `appShell: true` on route manifest entries that already declare `navigation.group`
  (the runtime auto-infers it; explicit repetition is not wrong but is redundant)

These constraints exist because `build_shell_config()` guarantees Admin Portal injection
after the full shell pipeline (`_inject_admin_portal`), and `appShell` is auto-set when
`navigation.group` is present. Generators that emit these fields create duplicates or
override the runtime guarantee.

## Generator Output Rule

Shared factory workflows live in `factory_app/workflows/`. Generator output must
not land directly in active runtime paths.

Workflow resolution is single-root by contract. A running host binds to one
workflow root via `MOZAIKS_WORKFLOWS_PATH` rather than auto-merging app and
factory roots. Studio defaults to `factory_app/workflows/`; product/app hosts
prefer the workspace root's `workflows/`. The first-party
`factory_app/app` bundle should not check in a nested workflows directory.
External hosted product workspaces define app-local workflows under
`workflows/`, beside `app/`.

Use `MOZAIKS_GENERATED_ARTIFACTS_PATH`, defaulting to:

```text
generated/
```

Canonical generated paths:

```text
generated/apps/{app_id}/{build_id}/app/
generated/workflows/{app_id}/{build_id}/{workflow_name}/
```

Only explicit promotion may copy validated artifacts into an active app root.

## Workflow Contract Rule

When working in or generating workflows:

- Canonical workflow shape:
  ```
  workflows/{workflow_name}/
  ├── orchestrator.yaml           ← required: bootstrap, entry point, constraints
  ├── agents.yaml                 ← required: agent roster and prompts
  ├── transition_graph.yaml               ← required: agent routing and transitions
  ├── context_variables.yaml      ← required: initial/default shared state schema
  ├── structured_outputs.yaml     ← required: output models + agent→model registry
  ├── tools.yaml                  ← required: tool bindings + UI metadata
  ├── ui_config.yaml              ← required: `visual_agents` contract for websocket-visible agents
  ├── middleware.yaml                  ← optional: lifecycle hooks
  ├── extended_orchestration/
  │   └── task_batches.yaml       ← optional: workflow-local AG2 task batch config
  ├── tools/
  │   ├── __init__.py
  │   └── *.py
  └── ui/{workflow_name}/         ← optional: React UI components for artifacts
      └── components/
  ```
- `ui_config.yaml` must declare `visual_agents`. Only agents listed there have messages and UI-bearing outputs streamed through the websocket to the user-facing UI.
- `middleware.yaml` and `extended_orchestration/task_batches.yaml` are canonical workflow surfaces when lifecycle hooks or workflow-local task batches are needed.
- `workflows/extended_orchestration/extension_registry.json` is single-root by
  default. App/product overlays can explicitly extend
  `mozaiks.default_workflow_registry`; only then may the runtime inherit default
  registry entries and resolve inherited factory workflow folders from
  `factory_app/workflows/`.
- Treat workflow YAMLs as structured-output-first contracts. They should map cleanly to strict models that generators can emit and runtime code can validate deterministically.
- Tools stay dumb. Reasoning belongs in prompts and structured outputs, not in Python tool code.

## UI System Rule

Treat the UI system as separate surface contracts sharing one primitive/design foundation:

1. `App UI` — schema-driven page primitives rendered by `SchemaPage`
2. `Agent UI tools` — event-driven React surfaces that compose shipped primitives
3. `Transition UI` — router/session components with routing-specific props
4. `Core shell pages` — first-class framework pages registered in `coreComponents.js`

Do not collapse these into one generic contract.

## Validation Rule

For runtime, generator, orchestration, or contract changes:

- run targeted tests
- update docs
- prefer at least one real runtime smoke when practical

## Autonomy

Bias toward action. Infer reasonable implementation details from the repository and carry
the work through rather than interrupting to confirm. The repo — its canonical
implementations, tests, ADRs, and git history — is the authority on architecture, not the
prompt that sent you. When the prompt and the repo disagree about how something is built,
read the code and believe the code.

Stop and ask ONLY when the ambiguity would materially change one of:

- a public contract or API shape
- a security or authorization boundary
- a persistence format or migration path
- an architectural decision that is expensive to reverse

Everything else: decide, implement, and report the decision in the deliverable. A question
the repository can answer is not a question for the requester.

## Investigate Before Changing

Before editing code: fetch current `origin/main`, read the applicable repository
instructions, locate the canonical implementation and its tests, inspect git history where
architectural intent is unclear, and determine whether the change is already partially
implemented. Do not assume architecture from the prompt when the repository can answer it.

## Verification Proportionality

Run the tests and checks proportionate to the affected surface. Do not re-run broader
suites after the relevant checks are green unless a failure or an unresolved concern
justifies it. Conversely, a change to a shared contract is not covered by its own unit
test — run what the blast radius requires.

## Deliverable

Report these, in this order, on any change that touches code:

1. Verdict — done / blocked / partial
2. Base SHA the work started from
3. Branch and PR
4. Files changed
5. Architectural decisions made, and what was rejected
6. Tests and checks run, with exact results — not "tests pass"
7. Unresolved risks
8. Whether the PR is safe to merge, and why

Claims must be checkable from the diff or from pasted command output. A green suite that
was never run against the changed surface is not evidence.
