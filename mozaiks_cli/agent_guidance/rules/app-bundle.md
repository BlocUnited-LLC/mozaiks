---
paths:
  - "app/app.json"
  - "app/config/**"
  - "app/security/**"
  - "app/brand/**"
  - "scripts/**"
---

# App Bundle Rules

This workspace is a standalone Mozaiks app that consumes the installed
`mozaiks` package.

## Ownership

- app-specific config belongs in `app/`
- local process wrappers belong in `scripts/`
- framework/runtime changes belong upstream in Mozaiks, not here

## Shell And Config

- shell, navigation, footer, mobile chrome, shortcuts, and route-level chrome
  behavior belong in `app/config/shell.json`
- AI startup behavior belongs in `app/config/ai.json`
- secret requirements and vault/provider policy belong in `app/security/secrets.yaml`
  when needed; store names and handles only, never raw secret values
- app identity and auth flags belong in `app/app.json`

Keep config declarative and app-agnostic where possible.
