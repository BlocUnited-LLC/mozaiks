# Refinement Harness Architecture

This document describes the canonical refinement harness shape for Mozaiks.

The Refinement Engine is the framework layer that sits above workflow-local AG2
execution and decides what should happen when a harnessed request or checkpoint
event arrives.

It is the piece that makes the Mozaiks build and refinement UX feel
intent-aware without turning every workflow into a giant router.

## Purpose

The harness exists for requests that are not well modeled as:

- normal runtime chat
- workflow-local handoffs
- workflow-local task batching
- workflow transition routing

Examples:

- "Fix this generated dashboard."
- "Add export controls to the current app."
- "Restart this from concept."
- "This should become investor-facing."

In the first-party builder experience today, this refinement loop is driven by
startup declared through `app/config/ai.json`, refinement runtime policy in
`app/config/refinement_policy.yaml`, and the selected
`refinement_harness/config/harness.yaml` pack. Do not document a dedicated `RefinementWorkflow` unless the runtime actually introduces one. Normal chat/workflow startup comes from `app/config/ai.json`; refinement policy and harness routing only take over once a refinement request or checkpoint needs routed work.

Those requests need:

- persisted session/artifact context
- intent interpretation
- deterministic continuation policy
- optional coding refinement
- clear user-facing decisions

That is the Refinement Engine.

---

## Two Paths: Factory vs. Harness

**First-time builds** bypass the Refinement Engine entirely. They enter through
`extension_registry.json` workflow sequences directly:

```
User starts a new build
  → extension_registry.json  "build" sequence
  → ValueEngine → ThemeCapture → DesignDocs → AgentGenerator → AppGenerator
```

**Refinements** (user already has a built artifact and wants to change it)
enter the Refinement Engine:

```
User submits a change request on an existing artifact
  → Refinement Engine
  → checkpoint: classify the change
  → checkpoint: route to a workflow_sequence
  → checkpoint: decide — direct code edit or factory re-run?

  Path A — small targeted change (patch):
    → scope_selection    find which files to touch
    → coding_refinement  write the scoped change
    [AppGenerator never runs]

  Path B — design or feature change to a saved app bundle:
    → contract_surface_planning   map request to bounded contract surfaces
    → inline regeneration when the verified surface plan is safe
    → otherwise launch the selected workflow_sequence

  Path C — core change or broad change without a safe inline plan:
    → launch workflow_sequence    re-enter factory at the right stage
```

The Refinement Engine does not replace the factory. It routes to it.

The Workbench's **Entire app** option submits an unscoped coding request to this
existing harness. A patch may resolve to a safe file scope; a multi-file proposal
uses the target-scoped `PendingHarnessDecision` for confirmation. Confirmation
must match the saved change request, revision, source artifact, request text, and
displayed paths. The server reloads file contents from the verified artifact;
approval does not rerun scope selection. Every continuation action must belong to
the saved decision. The existing session router consumes that exact decision with
a conditional update before classification or execution, so replayed or concurrent
confirmations cannot start work twice. A workflow continuation explicitly bypasses
inline coding and contract-surface generation; a changed workflow recommendation
requires a new decision rather than reusing the previous approval. If execution
fails after admission, the user submits a new request; the consumed approval is
not restored.

Only the bound top-level continuation action can confirm a request; a nested
`refinement_request.extra.harness_action` is discarded. Pending decisions are
normalized when written so displayed and reloaded values match the conditional
consumption check. ChatPage retains the original requested workflow (including
no selection), change request, revision and server-resolved build registry ID
when submitting a saved decision. Existing malformed pre-production decisions
must be reissued rather than bypassing confirmation.

**Limit to selected file** is a hard boundary. Patches use the scoped coding
worker; eligible design or feature requests use the existing surface planner,
which must resolve every write to saved contracts within that selection. Core
changes, schema migrations, workflow fallback and unresolved or wider writes
are rejected before allocating a refinement build or generating files. The user
can narrow the request or remove the file limit and submit a broader request.
This does not change classification to accommodate the selected file. While Apply is
pending, review mutations are disabled; the previous candidate's source and
validation evidence remain available if the new request fails or asks for scope
confirmation.

Saved review labels its initial history selection **Starting version**. A new
candidate remains in the Workbench for preview and review. Detailed model
summaries and version identifiers are expandable; failures, validation blockers
and review actions remain visible.

