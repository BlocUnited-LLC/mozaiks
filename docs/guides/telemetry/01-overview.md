# Telemetry Overview

Telemetry in Mozaiks exists to make runtime and product behavior observable
without pushing observability policy down into workflow prompts.

## What Telemetry Should Answer

Telemetry should help you answer:

- which app or workspace produced the traffic
- which workflow or run consumed tokens
- which provider/model combination drove cost
- whether runtime behavior is healthy, degraded, or failing

## Current Scope

The public contract focuses on:

- runtime and workflow observability
- token and cost attribution
- operator-facing diagnostics

Business policy such as billing or entitlement enforcement should consume this
data at the platform/product layer instead of teaching each workflow to manage
its own budget logic.

## Read Next

- [Token Management](../../reference/deep-dives/token-management.md)
- [Cost Tracking](03-cost-tracking.md)
- [Budget Management](04-budget-management.md)