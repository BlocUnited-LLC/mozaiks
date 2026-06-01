---
paths:
  - "docs/**/*.md"
  - ".claude/**/*.md"
  - "factory_app/workflows/**"
---

# Hosted Packs Rules

Use these rules when changing hosted-pack support, façade wiring, or hosted
product guidance in the OSS repo.

## Canonical Pattern

Treat these as separate roles:

- `hosted_pack` = hosted product capability owned outside this OSS repo
- `external_adapter` = thin client or callback bridge to an outside system
- app-owned `facade` module = the OSS-side API surface that pages and workflows
  call

Canonical flow:

`hosted_pack` -> app-owned facade module -> `external_adapter` / thin client -> hosted service

## Hard Rules

- Do not copy hosted engines, proprietary internals, secrets, or provider
  business logic into OSS code or generated output.
- Keep hosted examples provider-neutral in OSS docs, rules, skills, and tests.
- Pages and admin surfaces must bind through the app-owned facade module, not
  directly to hosted-pack endpoints.
- Hosted adapters stay thin and contract-bound. They do not become alternate
  business-logic homes.
- If a hosted-only concept is not available in OSS, describe it as hosted-only
  or external. Do not present it as required contributor setup.

## Reporting

When hosted-pack support changes, state whether the change affected hosted-pack
classification, facade wiring, adapter clients, or OSS/private boundary rules.