Schema-page refinement uses the page file stem as its identity; the page's
display name may differ. Workbench theme edits target the saved `app_bundle`
and explicitly scope the request to `brand/theme_config.json`.

Accept, reject and promote verify the selected app archive and its available
parent archive before changing lifecycle state. Their review response reuses
those verified bytes. An unavailable or changed archive therefore leaves the
draft and refinement session unchanged.

---

## Checkpoint Chain

### Completion and saved drafts

AG2 owns agent execution. The harness owns the decision to stage a change and
the evidence required before a saved draft can advance. Classification finishes
only the routing stage; it does not emit a successful build outcome.

| Coding result | Audit event | Workbench behavior |
|---|---|---|
| `validated` with a saved `build_record_id` | `completed` | Open the validated draft for review; explicit acceptance is still required. |
| `planned` | `planned` | Show incomplete validation and offer review when a draft was saved. Keep the active editor/preview on its previous version. |
| `failed` | `failed` | Show failed checks and offer review when a draft was saved. Keep the previous version active. |
| `ineligible` | `ineligible` | Explain the blocked request without reporting completion. |
| Cancelled coding task | `cancelled` | Propagate cancellation and mark the matching registered build as needing revision. |

The worker finalizer checks the proposed files against the approved scope before
validation or persistence, regardless of coding provider. Unknown validation
strategies fail; static-only checks, skipped commands, and partially executed
checks cannot produce a validated result. A saved draft identifies reviewable
work, not a promoted application. Persistence failure cannot emit completion.

Contract-surface regeneration uses that same worker finalizer and audit writer.
Generation alone does not emit completion: `surface_result.status=success` is
returned only after the finalizer validates and saves the draft; incomplete
validation becomes `partial`, and failure becomes `failed`. Exceptions and
cancellation in either generation or finalization emit the corresponding
terminal audit event. Tracking remains best-effort; saved records own the facts.
Cleanup after ordinary request cancellation is shielded. Process termination
past the server's shutdown grace period can still interrupt cleanup and leave a
registry entry marked `building`; automatic startup reconciliation is not implemented.

The workbench uses `coding_worker.metadata.build_record_id` or
`surface_result.metadata.build_record_id` as its review target. Surface success
also requires a passed validation result before replacing the editor/preview.
Its **Review patch** action opens that saved candidate; it does not submit another
coding request. Review, accept, and reject continue through the existing artifact
lifecycle APIs. A validated coding attempt alone does not certify the entire
application or replace independent runtime acceptance.

AppReview revision responses open the same workbench with the original bundle
and the inline result. They do not start another workflow or automatically
accept the candidate. A failed or partial saved draft remains reviewable while
the original bundle stays in the editor and preview.

The opt-in ACP workspace harvester rejects symbolic links and Windows reparse
points, including directory junctions, before traversal or file reads. A linked
workspace root is rejected too. These are scope violations; linked targets are
never harvested or deleted by the harvester.

The shipped refinement policy keeps ACP disabled, and the default local ACP
subprocess path refuses execution
until an isolated worker is available. The repository Docker executor keeps its
offline proof as the default and also offers a trusted-worker-only, opt-in live
profile. That profile runs a one-shot Codex or Claude ACP worker on a private
Docker network, with a separate credential-holding model gateway. The approved
source snapshot and file scope are checked before the worker receives source;
its archive still requires host staging and finalization. The live profile has
been exercised only with a local fake gateway, so it is not yet an accepted
production or default coding path.

#### Current readiness and promotion boundaries

`control_plane/app_validation.py` remains the source-project command validator.
Scoped coding uses `core.validation.validate_generated_app_candidate`, which
connects Factory's existing generated-contract, wiring, runtime-load and
database-backed smoke checks to its app build validator. It validates the complete
merged candidate and records both outcomes before Studio review. The default scoped coding provider makes one
structured-output attempt; a failed check does not start an automatic coding
repair loop. Audit events and Studio refinement-session writes are best-effort,
so their absence is not evidence that a candidate was never saved. Saved build
records and validation results remain the evidence to inspect.

Patch requests, approved execution contexts and surface finalization use the
same validation strategy resolver. Explicit operator environment configuration
takes precedence over an explicit request; otherwise automatic selection prefers
an available Docker daemon, then local npm, then skipped validation. Saved
validation evidence is historical information and does not select the next
request's strategy. The strategy controls app build validation; the existing
contract, runtime-load and database smoke acceptance checks still run in the
host process. A subsequent interactive preview is a separate sandbox operation.

