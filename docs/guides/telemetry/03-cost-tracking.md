# Cost Tracking

Cost tracking should stay attribution-first.

## Attribute Costs To Stable Units

Track token and model usage against stable runtime units such as:

- app or workspace
- workflow
- run or chat session
- provider and model

That keeps cost reporting useful for operators without pushing pricing math into
workflow authoring.

## Keep Pricing Policy Above Workflows

Workflows may need token-aware behavior in special cases, but pricing and cost
policy should normally stay in the platform/control-plane layer.

## Read Next

- [Telemetry Overview](01-overview.md)
- [Token Management](../../reference/deep-dives/token-management.md)