# Control-Plane Harness Architecture

This document describes the canonical control-plane shape for Mozaiks.

The control plane is the framework layer that sits above workflow-local AG2
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
startup declared through `app/config/ai.json`, control-plane runtime policy in
`control_plane/config/runtime.yaml`, and the selected
`control_plane/config/control_plane.yaml` pack. Do not document a dedicated `RefinementWorkflow` unless the runtime actually introduces one.

Those requests need:

- persisted session/artifact context
- intent interpretation
- deterministic continuation policy
- optional coding refinement
- clear user-facing decisions

That is the control plane.

---

## Two Paths: Factory vs. Harness

**First-time builds** bypass the control plane entirely. They enter through
`extension_registry.json` workflow sequences directly:

```
User starts a new build
  → extension_registry.json  "build" sequence
  → ValueEngine → ThemeCapture → DesignDocs → AgentGenerator → AppGenerator
```

**Refinements** (user already has a built artifact and wants to change it)
enter the control plane:

```
User submits a change request on an existing artifact
  → Control Plane
  → checkpoint: classify the change
  → checkpoint: route to a workflow_sequence
  → checkpoint: decide — direct code edit or factory re-run?

  Path A — small targeted change (patch):
    → scope_selection    find which files to touch
    → coding_refinement  write the scoped change
    [AppGenerator never runs]

  Path B — bigger change (design / feature / core):
    → contract_surface_planning   map request to contract surfaces
    → launch workflow_sequence    re-enter factory at the right stage
    [AppGenerator runs as part of the sequence]
```

The control plane does not replace the factory. It routes to it.

---

## Checkpoint Chain

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
| `contract_surface_planning` | `contract_surface_requested` | LLM | Maps a broader request to specific Mozaiks contract surfaces before workflow re-entry. |
| `coding_refinement` | `coding_requested` | LLM | Writes the scoped code change against the selected files. |

---

## Routing Table

The routing table in `control_plane.yaml` maps each combination of artifact
kind and change class to a named `workflow_sequence`. The sequence name is
resolved from `extension_registry.json` — the control plane declares the
target name only; the sequence declares which workflows run and in what order.

```
control_plane.yaml                        extension_registry.json
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

The control plane does not declare `affected_workflows` or
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

Framework-wide primitives that are not specific to the control plane itself.

Examples:

- generic runtime utilities
- session/runtime internals
- workflow/runtime foundations

### `mozaiksai/control_plane/`

The canonical control-plane subsystem.

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

- control-plane runtime
- checkpoint dispatch
- config/schema/loader/contracts
- generic tool execution boundaries
- first-party Mozaiks checkpoint handlers

This is the canonical runtime package.

### `factory_app/control_plane/`

First-party builder/reference app declaratives and builder-specific tools.

```text
factory_app/control_plane/
  config/
    control_plane.yaml
    runtime.yaml
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

- the first-party declarative control-plane pack
- first-party prompt text
- first-party artifact/workspace context tools
- future control-plane UI surfaces

It should not own the runtime engines.

## What The Harness Is Not

The harness is not:

- a workflow
- an AG2 beta workflow run
- a module handler under `app/modules/*`
- a global prompt wrapped around every message
- a replacement for `extension_registry.json`

The split is:

- control plane
  - interprets checkpoint events
  - decides continuation
- extension graph
  - defines legal workflow movement
- AG2/workflows
  - execute once a workflow is selected

## Pack Model

The app-level switch lives in `app/config/ai.json`:

```json
{
  "control_plane": {
    "enabled": true,
    "classifier": {
      "enabled": true,
      "llm_config": {
        "model": "gpt-5-nano",
        "temperature": 0.0
      }
    },
    "coding": {
      "enabled": true,
      "llm_config": {
        "model": "gpt-5.2-codex",
        "temperature": 0.1
      }
    }
  }
}
```

That config only enables capabilities and provides model config. It does not
point to Python implementation files.