Promotion currently has several implementation paths. The scoped coding worker
writes draft BuildRecords directly. Studio acceptance uses
`accept_staged_refinement_build_record` for staged-review metadata and otherwise
the BuildRecord store; its promotion endpoint restores an accepted current
bundle into the selected workspace. `artifact_promotion.py` also retains
parallel artifact-version and BuildRecord draft/accept helpers, while
`promotion.py` exposes direct source-workspace promotion. The exported staged
draft helpers and direct source-workspace promotion have no in-tree production
caller connecting them to the scoped coding worker. This inventory does not
establish that those exported APIs can be removed or that every promotion path
runs Factory acceptance.

Studio verifies the selected app bundle and its available app-bundle parent
before changing the workspace or registry. The promotion response builds its
review diff from those same verified bytes, keyed by saved BuildRecord ID;
an independent review request verifies the archives again.

Checkpoints are triggered by events, not by agent turns. Each checkpoint is a
discrete unit of work with a declared handler and optional LLM backing.

### Full sequence (patch path — direct code edit)

```
request_submitted        LLM classifies: patch / design / feature / core
        │
route_requested          deterministic: {artifact_kind, change_class} → workflow_sequence
        │
decision_requested       deterministic: direct edit eligible? → auto_patch | workflow_reentry | ...
        │
scope_requested          LLM selects files to touch using context graph + artifact workspace
        │
coding_requested         LLM writes scoped code change against selected files
```

### Sequence for design / feature (contract surface path)

```
request_submitted        classify → design or feature
        │
route_requested          → workflow_sequence e.g. "app_revision"
        │
decision_requested       → workflow_reentry
        │
contract_surface_requested   LLM maps request to contract surfaces needing update
        │
workflow re-entry        launch selected workflow_sequence from extension_registry.json
```

### Checkpoint reference

| Checkpoint | Event | Handler type | What it does |
|---|---|---|---|
| `request_intake` | `request_submitted` | LLM | Classifies change as patch / design / feature / core. Reads artifact state and staleness. |
| `refinement_route` | `route_requested` | Deterministic | Maps `{artifact_kind, change_class}` to a `workflow_sequence` name from the routing table. |
| `decision` | `decision_requested` | Deterministic | Decides outcome: `auto_patch`, `workflow_reentry`, `core_restart`, `clarify_scope`, or `fallback_workflow`. |
| `scope_selection` | `scope_requested` | LLM | Proposes which files to touch using context graph and artifact workspace catalog. |
| `contract_surface_planning` | `contract_surface_requested` | LLM | Maps design or feature changes to contract surfaces for bounded inline regeneration or workflow re-entry. |
| `coding_refinement` | `coding_requested` | Coding provider | Produces a staged patch for an approved file scope; the worker validates and persists it. |

---

## Routing Table

The routing table in `harness.yaml` maps each combination of artifact
kind and change class to a named `workflow_sequence`. The sequence name is
resolved from `extension_registry.json` — the Refinement Engine declares the
target name only; the sequence declares which workflows run and in what order.

```
harness.yaml                        extension_registry.json
────────────────────────────────────      ──────────────────────────────────────────
{artifact_kind: app_bundle}
  patch   → workflow_sequence: app_revision        steps: [AppGenerator]
  design  → workflow_sequence: app_surface_revision steps: [DesignDocs, AppGenerator]
  feature → workflow_sequence: app_revision        steps: [AppGenerator]
  core    → workflow_sequence: full_rebuild         steps: [ValueEngine, ThemeCapture,
                                                            DesignDocs, AgentGenerator,
                                                            AppGenerator]

{artifact_kind: design_docs}
  patch   → workflow_sequence: design_patch        steps: [DesignDocs]
  design  → workflow_sequence: design_revision     steps: [DesignDocs]
  feature → workflow_sequence: design_revision     steps: [DesignDocs]
  core    → workflow_sequence: full_rebuild

{artifact_kind: workflow_bundle}
  patch   → workflow_sequence: workflow_patch      steps: [AgentGenerator]
  design  → workflow_sequence: workflow_revision   steps: [AgentGenerator]
  feature → workflow_sequence: workflow_revision   steps: [AgentGenerator]
  core    → workflow_sequence: full_rebuild

{artifact_kind: concept}
  patch   → workflow_sequence: concept_patch       steps: [ValueEngine]
  design  → workflow_sequence: full_rebuild
  feature → workflow_sequence: full_rebuild
  core    → workflow_sequence: conceptual_replan   steps: [ValueEngine, ThemeCapture,
                                                            DesignDocs, AgentGenerator,
                                                            AppGenerator]
                                                   (with carry_forward context)
```

