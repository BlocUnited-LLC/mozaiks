# Agent Dependency Matrix

**Status**: Active  
**Date**: March 11, 2026  
**Purpose**: Define the current upstream dependencies and output contracts for orchestration-aware workflow agents.

## Why This Exists

Mozaiks workflows now rely on strict contracts between:

- interview and canon agents
- decomposition agents
- workflow-transfer agents
- MFJ judge or worker agents
- host / integration agents

This document is the short-form reference for what each agent may read, what it must produce, and how those outputs should be used by runtime orchestration.

## Non-Negotiable Rules

- Agents may reason in prompts; runtime routes from typed fields.
- `workflow_graph.json` never carries prose `logic`.
- If a workflow needs fan-out, add a `DecompositionAgent`.
- If a request may leave the current workflow, emit a typed transfer or change object first.
- If a UI response should affect routing, write it into context before the next handoff is evaluated.

## Canonical Agent Roles

| Agent Role | Reads | Must Produce | Used By |
|---|---|---|---|
| `InterviewAgent` | user message, prior canon summary | normalized seed fields | next canon / planning agent |
| `CanonAgent` | normalized seed fields | canonical object (`AppSpec`, `PitchCanon`, etc.) | persistence, next workflow |
| `ChangeClassifierAgent` | user change request, current canon | `ChangeIntent` | handoffs, workflow transfer |
| `WorkflowTransferAgent` | `ChangeIntent`, current workflow state | `WorkflowTransferRequest` | universal orchestrator |
| `DecompositionAgent` | canon plus current task goal | child workflow specs or task specs | MFJ runtime |
| `JudgeAgent` / `WorkerAgent` | child spec, child seed context | structured child result | fan-in |
| `HostAgent` / `IntegrationAgent` | merged MFJ results | summary, verdict, next-step guidance | user, UI tools |

## Role Contracts

### 1. InterviewAgent

Purpose:

- capture just enough user intent to avoid vague downstream planning

Allowed inputs:

- current user message
- lightweight canon summary if one already exists

Must produce:

- normalized seed fields through a tool call or typed output

Typical fields:

- `pitch_title`
- `pitch_brief`
- `audience`
- `chaos_level`

Should not produce:

- orchestration decisions
- MFJ child specs
- workflow transfers

### 2. CanonAgent

Purpose:

- convert loose user intent into a canonical object that downstream workflows can trust

Allowed inputs:

- normalized interview seed
- optional lightweight research or context

Must produce one canonical object:

- `PitchCanon` for showcase/demo workflows
- `AppSpec` for build workflows

Typical fields:

- `canonical_description`
- `primary_user`
- `core_loop`
- `signature_experience`
- `guardrail`

Runtime expectation:

- the canonical object must be persisted if another workflow depends on it

### 3. ChangeClassifierAgent

Purpose:

- decide whether a user request stays inside the current workflow or requires a higher-level redirect

Must produce:

- `ChangeIntent`

Minimum fields:

- `change_type`
- `change_scope`
- `requires_appspec_revision`
- `requires_replan`
- `requires_new_iteration`
- `target_workflow`
- `rationale`
- `confidence`

Important:

- this agent classifies
- runtime routes

Canonical example:

```json
{
  "change_type": "FOUNDATIONAL",
  "change_scope": "foundational",
  "requires_appspec_revision": true,
  "requires_replan": true,
  "requires_new_iteration": true,
  "target_workflow": "ValueEngine",
  "rationale": "request changes product identity and architecture",
  "confidence": 0.9
}
```

Prompt rule:

- only classifier or transfer-oriented agents should emit `ChangeIntent`
- do not add `ChangeIntent` instructions to every agent in a workflow
- keep this as a shared contract pack, not universal prompt baggage

### 4. WorkflowTransferAgent

Purpose:

- issue an explicit cross-workflow transfer request after classification is complete

Must produce:

- `WorkflowTransferRequest`

Minimum fields:

- `target_workflow`
- `transfer_mode`
- `carry_forward`

Use this only when leaving the current workflow.

### 5. DecompositionAgent

Purpose:

- make fan-out productive by turning canon into bounded child work

Must produce:

- structured child workflow specs for MFJ, or
- structured task specs for a builder/executor

MFJ minimum shape:

- `workflows[]`
- `agent_message`

Each child workflow spec should include:

- `name`
- `description`
- `initial_message`
- `initial_agent`

Build-task variant should additionally include:

- `task_id`
- `owned_paths`
- `depends_on`
- `acceptance_criteria`

Important:

- decomposition output is the input to runtime fan-out
- it is not stored as prose inside `workflow_graph.json`

### 6. JudgeAgent / WorkerAgent

Purpose:

- perform one bounded child evaluation or build task

Allowed inputs:

- child seed context from MFJ
- canonical pitch or app spec slice
- lane/task-specific instructions

Must produce:

- exactly one structured child result

Example evaluation fields:

- `judge_name`
- `lane_focus`
- `highlight`
- `verdict`
- `score`
- `objection`
- `upside`

Example builder fields:

- `task_id`
- `file_manifest`
- `write_batches`
- `validation_status`

### 7. HostAgent / IntegrationAgent

Purpose:

- consume merged child results and convert them into a user-facing decision or preview

Allowed inputs:

- `mfj_*` merged payload
- canonical pitch or app spec
- optional UI response state

Must produce:

- concise user summary
- optional UI tool call
- optional next-step suggestion

Typical outputs:

- verdict
- score
- top objections
- accepted changes
- recommended next iteration

## Handoff Rules

### In-Workflow Handoffs

Use AG2 handoffs for routing between agents inside the same workflow.

Route from context variables, not prose.

Examples:

- `arena_seed_ready == true` -> `DecompositionAgent`
- `_mfj_resume_pending == true` -> `ResumeRouterAgent -> HostAgent`
- `requires_appspec_revision == true` -> `WorkflowTransferAgent`

If a UI response affects routing:

1. capture the response
2. write it to context
3. evaluate the next handoff with `condition_scope: pre`

### Cross-Workflow Handoffs

Cross-workflow routing is a runtime concern.

The workflow should first emit a typed transfer object.

Do not use raw `StringLLMCondition` prose as the cross-workflow contract.

## Current Runtime Note

Structured-output-triggered MFJ now works without fake auto-tool bindings.

Current rule:

- use a registered structured-output model when runtime must observe the output
- use `auto_tool_mode: true` only when the runtime should also auto-execute a tool from that output

## Concrete Mapping in the Demo

The showcase example in this repo maps the generic roles like this:

| Generic Role | Demo Agent |
|---|---|
| `InterviewAgent` | `GreenRoom.ClubHostAgent` |
| `CanonAgent` | `GreenRoom.PremiseCanonAgent` |
| `InterviewAgent` fallback in second workflow | `WritersRoom.WritersHostAgent` |
| `DecompositionAgent` | `WritersRoom.DecompositionAgent` |
| `WorkerAgent` | `WritersRoom.RoastLaneAgent` |
| `WorkerAgent` | `WritersRoom.ObservationalLaneAgent` |
| `WorkerAgent` | `WritersRoom.AbsurdistLaneAgent` |
| `WorkerAgent` | `WritersRoom.CrowdWorkLaneAgent` |
| `HostAgent` | `WritersRoom.HostAgent` |

## Summary

- interview agents normalize
- canon agents define
- classifiers decide
- transfer agents escalate
- decomposition agents prepare fan-out
- child agents do bounded work
- host agents summarize and present

That is the dependency chain generator agents should author against.

