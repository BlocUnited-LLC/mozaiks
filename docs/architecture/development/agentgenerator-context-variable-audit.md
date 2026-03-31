# AgentGenerator Context Variable Audit

## Status

This audit captures the current `AgentGenerator` context-variable contract, how values are actually consumed, and a recommended v2 model focused on workflow quality (not backward compatibility).

Scope:

- `mozaiks-platform/app/workflows/AgentGenerator/context_variables.yaml`
- `mozaiks-platform/app/workflows/AgentGenerator/agents.yaml`
- `mozaiks-platform/app/workflows/AgentGenerator/tools/*.py`
- `mozaiks-platform/app/workflows/AgentGenerator/handoffs.yaml`

## Key Findings

1. The contract is drifted across declaratives.
2. Several declared variables are prompt-only or never read by tools/handoffs.
3. Several keys used in prompts/tools are not declared in `context_variables.yaml`.
4. Pack-loop state variables are declared but not explicitly written in this workflow tool layer.

## Contract Drift (High Priority)

Strict parser validation currently fails for multiple AgentGenerator declaratives:

- `orchestrator.yaml`: uses `startup_mode` instead of canonical `workflow_startup_mode`.
- `context_variables.yaml`: uses legacy list shape for `definitions` and `agents`.
- `structured_outputs.yaml`: wrapped under `structured_outputs` root instead of canonical shape.
- `tools.yaml`: lifecycle entries include unsupported fields for canonical lifecycle schema.
- `handoffs.yaml`: rule `description` fields are extra under current strict contract.

Implication:

- Any context-variable redesign should happen after canonical contract normalization, or the runtime will not consistently enforce/consume what is authored.

## Variable Usage Snapshot (Current)

Declared variables: 23

Routing-critical (actively used in handoff conditions):

- `interview_complete`
- `action_plan`
- `action_plan_acceptance`
- `pack_generation_complete`

Tool-read/write critical:

- Read by tools: `action_plan`, `workflow_strategy`, `technical_blueprint`, `context_include_schema`, `context_schema_db`, `is_multi_workflow`, `pack_name`, `workflows_spec`
- Written by tools: `action_plan`, `action_plan_acceptance`, `workflow_strategy`, `strategy_ready`, `technical_blueprint`, `download_complete`

Prompt-only signals (mentioned in prompts, not read by tools):

- `context_aware`
- `concept_overview`
- `monetization_enabled`
- `chat_attachments`
- `macro_workflow_graph`
- `current_workflow_index`

Declared but weakly connected:

- `generated_workflows` (declared/exposed but not explicitly set in current workflow tool layer)
- `pack_generation_complete` (declared and routed on, but no explicit set found in current workflow tools)

Undeclared-but-used keys (examples):

- Runtime/session: `app_id`, `chat_id`, `user_id`, `workflow_name`
- Schema runtime: `database_schema_available`, `database_schema_db`, `schema_overview`, `collections_first_docs_full`
- Workflow internals: `PatternSelection`, `action_plan_workflows`, `mermaid_sequence_diagram`, `mermaid_diagram_ready`, `mermaid_diagram_metadata`
- Prompt references: `is_child_workflow`, `decomposition_required`

## Why Quality Feels Low

The system is currently mixing four different classes of context without a clean boundary:

1. Seed context (from DB/config)
2. Intent context (interview-derived, should drive planning quality)
3. Build artifacts (strategy/blueprints/action plan)
4. Runtime/session internals (routing/UI/cache transport keys)

When those are mixed, prompts receive noisy context and tools rely on ad-hoc keys, which reduces deterministic generation quality.

## Recommended v2 Context Model

Keep top-level orchestration booleans small, move rich planning signal into structured objects.

Top-level orchestration state:

- `interview_complete: bool`
- `action_plan_acceptance: \"pending\" | \"accepted\" | \"adjustments_requested\"`
- `pack_generation_complete: bool`
- `current_workflow_index: int`

Seed context (input-only):

- `seed_concept: object`
  - `overview: str | null`
  - `api_endpoints: list`
  - `blueprint: object | null`
  - `attachments: list`
  - `monetization_enabled: bool`
  - `context_aware: bool`

Intent context (must be generated from interview):

- `intent_brief: object`
  - `goal`
  - `success_criteria`
  - `trigger_mode`
  - `human_in_loop`
  - `integrations`
  - `assets`
  - `constraints`
  - `open_questions`

Pattern/pack context:

- `pattern_selection: object` (single source of truth for `is_multi_workflow`, `pack_name`, `workflows`)

Build artifacts:

- `workflow_strategy: object`
- `technical_blueprint: object`
- `action_plan: object`

This reduces key sprawl and creates deterministic provenance: seed -> intent -> pattern -> build.

## Agent Impact Map (When Refactoring)

InterviewAgent:

- Replace direct dependency on `concept_*` with `seed_concept`.
- Add responsibility to produce/update `intent_brief`.

PatternAgent:

- Read `intent_brief` as primary signal, `seed_concept` as fallback.
- Write `pattern_selection` only (avoid duplicated `is_multi_workflow/pack_name/workflows_spec` keys).

WorkflowStrategyAgent onward:

- Read `pattern_selection` and `intent_brief`.
- Avoid reading raw seed DB fields directly unless needed.

ContextVariablesAgent / file generators:

- Consume stable build artifacts (`workflow_strategy`, `technical_blueprint`, `action_plan`) only.
- Keep runtime/session keys out of authored context contract.

## Proposed Execution Plan

1. Normalize declarative contracts to canonical schema.
2. Introduce `intent_brief` and `pattern_selection` as first-class context keys.
3. Collapse duplicated pack keys into `pattern_selection`.
4. Update prompts to reference `seed_concept` and `intent_brief` instead of scattered keys.
5. Remove or demote weak variables after usage verification.
6. Add a contract test that fails when declared keys are neither read nor routed.

## Immediate No-Risk Step

Run AgentGenerator with placeholder seed data (already enabled via `MOZAIKS_CONTEXT_PLACEHOLDERS_FILE`) and inspect whether `intent_brief`-style fields are missing from downstream prompts. That will confirm the redesign priority before changing all agents.
