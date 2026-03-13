# Workflow Authoring Contracts

This document defines the strict authoring rules for workflows built on mozaiks.

It is written for two audiences:

- human workflow authors
- generator/developer agents that produce workflow bundles

Its purpose is to answer one question clearly:

`Which primitive should a workflow use for a given behavior, and what files/contracts must exist for that behavior to be valid?`

When other workflow-writing guides conflict with this document, this document wins.

---

## Scope

This document covers:

- AG2 text pauses (`InputRequestEvent`)
- structured UI pauses (`use_ui_tool(...)`)
- context-variable updates from UI responses
- handoff timing and routing contracts
- native tool calling vs `auto_tool_mode`
- structured outputs vs tool annotations
- lifecycle tool boundaries
- MFJ authoring rules
- current AG-UI authoring stance

This document does not define:

- frontend visual design
- product-specific workflow ideas
- runtime implementation details beyond the authoring contract

---

## Core Rule

Workflows must choose the smallest valid primitive for the job.

Use this decision table:

| Need | Canonical primitive | Notes |
|---|---|---|
| User replies with plain chat text | AG2 native text pause (`InputRequestEvent` / handoff-to-user) | Use when the next input is just a message |
| User must fill a form, click buttons, choose options, or return typed JSON | `use_ui_tool(...)` | Use when backend needs structured response data |
| Agent should decide whether to call a tool | Native AG2 tool calling | Use tool schema and prompt guidance |
| Runtime must deterministically invoke a tool from agent output | `auto_tool_mode` + `structured_outputs` | Use for anti-hallucination and deterministic execution |
| Routing depends on user/UI-updated state | `context_conditions` with `condition_scope: pre` | Do not rely on `after_work` for this |
| Setup, cleanup, logging, metrics, resource prep | `lifecycle_tools` | Not for primary business routing |
| Parent/child fan-out fan-in across workflows | MFJ runtime orchestration | Do not misuse text pause primitives for orchestration |

---

## Contract 1: Text Human Checkpoints

### Use when

- the user can answer in ordinary chat text
- the response is a single message
- the backend does not require a custom component

### Canonical primitive

- AG2 native human pause semantics
- in practice: handoff-to-user / `InputRequestEvent`

### Authoring rules

Workflow authors must treat this as a `text` response path, not a typed JSON contract.

If the text response affects routing, the workflow must normalize that text before routing by using one of these patterns:

- a classifier agent that converts text into structured state
- a tool that parses the text and writes context variables

Do not route critical business logic directly off raw user prose when a typed state flag would be safer.

### Required files/contracts

- `orchestrator.yaml`
- `agents.yaml`
- `handoffs.yaml`
- `context_variables.yaml` if the user text must be normalized into state

### Forbidden patterns

- do not treat `InputRequestEvent` as a structured form response
- do not expect nested JSON from a plain text pause
- do not depend on `after_work` timing for user-updated state

---

## Contract 2: Structured UI Human Checkpoints

### Use when

- the frontend must render a component
- the user must return structured data
- the backend needs deterministic fields back from the UI

### Canonical primitive

- `use_ui_tool(...)`

This is not only a rendering primitive. It is a full round-trip primitive:

1. emit UI component request
2. pause backend execution
3. receive structured frontend response
4. optionally project response into context variables
5. continue workflow execution

### Required files/contracts

- `tools.yaml` entry for the UI tool
- frontend component implementation and registration
- response shape defined by component contract
- `context_variables.yaml` bindings if the response affects routing
- `handoffs.yaml` using `context_conditions` with `condition_scope: pre` if routing depends on the response

### Canonical state-binding pattern

If a UI response must drive routing, bind it into a `state` variable using a `ui_response` trigger.

Example:

```yaml
context_variables:
  definitions:
    approval_status:
      type: string
      description: Tracks the user's approval decision
      source:
        type: state
        default: pending
        transitions:
          - from: pending
            to: approved
            trigger:
              type: ui_response
              tool: approval_gate
              response_key: decision
```

Then route with a pre-scope context handoff:

