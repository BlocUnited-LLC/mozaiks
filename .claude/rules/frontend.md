---
paths:
  - "app/**/*"
  - "chat-ui/**/*"
  - "platform/**/*"
---

# Frontend And App-Surface Rules

Use these rules when editing the web shell, shared UI, or app bundle surfaces.

---

## Two UI Systems — Know Which One You're In

Mozaiks has two completely separate UI systems. Before touching any UI code, identify which system:

| | App UI | Agentic UI |
|-|--------|------------|
| **What** | Persistent pages (dashboard, tables, forms) | Agent-driven components in the chat interface |
| **Generator** | AppGenerator | AgentGenerator |
| **Files** | `platform/pages/*.yaml` (AppPageSchema) | `ui/<WorkflowName>/<Component>.jsx` + `tools/<name>.py` |
| **Renderer** | PageRenderer ← SchemaPage ← /api/pages/{name} | useAppEventBus ← ingestEvent ← WebSocket |
| **React?** | Never generated — primitives declared in YAML | Always generated — primitives imported as JSX |
| **Analogy** | Traditional SPA page | CopilotKit / ag-ui generative UI |

**Full spec:** [docs/architecture/specs/ui-systems.md](../../docs/architecture/specs/ui-systems.md)

### App UI quick rules
- Defined as AppPageSchema YAML in `platform/pages/`
- Primitives declared by type string (`primitive: DataTable`), not imported
- AppGenerator writes the files; `save_app_schema` tool persists them
- No custom React — if the layout doesn't fit, adjust the schema or primitive config

### Agentic UI quick rules
- Python tool calls `send_ui_tool_event(component_name, display_type, payload)`
- React component subscribes via `useAppEventBus('ui.tool.<name>', handler)`
- Always two files generated together: Python tool + React component
- User actions return via `onAction` prop; agent reads them from next turn context
- Primitives imported as JSX building blocks (`import { Card } from '../../ui/primitives/Card.jsx'`)

---

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