# End-to-End Build Lifecycle

## Purpose

This document defines the canonical lifecycle across:

- Mozaiks CLI
- the internal Studio host, its visible Studio surface, and the workflow-owned build sequence
- `factory_app`
- the active app workspace
- generated artifacts
- hosted product promotion/export

Terminology note:

- `Studio` is the browser product and `studio` is the host/composition name
- `mozaiks studio` is the current public CLI command for opening Studio
- customer-facing UX should prefer `Apps`, `Usage`, `Health`, and
  `Integrations` for the OSS factory Studio; hosted deployments may add their
  own provider-owned billing or hosting sections
- `Build` refers to the workflow-owned agent sequence for create and
  refinement, not a required persistent Studio page
- when this document says `Studio host`, it means the management composition
  layer serving the Studio product

The current system has the right primitives, but the lifecycle is not explicit
enough. That leads to confusion about:

- whether `init` creates a real app or just a scaffold
- whether Studio and the workflow-owned build sequence are operating
  on the active workspace or on staged artifacts
- when `factory_app` should mutate the live app root
- how generated output becomes the app that later runs

This document is the source of truth for that lifecycle.

## Core Principle

The active app workspace and the generated build output are not the same thing.

Use this mental model:

```text
workspace scaffold
  -> holds the active app shell and local config

Studio + factory_app workflows
  -> interpret intent and generate staged artifacts

generated artifact bundle
  -> reviewable build output, versioned by app_id/build_id

promotion
  -> explicit copy of approved artifacts into a runnable app root
```

In current code, the CLI creates the workspace scaffold and launches
Studio. Studio creates apps and manages build/review work inside that
workspace.

The generator should stage first and promote second.

## The Lifecycle

### Phase 0: Environment Setup

This phase configures Mozaiks itself, not the user app.

Owned by:

- CLI

Responsibilities:

- choose provider/model defaults
- set validation strategy
- choose local vs hosted posture
- choose default workspace root
- make sure required infra/env vars exist

Recommended command:

- `mozaiks onboard`

This is the preferred public entrypoint.

## Phase 1: Workspace Bootstrap

This phase creates a minimal app workspace scaffold.

Owned by:

- CLI

Responsibilities:

- create `app/app.json`
- create `app/config/ai.json`
- create `app/config/shell.json`
- create `app/brand/theme_config.json`
- create `app/ui/index.js`
- create `app/ui/route_manifest.json`
- create empty `app/modules/` and `workflows/`

Important rule:

- this scaffold is not the generated app
- it is the hostable workspace shell the platform can run against

Recommended commands:

- `mozaiks init <preset>` for explicit/dev scaffolding
- `mozaiks onboard` may create this implicitly when missing

## Phase 2: Studio Launch

This phase starts the host and opens the management UI.

Owned by:

- CLI for process launch
- Studio for lifecycle management UI

Responsibilities:

- select the active workspace
- boot backend host
- boot frontend shell
- open the current management surface

Recommended command:

- `mozaiks studio --open`

This is the current user-facing command path. Customer-facing UX should normalize to
`Apps` as the landing surface and route build/refinement through the
workflow-owned agent sequence rather than a persistent `Build` page.

Low-level equivalents:

- `mozaiks serve <workspace> --host studio`
- repo dev scripts such as `run-backend.ps1` and `run-frontend.ps1`

Important rule:

- repo dev scripts are framework-development tools, not the primary product UX

## Phase 3: Build Registry Record

This phase creates a durable hosted build/app record before deep generation.

Owned by:

- hosted product control plane
- invoked by `ValueEngine`

Responsibilities:

- allocate `build_registry_id`
- associate `app_id`, `user_id`, app name, and initial status
- expose build status to the `Apps` directory, app Studio summaries, and the
  workflow-owned build sequence
- create an idempotent provisional record as soon as the workflow chat starts,
  before the user completes the ValueEngine interview, so unfinished work can be
  resumed from `/apps`

Important rule:

- this is a hosted control-plane record
- it is not the generated app bundle itself

## Phase 4: Intent Decomposition

This phase decomposes user intent into planning and realization artifacts.

Owned by:

- `factory_app`

Workflow ownership:

1. `ValueEngine`
   - concept capture
   - capability-pack hints
   - surface candidate hints