```yaml
handoffs:
  handoff_rules:
    - source: user
      target: NextAgent
      handoff_type: condition
      condition_type: expression
      expression: ${approval_status} == "approved"
      condition_scope: pre
```

### Forbidden patterns

- do not use `after_work` to catch a UI response that arrives after the previous agent turn
- do not rely on freeform chat text when the decision should be typed and auditable
- do not emit UI without an explicit response contract

---

## Contract 3: Handoff Timing

### Rule

If routing depends on state that may change between turns, use:

- `context_conditions`
- `condition_scope: pre`

### Use `after_work` only when

- the next step should happen immediately after an agent finishes
- no later user/UI interaction needs to change the decision

### Practical guidance

- `after_work` is for step completion
- `context_conditions` with `pre` is for decisions that must see the latest state

This matters especially for:

- approvals
- revisions
- accept/reject flows
- any `use_ui_tool(...)` response that updates context variables

---

## Contract 4: Native Tool Calling vs Auto Tool

### Native AG2 tool calling

Use this when:

- the agent should decide whether to call the tool
- the tool is part of normal agent reasoning
- minor variance is acceptable

Required:

- good tool docstrings
- strong parameter descriptions
- `Annotated[...]` parameter metadata where appropriate

### `auto_tool_mode`

Use this when:

- tool invocation must be deterministic
- the agent must emit a typed object, not decide freely
- you want anti-hallucination guarantees stronger than prompt wording alone
- a structured output should map directly to tool execution

Required:

- `agents.yaml`: `auto_tool_mode: true`
- `agents.yaml`: `structured_outputs_required: true`
- `structured_outputs.yaml`: model registered for that agent
- `tools.yaml`: mapped tool exists and is explicitly configured for auto invocation

Generator agents must prefer explicit configuration over implicit defaults. If a workflow uses auto-tool mode, the tool entry should declare `auto_invoke` explicitly.

### Strict rule

For a given step, choose one mode:

- native tool calling
- or auto-tool execution

Do not prompt the same agent to both:

- manually decide to call a tool
- and emit structured output for runtime auto-invocation

That creates duplicate or conflicting execution paths.

---

## Contract 5: Structured Outputs vs Tool Annotations

These primitives solve different problems.

| Primitive | Solves |
|---|---|
| Tool docstrings + `Annotated[...]` | Better tool schema and parameter calling |
| `structured_outputs` | Forces agent output into a typed response shape |
| `auto_tool_mode` | Lets runtime deterministically execute a tool from structured output |

### Rules

- `Annotated[...]` does not replace structured outputs
- structured outputs do not replace tool parameter metadata
- if deterministic execution matters, use structured outputs plus `auto_tool_mode`
- if normal tool choice is acceptable, use native AG2 tool calling with strong tool schemas

### Anti-hallucination guidance

Prefer `auto_tool_mode` for:

- side-effecting tools
- irreversible actions
- workflow triggers
- MFJ trigger agents
- UI tools that must always fire when a schema-valid result is produced

Prefer native tool calling for:

- advisory tools
- lookup tools
- optional helper tools used inside normal reasoning

---

## Contract 6: Lifecycle Tools

### Allowed uses

- logging
- metrics
- validation
- resource setup
- resource cleanup
- context preparation

### Not allowed as primary workflow logic

Generator agents must not use `lifecycle_tools` as the primary mechanism for:

- business routing
- approval decisions
- user-facing checkpoints
- MFJ orchestration

Even though the runtime can compensate frontend visibility when a lifecycle tool pauses execution, that is not the preferred authoring path for normal workflow interactions.

### Rule

If the workflow needs a human checkpoint, use:

- AG2 text pause for plain text
- `use_ui_tool(...)` for structured UI response

Do not hide core workflow behavior inside `before_agent` or `after_agent` hooks.

---

## Contract 7: MFJ Authoring

### Rule

MFJ orchestration belongs to runtime coordination, but workflows must still author the trigger, resume, and routing contracts correctly.

### Trigger contract

The MFJ trigger step should be driven by structured output, not vague prose.

Best practice:

- trigger agent emits typed structured output
- runtime extracts child run plan
- runtime performs fan-out and fan-in
- parent resumes at a dedicated router or presenter agent

