---
paths:
  - "app/app.json"
  - "app/provenance.yaml"
  - "app/config/**"
  - "app/dashboard/**"
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
- app provenance, lineage, contract refs, and overlay refs belong in
  `app/provenance.yaml`; do not store secrets, local absolute paths, provider
  execution state, or runtime package pins there
- Workspace/App Dashboard portals belong in `app/dashboard/dashboard.yaml`
- secret requirements and vault/provider policy belong in `app/security/secrets.yaml`
  when needed; store names and handles only, never raw secret values
- app identity and auth flags belong in `app/app.json`

Keep config declarative and app-agnostic where possible.