2. `DesignDocs`
   - final `surface_map`
   - ownership/event/page boundary decisions
3. `SubscriptionContractDesigner`
   - generic SaaS subscription/token contract
   - `config/subscriptions.yaml` intent when the generated app sells plans
   - module entitlement gates and workflow metering declarations
   - no-op contract for non-SaaS apps
4. `AgentGenerator`
   - workflow-only surfaces
   - `workflow_stages`, tools, workflow-local UI
5. `AppGenerator`
   - app schema
   - module/control-plane/integration build tasks
   - layered module backend contracts

Important rule:

- decomposition decisions belong to `factory_app`, not to ad hoc logic in the
  CLI or hosted app shell
- when later refinement re-enters one of these workflows, the control-plane
  harness should preserve the shared refinement-context contract
  (`change_class`, `refinement_request`, `change_intent`, `impact_set`, and
  related artifact metadata) instead of relying on transcript reconstruction

## Phase 5: Staged Artifact Generation

This phase writes generated app output to a staging directory.

Owned by:

- `factory_app`

Current output root:

- `generated/apps/{app_id}/{build_id}/app`

This phase may write:

- `app.json`
- `ui/pages/*.yaml`
- `ui/route_manifest.json`
- `ui/pages/custom/*.jsx`
- `ui/index.js`
- `brand/theme_config.json`
- `config/shell.json`
- `config/subscriptions.yaml` when `SubscriptionContractDesigner` produced a
  required SaaS subscription/token contract
- `data/contract.json`
- optional `data/migrations/*.json`
- generated module files
- provider-neutral deployment artifacts (optional): `Dockerfile`,
  `docker-compose.yml`, `.github/workflows/readiness.yml`,
  `.github/workflows/deploy.yml`, `.env.example`, `.env.staging.example`,
  `.env.production.example`, and
  `deployment.manifest.json`

Important rule:

- generation should never mutate the live app root by default
- the staged bundle is the reviewable build artifact
- deployment artifacts are deterministic outputs from the provider-neutral
  generated-app deployment contract and must not contain real secrets
- deployment artifacts are emitted by the download/export deployment renderer,
  not by `service_foundation`, `api_surface`, or `app/services/adapters/` build
  tasks
- workflow UI code generation should stay deterministic: shared shipped workflow
  primitives do not produce workflow-local React files, while genuine
  workflow-local components are staged under the workflow `ui/` tree and get a
  synthesized `ui/index.js` barrel during assembly

## Phase 6: Review And Validation

This phase pauses the build sequence for user review before promotion.

Owned by:

- the `app_review` transition in `extension_registry.json` (build sequence terminal step)
- `AppReview` workflow and the `AppReviewSummary` in-chat UI artifact
- Studio artifact promotion endpoint
- `app_registry` module lifecycle update
- refinement control plane (revision path)

Responsibilities:

- surface build output summary, validation strategy used, and integration check results
- show sandbox preview URL when E2B or local npm validation ran
- present Promote and Revise paths to the user
- persist the active `current_build_run` on the app registry record, including
  the workflow sequence, active chat/workflow, staged bundle path, and app-bundle
  `artifact_version_id` when one was registered
- preserve a compact `build_context_profile` on the app registry record so
  Continue Build and refinement re-entry can reason about the selected build
  contexts/packs without copying raw prompt catalogs or provider secrets
- on Promote: call Studio artifact promotion for `artifact_version_id`, restore
  the reviewed bundle into the active app root, and transition
  `lifecycle_state` from `review` to `active`
- on Revise: accept user revision request via chat, route through the refinement
  control plane, operate on the staged `generated/` bundle path (not the active workspace)

How the pause works:

- `AppGenerator` terminates its AG2 session after writing the bundle, registering
  the app-bundle artifact version, and calling `update_build_status(status="review")`
  with the staged bundle path and artifact version
- `AppGenerator` sets `lifecycle_state=review`, `bundle_path`, and
  `artifact_version_id` in AG2 context_variables before terminating
- the build sequence advances to the `app_review` chat-session transition,
  which starts the `AppReview` workflow
- `ReviewAgent` presents the `AppReviewSummary` artifact as the HITL boundary;
  the user decides to promote or request revisions from that chat session
