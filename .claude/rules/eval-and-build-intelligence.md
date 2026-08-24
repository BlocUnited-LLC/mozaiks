# Eval And Build Intelligence Rules

Use these rules when changing `factory_app/eval/`, acceptance gates, bundle
scorers, or anything described as evaluation, scoring, learning, or build
intelligence.

Canonical boundary:
[docs/architecture/foundations/eval-and-build-intelligence-boundary.md](../../docs/architecture/foundations/eval-and-build-intelligence-boundary.md)

- Deterministic, rule-based scoring of canonical artifacts stays OSS:
  builder-specific policy in `factory_app/eval/`, generic scoring mechanics
  (if any emerge) in `mozaiksai/`.
- Learning loops, cross-tenant signal aggregation, and learned generation
  policy belong in the hosted product. OSS may only emit declared, app-scoped
  signals for the hosted side to consume.
- No OSS runtime call-outs to proprietary intelligence services. Learned
  policy enters the factory only as explicit, inspectable declared inputs
  (catalogs, config overlays, context assets).
- Evals are user-visible. Persist scores as artifacts; never run hidden evals
  on user builds.
- Declarative app-facing eval contracts (`eval.yaml` + bounded scorer stubs)
  are target state, not current behavior. Do not document them as current
  until a schema and loader exist.
