# Orchestration Control Loops

This document defines the canonical orchestration model for Mozaiks.

The core rule is simple:

- Mozaiks does not have one global chat loop.
- Mozaiks has three separate control loops.
- Each loop owns different state, limits, resume behavior, and routing decisions.

The builder-session loop is app-configurable through `app/config/ai.json`:

- `control_plane.enabled`
- `control_plane.classifier.enabled`
- `control_plane.classifier.llm_config`
- `control_plane.coding.enabled`
- `control_plane.coding.llm_config`

Those settings belong to the control plane. They are not workflow-local AG2
handoff settings.

The first-party declarative pack for those settings lives under:

- `factory_app/control_plane/config/*`
- `factory_app/control_plane/prompts/*`
- `factory_app/control_plane/tools/*`

For the target package split, declarative pack shape, and implementation
checklist for this harness, see
[Control-Plane Harness Architecture](control-plane-harness-architecture.md).

If those loops are collapsed together, AG2 handoffs, build sequencing,
refinement routing, and coding-agent repair all become harder to reason about.

## The Three Control Loops

### 1. Workflow execution loop

This is the AG2-owned loop inside one workflow run.

Examples:

- `ValueEngine`
- `DesignDocs`
- `AgentGenerator`
- `AppGenerator`
- any app-local workflow under `app/workflows/*`

This loop owns:

- AG2 handoffs
- agent turn-taking
- `max_consecutive_auto_reply`
- `max_rounds` or equivalent workflow-local limits
- workflow-local HITL pauses and resume
- `chat.tool_call` / `tool_call_response`
- workflow-local MFJ fan-out and fan-in
- workflow-local context variables and structured outputs

This loop does not own:

- global build sequencing
- change classification across artifact versions
- promotion gates
- preview approval policy
- coding-agent file repair policy

`factory_app/workflows/*/orchestrator.yaml`, `agents.yaml`, `handoffs.yaml`, and
`extended_orchestration/mfj_extension.json` all belong to this loop.

### 2. Builder session loop

This is the Mozaiks-owned control-plane loop that sits above workflow runs.

The user experiences one builder session even when the system internally starts,
pauses, resumes, or switches multiple workflows.

This loop owns:

- current build state
- active build id and artifact lineage
- current workflow sequence position
- coarse workflow sequencing from `factory_app/workflows/extended_orchestration/extension_registry.json`
- build-time validation gates
- preview readiness
- promotion readiness
- change classification for build-affecting requests
- routing to the smallest valid re-entry point
- typed continuation decisions for builder surfaces

This loop consumes:

- workflow outcomes such as `completed`, `paused`, `failed`, or `invalid`
- typed planning artifacts such as `BuildGraph`, `ChangeIntent`, and `ImpactSet`
- active artifact versions under `generated/...`
- control-plane tool summaries gathered from canonical concept, design, build,
  and artifact stores
- user requests that may mutate generated artifacts

This loop does not own:

- AG2 speaker selection inside a workflow
- MFJ fan-out inside a workflow
- direct file editing

#### Current builder-session harness binding

Today this loop enters the runtime through a control-plane harness, not through
one global AG2 prompt:

- Studio `/api/workflows/trigger`
- `OrchestrationControlHarness`
- `request_submitted` checkpoint when a builder-context request is ambiguous
- `route_requested` checkpoint
- `decision_requested` checkpoint
- `SessionRouter`, coding worker, or typed harness decision response

Important:

- there is no single "OrchestrationControl" prompt wrapped around every user
  request in the product
- there is one builder-session harness that intercepts only builder-context
  requests
- ordinary workflow chat stays in the workflow execution loop
- the harness can now return `execution_mode="harness_decision"` when the
  correct next step is confirmation, clarification, or workflow fallback

### 3. Refinement worker loop

This is the scoped repair or regeneration loop used after the first build pass
or when validation finds a localized defect.

This loop may be implemented by:

- a dedicated refinement workflow mode
- a bounded `AgentGenerator` or `AppGenerator` re-entry
- a coding-agent provider behind a Mozaiks-owned interface

Current first-party support includes a conservative control-plane coding worker
that can short-circuit eligible patch refinements when
`control_plane.coding.enabled=true`.
If explicit file scope is missing, the dedicated `scope_requested` checkpoint
can propose a bounded file set from artifact workspace context before the
coding worker runs.
The selected control-plane pack can now bound that inferred scope
declaratively through `policies.yaml`, and low-risk multi-file proposals can be
confirmed through a typed `apply_proposed_scope` harness action instead of
forcing a full workflow fallback.
If the request should not auto-run, the builder session loop can return a typed
`HarnessDecision` instead of launching either a workflow or coding worker.
The worker now produces concrete `updated_files`, validates the merged
workspace snapshot, and can persist a child artifact version for the refined
bundle. First-party builder surfaces can now supply explicit file payloads from
persisted artifact workbenches and in-flight workflow UI, or let the harness
infer scope when artifact lineage is available.

This loop owns:

- scoped file editing
- scoped regeneration of owned artifact units
- local retry policy
- unit-level validation retries
- file/path boundaries
- sandbox execution for targeted repair

This loop does not own:

- deciding whether a request is `patch`, `design`, `feature`, or `core`
- deciding whether the build should jump back to `ValueEngine`
- deciding whether promotion is allowed

Codex, Claude Code, or any future coding-agent provider belong here.
They are workers behind the harness, not the harness itself.

## Control Loops Versus Graph Artifacts