- revision requests re-enter the refinement router with `artifact_key`,
  `artifact_version_id`, `source_surface=app_review`, and staged-bundle
  metadata so `artifact_root` can be set to the generated bundle path
- when the refinement router returns a confirmation or clarification decision,
  the chat shell renders the existing pending harness decision panel in-place;
  workflow launches only proceed after the user selects a decision action

App lifecycle state:

- `building` — generation in progress
- `review` — bundle staged, awaiting user decision (promote or revise)
- `active` — bundle promoted; this is the runnable state

Canonical frontend/workflow UI smoke targets:

- `factory_app/workflows/RuntimeUIPrimitiveSmoke`
- `factory_app/workflows/AgentGenerator`

Important rule:

- canonical review/promotion must flow through the `app_review` transition and
  Studio artifact promotion endpoint, not through ad hoc CLI commands or side effects
- Studio artifact promotion validates the matching `app_registry` record is in
  `review`, restores the reviewed artifact version, and then invokes the module
  lifecycle update to mark the app `active`
- AppGenerator download zips may contain a single top-level bundle folder such
  as `GeneratedApp/`; promotion strips that wrapper only when it reveals an
  app-root bundle containing `app.json`, so the active root receives
  `app.json`, `config/`, `modules/`, and `ui/` directly
- revision requests from `app_review` operate on the staged `generated/` bundle,
  not the active workspace; `artifact_root` in the context seed is the guard for this
- AppReview revision payloads must preserve `artifact_key`, `artifact_version_id`,
  `source_surface`, lifecycle state, and `bundle_path`; dropping those fields
  causes the control plane to lose reviewed-bundle provenance
- AppReview-triggered `harness_decision` responses must be surfaced through the
  shared pending harness decision UI; they must not be silently ignored by the
  websocket bridge

Canonical frontend/workflow UI smoke targets:

- `factory_app/workflows/RuntimeUIPrimitiveSmoke`
- `factory_app/workflows/AgentGenerator`

Deployment contract reference:

- [../deployment/generated-app-deployment-contract.md](../deployment/generated-app-deployment-contract.md)

Use it when validating changes to:

- AgentGenerator workflow UI planning
- `ui.workflow_primitive` manifest contracts
- `ui.realization` workflow UI assembly contracts
- `chat.tool_call` / `tool_call_response` workflow UI transport
- workflow-local React component generation rules
- real AG2 multi-turn workflow generation and review flows

Recommended smoke command:

```bash
python scripts/run_live_workflow_smoke.py \
  --workflow RuntimeUIPrimitiveSmoke \
  --workflows-root factory_app/workflows \
  --tool-response-file factory_app/workflows/RuntimeUIPrimitiveSmoke/smoke_responses.json

python scripts/run_live_workflow_smoke.py \
  --workflow AgentGenerator \
  --workflows-root factory_app/workflows \
  --prompt-file factory_app/workflows/AgentGenerator/smoke_prompt.txt \
  --tool-response-file factory_app/workflows/AgentGenerator/smoke_responses.json \
  --timeout-seconds 300
```

## Phase 7: Promotion

This phase copies approved artifacts into a runnable app root.

Owned by:

- Studio host / control plane
- optionally CLI for advanced users

Promotion source:

- staged generated app bundle

Promotion target:

- active workspace app root
- export workspace
- hosted deployment bundle

Current implementation:

- Studio `/api/studio/build/artifacts/{artifact_version_id}/promote` endpoint -
  restores the accepted app-bundle artifact version into the active app root
- `app_registry.promote_build` module action - enforces the `review -> active`
  state guard and emits `domain.app_registry.app_promoted`
- called by the `AppReviewSummary` in-chat artifact with both
  `artifact_version_id` and `build_registry_id` when the user clicks Promote

Important rule:

- promotion is explicit — it is not an incidental side effect of generation
- promotion only runs when `lifecycle_state == "review"`
- promotion is the step that transitions `lifecycle_state` to `active`; generation alone
  does not make an app runnable

## Phase 8: Runtime / Deployment

This phase runs or exports the approved app.

Owned by:

- runtime host
- deployment/export tools
- hosted product modules for registry/hosting/deployment

Possible targets:

- local runtime
- downloadable zip bundle
- GitHub export
- hosted deployment pipeline

Important rule:

- running the promoted app is separate from generating it

## Objects In The System

Use these terms consistently.

