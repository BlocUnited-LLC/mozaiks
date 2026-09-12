# Mozaiks Agent Engineering Contract

Canonical engineering rules shared by every coding agent working in this repository,
regardless of which agent or model is running.

`AGENTS.md` (Codex entry point) and `CLAUDE.md` (Claude Code entry point) both point
here. Agent-specific execution guidance stays in those files; the architectural rules
below live here once, so the two entry points cannot drift apart and hand different
rules to different agents.

Changing a rule here changes it for every agent. Do not copy a rule from this file back
into an entry point.

## Working Path Constraint

**Always work on `C:\Repos\BlocUnitedRepo\mozaiks` (this repo) and `C:\Repos\BlocUnitedRepo\mozaiks-app`.**
Never read from or write to OneDrive paths (`C:\Users\...\OneDrive\...`). Those are stale copies, not the working repos.

**Read [ARCHITECTURE.md](ARCHITECTURE.md) first.** That file is the source of truth for how the system works.

This repo uses layered FastAPI hosts as the canonical OSS server composition:
- `mozaiksai.hosts.runtime`
- `mozaiksai.hosts.platform`
- `mozaiksai.hosts.studio`

`mozaiksai.hosts.studio` is the Studio management interface host and the default local run target. Studio is the shared management layer — available in both local and hosted deployments. Hosted product repos compose their own app-local hosts on top of Studio; this OSS repo does not own a hosted-product FastAPI host.

**CLI and Studio are parallel interfaces**, not a superset chain. CLI owns developer tooling (filesystem, scaffolding, process management). Studio owns the management interface (workspace status, build lifecycle, artifacts, run history, config). Do not conflate them.

Profile stays person-scoped. Studio / Workspace Shell is the org/workspace home base. App shells remain separate and brandable per app; do not collapse org management into `/me`.

**`mozaiks gen` is a developer convenience**, not the canonical build lifecycle. Do not expand CLI commands to duplicate Studio surfaces (artifact review, diff, run history, promotion, build state). Those belong in Studio. The CLI hands off to Studio — it does not grow a parallel project-management surface.

The current repo layout is transitional. The canonical target architecture is
documented in
[docs/architecture/foundations/distribution-and-workspace-model.md](docs/architecture/foundations/distribution-and-workspace-model.md).
Do not reintroduce a hybrid root that mixes the starter app bundle with shared
factory workflows.

## ADR Authoring Context

