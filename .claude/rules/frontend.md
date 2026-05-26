---
paths:
  - "app/**/*"
  - "chat-ui/**/*"
  - "platform/**/*"
---

# Frontend And App-Surface Rules

Use these rules when editing the web shell, shared UI, or app bundle surfaces.

---

## Four UI Surfaces — Know Which One You're In

Mozaiks has four distinct UI surfaces. Before touching any UI code, identify which one:

| | App UI | Agentic UI | Custom Route UI | Transition UI |
|-|--------|------------|-----------------|---------------|
| **What** | Generated persistent pages | Agent-driven artifacts in chat | Complex hand-authored full-page routes | Pre-workflow routing choices |
| **Generator** | AppGenerator | AgentGenerator | Hand-authored or contract-declared | Hand-authored |
| **Files** | `app/ui/pages/*.yaml` | `ui/<WorkflowName>/<Component>.jsx` + `tools/<name>.py` | `app/ui/pages/custom/*.jsx` + `app/ui/route_manifest.json` + `app/ui/index.js` | `extension_registry.json` transitions |
| **Renderer** | PageRenderer ← SchemaPage ← /api/pages/{name} | useAppEventBus ← ingestEvent ← WebSocket | `@platform/extensions` → register() → component registry | LauncherScreen / ConfirmScreen / TransitionScreen |
| **React?** | Never — primitives declared in YAML | Always — generated alongside Python tool | Always — hand-authored full-page routes | Optional — branded transition components |

**Canonical contract:** [docs/architecture/frontend/generated-frontend-surface-contract.md](docs/architecture/frontend/ui-system/generated-frontend-surface-contract.md)

### App UI quick rules
- Defined as AppPageSchema YAML in `app/ui/pages/`
- Primitives declared by type string (`primitive: DataTable`), not imported
- AppGenerator writes the files; `save_app_schema` tool persists them
- No custom React — if the layout doesn't fit, adjust the schema or primitive config
- Do not create an AppPageSchema YAML for a route already owned by Custom Route UI

### Agentic UI quick rules
- Python tool calls `send_ui_tool_event(component_name, display_type, payload)`
- React component subscribes via `useAppEventBus('ui.tool.<name>', handler)`
- Always two files generated together: Python tool + React component
- User actions return via `onAction` prop; agent reads them from next turn context
- Primitives imported as JSX building blocks only from shipped primitives such as `Panel`, `SurfaceCard`, `StatusPill`, `SummaryStrip`, and `Metric`; do not import removed `Card`, `Stat`, or `Badge` primitives.

### Custom Route UI quick rules
- Lives inside the active app root at `app/ui/pages/custom/`
- `app/ui/index.js` exports `register(registerComponent)` — called once at shell bootstrap
- `app/ui/route_manifest.json` declares routes — every page must be listed there and registered in `app/ui/index.js`
- Use when AppPageSchema primitives cannot express the layout or behavior needed
- Each route must have exactly one owner — never duplicate a custom-route entry in `app/ui/route_manifest.json` and `app/ui/pages/*.yaml`
- Every `route_manifest.json` component key must match a registration in `app/ui/index.js`
- `admin/admin_registry.yaml` declares admin page and panel metadata; it is not the owner of arbitrary full-page custom routes
- Custom React routes are not auto-discovered; `route_manifest.json`, `ui/pages/custom/*.jsx`, and `ui/index.js` registration must be present together
- Missing or mismatched custom route registrations are export/download blockers

## Admin UI Two-Tier Model

Mozaiks admin UI has two different extension tiers. Do not collapse them into
one registry.

- Tier 1: AdminPortal schema panels. Use `admin/admin_registry.yaml` plus
  `modules/{module}/contracts/admin.yaml` for framework-owned admin shell
  surfaces.
- Tier 2: custom operator React pages. Use `ui/route_manifest.json`,
  `admin/pages/*.jsx`, and `admin/index.js` together for full-page custom admin
  routes.
- `admin/admin_registry.yaml` is not a route registry and must not own
  full-page component registration fields.
- Tier 2 requires all three files together: route manifest entry, React page,
  and `admin/index.js` registration.
