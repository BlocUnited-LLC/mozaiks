# ADR 0003: Pre-1.0 OSS / Proprietary Boundary Freeze

Date: 2026-08-11

Status: accepted

## Decision

**DIFFERENT INTELLIGENCE, SAME CANONICAL APP.**

Mozaiks OSS owns the complete canonical application model and every generic
mechanism needed to understand, generate, validate, discover, and refine one
application. BlocUnited's hosted product owns the operator intelligence derived
from understanding or operating many applications.

The boundary is not about features — it is about the source of intelligence:

- Generic mechanisms (canonical app model, generation/refinement framework,
  evaluation framework interface, telemetry schema, capability routing
  interface) = OSS.
- BlocUnited-specific data filling those mechanisms (eval corpus, learned
  weights, correction datasets, customer priors, cross-app operational patterns)
  = private by default.

**Current OSS extraction candidates: NONE.**

A full pre-1.0 audit found zero candidate families ready or appropriate for
extraction. The current repository boundary is correct. See
[`docs/architecture/foundations/oss-boundary-families.md`](../architecture/foundations/oss-boundary-families.md)
for the DO-NOT-MOVE family registry and private-by-default category list.

## Reason

Rapid AI-assisted development increases the risk of accidentally publishing
one-way-door contracts. This ADR freezes the boundary so agents, contributors,
and reviewers have an explicit governance anchor before 1.0 publication.

The audit confirmed:

- `factory_app/workflows/` and `factory_app/refinement_harness/` contain
  baseline generation and refinement strategy that is generic and OSS-ready. No
  BlocUnited operational data or cross-app learned intelligence was found.
- `mozaiksai/` contains only generic runtime mechanics — no hosted-product
  business logic.
- `mozaiks-app` correctly consumes 22 declared public seams with zero framework
  duplication.
- Historical contamination is low and non-material. History retention is the
  correct call (see History section below).
- The source boundary is ready; release infrastructure is not yet protected
  (see Release section below).

## Alternatives Considered

- Extract `factory_app/workflows/AppGenerator/` to a separate proprietary repo:
  rejected. AppGenerator is the OSS reference builder — it expresses generic
  industry-standard app generation strategy, not BlocUnited intelligence.
  Extracting it would make the OSS framework less useful and harder to
  self-host.
- Extract `factory_app/refinement_harness/` to proprietary: rejected. The
  harness is the OSS mechanism. BlocUnited's learned refinement policies and
  eval data are already private (in `mozaiks-app/build_intelligence/`). Only
  the mechanism belongs in OSS.
- Extract `factory_app/workflows/ExistingAppDiscovery/`: rejected. Brownfield
  discovery is a generic OSS framework feature. No proprietary discovery
  heuristics were found.
- Retroactively purge git history: rejected. Contamination level is low and
  non-material. The OSS_PUBLICATION_POLICY.md One-Way Door Standard and
  governance guardrails protect the forward boundary. Purging history would
  destroy contributor context without meaningful risk reduction.
- Publish immediately with current release infrastructure: rejected. The GitHub
  `release` environment lacks branch protection rules. The tag trigger is
  disabled and protected by workflow_dispatch confirmation gate, but the
  environment itself is not protected. Release readiness requires completing
  the infra remediation in `docs/releasing.md` before publication.

## Consequences

- `factory_app/`, `mozaiksai/`, `mozaiks_cli/`, `web_shell/`, `chat-ui/` stay
  OSS. The DO-NOT-MOVE registry in `oss-boundary-families.md` is the
  authoritative list.
- New content that belongs in private families (eval data, learned strategy,
  cross-app corrections, operator-scoped intelligence) must stay in
  `mozaiks-app` or a new private repo — not enter `mozaiks/`.
- The `AppGenerator` baseline strategy (industry-standard patterns for
  understanding one application) is explicitly confirmed OSS. See ADR 0002.
- Future improvements that derive from BlocUnited operational data require a
  new ADR before publication.