The Refinement Engine does not declare `affected_workflows` or
`affected_declarative_families`. Those are owned by the sequence in
`extension_registry.json`.

### Staleness-aware classification

The classifier reads `get_stale_artifact_families` before finalising the change
class. If an upstream artifact family is stale relative to the target, the
classifier upgrades the class so the chosen route covers the stale upstream:

```
User wants: app_bundle patch
But: design_docs is stale
→ classifier upgrades to "design" so the route runs DesignDocs first
```

This prevents the factory from re-running AppGenerator on top of stale
upstream artifacts.

## Ownership Model

### `mozaiksai/core/`

Framework-wide primitives that are not specific to the Refinement Engine itself.

Examples:

- generic runtime utilities
- session/runtime internals
- workflow/runtime foundations

### `mozaiksai/control_plane/`

The canonical Refinement Engine subsystem.

```text
mozaiksai/control_plane/
  __init__.py
  config.py
  contracts.py
  executor.py
  loader.py
  ports.py
  runtime.py
  schema.py
  tools/
    get_revision_context.py
  implementations/
    change_classifier.py
    coding_worker.py
    contract_surface_planner.py
    harness_decision.py
    orchestration_control.py
    refinement_router.py
    scope_proposer.py
```

This layer owns:

- Refinement Engine runtime
- checkpoint dispatch
- config/schema/loader/contracts
- generic tool execution boundaries
- first-party Mozaiks checkpoint handlers

This is the canonical runtime package.

### `factory_app/refinement_harness/`

First-party builder/reference app declaratives and builder-specific tools.

```text
factory_app/refinement_harness/
  config/
    harness.yaml
    refinement_policy.yaml
    tools.yaml
    policies.yaml
  prompts/
    change_classifier_system.yaml
    coding_refinement_system.yaml
    coding_scope_selection_system.yaml
    contract_surface_selection_system.yaml
  tools/
    get_artifact_summary.py
    get_artifact_workspace_catalog.py
    get_artifact_workspace_scope.py
    get_carry_forward_candidates.py
    get_context_graph_catalog.py
    get_context_graph_scope.py
    get_contract_surface_context.py
    get_stale_artifact_families.py
    read_carry_forward_module_contract.py
    resolve_carry_forward_preservation.py
    _artifact_workspace.py
    _context_graph.py
    _module_inventory.py
    _shared.py
  ui/
```

`factory_app` is the first-party builder/reference app workspace. It should
feel like an authored app surface, not the owner of the framework runtime.

This layer owns:

- the first-party declarative refinement harness
- first-party prompt text
- first-party artifact/workspace context tools
- future refinement UI surfaces

It should not own the runtime engines.

## What The Harness Is Not

The harness is not:

- a workflow
- an AG2 1.0 workflow run
- a module handler under `app/modules/*`
- a global prompt wrapped around every message
- a replacement for `extension_registry.json`

The split is:

- Refinement Engine
  - interprets checkpoint events
  - decides continuation
- extension graph
  - defines legal workflow movement
- AG2/workflows
  - execute once a workflow is selected

## Pack Model

Startup stays in `app/config/ai.json`; refinement policy and routing stay in
the refinement artifacts:

```text
app/config/ai.json                  ask/chat/workflow startup
app/config/refinement_policy.yaml   LLM profiles and refinement capability flags
refinement_harness/config/harness.yaml  artifact routes and LLM-backed checkpoints
```

`app/config/refinement_policy.yaml` provides model config. `harness.yaml` does
not point to Python implementation files.

The default declarative pack lives under `factory_app/refinement_harness/`.
Apps that need local refinement behavior add
`<workspace>/refinement_harness/config/harness.yaml` as an overlay with
`extends: mozaiks.default_refinement_harness` and only app-specific
`overrides`.

## Generated App Authoring

