# Admin Observability Contract

This document defines the framework-owned admin portal data contract for runtime observability and subscription operations.

The admin portal is not an optional app-level add-on. It is a permanent platform surface whose data sources may be overridden declaratively when needed.

## Declarative Config

Admin is a first-class framework surface (like chat-ui).

The admin section list is owned by the layered host and the framework admin
router. Additional app-owned admin surfaces should be registered through the
current platform/Studio/Mozaiks composition layer, not through hardcoded shell files.

## Admin Endpoints

All endpoints are guarded by `require_admin_or_internal`.

### `GET /__mozaiks/admin/observability/overview`

Returns app-scoped aggregate dashboard metrics:

- runtime run counters and token totals
- event dispatcher counters
- subscription totals and plan/status distribution
- source health/degradation metadata

### `GET /__mozaiks/admin/observability/chats`

Query params:

- `limit` (default `50`, max `200`)
- `offset` (default `0`)
- `only_active` (default `false`)

Returns app-scoped run snapshots:

- workflow/user/runtime
- token/cost usage
- active/completed status

### `GET /__mozaiks/admin/subscriptions/overview`

Query params:

- `history_limit` (default `20`, max `100`)

Returns:

- monetization mode (`enabled` and write policy)
- totals by status and plan
- declared plans from subscription config
- recent subscription history entries

## Frontend Bridge

The current web shell should call the layered host APIs directly or through the
active runtime bridge module. Do not introduce a separate monolithic bridge file
for admin behavior.

Treat the contract as framework-owned even when an app overrides paths or registers additional sections.
