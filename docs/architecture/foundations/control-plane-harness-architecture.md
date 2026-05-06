# Control-Plane Harness Architecture

This document defines the target architecture for the Mozaiks control-plane
harness.

The harness is the missing layer above workflow-local AG2 execution and below
Studio/build session UX. It exists because some user requests are:

- not ordinary runtime chat
- not workflow-local handoff decisions
- not MFJ fan-out/fan-in
- not just static workflow-sequence routing

Examples:

- "Fix this generated dashboard."
- "Add an approval workflow."
- "Actually make this investor-facing instead of internal."
- "This should go back to concept/design."

Those requests need a builder-context interpreter that sees artifact lineage,
current build state, and upstream/downstream ownership before choosing the next
action.

## Core Rule

The control-plane harness is:

- a control-plane capability
- configurable per app/workspace
- host-injected above workflow execution
- modular in the same declarative spirit as workflow packs
- not AG2 workflow-local orchestration
- not a dynamic app module handler

## Final Layer Split

### 1. `mozaiksai/core/control_plane/`

This package owns framework-level, app/workflow-agnostic control-plane
contracts and loaders.

It should contain:

- typed contracts
  - `ControlPlaneConfig`
  - `HarnessRequest`
  - `HarnessDecision`
  - `ChangeIntent`
  - `ImpactSet`
  - `RoutingDecision`
- ports/interfaces
  - `ChangeClassifierPort`
  - `RoutingPolicyPort`
  - `CodingWorkerPort`
  - `ControlPlaneToolExecutorPort`
- declarative loaders/validators
  - `control_plane.yaml`
  - `prompts.yaml`
  - `tools.yaml`
  - optional `policies.yaml`
- config loading from `app/config/ai.json`
- generic profile resolution
- generic tool manifest validation and execution boundaries

It must not contain:

- factory-specific prompts
- `ValueEngine` / `DesignDocs` / `AgentGenerator` / `AppGenerator` routing
- Studio-only policy prose
- first-party refinement taxonomy beyond the generic contracts

### 2. `factory_app/control_plane/`

This package owns the first-party Mozaiks builder/control-plane implementation.

It should contain:

- `orchestration_control.py`
- `change_classifier.py`
- `refinement_router.py`
- first-party prompt profiles
- first-party control-plane tools
- first-party routing policy
- future coding-worker integration

This layer is allowed to know:

- the factory build workflow sequence
- `patch | design | feature | core`
- `ValueEngine -> DesignDocs -> AgentGenerator -> AppGenerator`
- first-party builder semantics

### 3. `app/control_plane/`

This is the optional app-local override layer for apps that intentionally opt
into harnessed control-plane behavior.

It should allow:

- profile overrides
- custom prompts
- custom control-plane tools
- custom routing policy components

It should not be required for ordinary apps.

## Final Declarative Shape

The harness should be modular in the same spirit as workflow packs, but it is
not itself a workflow pack.

Recommended pack shape:

```text
factory_app/control_plane/default/
  control_plane.yaml
  prompts.yaml
  tools.yaml
  policies.yaml
  implementations/
    orchestration_control.py
    change_classifier.py
    refinement_router.py
    tools/
      get_concept_overview.py
      get_design_summary.py
      get_artifact_summary.py
      run_scoped_validation.py
```

Optional app-local override:

```text
app/control_plane/custom/
  control_plane.yaml
  prompts.yaml
  tools.yaml
  policies.yaml
  implementations/
    ...
```

## Final `ai.json` Role

`app/config/ai.json` should select the control-plane profile and the model
config for each harness capability. It should not embed raw code paths or giant
prompt bodies.

Recommended direction:

```json
{
  "control_plane": {
    "enabled": true,
    "profile": "default",
    "classifier": {
      "enabled": true,
      "llm_config": {
        "model": "gpt-4o-mini",
        "temperature": 0.0
      }
    },
    "coding": {
      "enabled": false,
      "llm_config": {
        "model": "gpt-5.2-codex",
        "temperature": 0.1
      }
    }
  }
}
```

Rules:

- `enabled` gates the harness as a whole
- `profile` selects the declarative control-plane pack
- `classifier.llm_config` configures the authoritative request-analysis model
- `coding.llm_config` configures the refinement worker model
- secrets still belong in environment variables or the connector/secret system

## Final Control-Plane Tool System

The harness should support tools the same way workflows support tools:

- declarative tool entries in `tools.yaml`
- Python implementations behind declared entrypoints
- loader/validator in `mozaiksai/core/control_plane/`
- execution boundaries enforced by the harness/tool executor

These are not AG2 agent tools and not module actions.

They are harness-owned tools.

Examples:

- `get_concept_overview`
- `get_design_summary`
- `get_artifact_summary`
- `get_build_state`
- `run_scoped_validation`
- `invoke_coding_worker`

Recommended `tools.yaml` direction:

```yaml
tools:
  - id: get_concept_overview
    kind: context_tool
    description: Load the current concept overview for the active app/build.
    entrypoint: implementations.tools.get_concept_overview:get_concept_overview
    available_to:
      - classifier

  - id: get_design_summary
    kind: context_tool
    description: Load the latest design summary for the active app/build.
    entrypoint: implementations.tools.get_design_summary:get_design_summary
    available_to:
      - classifier

  - id: get_artifact_summary
    kind: context_tool
    description: Load artifact lineage and current version metadata.
    entrypoint: implementations.tools.get_artifact_summary:get_artifact_summary
    available_to:
      - classifier
      - router
```

