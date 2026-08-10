# Pre-1.0 Public Architecture Roadmap

This checklist is repo-internal and not part of the published docs navigation.

## Phase 0 — Architecture Documentation / Freeze

Status: approved for this documentation pass.

- Commit durable public architecture decisions.
- Establish framework/application/operator terminology.
- Establish Framework Intelligence versus Operator/Learned Intelligence.
- Do not move strategy code.
- Do not implement new strategy seams.

## Phase 1 — Managed Capability Boundary Review

Status: completed.

- MozaiksPay remains the recommended/default monetization provider where
  currently intended.
- The public MozaiksPay-compatible provider contract is documented in
  `docs/architecture/modules-systems/mozaikspay-provider-contract.md`.
- Self-hosted and alternative provider replacement paths are documented through
  the generated app facade/client, provider API, entitlement, and fulfillment
  boundaries.
- Hosted payment processor, wallet, payout, settlement, merchant operation,
  production authority, and provider credential topology details remain outside
  the OSS pack contract.

## Phase 2 — Strategy Seams

Status: KnowledgeStore seam implemented. Refinement routing seam deferred.

### AG2 KnowledgeStore injection seam — IMPLEMENTED

`AG2NetworkRunnerRequest.knowledge_store` is the injection point.
Default behavior (`MemoryKnowledgeStore`) is unchanged for self-hosters.
See [AG2 Ownership Boundary](../architecture/workflows/ag2-ownership-boundary.md)
for lifecycle contract, security notes, and threading path.

### Refinement harness routing seam — DEFERRED

Harness overlays (`refinement_harness/config/harness.yaml`) already allow
operators to change which workflow sequence runs per change class. Introducing
a `RefinementRoutingPort` is premature — the primary operator customization
axis (workflow routing) is covered; classification policy is
framework-owned. Revisit when a concrete operator use case emerges.

## Phase 3 — Versioned Strategy Artifacts

Status: future.

- Document versioned strategy/config artifact shapes before publishing them.
- Keep generated canonical app outputs versioned separately from operator
  strategy inputs.

## Phase 4 — Learned/Eval-Informed Strategy

Status: future and publication-review required.

- Integrate approved learned strategy only after deciding what is public,
  operator-owned, or proprietary.
- Keep eval corpora, eval results, cross-customer correction data, and
  production outcome correlations out of OSS unless explicitly approved through
  the publication policy.

## Explicitly Not Planned In This Roadmap

- Moving current MIT-published baseline strategy code.
- Requiring BlocUnited services for OSS generation/refinement.
- Replacing AG2 with a Mozaiks-owned agent framework.
- Implementing production authority, payment execution, DNS execution, or
  hosted provider operations in OSS.
