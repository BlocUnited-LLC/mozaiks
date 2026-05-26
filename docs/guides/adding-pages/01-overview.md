# Add a Page

Pages in Mozaiks are usually declared as YAML schemas instead of raw React. The
platform renders them automatically using pre-built UI primitives.

Use the declarative path by default. Only reach for a custom React route when the
available primitives genuinely can't cover the use case.

## File Structure

```text
app/ui/pages/
└── {name}.yaml          ← declarative page schema
```

Or folder form when the page has supporting assets:

```text
app/ui/pages/{name}/
└── page.yaml
```

For cases the schema cannot express:

```text
app/ui/pages/custom/{Name}.jsx   ← custom React route
app/ui/route_manifest.json       ← registers the route
app/ui/index.js                  ← registers the component key used by the route
```

## Minimum Page Schema

```yaml
name: {name}
title: {Page Title}
layout: grid           # grid | sidebar | full-width | split
shell_mode: standard   # standard | workspace | conversation | focused | immersive | public

sections:
  - id: {section_id}
    title: {Section Title}
    primitive: DataTable
    config:
      api_endpoint: /api/modules/{module}/{action}
      columns:
        - { key: id,   label: ID }
        - { key: name, label: Name }
```

Pages are loaded at startup. The route `/{name}` is served automatically — no
registration step needed.

## Layout Options

| Layout | Best for |
|--------|----------|
| `grid` | Dashboards, overview pages |
| `sidebar` | Master-detail, filtered lists |
| `full-width` | Forms, detail views, reports |
| `split` | Comparison views, side-by-side |

## Shell Mode

This controls the layout chrome around the page:

| Mode | Use for |
|------|---------|
| `standard` | Normal app pages |
| `workspace` | Dashboards, admin surfaces, module workspaces |
| `conversation` | Chat, DM, thread pages — composer owns the bottom edge |
| `focused` | Onboarding, review, approval, checkout-style flows |
| `immersive` | Map, canvas, media, full-viewport routes |
| `public` | Marketing, legal, unauthenticated pages |

## Available Primitives

| Primitive | Use for |
|-----------|---------|
| `PageHeader` | Durable page title and primary action buttons |
| `ResourceTable` | Primary record/index tables |
| `DataTable` | Dense operational record lists |
| `Form` | Data entry with field definitions and submit action |
| `SummaryStrip` | 2–4 key page metrics |
| `Metric` | Single supporting metric |
| `Panel` / `SurfaceCard` | Grouped support surfaces |
| `Grid` | Child primitive layout container |
| `Button` | Call to action |
| `Modal` | Overlay dialog |
| `Alert` | Inline message |
| `StatusPill` | Compact status label |
| `Skeleton` | Loading and empty states |

Do not use `Card`, `Stat`, or `Badge` — use `SurfaceCard`/`Panel`,
`SummaryStrip`/`Metric`, and `StatusPill` instead.

## Navigation

Add `navigation` to the page schema when it should appear in the shell nav:

```yaml
navigation:
  scope: global    # global | local | profile
  icon: dashboard
  order: 20
```

Use `scope: global` for primary destinations. Use `scope: local` for
workspace/module subsections that belong in local nav, not the global shell.

## Example: Dashboard

```yaml
name: dashboard
title: Dashboard
layout: grid
shell_mode: workspace

sections:
  - id: header
    primitive: PageHeader
    config:
      title: Dashboard
      subtitle: Monitor current activity.

  - id: summary
    primitive: SummaryStrip
    config:
      api_endpoint: /api/modules/orders/stats
      items:
        - { label: Orders, value_key: total }

  - id: recent-orders
    title: Recent Orders
    primitive: ResourceTable
    config:
      api_endpoint: /api/modules/orders/list_orders
      columns:
        - { key: id,     label: Order ID }
        - { key: status, label: Status, type: status }
        - { key: total,  label: Total }
```

## Example: Form Page

```yaml
name: new-customer
title: New Customer
layout: full-width
shell_mode: focused

sections:
  - id: customer-form
    title: Customer Details
    primitive: Form
    config:
      fields:
        - { name: name,  label: Full Name, type: text,  required: true }
        - { name: email, label: Email,     type: email, required: true }
      submitLabel: Create Customer
      api_endpoint: /api/modules/customers/create_customer
```

## Custom React Route

When the declarative schema can't express what you need:

```jsx
// app/ui/pages/custom/MyPage.jsx
export default function MyPage() {
  return <div>...</div>
}
```

```json
// app/ui/route_manifest.json
{
  "pages": [
    {
      "id": "my-page",
      "path": "/my-page",
      "component": "MyPage",
      "meta": { "requiresAuth": true }
    }
  ]
}
```

Register the component in `app/ui/index.js`:

```js
const MyPage = lazy(() => import('./pages/custom/MyPage.jsx'))
registerComponent('MyPage', MyPage)
```

There is no implicit discovery for custom React routes. A route works only when
`app/ui/route_manifest.json`, `app/ui/pages/custom/{Name}.jsx`, and
`app/ui/index.js` all use the same component key. `admin/admin_registry.yaml`
is not a route registry and must not own full-page custom React components.

Use custom routes sparingly. Extend an existing primitive before reaching for a
custom route.

## Rules

- `api_endpoint` must be `/api/modules/{name}/{action_id}` — no query strings
- Put filters and limits in the action input schema, not the URL
- Page-owned navigation belongs in the page's `navigation` field
- Page-owned chrome intent belongs in `shell_mode`
- `app/config/shell.json` is for app-wide chrome only, not per-route overrides

## Read Next

- [Add a Module](../adding-modules/01-overview.md)
- [UI Surface and Layout Architecture](../../architecture/app/ui-surface-and-layout-architecture.md)
- [Generated Frontend Surface Contract](../../architecture/frontend/ui-system/generated-frontend-surface-contract.md)
