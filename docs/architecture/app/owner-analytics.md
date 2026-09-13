# Owner Analytics — Portfolio and App Performance

Owner analytics is the deterministic World → App → Metric → Detail
progressive-disclosure system behind the Studio **Performance** page
(`/performance`), the per-app **Revenue** (`/apps/:appId/revenue`) and
**Users** (`/apps/:appId/users`) pages, and the universal metric drill-down
drawer. It answers, in order: what happened, is that good or bad, why did it
happen, and do I need to do anything.

No LLM sits in this path. Every number, insight, and funnel stage is computed
by the deterministic pipeline described here.

## Layering

```text
Metric Registry            mozaiksai/core/metrics/definitions.py
  ↓
Signal stores              AppMetrics (kpi.* snapshots, usage instrumentation)
  ↓
Aggregation math           mozaiksai/core/metrics/portfolio.py   (pure)
Period model               mozaiksai/core/metrics/periods.py     (pure)
Insight rules              mozaiksai/core/metrics/insights.py    (pure)
  ↓
Assembly service           mozaiksai/core/metrics/owner_analytics.py
  ↓
Owner-gated HTTP           /api/studio/analytics/* (studio host)
  ↓
View models                factory_app/app/admin/pages/analyticsModel.js (pure)
  ↓
Primitives                 chat-ui: MetricSummaryStrip, InsightList,
                           MetricBridge, StageFunnel, UsageTrendPanel
  ↓
Pages                      WorkspacePerformancePage, AppRevenuePage,
                           AppUsersPage, MetricDetailPanel
```

One metric has exactly one definition. MRR's math lives in the registry +
portfolio module and nowhere else; every surface (World View, App Overview,
Revenue view, metric drawer) renders the same envelope
`{value, previous, delta, delta_pct, available}`.

## Metric semantics

The registry (`build_default_metric_registry`) declares two domains —
**revenue** and **users** — and three semantic axes per metric:

- **aggregation** — `additive` portfolio values sum across apps
  (MRR, ARR components, user counts). `derived` values are recomputed from
  underlying portfolio totals, never averaged across apps: portfolio paid
  conversion is `total paying users / total users`, not the mean of per-app
  rates. NRR/GRR derive from summed movement components against the summed
  starting MRR.
- **temporality** — `stock` metrics read the latest level in the window
  (MRR, paying users); `flow` metrics sum activity across the window
  (New MRR, new users, churned MRR).
- **polarity** — whether up is good (`higher_is_better`), bad
  (`lower_is_better`, e.g. churn), or neutral; the UI colors deltas from
  polarity, never from raw sign.

**Benchmarks** are portfolio *medians* across per-app values, labelled as
benchmarks in the UI — context for comparison, never the portfolio operating
number.

**Periods** (`resolve_period`) are half-open UTC windows with an equal-shape
comparison window: rolling (`7d`/`30d`/`90d`) versus the immediately
preceding window; calendar (`mtd`/`qtd`/`ytd`) versus the same elapsed slice
of the previous month/quarter/year. No widget implements its own date math.

## Data sources — what is real

| Source | Metrics | Who writes it |
|---|---|---|
| `kpi.<metric_id>` snapshot events (`AppMetrics.record_snapshot`) | mrr, new/expansion/contraction/churned MRR, total/new/paying/churned users | the system that owns the fact — typically the app's billing integration or a host-operator job |
| Automatic usage instrumentation (`app.page_view`, `app.action_invoked`) | active_users (distinct actors per window), activity series | the platform host, automatically |
| Derived | arr, net_new_mrr, growth, nrr, grr, arpu, arppu, paid_conversion, churn_rate, retention | computed, single authority |

**Nothing is fabricated.** A metric without recorded data is `None` end to
end and renders as pending with an explanation of what will populate it.
`PlanDef.price` (`config/subscriptions.yaml`) makes MRR deterministically
computable — active assignments per plan × `price.monthly_amount_cents()` —
and the billing system that grants assignments is expected to record the
resulting `kpi.mrr` snapshots.

## Funnels — schema-driven, app-specific

Apps declare lifecycle funnels in `app/config/metrics.yaml`
(`mozaiks.metrics.v1`, loaded by
`mozaiksai/core/runtime/app/metrics_loader.py`, fail-closed like
subscriptions). Steps bind to metric event names the app records via
`ctx.metrics.track`; stage counts are **distinct subjects** (actor, session,
or attribution id — `AppMetrics.funnel(count_distinct=...)`), so conversion
compares populations rather than raw event volume. No funnel is hard-coded;
an app without the file simply shows the "no funnel configured" state.

## Authorization boundary

The studio endpoints resolve ownership through the app registry
(`AppRegistryService.list_apps / get_app_record` with `owner_user_id`), then
pass the owned records into `OwnerAnalyticsService`. The service never
resolves ownership itself and only reads stores for the app rows it was
given. These endpoints are deliberately **not** admin-role-gated
`/api/admin/*` reads: an owner sees exactly their own apps.

- `GET /api/studio/analytics/portfolio?period=`
- `GET /api/studio/analytics/apps/{app_id}?period=`
- `GET /api/studio/analytics/apps/{app_id}/metrics/{metric_id}?period=`

## Insights ("needs attention")

`build_portfolio_insights` evaluates explicit threshold rules (largest MRR
decline, growth leader, conversion drop, churn spike, milestone crossing,
retention gain, active-user swing) over per-app envelopes, ranks
attention > highlight > watch by impact, keeps one insight per app, and caps
the list. Thresholds are module constants; the rule set can grow without
touching the surface contract.

## UI composition

Reusable primitives live in `chat-ui/src/ui/primitives/`:
`MetricSummaryStrip` (KPI row with polarity-aware deltas and click-through),
`InsightList`, `MetricBridge` (start → drivers → end movement), and
`StageFunnel`, alongside the existing `UsageTrendPanel`. Pages compose these;
`MetricDetailPanel` is the universal drill-down drawer (definition, value,
comparison, trend, drivers, related metrics, portfolio-median benchmark) and
can re-target itself as the user keeps digging.

Demo mode (`VITE_USAGE_DEMO_MODE`) follows the established Studio convention:
explicitly badged demo payloads in `studioDemoData.js`, never silent
fabrication in live mode.

## Known gaps

- No shipped system records `kpi.mrr` snapshots yet; hosted billing
  (mozaiks-app) is the natural writer. Until then revenue metrics render as
  pending in live mode.
- `retention`/`churn_rate` are period ratios from churned-user snapshots,
  not cohort retention curves.
- Portfolio trend series render for metrics with daily data (MRR, net new
  MRR, ARR, active/paying/new users); NRR and retention appear as values,
  not series.
