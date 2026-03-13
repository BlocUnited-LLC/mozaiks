---
paths:
  - "app/**/*"
  - "chat-ui/**/*"
  - "platform/**/*"
---

# Frontend And App-Surface Rules

Use these rules when editing the web shell, shared UI, or app bundle surfaces.

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
- keep landing routes and shell navigation in `navigation_config.json`
- keep visual shell state in `theme_config.json`
- keep module route metadata in `module_registry.json`

## UI Editing Rules

Preserve the repo's established component and contract patterns.
Avoid unrelated refactors while fixing frontend or config behavior.