Most generated apps do not need an app-local Refinement Engine. They should use
ordinary workflow launches, module actions, and `extension_registry.json`
workflow sequences first.

AppGenerator may emit an app-local harness overlay only when the product explicitly
needs checkpointed lifecycle, refinement, session, or coding-control behavior
that cannot be expressed as normal workflow transitions.

See [app/refinement-harness.md](../../architecture/app/refinement-harness.md)
for the overlay contract and guidance on which app-specific deltas are allowed.

### Ownership Split

Keep startup separate from the harness pack:

- `app/config/ai.json` owns `ask`, `chat`, and `workflows` startup
- `app/config/refinement_policy.yaml` owns runtime policy (LLM profiles, feature flags)
- `refinement_harness/config/harness.yaml` owns declarative checkpoints and routing

### AppGenerator Build Task

The canonical AppGenerator build task for a refinement harness:

```yaml
task_type: refinement_harness
surface_kind: refinement
capability_pack_id: null
initial_agent: RefinementHarnessAgent
owned_paths:
  - config/refinement_policy.yaml
  - refinement_harness/config/harness.yaml
```

Optional owned paths:

```yaml
- refinement_harness/config/tools.yaml
- refinement_harness/config/policies.yaml
- refinement_harness/prompts/*.yaml
```

The default generated harness manifest is:

```yaml
schema_version: mozaiks.refinement_harness.v1
extends: mozaiks.default_refinement_harness
overrides: {}
```

Optional files must contain only app-specific deltas. Do not copy default OSS
routes, checkpoints, policies, tools, or prompts into generated app workspaces.

### Pack Constraints

Generated refinement harnesss are declarative only:

- no `module.yaml`
- no `app/modules/*`
- no `backend/control_plane/*.py`
- no custom harness Python
- no business-domain logic

The generated pack uses shipped `mozaiksai.control_plane` implementations and
declared tool entrypoints from `mozaiksai.control_plane.tools.*` and
`factory_app.refinement_harness.tools.*`. Custom harness Python is not a v1
generator contract.

### Route Rules

- `harness.yaml` routes declare `workflow_sequence` only.
- each `workflow_sequence` must exist in
  `workflows/extended_orchestration/extension_registry.json`
- sequence impact metadata, including `affected_declarative_families`, lives on
  the sequence in `extension_registry.json`, not in `harness.yaml`
- do not declare `affected_workflows`, `requires_replanning`, or
  `requires_rebuild` in route manifests; these are derived at runtime

## Declarative Files

### `config/harness.yaml`

The factory default pack declares:

- artifact routing
- checkpoint events
- prompt ids
- tool ids

App-local packs normally declare only an overlay:

```yaml
schema_version: mozaiks.refinement_harness.v1
extends: mozaiks.default_refinement_harness
overrides: {}
```

Factory default example:

```yaml
schema_version: mozaiks.refinement_harness.v1
routing:
  default_artifact_kind: app_bundle
  artifacts:
    - artifact_kind: app_bundle
      label: app bundle
      routes:
        patch:
          workflow_sequence: app_revision
        design:
          workflow_sequence: app_surface_revision
        feature:
          workflow_sequence: app_revision
        core:
          workflow_sequence: full_rebuild
checkpoints:
  - event: request_submitted
    prompt_id: change_classifier_system
    tool_ids:
      - get_revision_context
      - get_artifact_summary

  - event: route_requested

  - event: decision_requested

  - event: scope_requested
    prompt_id: coding_scope_selection_system
    tool_ids:
      - get_revision_context
      - get_artifact_summary
      - get_artifact_workspace_catalog

  - event: contract_surface_requested
    prompt_id: contract_surface_selection_system
    tool_ids:
      - get_contract_surface_context

  - event: coding_requested
    prompt_id: coding_refinement_system
    tool_ids:
      - get_revision_context
      - get_artifact_summary
      - get_artifact_workspace_scope
```

Route rules:

- `workflow_sequence` is the canonical route target.
- The sequence is resolved from `extension_registry.json`.
- If a route must start at a different workflow, define a dedicated sequence
  with that workflow first.
- Do not declare `affected_workflows` in `harness.yaml`; it is derived
  from the selected sequence.
- Do not declare `affected_declarative_families` in `harness.yaml`; it is
  declared once on the selected sequence in `extension_registry.json`.