- `app/brand/theme_config.json` remains the visual authority for both tiers.

### Custom Route UI Primitive Contract

Custom full-page React must still use the shared Mozaiks UI foundation. It may
compose domain-specific wrappers, but wrappers must delegate to primitives
instead of recreating visual systems.

Use shared primitives from `@mozaiks/chat-ui/ui` for:

- actions: `Button`, `ActionButton`, `IconButton`, `LinkButton`
- state: `StatusPill`, `Alert`, `AlertBanner`
- surfaces: `SurfaceCard`, `Panel`
- summaries: `Metric`, `SummaryStrip`, `SegmentedBar`
- collections: `CollectionToolbar`, `ResourceList`, `ResourceTable`, `DataTable`
- feedback: `InlineEmptyState`, `LoadingState`, `ErrorState`, `Skeleton`

Allowed:

```jsx
import { Button, StatusPill, SurfaceCard } from '@mozaiks/chat-ui/ui'

function NotificationCard({ notification }) {
  return (
    <SurfaceCard>
      <StatusPill tone={notification.active ? 'success' : 'default'}>
        {notification.active ? 'Active' : 'Muted'}
      </StatusPill>
      <Button variant="secondary">Review</Button>
    </SurfaceCard>
  )
}
```

Not allowed:

```jsx
function StatusPill({ status }) { /* local visual clone */ }
function MetricTile({ value }) { /* local card clone */ }
<button className="rounded-xl bg-primary px-4 py-2">Save</button>
```

`app/brand/theme_config.json` is the visual-token authority. Shared primitives
must resolve through semantic tokens/classes (`bg-card`, `text-primary`,
`border-border`, `text-success`, etc.). Do not hardcode brand colors, local
card shells, or page-specific button systems in generated custom pages.
Do not hardcode `font-family` values or literal brand font names in generated
React; use `font-sans`, `font-heading`, and shared primitive typography.
Do not hardcode hex/rgb/hsl color values in generated React. Use semantic classes
such as `bg-background`, `bg-card`, `text-foreground`, and
`text-muted-foreground`.
Do not create local primitive clones (`StatusPill`, `MetricTile`, `StatCard`,
`Badge`) or raw primary-styled `<button className="...bg-primary...">` markup
when shared primitives and semantic variants exist.
Avoid repeated local rounded card shells; prefer `SurfaceCard`/`Panel` and
collection primitives.
Do not define page-local `palette`, `colors`, or `theme` objects in generated
React; visual values belong in `app/brand/theme_config.json`.

Generated React audit scope note:
- deterministic generated React audits intentionally skip files under `docs/**`
  and `tests/**` fixture paths to reduce false positives.

---

## Frontend Layer Model

Three extension layers sit on top of `chat-ui/`:

| Layer | Files | Registered via |
|-------|-------|----------------|
| `chat-ui/` substrate | `src/registry/coreComponents.js` | Always loaded |
| Studio management | `factory_app/app/ui/pages/custom/studio/`, `src/admin/` | `@studio/extensions` → `registerStudioComponents()` |
| App/workspace extensions | `app/ui/index.js`, `app/ui/pages/custom/` | `@platform/extensions` → `register()` |

**Critical rules:**
- `chat-ui/coreComponents.js` must contain only substrate primitives: `ChatPage`, `SchemaPage`, `LauncherScreen`, `ConfirmScreen`, `ProfilePage`. Studio and product pages are not core primitives.
- `AdminPortal` and all Studio pages are **platform-management surfaces** — registered by Studio through `factory_app/app/ui/index.js` and the Studio route manifest, not by `coreComponents.js`.
- App-owned custom routes must not import from `factory_app/app/ui/pages/custom/studio/` or `chat-ui/src/admin/` directly. The dependency goes one way: app extensions build on the substrate; the substrate does not depend on extensions.
- CLI and Studio are parallel interfaces. Do not add UI to `chat-ui` just because it is used locally.

## Boundaries

Keep product shell behavior in app-surface code and declarative config.
Do not move UI-specific behavior into runtime internals unless the user is intentionally changing runtime architecture.

## Preferences