- The `capability_routing_hints` seam, if ever implemented, is a small future
  injection point — not a mechanism to pull private intelligence into OSS.
  Document only; no implementation until a new ADR approves it.

## App Zero No-Fork Contract

`mozaiks-app` must consume public OSS seams rather than maintaining a private
framework fork. The pre-1.0 audit confirmed zero framework duplication across
all `mozaiks-app` Python modules. The 22 declared public seams in
`oss_reuse_contract.yaml` are the correct integration surface.

When `mozaiks-app` needs new generic framework behavior, it opens a PR against
`mozaiks/` rather than duplicating the logic privately.

## Mechanism vs Artifact Rule

A mechanism is an interface, schema, framework, or evaluation contract that
any operator can use or implement. An artifact is the content, dataset, or
learned result that fills a mechanism.

Examples:
- `KnowledgeStore` interface = mechanism (OSS). BlocUnited's knowledge content
  loaded into it = artifact (private).
- Evaluation framework / scoring interface = mechanism (OSS). BlocUnited's eval
  corpus and correction datasets = artifact (private).
- Telemetry schema in `telemetry.py` = mechanism (OSS, narrowly bounded). Raw
  telemetry collected from BlocUnited customers = artifact (private).
- Capability routing interface = mechanism (OSS, future). BlocUnited's learned
  routing weights = artifact (private).

When reviewing a new addition: identify the mechanism and the artifact
separately. Publish mechanisms; require ADR review for artifacts.

## History Decision

**KEEP.** Historical contamination is low and non-material. The
`OSS_PUBLICATION_POLICY.md` One-Way Door Standard and `governance_guardrails.py`
protect the forward boundary. Future commits that would introduce private
content are caught by static scan before merge.

## Release Boundary

Two separate facts:

1. **Source boundary: READY.** The code, contracts, and content in this
   repository reflect the correct OSS/proprietary split.
2. **Release infrastructure: NOT READY.** The GitHub `release` environment
   lacks branch protection rules. See `docs/releasing.md` for the required
   remediation checklist before publication.

Do not interpret source readiness as release permission. The release hold in
`CLAUDE.md` remains in effect until the user explicitly authorizes publication.

## Future Strategy Rule

Baseline generation and refinement strategy = OSS. Learned strategy, eval
evidence, operator corrections, and multi-app intelligence = private by default.

Adding new strategy content to `factory_app/` that derives from BlocUnited
operational data (even as prompts or YAML) requires a new ADR before the
content enters this repository.

## Reversibility

High risk: once the repository is published under MIT and third-party apps
consume its contracts, extraction of any currently-OSS family becomes a
breaking change requiring a major version and community communication.

## Affected Invariants

- Generic App Intelligence Can Be OSS; Multi-App Learned Intelligence Requires Review
- Public Framework Contracts Stay Provider-Neutral
- Agents Produce Candidates; Deterministic Code Validates and Promotes
- Public Schemas and Contracts Are Classified and Versioned
- Mozaiks App Dogfoods Public Framework Contracts
- Operator Capabilities Are Explicitly Separated

## OSS Boundary

Keep OSS: foundational. The boundary families listed in
`oss-boundary-families.md` are the canonical OSS surface. New content entering
those families is OSS by default. New content that derives from BlocUnited
operational data requires publication review.

## Validation

- `tests/test_oss_boundary_policy.py` — confirms boundary policy document and
  ADR files exist and contain required family declarations.
- `scripts/governance_guardrails.py` — source-level scan for private-pattern
  violations.
- `scripts/package_content_guard.py` — artifact-level scan confirming only
  approved top-level families ship in built wheels.
- `scripts/run_release_audit.py` — offline acceptance suite confirming all
  governance layers are coherent before any release attempt.
- ADR review required before adding a new one-way-door strategy surface,
  provider, authority, schema, eval corpus, or learned-intelligence artifact
  to this repository.
