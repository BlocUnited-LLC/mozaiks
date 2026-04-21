# MFJ Authoring Roadmap

## Purpose

Keep MFJ authoring simple today while preserving a clear path for advanced runtime controls later.

This document is the holding area for MFJ integration logic that should **not** be in the default authoring surface yet.

## Baseline Authoring Profile (Current Default)

Author only what is needed for deterministic fork/join:

- `id`
- `decomposition_agent`
- `fan_out.spawn_mode` (usually `workflow`)
- optional `fan_out.child_initial_agent`
- optional `fan_out.max_children`
- `fan_in.resume_agent`
- optional `fan_in.resume_entry_agent` (defaults to `resume_agent`)
- optional `fan_in.inject_as` (auto-derived `mfj_*` key when omitted)
- optional `fan_in.aggregation_strategy` (defaults to `collect_all`)

Baseline rule: if a field is not needed for the first real use case, leave it out.

## Deferred Advanced Profile (Roadmap)

These fields remain runtime-capable but are intentionally deferred from baseline authoring:

- `fan_out.input_contract`
- `fan_out.child_context_seed`
- `fan_out.timeout_seconds`
- `output_contract`
- `fan_in.on_partial_failure`
- `fan_in.timeout_seconds`
- custom aggregation strategy registration (`custom:<name>`)

## Integration Milestones

1. Baseline GA
- Keep generated MFJ configs minimal and readable.
- Standardize resume behavior with `resume_agent` and optional router.

2. Advanced Contracts Pack
- Introduce opt-in templates for input/output contracts.
- Add lint checks for contract completeness when enabled.

3. Reliability Controls Pack
- Introduce opt-in timeout and partial-failure policies.
- Add UX surfacing for degraded runs and retry semantics.

4. Domain Profiles
- Publish reusable profiles (build pipelines, review workflows, consensus workflows).
- Keep profile docs separate from baseline MFJ authoring docs.

## Guardrails

- Do not make advanced fields mandatory in generators.
- Do not mix baseline and advanced examples in the same snippet without labeling.
- Prefer roadmap profile docs over widening baseline docs prematurely.
