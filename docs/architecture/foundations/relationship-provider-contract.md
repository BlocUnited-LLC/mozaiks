# Relationship Provider Contract

`relationships.yaml` lets modules declare how the platform should list current
user relationships to module-owned resources. It is the generic foundation for
My Apps, Portfolio, My Communities, and My Resources surfaces.

This contract answers one question:

> What resources is the current user connected to, and where should the shell
> route them?

It does not grant rights by itself. Authorization, payment, entitlement,
governance, and ownership policy remain in the owning module service and policy
layer.

## Contract File

```text
app/modules/{module_id}/contracts/relationships.yaml
```

```yaml
schema_version: mozaiks.relationships.v1

providers:
  - id: owned-apps
    label: Owned Apps
    description: Apps owned by the current user.
    order: 10
    action: list_user_app_relationships
    resource_types: [app]
    relationship_types: [owner, admin]
```

Each provider action must be declared in the same module's `module.yaml`.

## Provider Action Output

Provider actions should return a dict with a `relationships` array:

```json
{
  "relationships": [
    {
      "resource_type": "app",
      "resource_id": "orbit-launch",
      "resource_label": "Orbit Launch",
      "resource_subtitle": "Founder-led app",
      "relationship_type": "owner",
      "status": "active",
      "capabilities": ["app.view", "app.admin"],
      "primary_route": "/apps/orbit-launch/overview",
      "secondary_routes": [
        { "label": "Community", "path": "/apps/orbit-launch/community" }
      ],
      "updated_at": "2026-07-04T12:00:00Z",
      "metadata": {
        "stage": "seed"
      }
    }
  ]
}
```

The host also accepts `rows` or `items` arrays for provider actions, but
`relationships` is the canonical key.

Required row fields:

| Field | Meaning |
|---|---|
| `resource_type` | Stable resource category, such as `app`, `community`, `project`, or `document` |
| `resource_id` | Stable id routed by the owning app |
| `relationship_type` | User relationship, such as `owner`, `admin`, `member`, `watcher`, or `invited` |

Recommended row fields:

| Field | Meaning |
|---|---|
| `resource_label` | Human-readable resource name |
| `status` | Relationship state, defaulting to `active` |
| `capabilities` | UI-safe capability hints, not authorization truth |
| `primary_route` | Shell route for opening the resource |
| `secondary_routes` | Optional related routes with `{label, path}` |
| `metadata` | Safe display metadata only |

## Runtime Endpoint

`GET /api/me/relationships`

The platform:

1. Discovers `modules/*/contracts/relationships.yaml`.
2. Calls each provider action through the module executor.
3. Normalizes rows into a single `relationships` array.
4. Includes provider diagnostics so one failed provider does not blank the
   whole response.

Response shape:

```json
{
  "relationships": [],
  "providers": [
    {
      "id": "owned-apps",
      "module_id": "app_registry",
      "label": "Owned Apps",
      "action": "list_user_app_relationships",
      "resource_types": ["app"],
      "relationship_types": ["owner"],
      "count": 0,
      "error": null
    }
  ]
}
```

## Boundaries

Use `relationships.yaml` for resource inventory and routing. Do not use it for:

- identity fields or account preferences, which belong to `/api/me`
- module profile panels, which belong to `contracts/profile.yaml`
- admin operations, which belong to `contracts/admin.yaml`
- payment-provider details, hosted monetization policy, raw entitlements, or
  ownership-right claims

The contract is intentionally host-agnostic. Hosted products may expose
product-specific relationship types through their own module actions, but the
OSS framework only owns discovery, validation, endpoint composition, and row
normalization.
