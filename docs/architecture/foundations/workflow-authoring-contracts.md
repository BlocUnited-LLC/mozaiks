# Workflow Authoring Contracts

This document defines the authoring rules for workflows in Mozaiks.

It exists to stop two failure modes:

- forcing substrate behavior into workflows
- forcing workflow behavior into substrate actions

## Decision Table

Use this table before authoring a workflow.

| Need | Canonical primitive |
| --- | --- |
| Persistent page, list, detail, form, board | module plus view |
| Deterministic mutation or service call | action |
| Event-driven automation policy | automation route |
| Multi-turn reasoning, orchestration, or HITL | workflow |

If the capability does not clearly need reasoning or orchestration, do not start
with a workflow.

## Workflow Inputs

Workflows should accept one of two input classes:

### Direct user input

The workflow is entered explicitly by a user.

### Automation payload input

The workflow is started or resumed because an automation route matched a domain
event.

In that case, the route should normalize event payload into workflow input or
context variables before agent reasoning begins.

## Workflow Authoring Rules

### 1. Keep domain-event policy out of the workflow

Do not author workflows that assume they are always triggered by a hardcoded
domain event.

The route from domain event to workflow is automation policy, not workflow
authoring.

### 2. Keep CRUD out of the workflow unless reasoning is required

A workflow may call actions or tools that mutate business state, but it should
not replace the app substrate for ordinary data operations.

### 3. Use the smallest valid human checkpoint

Use:

- AG2 text pause or handoff-to-user for plain text input
- `use_ui_tool(...)` for structured UI responses

Do not use chat text when the system needs typed fields back.

### 4. Route off typed state, not raw prose

If user input affects routing:

- normalize the input into context variables
- route using `context_conditions`
- prefer `condition_scope: pre`

### 5. Choose one tool execution mode per step

Use either:

- native AG2 tool calling
- or deterministic `auto_tool_mode`

Do not design a step that relies on both.

### 6. Keep groupchat semantics internal

Agent names, handoffs, and internal workflow patterns belong inside the
workflow. They should not leak into:

- domain event types
- module APIs
- shell config

## Required Files

Every workflow bundle should be understandable through:

- `orchestrator.yaml`
- `agents.yaml`
- `handoffs.yaml`
- `context_variables.yaml`
- `structured_outputs.yaml`
- `tools.yaml`
- `ui_config.yaml`
- `hooks.yaml`

Use the workflow directory as the authoring boundary. Do not spread workflow
semantics into substrate config files.

## Human Checkpoint Guidance

### Plain text checkpoint

Use when:

- the user can answer in ordinary chat
- no structured data contract is required

### Structured UI checkpoint

Use when:

- the workflow needs typed user response data
- the frontend must render a specific component
- the response should be auditable and routable

## Artifacts and External State

If a workflow produces something that should persist outside the transcript,
prefer one of these outputs:

- artifact
- action invocation
- integration call

Do not rely on hidden conversational state as the only durable record.

## MFJ and Graph Rules

Use workflow graphs for:

- fan-out and fan-in
- child workflow coordination
- explicit resume structure

Do not use graph files to encode app domain policy or shell behavior.

## Cross References

- [workflow-architecture.md](workflow-architecture.md)
- [declarative-ag2-mapping.md](declarative-ag2-mapping.md)
- [app-creation-guide.md](app-creation-guide.md)
