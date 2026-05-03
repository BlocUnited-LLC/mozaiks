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
| `DataTable` | Record lists with columns | `columns[]`, `rows[]`, `api_endpoint` |
| `Form` | Data entry | `fields[]`, `onSubmit`, `submitLabel` |
| `Card` | Grouped content block | `title`, `children`, `variant` |
| `Stat` | KPI / metric display | `label`, `value`, `delta`, `unit`, `trend` |
| `Grid` | Card grid layout | `items[]`, `columns` (2\|3\|4) |
| `Button` | Call to action | `label`, `variant`, `onClick` |
| `Modal` | Overlay dialog | `id`, `title`, `open` |
| `Alert` | Inline message | `message`, `variant` (info\|success\|warning\|error) |
| `Badge` | Status label | `label`, `variant` |
| `Skeleton` | Loading / empty state | `rows`, `height` |

---

## Example: Dashboard Page

```yaml
name: dashboard
title: Dashboard
layout: grid

sections:
  - id: total_users
    title: Total Users
    primitive: Stat
    config:
      label: Users
      value: 0
      trend: up
    api_endpoint: /api/modules/users/stats

  - id: recent_orders
    title: Recent Orders
    primitive: DataTable
    config:
      columns:
        - { key: id,     label: Order ID }
        - { key: total,  label: Total }
        - { key: status, label: Status }
    api_endpoint: /api/modules/orders/list_orders
```

---

## Example: Form Page

```yaml
name: new-customer
title: New Customer
layout: full-width

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
- `api_endpoint` paths must be `/api/modules/{name}/{action_id}` routes

---

## When to Use This Skill

- User wants to add a new page to a generated app
- User says "add a customers page" or "I need a dashboard"
- User wants to display module data in the app UI
- User asks "how do I add a page"
