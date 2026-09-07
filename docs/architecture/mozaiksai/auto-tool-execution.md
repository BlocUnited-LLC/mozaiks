# Auto-Tool Execution

This note explains auto-tool execution in Mozaiks.

## How It Works

Auto-tool execution is **derived from tools.yaml**, not agents.yaml. Any agent with a tool marked `auto_tool_call: true` is considered an auto-tool agent.

When an auto-tool agent outputs structured JSON:
1. Runtime validates output against the registered Pydantic model
2. Runtime emits `agent_output_validated` event
3. `AutoToolEventHandler` automatically invokes the tool
4. Tool reads from `context_variables["structured_output"]` and persists/emits

## Runtime Contracts

**Declared structured outputs are exact at runtime.** Every model declared in
`structured_outputs.yaml` compiles with closed-object acceptance: an output
carrying an undeclared field — at the top level or nested — is rejected before
any normalization, no `agent_output_validated` event fires, and no auto tool
runs. Unknown fields are never silently discarded. A field deliberately
declared as `dict`/`optional_dict` stays open inside: arbitrary keys within
that declared open dict remain valid data.

**`structured_output` is a transient, runtime-owned, read-only projection —
not application state.** The runtime exposes the exact validated
`structured_data` for the current turn through a read-only overlay on the
live context:

- tools read it with `context_variables.get("structured_output")`;
  `structured_output` is reserved runtime vocabulary — declaring it in
  `context_variables.yaml` (definitions or agent views) is rejected at
  workflow load, with no metadata override;
- tools cannot set, delete, replace, or mutate it — attempts fail closed;
- it is never written into AG2 workflow state, never persisted, never
  replayed, and never appears in context snapshots; to keep any of it, a tool
  must deliberately save selected data under a different declared application
  key (ordinary declared context writes work unchanged);
- explicit function parameters that match model fields (for example
  `async def save_output(title: str, status: str, context_variables=None)`)
  keep working and receive the same exact validated values — the two views
  never disagree. A context-only tool whose only parameter is
  `context_variables` also works: the structured-output model validates the
  agent's output, not the tool's argument list.

## Configuration

**tools.yaml** - Mark tool for auto-invocation:
```yaml
- agent: OutputAgent
  file: save_output.py
  function: save_output
  tool_type: UI_Surface
  auto_tool_call: true  # This makes OutputAgent an auto-tool agent
  ui:
    component: MyComponent
    mode: artifact
```

If the auto-invoked tool is backend-only, use `tool_type: Agent_Tool` and omit
the `ui` block entirely.

**structured_outputs.yaml** - Register agent to output model (registry fragment;
the full document also requires `schema_version: mozaiks.structured_outputs.v1`
and the corresponding `models` definitions):
```yaml
registry:
  OutputAgent: MyOutputModel
```

**agents.yaml** - Set structured outputs required:
```yaml
- name: OutputAgent
  structured_outputs_required: true
  # No auto-tool field lives in agents.yaml; auto-tool execution is derived from tools.yaml.
```

## When to Use

Use auto-tool execution when you want:
- Guaranteed tool execution after structured output
- Runtime-controlled side effects (persistence, UI artifact emission)
- Predictable behavior without relying on LLM to call tools

Do not use it just because an agent has structured outputs. Some agents output structured JSON for downstream consumption without needing a tool call.

## Implementation Note

The runtime derives auto-tool agents from tools.yaml via `workflow_manager.get_auto_tool_agents()`. agents.yaml no longer carries a matching auto-tool field.

## Related Docs

- [Workflow Authoring Contracts](../../architecture/workflows/workflow-authoring-contracts.md)
- [UI Interaction Patterns](ui-interaction-patterns.md)
