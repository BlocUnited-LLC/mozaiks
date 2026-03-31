# Token Management

This note summarizes the current token-management stance in Mozaiks.

## What Matters

Token tracking should support:

- observability
- budget management
- cost attribution
- workflow diagnostics

At the product boundary, subscription policy and admin enforcement should consume token data as a native platform capability rather than push token logic into workflow authoring.

It should not leak into product logic or workflow authoring unless the workflow explicitly needs budget-aware behavior.

## Related Guides

- [Telemetry Overview](../../guides/telemetry/01-overview.md)
- [Cost Tracking](../../guides/telemetry/03-cost-tracking.md)
- [Budget Management](../../guides/telemetry/04-budget-management.md)
