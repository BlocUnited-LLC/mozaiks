# ADR 0014: Authenticated workflow outcome feedback

Date: 2026-10-06

Status: proposed; implementation in review.

## Decision

Publish a provider-neutral `mozaiks.workflow_feedback.v1` collection contract,
shipped workflow UI primitive, and generic generated app storage pack. Reuse
the authenticated UI response transport and existing session state for immutable
receipts. App module actions resolve those receipts through runtime provenance.

## Reason

An app needs attributable user reports about delivered workflow results without
accepting model-generated ratings or untrusted identity fields. A public
mechanism supports portable apps and auditable isolation. Its inputs are generic
product requirements, existing runtime contracts, and synthetic test fixtures;
no customer outcomes, learned policy, ranking, or evaluation corpus is included.

## Alternatives considered

- A new analytics receiver would duplicate transport authorization and leave
  ambiguous ownership of production data.
- Model-callable rating arguments would permit fabricated human evidence.
- Host-specific collection would make generated app feedback depend on a
  proprietary service and weaken self-hosting.

## Consequences

The runtime stores a private typed receipt with app/run/workflow/agent/outcome/
user/event attribution. Each app decides access, retention, export, and analysis
of copied records. Optional ratings and missing responses remain distinct.
General telemetry excludes these answers. No feedback engine, simulation
runner, cross-app analysis, investment policy, or commercial scoring is added.

The follow-on render acknowledgement records an authenticated, server-attributed
client visibility report separately from the response. The transport's offer
is not an impression. This primitive alone does not establish eligible
invitations or a response-rate denominator; consumer instrumentation and
coverage validation are required before such a rate can be reported.

## Reversibility

Medium risk: receipt fields and generated code adopt a versioned public
contract; future incompatible changes require migration. The publication is
MIT and contains generic collection mechanics only.

## Affected invariants

Invariants 1, 3, 4, 5, 6, 7, and 8 remain intact: provider neutrality,
deterministic validation, classified contracts, commercial separation, scoped
authority, and consumption through public primitives. Ordinary workflow module
dispatch still requires its authenticated principal; no authority bypass is
introduced.

## OSS boundary

Keep OSS: foundational. Customer data and analysis stay with the owning app.
Hosted learning, investment decisions, and any future cross-app optimization
remain independently reviewed proprietary surfaces.

## Validation

Strict-model and owner-checked transport tests; durable-receipt and concurrent
generated-storage checks against disposable real Mongo; browser controls and
authenticated transport; generator contracts, lint, and repository test suite.
See [runtime and generation contract](../architecture/workflows/outcome-feedback.md).