The declarative pack lives under `factory_app/control_plane/` or an app-local
override at `<workspace>/control_plane/`.

## Generated App Authoring

Most generated apps do not need an app-local control plane. They should use
ordinary workflow launches, module actions, and `extension_registry.json`
workflow sequences first.

AppGenerator may emit an app-local harness only when the product explicitly
needs checkpointed lifecycle, refinement, session, or coding-control behavior
that cannot be expressed as normal workflow transitions.

See [app/control-plane-pack.md](../../architecture/app/control-plane-pack.md)
for the full starter pack reference, annotated templates, and guidance on which
checkpoints and tools a generated app should include.

### Ownership Split

Keep startup separate from the harness pack:

- `app/config/ai.json` owns `ask`, `chat`, and `workflows` startup
- `control_plane/config/runtime.yaml` owns runtime policy (LLM profiles, feature flags)
- `control_plane/config/control_plane.yaml` owns declarative checkpoints and routing

### AppGenerator Build Task

The canonical AppGenerator build task for a control plane pack:

```yaml
task_type: control_plane_pack
surface_kind: control_plane
capability_pack_id: null
initial_agent: ControlPlaneAgent
owned_paths:
  - control_plane/config/runtime.yaml
  - control_plane/config/control_plane.yaml
  - control_plane/config/tools.yaml
```

Optional owned paths:

```yaml
- control_plane/config/policies.yaml
- control_plane/prompts/*.yaml
```

### Pack Constraints

Generated control-plane packs are declarative only:

- no `module.yaml`
- no `app/modules/*`
- no `backend/control_plane/*.py`
- no custom harness Python
- no business-domain logic

The generated pack uses shipped `mozaiksai.control_plane` implementations and
declared tool entrypoints from `mozaiksai.control_plane.tools.*` and
`factory_app.control_plane.tools.*`. Custom harness Python is not a v1
generator contract.

### Route Rules

- `control_plane.yaml` routes declare `workflow_sequence` only.
- each `workflow_sequence` must exist in
  `workflows/extended_orchestration/extension_registry.json`
- sequence impact metadata, including `affected_declarative_families`, lives on
  the sequence in `extension_registry.json`, not in `control_plane.yaml`
- do not declare `affected_workflows`, `requires_replanning`, or
  `requires_rebuild` in route manifests; these are derived at runtime

## Declarative Files

### `config/control_plane.yaml`

Declares:

- harness entrypoint
- artifact routing
- checkpoint events
- handler entrypoints
- prompt ids
- tool ids

Example:

```yaml
schema_version: mozaiks.control_plane
profile:
  id: factory_app
  display_name: Factory App Harness
  description: First-party declarative control-plane pack for the Mozaiks build experience.
harness:
  implementation: mozaiksai.control_plane.implementations.orchestration_control:OrchestrationControlHarness
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
  - id: request_intake
    event: request_submitted
    entrypoint: mozaiksai.control_plane.implementations.change_classifier:LLMChangeClassifier
    prompt_id: change_classifier_system
    tool_ids:
      - get_revision_context
      - get_artifact_summary

  - id: refinement_route
    event: route_requested
    entrypoint: mozaiksai.control_plane.implementations.refinement_router:RefinementTriggerRouteResolver

  - id: decision
    event: decision_requested
    entrypoint: mozaiksai.control_plane.implementations.harness_decision:FirstPartyHarnessDecisionPolicy

  - id: scope_selection
    event: scope_requested
    entrypoint: mozaiksai.control_plane.implementations.scope_proposer:ArtifactScopeProposer
    prompt_id: coding_scope_selection_system
    tool_ids:
      - get_revision_context
      - get_artifact_summary
      - get_artifact_workspace_catalog

  - id: contract_surface_planning
    event: contract_surface_requested
    entrypoint: mozaiksai.control_plane.implementations.contract_surface_planner:ContractSurfacePlanner
    prompt_id: contract_surface_selection_system
    tool_ids:
      - get_contract_surface_context

  - id: coding_refinement
    event: coding_requested
    entrypoint: mozaiksai.control_plane.implementations.coding_worker:ScopedRefinementCodingWorker
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
- Do not declare `affected_workflows` in `control_plane.yaml`; it is derived
  from the selected sequence.
- Do not declare `affected_declarative_families` in `control_plane.yaml`; it is
  declared once on the selected sequence in `extension_registry.json`.
- Do not declare `requires_replanning`; it is derived from the typed change
  class: `patch=false`, `design|feature|core=true`.
- Do not declare `requires_rebuild`; control-plane rebuild decisions are
  runtime decision outputs, not route manifest inputs.

### `config/tools.yaml`

Declares harness-owned tools.

Example:

```yaml
tools:
  - id: get_artifact_summary
    kind: context_tool
    description: Load artifact lineage and version metadata.
    entrypoint: factory_app.control_plane.tools.get_artifact_summary:get_artifact_summary
    available_to:
      - request_submitted
      - route_requested