### Human checkpoints in MFJ

Human checkpoints are valid in three places:

- before fan-out
- inside a child workflow
- after fan-in when parent resumes

### Recommended post-fan-in pattern

1. parent resumes at `PresenterAgent` or `HostAgent`
2. presenter shows results
3. presenter or tool emits `use_ui_tool(...)` if structured approval is needed
4. UI response updates `state` variables
5. next routing decision uses `context_conditions` with `condition_scope: pre`

### Forbidden patterns

- do not use `InputRequestEvent` as a substitute for parent/child orchestration
- do not encode child-run plans in freeform text when structured outputs are available
- do not rely on `after_work` to catch post-fan-in approvals

---

## Contract 8: AG-UI

### Current authoring stance

Workflow authors and generator agents must not author workflows directly against raw AG-UI event names at this time.

Reason:

- frontend has partial AG-UI handling
- backend is not yet the canonical AG-UI emitter
- AG-UI would standardize transport envelopes, but it does not replace workflow contracts

### Current rule

Author against Mozaiks canonical primitives:

- AG2 text pause
- `use_ui_tool(...)`
- `context_variables`
- handoffs
- structured outputs
- lifecycle tools
- MFJ contracts

AG-UI is a protocol concern, not a workflow authoring primitive.

---

## Generator Agent Checklist

Every generator/developer agent that creates workflows must answer these questions explicitly for each step:

1. Does this step require human input?
2. If yes, is the input plain text or structured UI data?
3. If the response affects routing, which `state` variable records it?
4. Which handoff reads that state, and is it `condition_scope: pre`?
5. Is tool invocation model-chosen or runtime-deterministic?
6. If deterministic, does the agent have `auto_tool_mode: true` and a registered structured output model?
7. Is any lifecycle tool being used for real workflow logic that should instead live in handoffs, tools, or context?
8. Is this step part of an MFJ trigger, child workflow, fan-in, or resume path?

If the generator agent cannot answer those questions, it is not ready to emit the workflow.

---

## Minimum Output Contract For Generator Agents

When generating a workflow that uses a capability, the generator must emit these artifacts:

| Capability | Required artifacts |
|---|---|
| Text checkpoint | `orchestrator.yaml`, `agents.yaml`, `handoffs.yaml` |
| Structured UI checkpoint | `tools.yaml`, frontend component registration, response contract |
| UI-driven routing | `tools.yaml`, `context_variables.yaml`, `handoffs.yaml` with `condition_scope: pre` |
| Auto-tool execution | `agents.yaml`, `structured_outputs.yaml`, `tools.yaml` |
| Lifecycle hooks | `tools.yaml` plus hook file |
| MFJ | trigger workflow config, child workflow references, parent resume routing contract |

---

## Anti-Patterns

- using lifecycle tools as secret workflow routers
- routing on raw user prose when a state variable should exist
- mixing manual tool instructions with auto-tool mode for the same step
- using `after_work` for decisions that depend on later UI responses
- authoring raw AG-UI event names into workflow logic
- treating `use_ui_tool(...)` as display-only and forgetting the response/state contract

---

## Recommended Prompt Contract For Developer Agents

When instructing a developer/generator agent to build a workflow, require it to state:

1. the human checkpoint type for each step: `none`, `text_pause`, or `ui_pause`
2. the routing state variables it will create
3. whether each tool step is `native_tool` or `auto_tool`
4. whether any lifecycle hook is used, and why it is not business routing
5. whether the workflow contains MFJ behavior and where the human checkpoints occur

The developer agent should be rejected if it produces a workflow without those declarations.

---

## Bottom Line

Workflows in mozaiks must be authored against explicit contracts:

- text pauses are AG2-native
- structured UI pauses are `use_ui_tool(...)`
- routing after user/UI interaction must use typed state plus pre-scope context handoffs
- `auto_tool_mode` is for deterministic schema-to-tool execution
- lifecycle tools are boundary hooks, not hidden workflow brains
- AG-UI is optional protocol alignment, not a substitute for workflow design