Prefer:
- declarative config changes before React code changes when the repo already supports them
- stable transport and payload contracts when changing UI behavior
- shared surface patterns over one-off hacks

## Config Ownership

When working in `app/config/`:
- keep startup and workflow boot behavior in `ai.json`
- keep shell navigation policy and chrome mode policy in `shell.json`
- keep branding/theme assets in `app/brand/theme_config.json`

`app/brand/theme_config.json` is the visual identity authority. It owns
`theme.primary`, `theme.radius`, `theme.font`, `theme.font_heading`,
`theme.appearance`, `theme.density`, and any expanded `fonts`, `colors`,
`shadows`, `ui`, or `primitives` values still needed by runtime compatibility
tokens. `app/config/shell.json` owns behavior and chrome only; it must not
carry raw visual token values.

Local fonts live under `app/brand/fonts/` and are referenced as `/fonts/...`.
Google Fonts are declared in `theme_config.json` and loaded by the theme
loader. Do not copy font binaries into generated artifacts outside `brand/`.

Route-level shell intent belongs on the route, not in ad hoc React wrappers:
- `app/ui/pages/*.yaml -> shell_mode`
- `app/ui/route_manifest.json -> pages[].meta.shellMode`
- `extension_registry.json -> entrypoints[].meta.shellMode`

Use `conversation` for DM/chat/thread routes, `workspace` for dense module or
profile workspaces, `focused` for setup/review/transition screens, `immersive`
for full-viewport experiences, `public` for public/legal routes, and `standard`
for ordinary app pages.

## UI Editing Rules

Preserve the repo's established component and contract patterns.
Avoid unrelated refactors while fixing frontend or config behavior.

---

## v2 CSS Token Rules (ENFORCED)

**These apply to all workflow UI components under `ui/{WorkflowName}/` and any component in an app workspace's `app/ui/`.**

### Use `--mz-*` semantic tokens via Tailwind

```jsx
// GOOD — semantic, theme-reactive
<div className="bg-card border border-border text-foreground">
<span className="text-primary">
<div className="bg-muted text-muted-foreground">
<div className="bg-warning/20 border-warning/50 text-warning">
<div className="bg-success/20 border-success/50 text-success">
<div className="bg-destructive/20 text-destructive">
<div className="bg-secondary/20 border-secondary/40 text-secondary-foreground">
```

```jsx
// BAD — hardcoded or removed token family
<div className="bg-gray-800 text-gray-300">       // hardcoded color scale
<div className="bg-slate-900 border-slate-700">   // hardcoded color scale
<div className="text-[var(--color-primary)]">      // removed --color-* token
<div className="bg-amber-500/20 text-amber-300">  // hardcoded color scale
```

### Never import from artifactDesignSystem

```js
// BAD — removed design-system import
import { components, colors, fonts } from '../../styles/artifactDesignSystem'
```

There is no replacement import. Use Tailwind semantic classes directly.

### React imports

```jsx
// GOOD — named imports only (JSX transform handles React)
import { useState, useEffect, useRef } from 'react'

// BAD — default import is unnecessary and triggers lint warnings
import React, { useState } from 'react'
```

### Semantic token reference

| Intent | Class |
|--------|-------|
| Page/card background | `bg-background` / `bg-card` |
| Muted surface | `bg-muted` |
| Primary brand | `bg-primary`, `text-primary`, `border-primary` |
| Secondary brand | `bg-secondary`, `text-secondary-foreground`, `border-secondary` |
| Accent | `bg-accent`, `border-accent` |
| Success | `bg-success/20`, `border-success/50`, `text-success` |
| Warning | `bg-warning/20`, `border-warning/50`, `text-warning` |
| Error/destructive | `bg-destructive/20`, `text-destructive` |
| Body text | `text-foreground` |
| Subdued text | `text-muted-foreground` |
| Dividers | `border-border`, `divide-border` |
| Opacity gradients | `from-primary to-secondary`, `from-background/80 to-card/80` |

### Intentional exception

`detectTheme()` in Mermaid diagram components reads `--color-*` via `getComputedStyle` to bridge the platform theme into Mermaid's renderer. This is correct and intentional — do not replace those reads.
