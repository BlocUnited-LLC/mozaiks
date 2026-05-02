# Surface Realization Refactor

## Purpose

This document defines the exact contract changes needed to separate:

- product planning (`capability_pack`)
- durable app/runtime surfaces (`module`)
- agentic orchestration (`workflow`)
- build/session coordination (`control_plane`)
- UI-only surfaces and external integrations

The current factory flow is directionally correct, but it still conflates these
concepts in a few important places:

- `ValueEngine` emits capability-pack hints without realization hints.
- `DesignDocs` defines event boundaries but not an explicit ownership map.
- `AgentGenerator` uses the term `module` for workflow stages, which conflicts
  with runtime app modules under `app/modules/*`.
- `AppGenerator` effectively treats most business capability packs as modules.

This refactor introduces one missing concept:

- `surface_kind`: the realization boundary for a planned surface

## Design Goal

Capability packs remain planning units.

They do **not** automatically become runtime modules.

Instead, planning must explicitly resolve each surface into one of:

- `module`
- `workflow`
- `control_plane`
- `external_integration`
- `ui_only`

Only `module` surfaces emit `module_contract` tasks in `AppGenerator`.

## Vocabulary Changes

Use these terms consistently:

| Term | Meaning |
| --- | --- |
| `capability_pack` | Product-planning bundle inferred from the concept. |
| `surface` | Concrete realization unit produced after design/planning. |
| `surface_kind` | How that surface is realized: `module`, `workflow`, `control_plane`, `external_integration`, or `ui_only`. |
| `workflow_stage` | An internal stage of a generated workflow. This replaces AgentGenerator's current overloaded `module` term. |
| `runtime module` | Durable deterministic business surface under `app/modules/{module_id}`. |

## Stage Ownership

The stages should own different levels of commitment:

- `ValueEngine`
  - concept summary
  - capability-pack hints
  - agentic-capability hints
  - coarse realization hints only
- `DesignDocs`
  - explicit surface ownership map
  - event ownership
  - page-to-surface mapping
  - control-plane vs workflow vs module decisions
- `AgentGenerator`
  - workflow generation only
  - workflow-local stages/tools/UI only
  - no app-module planning
- `AppGenerator`
  - app schema generation
  - runtime module contracts for `surface_kind=module`
  - control-plane artifacts for `surface_kind=control_plane`
  - integration artifacts for `surface_kind=external_integration`

## 1. ValueEngine Changes

`ValueEngine` should stay coarse. It should not finalize module boundaries.

### 1.1 Structured Output Changes

File:

- `factory_app/workflows/ValueEngine/structured_outputs.yaml`

Keep `capability_pack_hints` and `agentic_capabilities`.

Add the following new model:

```yaml
  SurfaceCandidateHint:
    type: model
    fields:
      surface_id:
        type: str
        description: Stable snake_case identifier for a likely downstream surface.
      label:
        type: str
        description: Human-readable label for the surface.
      source_capability_packs:
        type: optional_list
        items: str
        description: Capability-pack hints this surface appears to depend on.
      likely_surface_kind:
        type: literal
        values:
          - module
          - workflow
          - control_plane
          - external_integration
          - ui_only
        description: Coarse realization hint only. Not a final contract.
      confidence:
        type: literal
        values:
          - low
          - medium
          - high
      rationale:
        type: str
        description: One-sentence explanation for the hint.
```

Add this field to `ConceptBlueprint`:

```yaml
      surface_candidate_hints:
        type: optional_list
        items: SurfaceCandidateHint
        description: >
          Coarse realization hints for downstream design/planning. These do not
          finalize module or workflow boundaries.
```

### 1.2 Prompt Changes

File:

- `factory_app/workflows/ValueEngine/agents.yaml`

Update the Gap Analysis instructions so the reasoning order becomes:

1. deterministic product capabilities
2. capability-pack hints
3. coarse realization hints
4. agentic augmentation opportunities

Required prompt additions:

- explain that `surface_candidate_hints` are suggestions, not final contracts
- explicitly allow `control_plane` when the concept implies Studio/build/session
  coordination
- explicitly allow `external_integration` when the concept implies Stripe
  Connect, hosting providers, ads APIs, CRM APIs, etc.
- explicitly allow `ui_only` for purely presentational/reporting surfaces

### 1.3 BuildPlan Persistence Changes

File:

- `factory_app/workflows/ValueEngine/tools/decompose.py`

The saved `build_plan` should preserve `surface_candidate_hints` from the
approved concept when present:

```python
"surface_candidate_hints": manifest.get("surface_candidate_hints", [])
```

This remains a hint layer, not a final decomposition.

### 1.4 Context Variable Cleanup

