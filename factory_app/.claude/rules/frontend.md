---
paths:
  - "app/ui/**"
  - "app/config/shell.json"
---

<!-- BEGIN MOZAIKS MANAGED: agent-guidance -->
# Frontend Rules

Use declarative UI before custom code.

## Placement

- declarative pages: `app/ui/pages/`
- custom pages: `app/ui/pages/custom/`
- reusable custom components: `app/ui/components/`
- route registration: `app/ui/route_manifest.json`
- component registration: `app/ui/index.js`
- shell/navigation/chrome: `app/config/shell.json`

## Constraints

- Do not create custom React for simple forms, lists, tables, dashboards, or detail views when page schemas can express the surface.
- Keep custom UI mounted through declared routes and registries.
- Keep mobile and desktop chrome behavior in shell config rather than hardcoded per-page conditionals.
- Avoid framework-specific imports that assume a local Mozaiks source checkout.
<!-- END MOZAIKS MANAGED: agent-guidance -->
