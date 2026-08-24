# Eval And Build Intelligence Boundary

This document fixes the ownership boundary between generated-artifact
evaluation and build intelligence so the split is decided once, not
re-litigated per change.

Two different things get called "eval". They belong on different sides of the
OSS/hosted boundary.

## Deterministic evaluation — OSS

Deterministic evaluation is rule-based scoring of canonical Mozaiks artifacts:
the bundle scanner, acceptance gates, and the bundle-quality scorers and
regression eval under `factory_app/eval/`. Same inputs produce the same score
on every run. No learned state, no cross-tenant data.

This stays in the OSS repo, and specifically in `factory_app/` when the policy
being scored is builder-specific:

- It is the quality gate on open contracts. Contributors and self-hosters must
  be able to run the same gates the factory runs, or the OSS framework is not
  trustworthy.
- Transparency is a feature. Nothing about a deterministic checklist is a
  competitive asset, and hiding evals from OSS users would undermine the
  contract-first model.
- Per the repo decision rules: generic scoring mechanics would belong in
  `mozaiksai/`; builder-specific scoring policy (which scorers, which
  thresholds, which fixture corpus) belongs in `factory_app/`. Today the
  scorers and the policy are small enough to live together in
  `factory_app/eval/`. If a generic scoring engine emerges, move the engine to
  `mozaiksai/` and leave the policy and fixtures in `factory_app/`.

## Build intelligence — hosted product

Build intelligence is the learning loop: aggregating signals across many
builds and tenants, learning which generation patterns produce better apps,
ranking or steering generation from fleet data, and any model or service
trained on hosted usage.

This belongs in the hosted product (`mozaiks-app`), not in this repo:

- The data is the moat, not the scorer code. Cross-tenant learning signals are
  hosted-product assets and must never be aggregated inside OSS runtime code.
- OSS code may *emit* neutral, app-scoped signals through declared contracts
  (events, usage ledger records, eval scores). The hosted product consumes
  them through its own ingestion — OSS never reaches into hosted stores, and
  hosted learning never becomes a hidden dependency of OSS generation.
- A learned policy that the factory should apply must arrive as an explicit,
  inspectable input (a catalog, a config overlay, a declared context asset),
  never as an opaque runtime call from OSS to a proprietary service.

## Target state: declarative eval contracts (not implemented)

The intended long-term shape for app-leverageable evals follows the
contract-declared customization rule:

- an `eval.yaml` (or equivalent) declares scorers, thresholds, and fixture
  references as a structured-output-first contract;
- bounded Python scorer stubs are referenced explicitly by the contract, local
  to the declaring boundary, deterministic, and side-effect free;
- eval runs execute as ordinary workflow steps or acceptance-gate hooks, with
  scores persisted as artifacts the user can see.

Do not present this as current behavior. Until a loader and schema exist,
`factory_app/eval/` remains a repo-internal regression suite invoked by tests
and scripts, not an app-facing contract.

## Review checklist

When a change touches evaluation or intelligence:

- Deterministic scorer or gate on canonical artifacts → OSS
  (`factory_app/eval/`, or `mozaiksai/` if generic mechanics).
- Anything that learns from, aggregates, or ranks across tenants/builds →
  hosted product; OSS side limited to emitting declared, app-scoped signals.
- No OSS runtime call-outs to proprietary intelligence services; learned
  policy enters the factory only as explicit declared inputs.
- Evals visible to the user; scores persisted as artifacts, never hidden.
