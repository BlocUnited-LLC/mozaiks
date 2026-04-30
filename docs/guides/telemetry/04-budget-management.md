# Budget Management

Budget management should use telemetry as input, not as a reason to bury policy
logic inside workflow prompts or tool code.

## Practical Rule

Keep budget policy above the workflow layer whenever possible.

Examples:

- warn when a run crosses a soft threshold
- gate expensive create/refinement flows at the control-plane layer
- separate reporting from enforcement

## Why This Matters

When budget enforcement lives above workflow authoring:

- workflow prompts stay focused on product behavior
- operators can tune policy without regenerating workflows
- cost controls remain observable and auditable

## Read Next

- [Telemetry Overview](01-overview.md)
- [Token Management](../../reference/deep-dives/token-management.md)