```

### `prompts/*.yaml`

One prompt per file.

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

The control plane is checkpoint-driven.

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
(`module_action`, `page_binding`, `data_schema`, `workflow_agent`, etc.) before
workflow re-entry. Fires when a request is broader than a coding patch but
narrow enough to target specific contract surfaces rather than a full rebuild.

Current first-party handler:

- `mozaiksai/control_plane/implementations/contract_surface_planner.py`

### `coding_requested`

Scoped coding-worker execution for eligible patch refinements.

Current first-party handler:

- `mozaiksai/control_plane/implementations/coding_worker.py`

## Tool Model

Control-plane tools are leaf capabilities used by checkpoints.

They are not:

- AG2 agent tools
- workflow-local lifecycle tools
- module actions

Examples:

- `get_revision_context`
- `get_artifact_summary`
- `get_artifact_workspace_catalog`
- `get_artifact_workspace_scope`

The current first-party tools live under:

- `factory_app/control_plane/tools/*`

## AG2 Implementation Model

LLM-backed checkpoints use `autogen.beta.Agent.ask()` to enforce structured
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

LLM config flows from the declarative pack, not from workflow-local AG2 config:

1. `control_plane/config/runtime.yaml` declares `llm_profiles` keyed by
   capability name, each with `model` and `temperature`.
2. `ControlPlaneConfig.resolve_capability_llm_config(capability)` returns a flat
   `{"model": ..., "temperature": ...}` dict for the resolved profile.
3. The dict maps directly to `OpenAIConfig(model=..., temperature=...)` inside
   each handler's `_make_agent()`.

App-level overrides in `app/config/ai.json` under
`control_plane.<capability>.llm_config` take precedence over the profile
default.

## Runtime Flow

At runtime:

1. `mozaiksai/core/runtime/app/ai_config.py` resolves startup from `app/config/ai.json`
2. `mozaiksai/control_plane/config.py` resolves runtime policy from `control_plane/config/runtime.yaml`
3. `mozaiksai/control_plane/loader.py` resolves the active pack from `control_plane/config/control_plane.yaml`
4. `mozaiksai/control_plane/runtime.py` builds a checkpoint runtime
5. the harness entrypoint is instantiated from `harness.implementation`
6. the harness binds and runs the checkpoints it needs

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
- control plane
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
- `factory_app/control_plane/config/*`
- `factory_app/control_plane/prompts/*`
- `factory_app/control_plane/tools/*`

Do not treat these as canonical:

- `factory_app/app/modules/factory_control_plane/backend/*`

## Guidance

If you are changing framework runtime behavior:

- edit `mozaiksai/control_plane/*`

If you are changing the first-party builder pack:

- edit `factory_app/control_plane/config/*`
- edit `factory_app/control_plane/prompts/*`
- edit `factory_app/control_plane/tools/*`

If you are looking at the identity module under `app/modules/...`, you are not
in the live harness runtime.
