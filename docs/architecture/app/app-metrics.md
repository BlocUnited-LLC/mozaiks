# App Metrics

Mozaiks includes a host-neutral app metrics primitive for durable product
signals such as page views, feature usage, campaign clicks, conversions, and
funnel steps. It can also record numeric KPI snapshots such as active users,
revenue, retention, usage, and conversion totals over a reporting period.

Module services can record a signal through the injected module context:

```python
await ctx.metrics.track(
    "app.viewed",
    subject_type="app",
    subject_id=app_id,
    session_id=session_id,
    attribution_id=attribution_id,
    dimensions={"surface": "discover"},
    visibility="public",
)
```

Use `record_snapshot(...)` when the value is a measured KPI rather than a
one-off event:

```python
await ctx.metrics.record_snapshot(
    "kpi.active_users",
    subject_type="app",
    subject_id=app_id,
    value=1840,
    unit="users",
    period_start="2026-07-01T00:00:00+00:00",
    period_end="2026-07-31T23:59:59+00:00",
    aggregation="monthly_unique",
    visibility="admin",
)
```

Metrics are app-scoped through `ctx.persistence` and are stored in the generic
`app_metrics.events` collection pair. The runtime records the app, tenant,
workspace, actor, correlation id, source module, source action, subject,
attribution id, session id, dimensions, metadata, optional measurement period,
unit, aggregation label, and numeric value.

Use app metrics for measured usage signals. Keep business interpretation in the
owning app:

- payment, billing, and payout truth belongs in payment and wallet modules
- ad pricing, campaign budgets, ranking, and marketplace packaging belong in the
  hosted product or app module that owns that business model
- campaign ROI, revenue-share eligibility, and settlement formulas belong in
  the owning app or hosted product; metrics only provide measured inputs
- generated/self-hosted apps can use the same primitive for their own product
  analytics without depending on hosted Mozaiks services

For aggregate reads, use `ctx.metrics.summarize(...)` for counts and
`ctx.metrics.funnel([...])` for step-to-step conversion rates. Use
`ctx.metrics.summarize_values(...)` for KPI value aggregates; it returns
count, sum, average, min, max, first, and latest values over the scoped query.
Use module-owned facade actions when exposing those aggregates to UI so
visibility, tenancy, and business rules remain explicit.
