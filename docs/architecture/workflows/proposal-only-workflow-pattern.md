# Proposal-Only Workflow Pattern

## Purpose

A **proposal-only workflow** (archetype: `proposal_only`) is a human-in-the-loop
workflow that produces a structured plan, review brief, or proposal artifact for
operator review. The workflow itself takes no irreversible action. All phases or
steps in the output artifact are `status=proposed` or `status=blocked` — they are
never executed automatically.

This pattern separates **planning** from **execution**. The workflow produces a
proposal; a human reviews it; a downstream execution or approval workflow (if
needed) performs the actual actions.

---

## When to Use

Use `proposal_only` when the workflow's purpose is to **plan, assess, or
recommend** rather than to execute:

- Compliance review and gap assessment
- Migration planning (data, infrastructure, DNS, registrar)
- Deployment impact assessment and remediation planning
- Data cleanup or deduplication proposals
- Content moderation review briefs
- Change proposal reviews (schema, API, config)
- Any high-stakes operation that requires explicit operator sign-off

Do **not** use `proposal_only` when the workflow is expected to write data or
call external write APIs during the session. Use the `standard` archetype instead.

---

## AppGenerator Routing

When AppPlanAgent plans a capability pack with `surface_kind: workflow` whose
purpose is planning or assessment, the `[WORKFLOW ARCHETYPE CONTEXT]` injected
section provides guidance to select `proposal_only`. The archetype specifies:

- Orchestrator defaults (including `human_in_the_loop: true`)
- The canonical agent sequence
- Tool constraints
- Required structured output fields
- Output invariants that the OutputAgent must enforce

---

## Orchestrator Shape

```yaml
workflow_name: ContentReviewWorkflow
max_turns: 30
human_in_the_loop: true          # Always true for proposal_only
workflow_startup_mode: AgentDriven
orchestration_pattern: ag2_network
initial_agent: IntakeAgent
```

**Rule:** `human_in_the_loop: true` is required and non-negotiable for
proposal-only workflows.

---

## Canonical Agent Sequence

```
IntakeAgent → PlannerAgent [→ ReviewerAgent (optional)] → OutputAgent
```

### IntakeAgent

- Gathers operator scope: subject, tenant, domain, parameters.
- Confirms scope with the operator.
- Emits `NEXT` on confirmation.
- Completely read-only. Does not call module write actions or provider APIs.
- `structured_outputs_required: false`

### PlannerAgent

- Calls read-only tools: `list_*`, `get_*`, `read_*`.
- Synthesizes findings, recommendations, phases, risks, and assumptions.
- Emits `NEXT` when the analysis is ready for serialization.
- Must not call `execute_action`, `approve`, `create_*`, `update_*`, or `delete_*`.
- `structured_outputs_required: false`

### ReviewerAgent (optional)

- Second-opinion agent for stakeholder review.
- May inject additional risks or caveats.
- Must not approve or execute anything.
- `structured_outputs_required: false`

### OutputAgent

- Serializes the proposal into the top-level structured output model.
- **Must enforce all output invariants** — regardless of what prior agents produced.
- Outputs JSON only (no prose).
- `structured_outputs_required: true`
- Has a `save_*` tool with `auto_tool_call: true` to persist the artifact.

---

## Tools

```yaml
tools:
  # ── Read-only tools ────────────────────────────────────────────────────────
  - agent: PlannerAgent
    file: tools/list_items.py
    function: list_items
    description: >
      Lists relevant records. Read-only. Does NOT create, update, or delete.
    tool_type: Agent_Tool
    auto_tool_call: false

  - agent: PlannerAgent
    file: tools/get_snapshot.py
    function: get_snapshot
    description: >
      Fetches a data snapshot. Read-only. Does NOT mutate the snapshot.
    tool_type: Agent_Tool
    auto_tool_call: false

  # ── Proposal artifact save tool ────────────────────────────────────────────
  - agent: OutputAgent
    file: tools/save_proposal.py
    function: save_proposal
    description: >
      Persists the proposal artifact to context and emits a UI artifact.
      Does NOT execute any phase. human_review_required enforced as true.
    tool_type: Agent_Tool
    auto_tool_call: true         # auto-called after OutputAgent speaks

lifecycle_tools: []
```

