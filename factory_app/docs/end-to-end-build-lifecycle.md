# End-to-End Build Lifecycle

## Purpose

This document defines the canonical lifecycle across:

- Mozaiks CLI
- Studio
- `factory_app`
- the active app workspace
- generated artifacts
- hosted product promotion/export

The current system has the right primitives, but the lifecycle is not explicit
enough. That leads to confusion about:

- whether `init` creates a real app or just a scaffold
- whether Studio is operating on the active workspace or on staged artifacts
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
- create empty `app/modules/` and `app/workflows/`

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
- open `/studio`

Recommended command:

- `mozaiks studio --open`

This should become the standard user path.

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
- expose build status to Studio/hosted product UI

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
3. `AgentGenerator`
   - workflow-only surfaces
   - `workflow_stages`, tools, workflow-local UI
4. `AppGenerator`
   - app schema
   - module/control-plane/integration build tasks
   - layered module backend contracts

Important rule:

- decomposition decisions belong to `factory_app`, not to ad hoc logic in the
  CLI or hosted app shell

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
- `config/database_intent.json`
- optional `config/database_migrations/*.json`
- generated module files

Important rule:

- generation should never mutate the live app root by default
- the staged bundle is the reviewable build artifact
- workflow UI code generation should stay deterministic: shared shipped workflow
  primitives do not produce workflow-local React files, while genuine
  workflow-local components are staged under the workflow `ui/` tree and get a
  synthesized `ui/index.js` barrel during assembly

## Phase 6: Review And Validation

This phase determines whether staged artifacts are acceptable.

Owned by:

- Studio
- validation tools
- optionally CLI for terminal-oriented users

Responsibilities:

- show build output summary
- show generated files and diffs
- run validation strategy
- expose build status
- allow approval, rejection, or refinement
- run the workflow/frontend acceptance smoke when generator or workflow UI
  contracts changed

Important rule:

- `mozaiks gen` may remain a convenience path
- but canonical review/history/diff/promotion must live in Studio

Canonical frontend/workflow UI validation targets:

- `factory_app/workflows/WorkflowPrimitiveAcceptance`
- `factory_app/workflows/AgentGenerator`

Use it when validating changes to:

- AgentGenerator workflow UI planning
- `ui.workflow_primitive` manifest contracts
- `ui.realization` workflow UI assembly contracts
- `chat.tool_call` / `tool_call_response` workflow UI transport
- workflow-local React component generation rules
- real AG2 multi-turn workflow generation and review flows

Recommended smoke command:

```bash
python scripts/run_live_mfj_smoke.py \
  --workflow WorkflowPrimitiveAcceptance \
  --workflows-root factory_app/workflows \
  --tool-response-file factory_app/workflows/WorkflowPrimitiveAcceptance/smoke_responses.json

python scripts/run_live_mfj_smoke.py \
  --workflow AgentGenerator \
  --workflows-root factory_app/workflows \
  --prompt-file factory_app/workflows/AgentGenerator/smoke_prompt.txt \
  --tool-response-file factory_app/workflows/AgentGenerator/smoke_responses.json \
  --timeout-seconds 300
```

## Phase 7: Promotion

This phase copies approved artifacts into a runnable app root.

Owned by:

- Studio/control plane
- optionally CLI for advanced users

Promotion source:

- staged generated app bundle

Promotion target:

- active workspace app root
- export workspace
- hosted deployment bundle

Current implementation concept:

- `promote_generated_app(source_dir, target_root)`

Important rule:

- promotion is explicit
- it is not an incidental side effect of generation

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
| `workspace root` | Local folder selected by CLI or Studio. |
| `app root` | The runnable app bundle directory, usually `<workspace>/app`. |
| `build_registry_id` | Hosted control-plane/build-tracking record id. |
| `app_id` | Logical app identity used across workflows and artifacts. |
| `build_id` | Specific build/run identity for staged generation output. |
| `generated_app_dir` | Staged artifact directory under `generated/apps/.../app`. |
| `promoted app` | App root after explicit promotion from staged artifacts. |

## Recommended CLI Model

The CLI should expose two different paths.

### Public Path

For most users:

1. `mozaiks onboard`
2. `mozaiks studio --open`
3. build in Studio
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
- route the user into `/studio/create`

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
3. Promotion exists conceptually, but Studio/CLI responsibilities around it are
   not yet the dominant UX.
4. `mozaiks-app` and hosted product behavior should consume the same staged
   build/promotion lifecycle instead of inventing a second builder path.
5. `mozaiks gen`, `mozaiks onboard`, and `mozaiks studio` still need one
   coherent story rather than three adjacent tools.

## Recommended Next Changes

1. Make `mozaiks onboard` the primary first-run command.
2. Add `mozaiks studio --open` as the standard builder launch command.
3. Teach onboarding/studio status screens to explain:
   - scaffold
   - staged build
   - promotion
4. Add first-class review/promotion UX in Studio around `generated_app_dir`.
5. Keep repo dev scripts as framework tooling only.
6. Align hosted product modules (`app_registry`, `hosting`, etc.) to the same
   build registry and staged artifact lifecycle.

## Relationship To Other Docs

- `surface-realization-refactor.md`
  - defines decomposition and `surface_kind`
- `database-intent-and-revision-contract.md`
  - defines canonical database intent, staged database artifacts, and revision-time migration rules
- `mozaiks-app/docs/hosted-platform-rebuild.md`
  - defines hosted product boundaries built around this lifecycle

This document defines the lifecycle that those documents assume.
