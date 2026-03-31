# Admin Observability Contract

This document defines the framework-owned admin portal data contract for runtime observability and subscription operations.

The admin portal is not an optional app-level add-on. It is a permanent platform surface whose data sources may be overridden declaratively when needed.

## Declarative Config

Admin is a first-class framework surface (like chat-ui).

The admin section list is derived from the registered admin sections inside
`chat-ui/src/adminPortalRegistry.js` plus any extra sections registered by
workflows/modules via their admin.yaml files.

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

`chat-ui/src/coreBridge.js` now exports:

- `adminGetObservabilityOverview(path?)`
- `adminListObservabilityChats({ path, limit, offset, onlyActive })`
- `adminGetSubscriptionsOverview({ path, historyLimit })`

The AdminPortal sections consume these bridge calls and degrade to local stub payloads when the endpoints are unavailable.

Treat the contract as framework-owned even when an app overrides paths or registers additional sections.