**Tool rules:**
- All tool functions must start with `list_`, `get_`, `read_`, or `save_`.
- `execute_action` must not appear as a callable tool.
- The `save_*` tool on OutputAgent must have `auto_tool_call: true`.
- No write-path tools (`create_*`, `update_*`, `delete_*`) may be callable in the workflow.

---

## Structured Output

### Minimum required fields

```yaml
models:
  ChangeProposalBrief:
    type: model
    description: >
      PLAN ONLY. No action is executed by this workflow.
      human_review_required is always true.
    fields:
      proposal_id:
        type: str
        description: Unique identifier for this proposal (uuid4 recommended).
      artifact_type:
        type: str
        description: >
          Semantic type of this proposal
          (e.g. migration_plan, compliance_brief, deployment_assessment).
      human_review_required:
        type: bool
        description: >
          Always true. This workflow takes no irreversible action.
          Operator review is required before any change is made.
      status:
        type: str
        description: >
          Always "proposed" in workflow output.
          Never "approved", "active", "in_progress", or "executed".
      risks:
        type: list
        items: str
        description: Identified risks or open questions for operator validation.
      assumptions:
        type: list
        items: str
        description: Key assumptions the proposal is based on.
      next_step:
        type: NextStepRef
        description: >
          Operator instructions for advancing the proposal after review.
          next_step.requires_human_approval is always true.
```

### Output invariants

The following invariants must be enforced by OutputAgent through explicit
prompt instructions. They are **not** currently enforced at the schema level
(schema-level invariant metadata is a future enhancement, described below).

| Field | Invariant |
|---|---|
| `human_review_required` | Always `true` |
| `status` (top-level and all phases/steps) | `proposed` or `blocked` only |
| `next_step.requires_human_approval` | Always `true` |
| Tool functions | Must start with `list_`, `get_`, `read_`, or `save_` |
| `execute_action` | Never callable in this workflow |

**OutputAgent `[OUTPUT FORMAT]` section must explicitly state all invariants:**

```yaml
- id: output_format
  heading: "[OUTPUT FORMAT]"
  content: |
    Output ONLY valid JSON matching the ChangeProposalBrief schema. No prose.

    Critical invariants — enforce these regardless of prior agent output:
    - human_review_required: true (always — never false)
    - next_step.requires_human_approval: true (always)
    - All status values must be "proposed" or "blocked".
      Never "approved", "active", "in_progress", or "executed".
    - No provider secrets or credentials in the output.
    - No instructions to execute phases automatically.
```

> **Future enhancement:** Add schema-level invariant metadata to
> `structured_outputs.yaml` so the runtime can validate invariants without
> relying solely on agent prompt text. Proposed shape:
>
> ```yaml
> invariants:
>   - field: human_review_required
>     equals: true
>   - field: status
>     allowed_values: [proposed, blocked]
> ```

---

## Blocked/Deferred Phase Pattern

When a proposal includes phases that cannot be executed in the current product
phase (write adapters not yet implemented, credentials not yet available,
dependency not yet released), those phases are represented as **blocked** —
not omitted.

### Why blocked phases must appear in output

Operators benefit from seeing blocked phases so they understand what the
workflow cannot do yet. A phase that is hidden is a phase the operator
cannot plan around.

### Blocked phase fields

```yaml
phases:
  - phase_id: dangerous_cutover
    phase_type: nameserver_cutover
    status: blocked              # never proposed/approved/executed
    blocked_reason: WRITE_NOT_IMPLEMENTED
    deferred_to: Phase 3D        # phase or condition when this becomes executable
    risk_level: high
    rollback_note: >
      Revert nameservers to previous values via the registrar admin panel.
      DNS propagation takes up to 48 hours.
```

### Rule

A tool call returning `WRITE_NOT_IMPLEMENTED` or a similar error code is a
signal to mark the related phase as `blocked` — not to remove it from the plan.

### Blocked phase structured output fields

Add these fields to phase/step models in proposal_only workflows:

```yaml
MigrationPhase:
  type: model
  fields:
    status:
      type: str
      description: >
        Always "proposed" (planned) or "blocked" (not yet executable).
        Never "approved", "in_progress", or "executed".
    blocked_reason:
      type: optional_str
      description: >
        If status="blocked": error code or reason (e.g. WRITE_NOT_IMPLEMENTED).
        Null when status="proposed".
    deferred_to:
      type: optional_str
      description: >
        Phase or condition when this will become executable (e.g. "Phase 3D").
        Null when status="proposed".
```

