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

| | App UI | Agentic UI | Product Extension UI | Transition UI |
|-|--------|------------|----------------------|---------------|
| **What** | Generated persistent pages | Agent-driven artifacts in chat | Complex product pages (hand-authored) | Pre-workflow routing choices |
| **Generator** | AppGenerator | AgentGenerator | Hand-authored | Hand-authored |
| **Files** | `app/pages/*.yaml` | `ui/<WorkflowName>/<Component>.jsx` + `tools/<name>.py` | `mozaiks-platform/ui/pages/*.jsx` | `extension_registry.json` transitions |
| **Renderer** | PageRenderer ← SchemaPage ← /api/pages/{name} | useAppEventBus ← ingestEvent ← WebSocket | `@platform/extensions` → register() → component registry | LauncherScreen / ConfirmScreen / TransitionScreen |
| **React?** | Never — primitives declared in YAML | Always — generated alongside Python tool | Always — hand-authored product pages | Optional — branded transition components |

**Full spec:** [docs/architecture/specs/ui-systems.md](../../docs/architecture/specs/ui-systems.md)

### App UI quick rules
- Defined as AppPageSchema YAML in `app/pages/` (or `mozaiks-platform/app/pages/` for App Zero)
- Primitives declared by type string (`primitive: DataTable`), not imported
- AppGenerator writes the files; `save_app_schema` tool persists them
- No custom React — if the layout doesn't fit, adjust the schema or primitive config
- Do not create an AppPageSchema YAML for a route already owned by Product Extension UI

### Agentic UI quick rules
- Python tool calls `send_ui_tool_event(component_name, display_type, payload)`
- React component subscribes via `useAppEventBus('ui.tool.<name>', handler)`
- Always two files generated together: Python tool + React component
- User actions return via `onAction` prop; agent reads them from next turn context
- Primitives imported as JSX building blocks (`import { Card } from '../../ui/primitives/Card.jsx'`)

### Product Extension UI quick rules
- Lives in `mozaiks-platform/ui/` — the App Zero product UI extension layer
- `index.js` exports `register(registerComponent)` — called once at shell bootstrap
- `extension.json` declares routes — every page must be listed here and registered in `index.js`
- Use when AppPageSchema primitives cannot express the layout or behavior needed
- Each route must have exactly one owner — never duplicate a `ui/extension.json` route in `app/pages/*.yaml`

---

## Frontend Layer Model

Three extension layers sit on top of `chat-ui/`:

| Layer | Files | Registered via |
|-------|-------|----------------|
| `chat-ui/` substrate | `src/registry/coreComponents.js` | Always loaded |
| Studio management | `factory_app/app/ui/studio/`, `src/admin/` | `@studio/extensions` → `registerStudioComponents()` |
| Mozaiks App extensions | `mozaiks-platform/ui/` | `@platform/extensions` → `register()` |

**Critical rules:**
- `chat-ui/coreComponents.js` must contain only substrate primitives: `ChatPage`, `SchemaPage`, `LauncherScreen`, `ConfirmScreen`, `ProfilePage`. Studio and product pages are not core primitives.
- `AdminPortal` and all Studio pages are **platform-management surfaces** — registered by Studio (`factory_app/app/ui/studio/index.js` via `@studio/extensions`), not by `coreComponents.js`.
- `mozaiks-platform/ui/` never imports from `factory_app/app/ui/studio/` or `chat-ui/src/admin/` directly. The dependency goes one way: product extensions build on the substrate; the substrate does not depend on extensions.
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

When working in `platform/config/`:
- keep startup and workflow boot behavior in `ai.json`
- keep visual shell state in `theme_config.json`

## UI Editing Rules

Preserve the repo's established component and contract patterns.
Avoid unrelated refactors while fixing frontend or config behavior.

---

## v2 CSS Token Rules (ENFORCED)

**These apply to all workflow UI components under `ui/{WorkflowName}/` and any component in `mozaiks-platform/app/`.**

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
// BAD — hardcoded or legacy
<div className="bg-gray-800 text-gray-300">       // hardcoded color scale
<div className="bg-slate-900 border-slate-700">   // hardcoded color scale
<div className="text-[var(--color-primary)]">      // legacy --color-* token
<div className="bg-amber-500/20 text-amber-300">  // hardcoded color scale
```

### Never import from artifactDesignSystem

```js
// BAD — legacy system, being removed
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