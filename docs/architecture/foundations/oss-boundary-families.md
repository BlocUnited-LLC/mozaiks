# OSS Boundary Family Registry

This document is the authoritative DO-NOT-MOVE family registry for the Mozaiks
OSS repository. It records which code families must remain OSS, which
categories are private by default, and the governing rule for distinguishing
mechanisms from artifacts.

The governing ADR is
[ADR 0003: Pre-1.0 OSS / Proprietary Boundary Freeze](../../adr/0003-pre-1-0-oss-proprietary-boundary-freeze.md).

---

## Core Rule

**DIFFERENT INTELLIGENCE, SAME CANONICAL APP.**

Open the ability to understand and build one application. Deliberately review
what BlocUnited learns from understanding or operating many applications.

---

## DO-NOT-MOVE Families

The following families must remain in this OSS repository. Moving them to a
private or proprietary repo would break the public canonical app model and the
self-hosting promise.

| Family | Why it must stay OSS |
|--------|----------------------|
| `mozaiksai/core/` | Universal runtime substrate — execution, transport, persistence, event primitives |
| `mozaiksai/hosts/` | Layered FastAPI host compositions — runtime, platform, studio |
| `mozaiksai/control_plane/` | Refinement harness engine and checkpoint runtime |
| `mozaiks_cli/` | Developer tooling and workspace scaffolding |
| `web_shell/` | OSS web shell surface |
| `chat-ui/` | React chat component library |
| `factory_app/workflows/AppGenerator/` | Canonical OSS app generator — baseline industry strategy, not BlocUnited operational data |
| `factory_app/workflows/AgentGenerator/` | Canonical OSS workflow/agent bundle generator |
| `factory_app/workflows/DesignDocs/` | Frontend, backend, database, and UI-schema design intent workflow |
| `factory_app/workflows/ValueEngine/` | Concept and value decomposition workflow |
| `factory_app/workflows/ThemeCapture/` | Visual identity capture and theme authority workflow |
| `factory_app/workflows/ExistingAppDiscovery/` | Brownfield app adoption workflow — generic OSS capability |
| `factory_app/refinement_harness/` | OSS refinement harness pack — mechanism only; BlocUnited's learned policies live in `mozaiks-app` |
| `factory_app/build_context/AppGenerator/` | AppGenerator prompt catalogs and file contracts — baseline strategy |
| `factory_app/build_context/mozaikspay/` | MozaiksPay capability pack — OSS facade wiring mechanism |
| `factory_app/app/` | Studio first-party reference app workspace |

---

## Private-by-Default Categories

Content in these categories must NOT enter this repository without a new ADR
approving publication:

| Category | Examples | Reason |
|----------|----------|--------|
| Eval corpus and corrections | Scored outputs, human correction datasets, eval benchmarks | BlocUnited operational data |
| Learned strategy and weights | Learned ranking tables, correction-derived prompt variants | Derived from operating many applications |
| Cross-app operational patterns | Patterns extracted from multiple customer apps | Multi-app learned intelligence |
| Customer priors and usage data | Customer-specific context, usage frequency data | Private by definition |
| Hosted-product provider adapters | Stripe adapter, DNS provider code, deployment platform mechanics | `mozaiks-app` only |
| Commercial policy and billing logic | Specific plan tiers, payout rules, revenue split | `mozaiks-app` only |

---

## Mechanism vs Artifact Rule

A **mechanism** is a generic interface, schema, framework, or evaluation
contract that any operator can use or implement independently. An **artifact**
is the specific content, dataset, or learned result that fills a mechanism.

**Publish mechanisms. Require ADR review for artifacts.**

| Item | Classification | Location |
|------|---------------|----------|
| `KnowledgeStore` interface | Mechanism | OSS (`mozaiksai/`) |
| BlocUnited knowledge content loaded into `KnowledgeStore` | Artifact | Private |
| Evaluation framework interface / scoring contract | Mechanism | OSS |
| BlocUnited eval corpus and correction datasets | Artifact | Private (`mozaiks-app/build_intelligence/`) |
| Telemetry schema (`telemetry.py`, narrow bounded fields) | Mechanism | OSS |
| Raw telemetry collected from BlocUnited customers | Artifact | Private |
| Capability routing interface (future seam) | Mechanism | OSS (future, no implementation yet) |
| BlocUnited's learned routing weights | Artifact | Private |
| AppGenerator prompt catalogs — baseline industry patterns | Mechanism | OSS |
| Prompt variants derived from BlocUnited customer corrections | Artifact | Private |

---

## Future Seam: `capability_routing_hints`

A `capability_routing_hints` injection point may be introduced in a future
release to allow operators to inject learned routing preferences without
modifying OSS code. This is a **small seam only** — document only at this
time. No implementation is approved until a new ADR records the design and
confirms the artifact boundary is preserved.

---

## App Zero No-Fork Contract

`mozaiks-app` (App Zero) must consume public OSS seams from this repository
rather than maintaining a private framework fork. The pre-1.0 audit confirmed:

- Zero framework duplication across `mozaiks-app` Python modules.
- 22 declared public seams consumed correctly via `oss_reuse_contract.yaml`.

When `mozaiks-app` needs new generic framework behavior, it opens a PR against
`mozaiks/` rather than duplicating the logic privately.

---

## Change Discipline

1. Adding content to a DO-NOT-MOVE family: OSS by default. No ADR needed.
2. Adding content derived from BlocUnited operational data to any family:
   requires ADR review before the content enters this repository.
3. Moving any DO-NOT-MOVE family to a private repo: requires ADR review and
   would be a breaking change after 1.0 publication.
4. Adding a new public mechanism or interface: follow the
   [One-Way Door Standard](../../../OSS_PUBLICATION_POLICY.md).
