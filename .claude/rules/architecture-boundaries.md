---
paths:
  - "AGENTS.md"
  - "CLAUDE.md"
  - "ARCHITECTURE.md"
  - "docs/**/*.md"
  - ".claude/**/*.md"
---

# Architecture Boundaries Rules

Use these rules when classifying a change, deciding placement, or writing
contributor guidance.

## First Classify The Layer

- Universal substrate: `mozaiksai/core/`, `mozaiksai/hosts/runtime`,
  execution/session/transport/event primitives, workflow loading, and generic
  `mozaiksai/control_plane/` runtime machinery.
- Framework capability: `mozaiksai.hosts.platform`,
  `mozaiksai.hosts.studio`, app/module/page/shell/admin contracts, `chat-ui/`,
  CLI developer tooling, `factory_app/workflows/`, `factory_app/build_context/`,
  `factory_app/refinement_harness/`, and `factory_app/app/` as the first-party
  builder/reference app workspace.
- Hosted product capability: app-local hosted product hosts outside this OSS
  repo, hosted-only collaboration/billing/marketplace/deployment surfaces, and
  proprietary hosted product engines or provider-owned managed capabilities
  consumed from outside this OSS repo.

## Ownership Boundaries

- `factory_app` is the first-party builder/reference app workspace plus shared
  factory workflows. It dogfoods contracts; it is not the runtime substrate.
- `factory_app/app/modules/factory_control_plane/` is a Studio identity stub,
  not the control-plane engine.
- `platform/` is repo-local infrastructure assets only, not an app workspace.
- `generated/` contains artifacts awaiting validation or promotion, not
  canonical runtime source.
- CLI and Studio are parallel interfaces over shared system capabilities.

## Placement Rules

- Put generic runtime mechanics in `mozaiksai/`.
- Put cross-workflow builder journey composition in
  `factory_app/workflows/extended_orchestration/extension_registry.json`.
- Put static factory build-time catalogs, contracts, reusable pack descriptors,
  and deterministic templates in named `factory_app/build_context/{context_name}/`
  directories declared by `context.yaml` `assets[]`.
- Put builder-session harness policy in `factory_app/refinement_harness/`, not in
  workflow-local handoffs or module handlers.
- Put app/module/page/shell behavior in platform or app workspace contracts,
  not in hosted-product surfaces.
- Put hosted-only behavior behind hosted-product layers or provider-neutral
  interfaces. Do not copy hosted product logic into OSS runtime or framework
  code.

## Reporting

In reviews and final reports, always state:

- layer changed
- whether the change affects universal substrate, framework capability, or
  hosted product capability
- whether `factory_app`, `chat-ui`, or active app workspace contracts were
  touched

