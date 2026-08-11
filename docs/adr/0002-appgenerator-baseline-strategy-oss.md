# ADR 0002 — AppGenerator Baseline Strategy Is Intentionally Public

## Status

Accepted

## Context

`factory_app/workflows/AppGenerator/agents.yaml` and companion build-context files
under `factory_app/build_context/AppGenerator/` encode the generation strategy the
Factory uses to decompose a product brief into a canonical app build plan, including:

- agent role and instruction prompts for `InterviewAgent`, `AppPlanAgent`, and
  downstream code-generation agents
- industry-standard defaults for subscription tiers, social feature sets, layout
  patterns, and monetization models by product category
- capability directory, capability routing, file contracts, module archetypes, and
  shell presets used at generation time
- structured output contracts for `AppBuildPlan` and companion artifacts

The `AppGenerator` agent file is large (≈ 300 KB combined with prompts and context
catalogs) and ships in the PyPI wheel because the installed package must be able to
generate apps without a separate download step
(`mozaiksai.resources.resolve_factory_workflows_root()` resolves the path from the
installed package).

The question reviewed here is whether this content is appropriate for public MIT
distribution or whether it contains commercially sensitive accumulated intelligence
that should remain private.

## Decision

**Publish as-is.** The current AppGenerator baseline strategy is intentionally public.

The content was reviewed against the framework/operator boundary defined in
`OSS_PUBLICATION_POLICY.md` and
`docs/architecture/foundations/framework-operator-intelligence-boundary.md`.

### What the current content is

The AppGenerator prompts encode **baseline reasoning derived from public industry
knowledge**, not from BlocUnited customer history or accumulated operational data:

- Subscription tier defaults by product category (B2B SaaS, consumer, creator, etc.)
  are widely published industry benchmarks.
- Social feature defaults by app domain reflect established product patterns.
- Profile layout defaults, capability directory, and monetization defaults are
  domain-standard recommendations, not operator-learned rankings.
- Module archetypes and file contracts are Mozaiks framework conventions — they must
  be public for app contributors to follow them.
- The instruction set is structural: it tells agents *how to structure a plan*, not
  *how BlocUnited has ranked historical outcomes*.

None of the current content includes:
- correction corpora or learned repair rankings
- cross-customer outcome correlations
- eval-derived model routing
- production outcome data
- BlocUnited customer history or feedback signals

### Why it must be public

`factory_app` is the OSS reference canonical application.  If the AppGenerator
strategy were private, Factory would produce lower-quality output and would not be
genuinely self-hostable — violating Core Invariant 4 ("OSS must not be intentionally
crippled merely to force hosted adoption").

Removing the strategy prompts from the wheel would make the installed package
non-functional for app generation, failing the no-fork test.

### What remains commercial

Operator intelligence layered on top of the public baseline — eval corpora, learned
rankings, cross-customer corrections, production outcome correlations, and hosted
operational knowledge — is NOT present in this repository.  Those artifacts are
private by default under `OSS_PUBLICATION_POLICY.md`.

## Consequences

- Future substantial additions to AppGenerator strategy that are **learned or
  operator-derived** (not baseline industry knowledge) require a new publication
  review ADR before they enter this repository.
- Future eval-derived generator optimizers, correction datasets, or cross-customer
  repair data must be committed to `mozaiks-app` or a private artifact store, not
  to this repo.  `governance_guardrails.py` enforces learned-artifact directory
  quarantine at the source level; `scripts/package_content_guard.py` enforces it at
  the package level.
- This ADR must be updated if the content character changes — for example if
  agent prompts are retrained from customer feedback or if eval-derived routing is
  added inline.

## Affected invariants

- ARCHITECTURAL_INVARIANTS.md §5 — Generic App Intelligence Can Be OSS; Multi-App
  Learned Intelligence Requires Review.
- `OSS_PUBLICATION_POLICY.md` §One-Way Door Standard.

## Alternatives considered

**Make AppGenerator strategy private, inject via env/config at runtime.**
Rejected: would require a runtime credential or separate download to use Factory at
all, violating OSS self-hosting and the no-fork requirement for App Zero.

**Publish a stripped-down version with reduced defaults.**
Rejected: the defaults are derived from public industry knowledge; stripping them
would cripple generation quality without protecting any real proprietary advantage.

**Tag each prompt section with an explicit publication marker.**
Considered but deferred: the governance guardrail (`notice: publication_review_surface`)
already surfaces review prompts in CI; per-section markers would duplicate that signal
without adding enforcement power.

## Review

- Reviewed by: distribution hardening audit (August 2026)
- OSS_PUBLICATION_POLICY.md sections applied: Fast Path, One-Way Door Standard
- framework-operator boundary document: `docs/architecture/foundations/framework-operator-intelligence-boundary.md`