## Final Runtime Flow

The harness belongs above workflow execution.

Canonical flow:

```text
user builder-context request
  -> host surface
  -> control-plane harness
  -> control-plane tools gather context
  -> classifier decides intent
  -> routing policy computes re-entry point
  -> SessionRouter enforces dependencies / persists lifecycle
  -> workflow run or refinement worker starts
  -> validation / preview / promotion loop continues
```

Current first-party path is an early form of this:

```text
Studio /api/workflows/trigger
  -> OrchestrationControlHarness
  -> LLMChangeClassifier
  -> RefinementTriggerRouteResolver
  -> SessionRouter
```

The target architecture keeps that flow, but replaces hardcoded first-party
placement and inline policy with a real control-plane pack system.

## Final Relationship To Other Orchestration Layers

The harness is different from:

- AG2 handoffs
  - workflow-local turn routing
- MFJ
  - workflow-local fan-out/fan-in
- `extension_registry.json`
  - legal workflow sequence / transitions / dependencies

The harness owns:

- builder-context input interpretation
- change/refinement classification
- route selection above any single workflow
- future coding-worker invocation

## Final Scope Rules

The harness should not wrap every message in every app session.

It should run only when:

1. the app/workspace has `control_plane.enabled = true`
2. the current host/surface supports control-plane interception
3. the request is in builder or artifact-refinement context
4. the request may mutate generated artifacts or build direction

Ordinary runtime app chat should stay outside the harness unless an app
explicitly opts into that behavior.

## Current Transitional State

Today the repo is in a bridge state:

- the concept is correct
- the file placement is still transitional
- the classifier is live
- the harness is live
- the config contract is early
- the tool/profile system does not exist yet

Current transitional placement:

- `factory_app/app/modules/factory_control_plane/backend/*`

That is functional, but misleading. Those files are not loaded by the dynamic
module system. They are directly imported by the Studio host.

## Implementation Checklist

### Phase 1: Establish core/control-plane boundary

- [ ] Create `mozaiksai/core/control_plane/`
- [ ] Move generic control-plane config/contracts out of `factory_app`
- [ ] Define ports for classifier, router policy, coding worker, and tool executor
- [ ] Keep `factory_app` implementation-specific logic out of core

### Phase 2: Move first-party implementation

- [ ] Create `factory_app/control_plane/`
- [ ] Move `orchestration_control.py` there
- [ ] Move `change_classifier.py` there
- [ ] Move `refinement_router.py` there
- [ ] Update Studio imports to use the new package
- [ ] Leave `factory_app/app/modules/factory_control_plane/` only for true module/admin/runtime concerns

### Phase 3: Introduce control-plane pack declaratives

- [ ] Define `control_plane.yaml` schema
- [ ] Define `prompts.yaml` schema
- [ ] Define `tools.yaml` schema
- [ ] Decide whether `policies.yaml` is needed in v1
- [ ] Add loader + validator for control-plane packs

### Phase 4: Introduce control-plane tools

- [ ] Define the control-plane tool contract
- [ ] Implement first-party context tools
  - [ ] `get_concept_overview`
  - [ ] `get_design_summary`
  - [ ] `get_artifact_summary`
  - [ ] `get_build_state`
- [ ] Restrict tool availability by harness component
- [ ] Add tests for manifest loading and tool execution

### Phase 5: Refactor `ai.json` to profile selection

- [ ] Add `control_plane.profile`
- [ ] Make profile resolution load `factory_app/control_plane/<profile>/...`
- [ ] Allow optional `app/control_plane/<profile>/...` overrides
- [ ] Keep `classifier.llm_config` and `coding.llm_config` as capability-specific settings

### Phase 6: Refine routing and state model

- [ ] Feed classifier current concept/design/artifact summaries through tools instead of only raw request text
- [ ] Keep `ChangeIntent`, `ImpactSet`, and routing decisions typed and persisted
- [ ] Ensure dependency reroutes preserve needed refinement metadata into upstream workflows

### Phase 7: Add coding-worker loop

- [ ] Define `CodingWorkerPort` in core
- [ ] Implement first-party coding worker integration in `factory_app/control_plane/`
- [ ] Honor `control_plane.coding.enabled`
- [ ] Add sandbox/validation boundaries
- [ ] Keep coding-worker invocation subordinate to harness routing

### Phase 8: Host and surface gating

- [ ] Make harness mounting host-aware and config-aware
- [ ] Keep ordinary `platform` runtime behavior non-harnessed by default
- [ ] Allow future apps to opt into harnessed control-plane behavior intentionally

### Phase 9: Acceptance and regression coverage

- [ ] Add control-plane pack loader tests
- [ ] Add control-plane tool manifest tests
- [ ] Add live classifier smoke with profile-selected `llm_config`
- [ ] Add routed refinement smoke through Studio
- [ ] Add future coding-worker refinement smoke once implemented

## Success Criteria

The target architecture is achieved when:

- control-plane logic lives outside dynamic module runtime handlers
- reusable contracts live in `mozaiksai/core/control_plane/`
- first-party builder policy lives in `factory_app/control_plane/`
- apps can enable or disable the harness explicitly
- control-plane prompts/tools/policies are declarative and swappable by profile
- workflow-local AG2 orchestration remains separate from control-plane routing
- coding agents operate as refinement workers behind the harness, not as the harness itself