| Object | Meaning |
| --- | --- |
| `workspace root` | Local folder selected by CLI or the current Studio host. |
| `app root` | The runnable app bundle directory, usually `<workspace>/app`. |
| `build_registry_id` | Hosted control-plane/build-tracking record id. |
| `app_id` | Logical app identity used across workflows and artifacts. |
| `build_id` | Specific build/run identity for staged generation output and lifecycle events. For routed workflow sequences this is the active `journey_instance_id`. |
| `journey_instance_id` | Runtime sequence instance id shared across all chats in the same authored workflow sequence. |
| `generated_app_dir` | Staged artifact directory under `generated/apps/.../app`. |
| `promoted app` | App root after explicit promotion from staged artifacts. |

## Build Lifecycle Events

Runtime lifecycle hook events emitted from build workflows are sequence-scoped.

- `build_started` is emitted from the first workflow step in the active sequence.
- `build_completed` is emitted from the terminal workflow step in the active sequence.
- `build_failed` may be emitted by any workflow that belongs to the active sequence.
- Event payload `buildId` uses the active `journey_instance_id` when the workflow is part of a routed sequence.
- Event payload `buildRegistryId` is included separately once the hosted control-plane record exists.

This keeps lifecycle reporting aligned with the full routed build journey instead
of treating each workflow chat id as an independent build.

## Recommended CLI Model

The CLI should expose two different paths.

### Public Path

For most users:

1. `mozaiks onboard`
2. `mozaiks studio --open`
3. launch the build workflow sequence from Studio
4. review staged artifacts
5. promote/export/deploy

This path should hide framework details such as:

- `PLATFORM_PATH`
- `MOZAIKS_APP_WORKSPACE_PATH`
- direct `run-backend.ps1` / `run-frontend.ps1`
- manual distinction between `factory_app` and active workspace internals

### Power / Dev Path

For framework contributors and advanced users:

1. `mozaiks init <preset>`
2. `mozaiks onboard`
3. `mozaiks serve <workspace> --host studio`
4. or repo dev scripts

This path is allowed to expose more internal mechanics.

## Command Responsibilities

### `mozaiks init`

Should:

- create a workspace scaffold only

Should not:

- imply that generation already happened
- launch Studio automatically unless explicitly requested by a flag

### `mozaiks onboard`

Should:

- configure environment and product intent defaults
- create a scaffold when missing
- optionally offer to open Studio immediately

### `mozaiks studio`

Should:

- become the primary entrypoint for actual building
- start backend + frontend together
- open the browser
- route the user into the current app or workspace Studio surface and launch the
  workflow-owned build sequence from there

### `mozaiks gen`

Should:

- stay a convenience shortcut
- reuse the same canonical workflow contracts

Should not:

- become a parallel artifact-management surface

## Gaps To Close

These are the remaining lifecycle gaps.

1. There is no explicit user-facing contract saying that the scaffold is only a
   shell and that generated output stages separately.
2. The preferred public path still feels too dev-script-centric.
3. Promotion exists conceptually, but Studio/CLI responsibilities
   around it are not yet the dominant UX.
4. External hosted product workspaces should consume the same staged
  build/promotion lifecycle instead of inventing a second builder path.
5. `mozaiks gen`, `mozaiks onboard`, and `mozaiks studio` still need one
   coherent story rather than three adjacent tools.

## Recommended Next Changes

1. Make `mozaiks onboard` the primary first-run command.
2. Add `mozaiks studio --open` as the standard builder launch command.
3. Teach onboarding and Apps/Build status surfaces to explain:
   - scaffold
   - staged build
   - promotion
4. Add first-class review/promotion UX in Build around `generated_app_dir`.
5. Keep repo dev scripts as framework tooling only.
6. Align hosted product modules (`app_registry`, `hosting`, etc.) to the same
   build registry and staged artifact lifecycle.

## Relationship To Other Docs

- `docs/architecture/frontend/ui-system/generated-frontend-surface-contract.md`
  - defines persistent frontend surface contracts and realization boundaries
- `data-contract-and-revision-contract.md`
  - defines canonical data contract, staged database artifacts, and revision-time migration rules
- external hosted product workspace docs (outside this repo)
  - may define hosted-only boundaries built around this lifecycle

This document defines the lifecycle that those documents assume.



