---
name: add-page
description: Add a frontend page (AppPageSchema) to an existing Mozaiks app.
argument-hint: "[page name or description]"
---

Help the user add a **page** to an existing Mozaiks application.

Pages in Mozaiks are declared as YAML schemas (`AppPageSchema`) — not raw React.
The platform shell renders them automatically using pre-built primitives.

---

## What a Page Is

```
app/ui/pages/
└── <name>.yaml       ← the page schema

# Or folder form:
app/ui/pages/<name>/
└── page.yaml
```

The `SchemaPage` route component fetches `/api/pages/<name>`, and `PageRenderer`
assembles the layout from the schema using the primitive registry.

---

## Steps to Add a Page

### 1. Create the page schema

```bash
touch app/ui/pages/<name>.yaml
```

### 2. Write the schema

```yaml
name: <name>
title: <Page Title>
layout: grid               # grid | sidebar | full-width | split
shell_mode: standard       # standard | workspace | conversation | focused | immersive | public

sections:
  - id: <section_id>
    title: <Section Title>
    primitive: DataTable   # see primitives below
    config:
      columns:
        - { key: id,    label: ID }
        - { key: name,  label: Name }
    api_endpoint: /api/modules/<module>/<action>   # optional live data
```

### 3. Restart the backend

```bash
mozaiks serve .
```

Pages are loaded at startup. The route `/<name>` is served automatically.

### 4. Add shell access when needed

If the page should be globally reachable, prefer route-level navigation metadata
on the page itself:

```yaml
navigation:
  scope: global
  icon: dashboard
  order: 20
```

Use `scope: local` for workspace/module subsections that should render through
local navigation instead of crowding the global shell.

Choose `shell_mode` for route chrome:
- `standard`: normal app page.
- `workspace`: dense dashboard, admin/profile/module workspace, or local nav surface.
- `conversation`: chat, DM, inbox thread, or support conversation where the composer owns the bottom edge.
- `focused`: onboarding, setup, review, approval, or checkout-style route.
- `immersive`: map, canvas, media, game, or full-viewport route.
- `public`: legal, marketing, or unauthenticated information route.

Use compact shell shortcuts in `app/config/shell.json` for built-in chrome such
as profile/auth/footer items:

```json
{
  "shortcuts": {
    "header": ["dashboard", "<name>"],
    "mobile": ["dashboard", "<name>", "profile"]
  }
}
```

Use explicit `header`, `profile`, or `mobile.bottomBar` entries only when the
label, icon, role gate, or path needs to differ from the route/page catalog.
Use `app/config/shell.json -> navigation.policy` when the app needs a different
placement model, such as desktop sidebar global nav or mobile local sheet nav.
Use `app/config/shell.json -> chrome` only to override app-wide behavior for the
standard shell modes. Do not encode per-route chrome there; the page owns
`shell_mode`.

---

## Layout Options

| Layout | Description | Best for |
|--------|-------------|----------|
| `grid` | 2–3 column responsive grid | Dashboards, overview pages |
| `sidebar` | First section → aside, rest → main | Master-detail, filtered lists |
| `full-width` | Single column, full width | Forms, detail views, reports |
| `split` | Two equal columns | Comparison views, side-by-side |

---

## Available Primitives

| Primitive | Use case | Key config |
|-----------|----------|------------|
| `PageHeader` | Durable page title and primary actions | `title`, `subtitle`, `actions[]` |
| `ResourceTable` | Primary record/index page table | `columns[]`, `api_endpoint`, `data_key`, `actions[]` |
| `DataTable` | Dense operational record lists | `columns[]`, `api_endpoint`, `data_key` |
| `Form` | Data entry | `fields[]`, `onSubmit`, `submitLabel` |
| `SummaryStrip` | 2-4 useful page metrics | `items[]` with `value` or `value_key` |
| `Metric` | Single supporting metric | `label`, `value` or `value_key`, `detail` |
| `Panel` / `SurfaceCard` | Purposeful grouped support surface | `title`, `subtitle`, `children` |
| `Grid` | Small child primitive layout | `children[]`, `columns` (2\|3\|4) |
| `Button` | Call to action | `label`, `variant`, `onClick` |
| `Modal` | Overlay dialog | `id`, `title`, `open` |
| `Alert` | Inline message | `message`, `variant` (info\|success\|warning\|error) |
| `StatusPill` | Compact status label | `label`, `tone` |
| `Skeleton` | Loading / empty state | `rows`, `height` |

Removed primitives: do not use `Card`, `Stat`, or `Badge`. Use `SurfaceCard` or `Panel`, `SummaryStrip` or `Metric`, and `StatusPill`.

---

## Example: Dashboard Page

```yaml
name: dashboard
title: Dashboard
layout: grid
shell_mode: workspace

sections:
  - id: dashboard-header
    primitive: PageHeader
    config:
      title: Dashboard
      subtitle: Monitor current user and order activity.

  - id: user_summary
    primitive: SummaryStrip
    config:
      api_endpoint: /api/modules/users/stats
      items:
        - label: Users
          value_key: total_users
          format: number

  - id: recent_orders
    title: Recent Orders
    primitive: ResourceTable
    config:
      api_endpoint: /api/modules/orders/list_orders
      columns:
        - { key: id,     label: Order ID }
        - { key: total,  label: Total }
        - { key: status, label: Status, type: status }
```

---

## Example: Form Page

```yaml
name: new-customer
title: New Customer
layout: full-width
shell_mode: focused

sections:
  - id: customer_form
    title: Customer Details
    primitive: Form
    config:
      fields:
        - { name: name,  label: Full Name,  type: text,  required: true }
        - { name: email, label: Email,      type: email, required: true }
        - { name: phone, label: Phone,      type: text }
      submitLabel: Create Customer
      api_endpoint: /api/modules/customers/create_customer
```

---

## Live Data Binding

When a section has `api_endpoint`, `SectionRenderer` fetches that endpoint on mount
and injects the response into the primitive's `rows` / `value` props.

The agent can also trigger a refresh by emitting `ui.datatable.refresh` with the section id.

---

## Custom Full-Page React Routes

For cases the declarative schema cannot express, use the escape hatch:

```
app/ui/pages/custom/<name>.jsx   ← custom React page
app/ui/route_manifest.json       ← registers the route
```

```json
// app/ui/route_manifest.json
{
  "routes": [
    {
      "path": "/custom/<name>",
      "component": "<Name>Page",
      "meta": { "requiresAuth": true }
    }
  ]
}
```

Use custom routes sparingly. Declarative `app/ui/pages/` is the default.

---

## Rules

- **Prefer declarative YAML** — if the primitives don't cover the use case, extend the primitive
- Pages belong in `app/ui/pages/` — never in a module's backend directory
- `api_endpoint` paths must be `/api/modules/{name}/{action_id}` routes with no query strings or fragments. Put limits in `page_size` and filters/selected-row values in action `payload`, form state, or module action input schemas.
- Page-owned shell access belongs in the page's `navigation` field. Use `app/config/shell.json -> shortcuts` for built-in chrome and `navigation.policy` for app-wide placement behavior.
- Page-owned chrome intent belongs in `shell_mode`; use `conversation` for DM/chat routes and `workspace` for dense module/profile/admin-like pages.

---

## When to Use This Skill

- User wants to add a new page to a generated app
- User says "add a customers page" or "I need a dashboard"
- User wants to display module data in the app UI
- User asks "how do I add a page"
