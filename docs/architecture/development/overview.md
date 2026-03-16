# Development Specs

This directory contains development-stage architecture specs for planning and
compiling app bundles.

Unlike the foundations docs, these files are intentionally operational. They
describe how to derive non-AI CRUD, UI page, and automation declaratives from
typed planning contracts during build.

## Why This Folder Exists

The workflow/runtime docs explain AI execution behavior. They do not fully
cover the non-AI substrate derivation path.

This folder closes that gap by defining:

- how CRUD and page logic are planned
- how domain events are emitted from substrate actions
- how event routes trigger workflows without leaking workflow names into
  `mozaikscore`
- which stubs are generated and what strict signatures they must follow

## Source Inputs

These development specs were derived from:

- current typed contracts in `mozaiksai/core/orchestration/planning_contracts.py`
- current bundle shape under `platform/`
- archived planning logic:
  - `ARCHIVED_mozaiks-core/docs/archive/architecture-specs/agent-dependency-matrix.md`
  - `ARCHIVED_mozaiks-core/docs/archive/architecture-specs/ai-agent-codebase-8-part-audit.md`

The archived docs are used as derivation style input, not as canonical runtime
file contracts.

## Documents

- [Substrate UI Planning Spec](./substrate-ui-planning-spec.md)
- [Substrate UI Dependency Matrix](./substrate-ui-dependency-matrix.md)

