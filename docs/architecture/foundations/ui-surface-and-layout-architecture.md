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

This is where controls such as `UserProfile`, `Notifications`, and `Discover`
belong.

### 2. Module surface

Owns:

- durable product pages
- entity lists and detail screens
- forms
- dashboards
- boards

Modules are the default surface for non-AI product behavior.

### 3. Workflow surface

Owns:

- transcripts
- workflow progress
- agent interactions
- structured human checkpoints

This surface is live and session-oriented rather than durable by default.

### 4. Artifact surface

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

### Modules persist

If the user expects to come back to a screen, filter, board, or record, it
should usually be a module surface.

### Workflows guide

If the user needs reasoning, orchestration, or guided HITL, use a workflow
surface.

### Artifacts bridge

If a workflow produces something the app should keep using, promote it to an
artifact or substrate state rather than leaving it only in chat.

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

## Guardrails

Do not:

- use chat as the only product surface
- use modules as a substitute for shell layout
- leave durable outputs trapped inside transcripts

## Cross References

- [canonical-app-structure.md](canonical-app-structure.md)
- [workflow-architecture.md](workflow-architecture.md)
- [process-and-event-map.md](process-and-event-map.md)