The repo already contains several graph/config artifacts. Those are not the same
thing as the control loops above.

| Artifact | Scope | Owned by |
|---|---|---|
| `workflow_sequences[]` in `extension_registry.json` | coarse workflow sequencing across workflows | builder session loop |
| `mid_flight_journeys[]` in `mfj_extension.json` | workflow-local fan-out and fan-in | workflow execution loop |
| `BuildGraph` | bounded authoring work inside one build | builder session loop |

Important rule:

- graph artifacts define legal structure
- control loops decide when and how those structures are executed

## Resume Means Different Things At Each Layer

### Workflow execution resume

Resume means:

- continue one paused workflow run
- preserve workflow-local context
- continue AG2 handoffs inside that run

Examples:

- user replies to a pending `tool_call`
- MFJ fan-in resumes the parent at the configured resume agent

### Builder session resume

Resume means:

- restore the build session
- know which workflow last owned the session
- know which artifacts are current or stale
- know whether the build was in `building`, `validation`, `preview`, or `iterating`

This is not the same as AG2 `a_resume(...)` or workflow-local handoff resume.

### Refinement worker resume

Resume means:

- continue one scoped repair unit
- keep the owned-path boundary and validation context
- continue within the current sandbox or staged workspace

This should be treated as optional worker behavior, not as the primary session
continuity mechanism.

## Limits And Guardrails

Each loop needs its own limits.

### Workflow execution loop limits

Examples:

- `max_consecutive_auto_reply`
- `max_rounds`
- tool-call throttle
- MFJ child concurrency
- per-workflow timeout

### Builder session loop limits

Examples:

- max workflow restarts
- max upstream re-entry hops
- max preview attempts
- total build budget
- max validation cycles before escalation

### Refinement worker loop limits

Examples:

- max files touched
- max retries
- max validation reruns
- max commands or sandbox runs
- max diff size

One loop's guardrail must not be treated as a substitute for another loop's
guardrail.

## When Change Classification Runs

Change classification should not run for every user message in every product
session.

It runs only when all of these are true:

1. the session is in builder or artifact-refinement context
2. there is an active build or artifact lineage
3. the user request may mutate generated artifacts

Normal workflow interaction stays in the workflow execution loop.

Examples that do not require build-time change classification:

- answering a workflow question
- approving an inline workflow step
- chatting with an already-built app agent

Examples that do require build-time change classification:

- "fix this dashboard layout"
- "add a new approval workflow"
- "actually make this a blockchain marketplace"

The harness rule is:

- if the request is explicit and structured, route directly
- if the request is builder-context free text that may mutate generated
  artifacts, classify it first
- otherwise, let the request stay in its normal runtime path

## Validation Ownership

Strict workflow and app contracts are runtime-owned code, but they should be
invoked by the builder session loop against staged artifacts before promotion.

That means:

- staged validation is the primary gate
- runtime load failure is the backstop

Example:

- if generated `tools.yaml` is missing `ui.realization`
- the workflow should fail staged load validation in the builder session loop
- the system should route refinement before promotion
- a live user runtime should not be the main discovery path

## Canonical Routing Model

The builder session loop should route build-affecting requests in this order:

1. classify the request into typed `ChangeIntent`
2. compute `ImpactSet`
3. choose the smallest valid re-entry point
4. decide whether to auto-run, clarify, confirm, or fallback
5. start the selected workflow run or refinement worker when appropriate
6. validate the result
7. preview or promote

Typical routing bias after the first complete build:

1. scoped refinement worker
2. workflow-level regeneration
3. app-level regeneration
4. upstream restart at `DesignDocs` or `ValueEngine` only when required

## Example: Small Patch

User request:

> "Move the approval controls into the artifact panel and fix the button label."

Flow:

1. builder session loop classifies it as local refinement
2. `ImpactSet` stays narrow
3. harness may auto-propose file scope or ask for clarification
4. refinement worker loop receives owned paths
5. worker edits files or reruns one scoped unit
6. builder session loop reruns validation and preview

The workflow execution loop may still be used inside that refinement unit, but
the builder session loop remains the owner of routing and promotion.

## Example: Core Change

User request:

> "Actually this should be a blockchain investment marketplace."

Flow:

1. builder session loop classifies it as `core`
2. harness returns a typed `core_restart` decision for the builder surface
3. once confirmed, downstream artifacts are marked stale
4. router re-enters `ValueEngine`
5. a new concept revision is created before downstream rebuild

This is not a scoped file-edit problem.

## Current Mozaiks Position

Today, the workflow execution loop is the most mature and directly implemented
layer. The builder session loop and refinement worker loop already exist in
partial form across the builder docs, refinement routing, staged artifacts, and
preview tooling, but they must be treated as distinct loops rather than as one
extended AG2 chat.

That is the canonical direction for Mozaiks:

- AG2 owns workflow-local orchestration
- Mozaiks owns builder-session orchestration
- coding agents or scoped refinement workflows own local repair work

## Cross References

- [workflow-architecture.md](workflow-architecture.md)
- [../orchestration-and-decomposition.md](../orchestration-and-decomposition.md)
- [../specs/REFINEMENT_CONTROL_PLANE_SPEC.md](../specs/REFINEMENT_CONTROL_PLANE_SPEC.md)

Relevant repo-local builder docs:

- `factory_app/app-builder/builder-execution-model.md`
- `factory_app/app-builder/app-builder-state-and-routing.md`
- `factory_app/app-builder/builder-orchestration-taxonomy.md`