---

## Handoffs

```yaml
transition_rules:
  # Intake → Planner on scope confirmation
  - source_agent: IntakeAgent
    target_agent: PlannerAgent
    transition_type: condition
    condition_type: context_equals
    condition_key: intake_confirmed
    condition_value: true
    transition_target: AgentTarget

  # Intake waits for operator between turns
  - source_agent: IntakeAgent
    target_agent: user
    transition_type: after_turn
    transition_target: RevertToUserTarget

  # Planner → Output when proposal is ready
  - source_agent: PlannerAgent
    target_agent: OutputAgent
    transition_type: condition
    condition_type: context_equals
    condition_key: proposal_ready
    condition_value: true
    transition_target: AgentTarget

  # Output → user to present the proposal
  - source_agent: OutputAgent
    target_agent: user
    transition_type: after_turn
    transition_target: RevertToUserTarget

  # Operator terminates session
  - source_agent: user
    target_agent: terminate
    transition_type: condition
    condition_type: context_equals
    condition_key: proposal_review_complete
    condition_value: true
    transition_target: TerminateTarget
```

---

## Context Variables

```yaml
definitions:
  # ── Intake inputs ───────────────────────────────────────────────────────────
  tenant_id:
    type: string
    description: Tenant scope for this proposal.
    source:
      type: state
      default: null

  intake_confirmed:
    type: boolean
    description: True once the operator confirms the review scope.
    source:
      type: state
      default: false
      triggers:
        - type: agent_text
          agent: IntakeAgent
          ui_hidden: true
          match:
            equals: NEXT

  # ── Analysis outputs ─────────────────────────────────────────────────────
  proposal_artifact:
    type: object
    description: The finalized proposal artifact. Set by the save_* tool.
    source:
      type: state
      default: null

  proposal_ready:
    type: boolean
    description: True once PlannerAgent has completed the proposal plan.
    source:
      type: state
      default: false
      triggers:
        - type: agent_text
          agent: PlannerAgent
          ui_hidden: true
          match:
            equals: NEXT

  proposal_review_complete:
    type: boolean
    description: True once the operator completes the proposal review.
    source:
      type: state
      default: false
```

---

## What Proposal-Only Workflows Must NOT Do

- Change data, infrastructure, or external system state.
- Call `execute_action` or `approve` as workflow actions.
- Set any phase/step status to `approved`, `active`, `in_progress`, or `executed`.
- Set `human_review_required` to `false`.
- Call provider write APIs (DNS, registrar, storage, external SaaS).
- Omit blocked phases from the output artifact.
- Proceed to a next step without instructing the operator.

---

## Future Activation Path

When blocked phases become executable (write adapters implemented, credentials
issued, dependency released):

1. The downstream execution or approval workflow gains access to the write-path actions.
2. The operator launches that workflow with the approved proposal's phase list.
3. Per-phase/step approval gates advance each item through the approval state machine.
4. After approval: the execution workflow calls the write-path actions.
5. Post-execution: an assurance or validation pass confirms success.
6. Decommission or cleanup actions (if any) run as a final phase.

---

## Relationship to Other Patterns

| Pattern | Purpose | Executes actions? |
|---|---|---|
| `proposal_only` | Plan, assess, recommend | No — output is a proposal |
| `standard` | Execute tasks with HITL scope gate | Yes — after scope confirmation |
| Quality gate (AppGenerator) | Validate generated artifacts | No — sets passed/blocked status |
| Task batch | Workflow-local parallel task execution | Depends on worker archetype |

---

## Checklist for New Proposal-Only Workflows

- [ ] `orchestrator.yaml` has `human_in_the_loop: true`
- [ ] All callable tool functions start with `list_`, `get_`, `read_`, or `save_`
- [ ] No `execute_action` tool declared
- [ ] OutputAgent has `structured_outputs_required: true`
- [ ] Top-level output model has `human_review_required: bool` field
- [ ] OutputAgent `[OUTPUT FORMAT]` section explicitly states all invariants
- [ ] Blocked phases have `status`, `blocked_reason`, and `deferred_to` fields
- [ ] `next_step` field guides operator to the downstream execution workflow
- [ ] `README.md` states "PLAN ONLY" and "no action is executed by this workflow"
- [ ] No provider secrets or credentials in any workflow YAML file