Files:

- `factory_app/workflows/ValueEngine/context_variables.yaml`
- `factory_app/workflows/ValueEngine/tools/create_app_record.py`
- `factory_app/workflows/AppGenerator/tools/update_app_record.py`

Current naming is coupled to a specific hosted module boundary.

Replace the conceptual contract:

- `app_record_id`

With a more neutral hosted-product contract:

- `build_registry_id`

This ID should refer to the hosted platform's app/build registry record rather
than to a hardcoded module name such as `app_builder`.

Implementation note:

- do not require `ValueEngine` to know the final hosted module name

## 2. DesignDocs Changes

`DesignDocs` is where final surface ownership should become explicit.

Unlike `ValueEngine`, this stage should make real decisions.

### 2.1 Backend Doc Contract Additions

File:

- `factory_app/workflows/DesignDocs/agents.yaml`

Add a required backend-doc section:

- `## Surface Realization Map`

This section should contain one deterministic entry per concrete surface.

Recommended entry shape:

```yaml
surface_map:
  - surface_id: app_registry
    surface_kind: module
    source_capability_packs: [crud_pack]
    primary_entities: [AppRecord, AppArtifact]
    owned_mutations: [create_app_record, update_build_status]
    emits: [domain.app_registry.app_created, domain.app_registry.status_changed]
    consumes: []
    primary_pages: [Dashboard]
    notes: User-owned app catalog and metadata registry.
```

Required fields per entry:

- `surface_id`
- `surface_kind`
- `source_capability_packs`
- `primary_entities`
- `owned_mutations`
- `emits`
- `consumes`
- `primary_pages`
- `notes`

### 2.2 UI Schema Contract Additions

File:

- `factory_app/workflows/DesignDocs/agents.yaml`

The `ui_schema` document should gain a top-level `surface_map` block that
matches the backend doc semantically, but is UI-oriented.

Recommended shape:

```yaml
surface_map:
  - surface_id: app_registry
    surface_kind: module
    pages: [Dashboard]
    page_actions:
      - page: Dashboard
        action_id: create_app
        target_surface: control_plane.build_journey
      - page: Dashboard
        action_id: open_app
        target_surface: module.app_registry
```

This allows downstream generation to answer:

- which page belongs to which surface
- whether an interaction should call a module action
- whether it should launch a workflow
- whether it should enter a control-plane route

### 2.3 Event Architecture Additions

The existing event rules are good and should stay.

Add these explicit rules:

- every `domain.*` event must be owned by exactly one `surface_kind=module`
  surface
- workflows may consume `domain.*` events but never own them
- `control_plane` surfaces may emit `platform.*` events
- hosted product surfaces may emit `hosted.*` events only when the product
  explicitly requires them
- `ui.*` events remain ephemeral and must reference `surface_id` indirectly
  through page/action ownership, not replace durable state transitions

### 2.4 DesignDocs Prompt Changes

Add prompt language that forces the agent to classify every major concern from
the concept into one of:

- module
- workflow
- control_plane
- external_integration
- ui_only

This is the stage where "hosting", "campaign optimization", "investor
marketplace", and "revenue-share payouts" should be separated instead of left as
one vague pack.

## 3. AgentGenerator Changes

`AgentGenerator` should not change its core purpose.

It should still generate workflows.

The main fixes are:

- terminology cleanup
- explicit boundary with app/runtime modules
- consumption of the new surface map

### 3.1 Rename `modules` to `workflow_stages`

Files:

- `factory_app/workflows/AgentGenerator/structured_outputs.yaml`
- `factory_app/workflows/AgentGenerator/agents.yaml`

Current problem:

- AgentGenerator uses `modules` to mean sequential workflow stages
- the rest of the system uses `modules` to mean `app/modules/*`

This should be renamed.

Required model changes:

```yaml
  WorkflowStage:
    type: model
    fields:
      stage_name:
        type: str
      stage_index:
        type: int
      stage_description:
        type: str
      pattern_id:
        type: int
      pattern_name:
        type: str
      agents_needed:
        type: list
        items: str
```

Update `WorkflowStrategy`:

```yaml
      workflow_stages:
        type: list
        items: WorkflowStage
        description: Complete stage roadmap for this workflow.
```

Replace the old field names directly. This codebase is pre-production and does
not need a long-lived compatibility layer.

### 3.2 Rename `module_index` to `stage_index`

This rename must be applied consistently across:

- `WorkflowStrategy`
- `AgentRoster`
- tool planning
- handoff generation
- any structured outputs currently keyed by `module_index`

The meaning is sequencing inside one workflow, not ownership of an app module.

### 3.3 Rename `ModuleAgents` to `StageAgents`