- Do not declare `requires_replanning`; it is derived from the typed change
  class: `patch=false`, `design|feature|core=true`.
- Do not declare `requires_rebuild`; Refinement Engine rebuild decisions are
  runtime decision outputs, not route manifest inputs.

### `config/tools.yaml`

Declares harness-owned tools. The default file lives in
`factory_app/refinement_harness/config/tools.yaml`; app-local `tools.yaml`
files are deltas only.

Example:

```yaml
tools:
  - id: get_artifact_summary
    kind: context_tool
    description: Load artifact lineage and version metadata.
    entrypoint: factory_app.refinement_harness.tools.get_artifact_summary:get_artifact_summary
    available_to:
      - request_submitted
      - route_requested
```

### `prompts/*.yaml`

One prompt per file. The default prompts live in
`factory_app/refinement_harness/prompts/`; app-local prompt files are overrides
only.

Example:

```yaml
id: change_classifier_system
content: |
  You are the authoritative Mozaiks refinement change classifier.
```

### `config/policies.yaml`

Declares deterministic bounds.

Current first use:

- scope size limits
- auto-apply thresholds
- overflow behavior

## Checkpoint Model

The Refinement Engine is checkpoint-driven.

Current first-party checkpoints:

- `request_submitted`
- `route_requested`
- `decision_requested`
- `scope_requested`
- `contract_surface_requested`
- `coding_requested`

These are the harness-native units of execution.

### `request_submitted`

LLM-backed interpretation of the request.

Current first-party handler:

- `mozaiksai/control_plane/implementations/change_classifier.py`

### `route_requested`

Deterministic workflow-route selection from typed request intent.

Current first-party handler:

- `mozaiksai/control_plane/implementations/refinement_router.py`

### `decision_requested`

Deterministic user-facing decision shaping.

Examples:

- `workflow_reentry`
- `core_restart`
- `auto_patch`
- `clarify_scope`
- `fallback_workflow`

Current first-party handler:

- `mozaiksai/control_plane/implementations/harness_decision.py`

### `scope_requested`

LLM-backed file-scope proposal when explicit coding scope is missing.

Current first-party handler:

- `mozaiksai/control_plane/implementations/scope_proposer.py`

### `contract_surface_requested`

LLM-backed contract surface planning for feature and design refinements.
Maps the request to the specific Mozaiks contract surfaces that need updating
(`module_action`, `page_binding`, `data_schema`, `workflow_agent`, etc.). For a
verified saved app bundle, Studio can execute a bounded surface plan inline;
otherwise it offers the resolved workflow sequence. This checkpoint fires when
a request is broader than a coding patch but narrow enough to target specific
contract surfaces rather than a full rebuild.

Current first-party handler:

- `mozaiksai/control_plane/implementations/contract_surface_planner.py`

The planner requires the complete verified saved bundle and receives explicit
allowed write paths separately. Context Graph candidates provide retrieval hints;
they cannot authorize writes. The classifier receives a deterministic inventory
of saved targets, and the planner resolves the returned identities before a build
is allocated. The worker repeats whole-plan admission before its first model call.

| Surface kinds | Target kind | Build family |
| --- | --- | --- |
| `module_action`, `module_contract`, `data_schema` | `module` | `app_bundle` |
| `page_binding` | `page` | `app_bundle` |
| `app_config` | `app` | `app_bundle` |
| `workflow_tool`, `workflow_agent`, `ui_component` | `workflow` | `workflow_bundle` |

`page_binding` resolves a schema page by its exact saved schema name. A custom
React page uses its exact declared route ID in `ui/route_manifest.json`, then the
existing component resolver follows the `ui/index.js` registration to its local
`ui/pages/custom/*.jsx` source. Component names do not imply filenames, and a
filename such as `custom/focus` is not a route identity. Route and registry files
are supplied to generation as read-only context from the same saved snapshot.
`ui_component` remains a workflow surface and cannot select an app page.
The `app_config` target uses the request's owner-resolved artifact app identity;
`app.json` may omit `appId`, but a conflicting declared identity is rejected.

Missing, empty, unsafe, ambiguous, dynamic, unknown or out-of-scope targets fail
before generation. Admission checks every surface, including manually constructed
plans; one invalid surface rejects the whole plan. The worker also rejects generated
writes outside the admitted paths. New files or undeclared surfaces require the
existing workflow route rather than guessed path creation. These checks establish
source ownership, not the generated behavior's correctness: normal validation,
review and browser acceptance remain required.