For an ADR that changes or extends app generation, semantic authority,
compilation/materialization, or refinement, review
[issue #411](https://github.com/BlocUnited-LLC/mozaiks/issues/411) and state
explicitly whether the ADR adopts, modifies, rejects, or defers that direction.

Issue #411 is a design prompt, not architecture authority. Current source and
Accepted ADRs remain authoritative. Any ADR using the issue must verify its
claims against the repository and must not make a typed decision ledger a
second authority for application meaning.

## AG2 Ownership Boundary

Mozaiks uses AG2 as the long-term backbone for agentic execution. Do not build
a parallel agent framework in this repo when AG2 already owns the concept or is
the right upstream home for it. See
[docs/architecture/workflows/ag2-ownership-boundary.md](docs/architecture/workflows/ag2-ownership-boundary.md)
for the durable architecture contract and upgrade watchpoints.

AG2 should own:

- agent primitives, model calls, tools, middleware, and task execution
- multi-agent network mechanics, Hub/AgentClient behavior, channels, adapters,
  and workflow state progression
- task delegation, task lifecycle observation, event mirroring, and generic
  agent runtime observability

Mozaiks should own:

- declarative workflow contracts and strict structured-output validation
- canonical generated app, workflow, module, page, persistence, and secret
  artifact shapes
- app/runtime persistence, transport integration, tenant/session boundaries,
  Studio/platform lifecycle, and artifact promotion
- deterministic factory refinement policy and decomposition contracts that
  define what work must be executed by AG2 agents

When a needed capability is missing from AG2, inspect AG2's current docs, APIs,
and source direction before adding runtime code here. Keep Mozaiks custom logic
as a small adapter or contract layer around AG2 — preferably behind
`mozaiksai.core.adapters` or a similarly narrow boundary, document the divergence, and
track it as an AG2 compatibility watchpoint. If the missing capability is
generic agent orchestration rather than Mozaiks-specific contract enforcement,
prefer communicating the need upstream to the AG2 team instead of growing a
permanent Mozaiks substitute.

Do not introduce Mozaiks-owned replacements for AG2 Hub, AgentClient, network
adapters, task streams, task observation, delegation engines, or generic
agent scheduling unless there is no viable AG2-aligned implementation path and
the boundary is documented before or with the code change.

## Release Hold

Do **not** publish this repo yet.

- Do not create or push `v*` Git tags (`v0.1.0`, `v1.0.0`, any of them).
- Do not trigger `.github/workflows/release.yml`.
- Do not publish to PyPI or create a GitHub release.
- Do not treat trusted-publisher or GitHub environment setup as approval to
  release. A configured PyPI trusted publisher or a GitHub `pypi` environment
  is not permission.
- Do not bump `mozaiksai/version.py` for a public release unless the user
  explicitly says the repo is production-ready and wants to publish.

Normal code pushes are fine. Public release actions are not.

## Contributor Guidance Operating System

For nontrivial OSS changes:

- choose the closest active task skill before editing. Codex-facing skills live
  under `.agents/skills/`; Claude Code-facing skills live under
  `.claude/skills/`.
- use `oss-contribution-review` when scope spans layers or the right skill is
  unclear
- include the appropriate impact section from `.claude/rules/testing.md` in the
  final report and always list tests run

## Generated Deployment Artifact Contract

Generated deployment artifacts are provider-neutral app-bundle root files:

- `Dockerfile`
- `docker-compose.yml`
- `env.example`
- `deployment.manifest.json`
- `.github/workflows/deploy.yml`

These files describe how the app runs and which env/CI secret names are
expected. They must never contain raw secrets, cloud tenant ids, hosted product
policy defaults, or provider execution code.

AppBuildPlan build tasks must not own these paths. They are emitted by the
DownloadAgent through the `generate_and_download` deployment contract renderer
using `deployment_profile`, `include_dockerfiles`, `include_workflow`, and
`include_compose`. Hosted products consume the manifest and apply provider
policy, secret delivery, DNS, and deployment adapters outside the generated app
bundle.

## Generated Persistence Contract

AppGenerator persistence output is data-contract-first, not runtime-DB-first:

- `data_contract` is the canonical generated data planning object.
- Generated app bundles write it to `data/contract.json`.
- Additive refinement plans belong under
  `data/migrations/{migration_id}.json`.
- Persistent modules use `backend/repo.py`, `backend/policy.py`, and
  `backend/schemas.py`; do not generate `backend/models.py` or
  `backend/models/*.py`.
- Do not generate `backend/database/schema.json` or
  `backend/database/seed.json`.
- Do not put database access in `handler.py`, and do not put raw persistence
  operations in `service.py`.
- Runtime injects `ctx.persistence` into `ModuleContext` when `app_id` exists.
  Generated `backend/repo.py` must use
  `ctx.persistence.collection(module_id, entity_name)`.
- `data/contract.json` also covers cross-module aggregate ownership and explicit
  existing database integration when needed.
- External database provider mechanics, when explicitly required, belong under
  `app/services/adapters/database/`. Do not generate `app/services/data/` for
  customer apps.
- Do not generate Python helper files under `data/` or `security/`. Those app
  planes are declarative: data contracts/migrations and names-only secret
  policy.
- `ctx.db` remains absent and non-canonical; generated code must not require or
  emit it.

## Structured-Output-First Contract Rule

Canonical YAML contracts in Mozaiks are **structured-output-first contracts**.
They are not prose-first configuration files that agents happen to write.

- Every canonical YAML shape must be representable as a strict structured
  output model before it is treated as a runtime or generator contract.
- If a YAML shape cannot be generated repeatably and validated
  deterministically from typed structured output, it is not ready to become a
  canonical Mozaiks contract.
- Shared taxonomies such as event namespaces, target kinds, capability kinds,
  setting types, and admin panel kinds must use explicit reusable fields/enums,
  not ad hoc freeform strings scattered across prompts.
- When a contract changes, update the structured output model, generator
  prompts, runtime validation/loaders, docs, and tests together.

Use this standard for `module.yaml`, `contracts/events.yaml`, `contracts/reactions.yaml`,
`contracts/profile.yaml`, `contracts/notifications.yaml`, `contracts/settings.yaml`,
`contracts/admin.yaml`,
workflow YAMLs, page schemas, and any future declarative contracts.

## Contract-Declared Customization Rule

Mozaiks must allow customization, but customization is an extension of the
contract, not an escape hatch from it.

- YAML may reference bounded helper/customization stubs only through explicit
  contract fields with a defined schema and loader behavior.
- Python stubs extend backend/runtime-adjacent behavior; JS/TS stubs extend UI,
  admin, or workflow-facing frontend behavior.
- Referenced stubs must stay local to the declared app/module/workflow
  boundary and must not invent undeclared fields, side channels, or alternate
  schemas.
- Agents generating these contracts must understand both halves of the shape:
  the YAML contract and the stub entrypoint it references.
- If a customization point is generator-facing, its prompt and structured
  output model must define the exact allowable reference shape and when the
  stub is required vs optional.

## Decision Rules

When adding code, decide placement in this order:

1. Is this required for every runtime instance and independent of app semantics? → **Runtime**.
2. Is this generic Refinement Engine behavior over execution contexts, state, events, and routing? → **`mozaiksai/control_plane`**.
3. Is this app hosting, routing, sessions, pages, modules, shell config, or app workspace composition? → **Platform**.
4. Is this workspace management, build lifecycle, artifact review, run history, or configuration UI? → **Studio**.
5. Is this first-party builder behavior, app generation logic, or builder-specific harness configuration? → **`factory_app`**.
6. Is this hosted-only capability such as collaboration, billing, marketplace, deployment, or org management? → **Mozaiks App**.
7. Is this filesystem scaffolding, process management, or terminal diagnostics? → **CLI**.

Key: a feature is not CLI just because it runs locally. If it is management UI, it belongs in Studio. If it is generic intent routing across execution contexts, it belongs in the harness implementation. If it is builder-specific policy, it belongs in the factory harness pack.