Wherever the workflow-generation contract refers to per-module agents, rename
that concept to `StageAgents` or `WorkflowStageAgents`.

The objective is to prevent confusion between:

- workflow-stage agent groupings
- app runtime modules

### 3.4 Consume `surface_map`

AgentGenerator should read `surface_map` from design context and only generate
workflows for entries where:

- `surface_kind = workflow`

It may reference `module` surfaces in `event_boundary.module_actions`, but it
must not infer or generate those modules itself.

### 3.5 Preserve Current Event Rules

These current rules should remain unchanged:

- workflows may react to `domain.*`
- workflows may call module capabilities/actions
- workflows must not publish `domain.*` directly

This is already the correct boundary.

## 4. AppGenerator Impact

This document focuses on `ValueEngine`, `DesignDocs`, and `AgentGenerator`, but
the refactor is incomplete unless `AppGenerator` also changes.

### 4.1 Replace Capability-Pack-to-Module Collapse

Current behavior:

- most business capability packs receive `module_contract` tasks

Target behavior:

- only `surface_kind=module` surfaces emit `module_contract`
- `surface_kind=control_plane` emits control-plane task types
- `surface_kind=external_integration` emits integration task types
- `surface_kind=ui_only` emits only page/schema work
- `surface_kind=workflow` is delegated to AgentGenerator artifacts plus page
  launch wiring where needed

### 4.2 New AppBuild Surface Layer

Add a new `AppSurface` model in:

- `factory_app/workflows/AppGenerator/structured_outputs.yaml`

Recommended fields:

```yaml
  AppSurface:
    type: model
    fields:
      surface_id:
        type: str
      surface_kind:
        type: literal
        values:
          - module
          - workflow
          - control_plane
          - external_integration
          - ui_only
      source_capability_pack_ids:
        type: optional_list
        items: str
      title:
        type: str
      primary_entities:
        type: optional_list
        items: str
      primary_pages:
        type: optional_list
        items: str
      owned_events:
        type: optional_list
        items: str
      depends_on_surfaces:
        type: optional_list
        items: str
```

Then add to `AppBuildPlan`:

```yaml
      surfaces:
        type: optional_list
        items: AppSurface
        description: Final realization surfaces derived from design docs and concept planning.
```

### 4.3 New Task Types

Add to `AppBuildTask.task_type`:

- `control_plane_surface`
- `integration_surface`

Keep:

- `module_contract`
- `page_bundle`
- `backend_foundation`
- `admin_config`

But make them surface-aware instead of pack-only.

## 5. Recommended Example Decomposition

For a hosted product like:

- build apps with Mozaiks
- host those apps with us
- create marketing campaigns
- participate in investor marketplace
- distribute revenue via Stripe Connect

The target realization should look more like:

```yaml
surfaces:
  - surface_id: build_journey
    surface_kind: control_plane
    source_capability_pack_ids: [custom_domain]

  - surface_id: app_registry
    surface_kind: module
    source_capability_pack_ids: [crud_pack]

  - surface_id: hosting
    surface_kind: module
    source_capability_pack_ids: [custom_domain]

  - surface_id: campaigns
    surface_kind: module
    source_capability_pack_ids: [campaigns_pack]

  - surface_id: investor_marketplace
    surface_kind: module
    source_capability_pack_ids: [marketplace_pack]

  - surface_id: revenue_share
    surface_kind: module
    source_capability_pack_ids: [billing_pack]

  - surface_id: stripe_connect
    surface_kind: external_integration
    source_capability_pack_ids: [billing_pack]

  - surface_id: campaign_optimizer
    surface_kind: workflow
    source_capability_pack_ids: [campaigns_pack]

  - surface_id: communications
    surface_kind: module
    source_capability_pack_ids: [messaging_pack]
```

This is the type of output the refactor should make possible.

## 6. Migration Order

Implement in this order:

1. `ValueEngine`
   - add `surface_candidate_hints`
   - preserve them in build plan persistence
   - decouple hosted module naming
2. `DesignDocs`
   - add required `surface_map` contract
   - add ownership rules for pages/events/surfaces
3. `AgentGenerator`
   - rename `modules` -> `workflow_stages`
   - rename `module_index` -> `stage_index`
   - consume `surface_map` and ignore non-workflow surfaces
4. `AppGenerator`
   - introduce `surfaces`
   - emit task types from `surface_kind`
   - stop mapping every business capability pack directly to a module

## 7. Summary

The correct mental model after this refactor is:

- `ValueEngine` suggests
- `DesignDocs` decides
- `AgentGenerator` builds workflows
- `AppGenerator` builds app/runtime surfaces

That is the clean boundary the current system is missing.