### `coding_requested`

Scoped coding-worker execution for eligible patch refinements.

Current first-party handler:

- `mozaiksai/control_plane/implementations/coding_worker.py`

## Staged Coding Worker

The staged coding worker (`mozaiksai/control_plane/implementations/coding_worker.py`)
is the checkpoint handler that applies LLM-generated file edits during a
coding refinement turn. It bridges between an LLM checkpoint's structured
output and the staging area.

### Flow

```
LLM coding checkpoint
    → provider-neutral StagedPatchProposal
    → enforce approved file scope and reject ineffective changes
    → merge changes into the complete saved baseline
    → validate_generated_app_candidate()  # existing acceptance + build owners
        → deterministic bundle and runtime acceptance
        → operator-selected Docker/E2B/local build execution
        → require both checks to pass
    → persist canonical archive and draft BuildRecord with candidate evidence
    → emit tool event to Studio panel
```

### What the coding worker does NOT do

- It does not modify the live workspace. All writes go to a staging area.
- It does not interpret the LLM's reasoning. It receives already-typed
  structured output and applies it deterministically.
- It does not let the model choose execution policy. The canonical app validation
  resolver applies operator settings before a request strategy; `skip` leaves
  the candidate unverified and blocks activation.
- It does not promote staged changes. Promotion requires a separate
  acceptance step through the Studio promotion flow.

### Security guarantees from scoped execution

Every candidate staged by the coding worker passes through
`materialize_coding_workspace()` and its harvest check, enforcing path containment
and rejecting symlinks or reparse points. Proposal admission additionally enforces
the exact approved file scope. The separate scoped-execution helper enforces:

- no `..` traversal components
- no absolute paths (Windows drive qualifiers or POSIX `/` prefixes)
- no secret-sensitive filenames (`.env`, `id_rsa`, `.pem`, `.key`, etc.)
- new files only created inside directories already referenced in the change set

Files that fail these checks get status `skipped_unsafe` or `skipped_secret`
and are never written. The worker reports these in the tool event so Studio
can surface them.

## Tool Model

Refinement Engine tools are leaf capabilities used by checkpoints.

They are not:

- AG2 agent tools
- workflow-local lifecycle tools
- module actions

Examples:

- `get_revision_context`
- `get_artifact_summary`
- `get_artifact_workspace_catalog`
- `get_artifact_workspace_scope`
- `run_app_source_validation`

The current first-party tools live under:

- `factory_app/refinement_harness/tools/*`

## AG2 Implementation Model

LLM-backed checkpoints use `ag2.Agent.ask()` to enforce structured
outputs without custom JSON-parsing fallbacks.

### Structured Output Pattern

Every LLM-backed handler follows this pattern:

```python
agent = self._make_agent(system_prompt=system_prompt, llm_config=llm_config)
stream = MemoryStream()
reply = await agent.ask(
    user_prompt,
    stream=stream,
    middleware=[RetryMiddleware(max_retries=2)],
    observers=[TokenMonitor()],
    response_schema=ChangeClassifierResult,
)
result = await reply.content()
```

- `response_schema` — AG2 enforces the Pydantic model at the provider level.
  No JSON extraction or repair is needed in handler code.
- `RetryMiddleware(max_retries=2)` — transient failures retry automatically.
- `TokenMonitor()` — token accounting without custom hooks.
- `MemoryStream` — captures the full conversation for observability.

### Agent Factory Injection

Every LLM-backed handler accepts an `agent_factory` callable:

```python
LLMChangeClassifier(
    agent_factory=lambda system_prompt, llm_config: _FakeAgent(system_prompt, llm_config),
    config_loader=...,
    pack_loader=...,
)
```

Production code passes `None` — the default builds a real `Agent` from the
resolved `llm_config`. Tests inject a fake agent that records calls and returns
preset structured responses without hitting the network. This makes every
checkpoint independently unit-testable.

LLM-backed checkpoints:

| Checkpoint | Handler | Response schema |
|---|---|---|
| `request_submitted` | `LLMChangeClassifier` | `ChangeClassifierResult` |
| `scope_requested` | `ArtifactScopeProposer` | `ScopeProposal` |
| `contract_surface_requested` | `ContractSurfacePlanner` | `ContractSurfacePlan` |
| `coding_requested` | `ScopedRefinementCodingWorker` | `CodingWorkerPlan` |

