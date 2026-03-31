# Auto-Tool Execution

This note explains the current role of `auto_tool_mode` in Mozaiks.

## Runtime Meaning

`auto_tool_mode: true` means the runtime may deterministically invoke a mapped tool after validating a structured output.

Use it when you want:

- predictable side effects
- runtime-controlled tool execution
- less prompt ambiguity around whether a tool should fire

Do not use it just because an agent has structured outputs.

## Important Clarification

AG2 can now support structured outputs and normal tool calling together.

`auto_tool_mode` is a Mozaiks execution mode for cases where the runtime, not the model, should own the final tool invocation step.

## Related Docs

- [Workflow Authoring Contracts](../../architecture/foundations/workflow-authoring-contracts.md)
- [AG2 Touchpoints and Extensions](ag2-touchpoints-and-extensions.md)
- [UI Interaction Patterns](ui-interaction-patterns.md)
