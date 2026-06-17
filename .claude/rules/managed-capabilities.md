---
paths:
  - "docs/**/*.md"
  - ".claude/**/*.md"
  - "factory_app/workflows/**"
---

# Managed Capabilities Rules

Use these rules when changing managed-capability support, facade wiring, or
provider-backed capability guidance in the OSS repo.

## Canonical Pattern

Treat these as separate roles:

- `managed_capability` = provider- or operator-backed capability owned outside
  the generated app artifact
- `external_adapter` = thin client or callback bridge to an outside system
- app-owned `facade` module = the OSS-side API surface that pages and workflows
  call

Canonical flow:

`managed_capability` -> app-owned facade module -> `external_adapter` / thin client -> provider/operator service

## Hard Rules

- Do not copy managed engines, proprietary internals, secrets, or provider
  business logic into OSS code or generated output.
- Keep managed-capability examples provider-neutral in OSS docs, rules, skills,
  and tests.
- Pages and admin surfaces must bind through the app-owned facade module, not
  directly to provider-owned capability endpoints.
- Managed-capability adapters stay thin and contract-bound. They do not become alternate
  business-logic homes.
- If a provider-owned concept is not available in OSS, describe it as
  provider-owned, managed, or external. Do not present it as required
  contributor setup.

## Reporting

When managed-capability support changes, state whether the change affected
managed-capability classification, facade wiring, adapter clients, or
OSS/provider boundary rules.
