# UI Surface and Layout Architecture

This document defines how Mozaiks should think about user-facing surfaces.

The key rule is that the shell should compose different surface types without
pretending they are all chat.

## Surface Types

### 1. Shell surface

Owns:

- navigation
- semantic header controls
- discover
- shell-level layout and chrome
- brand and theme chrome

This is where controls such as `UserProfile`, `Notifications`, and `Discover`
belong.

Shell defaults (such as startup mode and landing spot) are derived from
`app/config/ai.json` and served via `/api/shell-config`.

Current repo note:

- the local App Zero workspace now uses `mozaiks-platform/app/config/ai.json`
- the canonical target for generated/customer apps is `app/config/ai.json`

### 2. Page surface

Owns:

- durable product pages
- entity lists and detail screens
- forms
- dashboards
- boards

Pages are the default surface for non-AI product behavior.

Shared page UI should live in `app/ui/pages/_shared/`.

Admin is a first-class framework surface (like chat-ui), not an app-level page.

### 3. Workflow surface

Owns:

- transcripts
- workflow progress
- agent interactions
- structured human checkpoints

This surface is live and session-oriented rather than durable by default.

### 5. Artifact surface

Owns persisted outputs that cross the module and workflow boundary.

Examples:

- generated brief
- review package
- summary artifact
- plan artifact

Artifacts can be rendered in modules, side panels, or workflow sessions.

## Layout Rules

### Shell composes

The shell decides what major surfaces are visible and where entrypoints live.

### Pages persist

If the user expects to come back to a screen, filter, board, or record, it
should usually be a page surface.

### Workflows guide

If the user needs reasoning, orchestration, or guided HITL, use a workflow
surface.

### Artifacts bridge

If a workflow produces something the app should keep using, promote it to an
artifact or app-backend state rather than leaving it only in chat.

## Hybrid Surface Patterns

### Module launches workflow

Examples:

- "Generate options"
- "Review with AI"
- "Summarize and escalate"

### Domain event creates background automation

Examples:

- workflow runs silently
- artifact updates a module
- user gets a notification only if review is needed

### Workflow opens persistent surface

Examples:

- create record from guided intake
- generate artifact, then route to module detail page

## Header and Shell Controls

Header controls should be semantic shell declarations, not hardcoded widget
logic.

Examples:

- `UserProfile`
- `Notifications`
- `Discover`

These are shell concerns. They are not workflow authoring concerns.

Discovered pages and adapters should provide the actual route surfaces.

## Guardrails

Do not:

- use chat as the only product surface
- use modules as a substitute for pages
- leave durable outputs trapped inside transcripts

## Cross References

- [canonical-app-structure.md](canonical-app-structure.md)
- [workflow-architecture.md](workflow-architecture.md)
- [event-system.md](event-system.md)