Deterministic checkpoints (`route_requested`, `decision_requested`) do not use
AG2 at all — they derive results from typed inputs and routing tables.

### LLM Config Resolution

LLM config for structured-output checkpoint calls comes from the app policy:

1. `app/config/refinement_policy.yaml` declares `llm_profiles` keyed by
   profile id, each with an AG2 `llm_config` such as `api_type` and `model`.
2. `ControlPlaneConfig.resolve_capability_llm_config(capability)` returns a flat
   config dict for the profile named by that capability.
3. The checkpoint handler passes that dict to the AG2 structured agent runner.

The first-party policy uses `impact_analyzer` for file-scope and
contract-surface selection. Its `coding.llm_profile` and
`contract_surface.regeneration_llm_profile` independently select `codegen`
for structured patches and contract-surface file generation. Regeneration
requires an explicit model even if the coding capability is disabled. ACP coding is a separate execution provider:
its adapter does not consume `llm_profiles.codegen.llm_config.model`.

Every enabled capability must reference a declared `llm_profile` with a
non-empty model. The surface capability also needs
`regeneration_llm_profile`. Inline capability `llm_config` and unknown
capability fields are rejected when the policy loads. Do not put refinement
model overrides in `app/config/ai.json`.

## Runtime Flow

At runtime:

1. `mozaiksai/core/runtime/app/ai_config.py` resolves startup from `app/config/ai.json`
2. `mozaiksai/control_plane/config.py` resolves runtime policy from `app/config/refinement_policy.yaml`
3. `mozaiksai/control_plane/loader.py` resolves the active pack from `refinement_harness/config/harness.yaml`
4. `mozaiksai/control_plane/runtime.py` builds a checkpoint runtime
5. `OrchestrationControlHarness` binds the loaded declarative pack
6. the harness runs the checkpoints it needs

Current Studio refinement flow:

```text
Studio trigger
  -> OrchestrationControlHarness
  -> request_submitted   (LLMChangeClassifier)
  -> route_requested     (RefinementTriggerRouteResolver)
  -> decision_requested  (FirstPartyHarnessDecisionPolicy)
  -> SessionRouter | coding worker | harness decision response
```

If coding is eligible (`patch` + scoped files):

```text
... -> scope_requested     (ArtifactScopeProposer)
    -> coding_requested    (ScopedRefinementCodingWorker)
```

If contract surface planning is needed (`feature` or `design`):

```text
... -> contract_surface_requested  (ContractSurfacePlanner)
    -> workflow re-entry via resolved workflow_sequence
```

## Relation To Workflows And Extensions

The harness depends on the workflow graph, but it is not the graph.

- `extension_registry.json`
  - legal transitions and workflow movement
- Refinement Engine
  - semantic interpretation and continuation choice
- workflow runtime
  - actual execution

This is why the harness was required for the Mozaiks build UX. The extension
graph alone cannot interpret `"make this a blockchain marketplace"` or decide
between `clarify_scope`, `run_workflow`, or `restart_upstream`.

## Host Model

Today the first-party harness is mounted by Studio.

That means:

- Studio is the primary harnessed surface
- platform/runtime apps should remain passthrough unless they opt in later

Host-aware gating still matters, but the canonical runtime ownership is now
correct.

## Identity Module

This path still exists:

```text
factory_app/app/modules/factory_control_plane/backend/
```

It is only the zero-action Studio identity module.

It is not the harness runtime.

## Canonical Paths

Use these paths as source of truth:

- `mozaiksai/control_plane/*`
- `factory_app/refinement_harness/config/*`
- `factory_app/refinement_harness/prompts/*`
- `factory_app/refinement_harness/tools/*`

Do not treat these as canonical:

- `factory_app/app/modules/factory_control_plane/backend/*`

## Guidance

If you are changing framework runtime behavior:

- edit `mozaiksai/control_plane/*`

If you are changing the first-party builder pack:

- edit `factory_app/refinement_harness/config/*`
- edit `factory_app/refinement_harness/prompts/*`
- edit `factory_app/refinement_harness/tools/*`

If you are looking at the identity module under `app/modules/...`, you are not
in the live harness runtime.
