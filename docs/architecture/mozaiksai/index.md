# MozaiksAI Runtime

These docs cover the lower-level runtime architecture implemented under
`mozaiksai/`: orchestration, handoffs, transport, hooks, context variables,
token controls, and UI interaction mechanics.

Use this section after you understand the high-level architecture and workflow
model. If you are evaluating Mozaiks runtime differentiators first, start with
[Workflow Task Batches](task-batches.md).

## Workflow and Orchestration

- [Workflow Task Batches](task-batches.md)
- [Universal Orchestrator](universal-orchestrator.md)
- [Pack Graph Semantics](pack-graph-semantics.md)
- [Handoff Context Conditions](handoff-context-conditions.md)

## UI and Interaction

- [UI Interaction Patterns](ui-interaction-patterns.md)
- [Lifecycle Tools](lifecycle-tools.md)

## Runtime and Transport

- [Transport and Streaming](transport-and-streaming.md)
- [Hook System Deep Dive](hook-system-deep-dive.md)
- [API Reference Notes](api-reference.md)

## Agent and Contract Notes

- [Context Variables Complete](context-variables-complete.md)
- [Auto Tool Execution](auto-tool-execution.md)

## Tokens and Cost Control

- [Monetization Contract](monetization-contract.md)
- [Core Monetization Scope](core-monetization-scope.md)
- [Token Management](token-management.md)

## Reading Order

Use these docs to answer precise runtime questions after you know the
high-level model from:

- [Architecture Overview](../index.md)
- [Architecture Foundations](../foundations/overview.md)
- [Workflow Architecture](../workflows/workflow-architecture.md)
