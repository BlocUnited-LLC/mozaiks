"""Deterministic portfolio insight rules ("needs attention").

Pure rule evaluation over per-app metric values — no LLM involvement and no
I/O. Each rule inspects the current-versus-previous values produced by the
analytics service and emits at most a handful of ranked, concise insights.
The rule set can grow more sophisticated later without changing the surface
contract: an insight is always ``{insight_id, severity, app_id, app_name,
metric_id, headline, detail}`` plus optional delta fields.

Severity ordering (most important first): ``attention`` > ``highlight`` >
``watch``. Within a severity, insights rank by absolute impact. Output is
fully deterministic for a given input.
"""

from __future__ import annotations

from typing import Any

# Rule thresholds — deliberately explicit module constants so behavior is
# auditable and adjustable in one place.
MRR_DECLINE_PCT_THRESHOLD = -5.0
MRR_GROWTH_PCT_THRESHOLD = 5.0
CONVERSION_DROP_PP_THRESHOLD = -2.0
CHURN_RISE_PP_THRESHOLD = 2.0
RETENTION_GAIN_PP_THRESHOLD = 2.0
ACTIVE_USERS_SWING_PCT_THRESHOLD = 20.0
MRR_MILESTONES = (100.0, 1_000.0, 10_000.0, 100_000.0, 1_000_000.0)

MAX_INSIGHTS = 6

_SEVERITY_RANK = {"attention": 0, "highlight": 1, "watch": 2}


def _metric(app: dict[str, Any], metric_id: str) -> dict[str, Any]:
    metrics = app.get("metrics") or {}
    value = metrics.get(metric_id)
    return value if isinstance(value, dict) else {}


def _value(app: dict[str, Any], metric_id: str) -> float | None:
    raw = _metric(app, metric_id).get("value")
    return None if raw is None else float(raw)


def _previous(app: dict[str, Any], metric_id: str) -> float | None:
    raw = _metric(app, metric_id).get("previous")
    return None if raw is None else float(raw)


def _delta_pct(app: dict[str, Any], metric_id: str) -> float | None:
    raw = _metric(app, metric_id).get("delta_pct")
    return None if raw is None else float(raw)


def _insight(
    *,
    insight_id: str,
    severity: str,
    app: dict[str, Any],
    metric_id: str,
    headline: str,
    detail: str,
    delta_pct: float | None = None,
    delta: float | None = None,
    impact: float = 0.0,
) -> dict[str, Any]:
    return {
        "insight_id": insight_id,
        "severity": severity,
        "app_id": app.get("app_id"),
        "app_name": app.get("name") or app.get("app_id"),
        "metric_id": metric_id,
        "headline": headline,
        "detail": detail,
        "delta_pct": delta_pct,
        "delta": delta,
        "_impact": impact,
    }


def _format_pct(value: float) -> str:
    return f"{abs(value):.1f}%"


def _format_pp(value: float) -> str:
    return f"{abs(value):.1f} points"


def _rule_mrr_decline(apps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    worst: dict[str, Any] | None = None
    worst_pct = 0.0
    for app in apps:
        change = _delta_pct(app, "mrr")
        if change is None or change > MRR_DECLINE_PCT_THRESHOLD:
            continue
        if worst is None or change < worst_pct:
            worst, worst_pct = app, change
    if worst is None:
        return []
    return [
        _insight(
            insight_id="mrr_decline",
            severity="attention",
            app=worst,
            metric_id="mrr",
            headline=f"{worst.get('name')} MRR declined {_format_pct(worst_pct)}",
            detail="Largest revenue decline in the portfolio this period.",
            delta_pct=worst_pct,
            impact=abs(worst_pct),
        )
    ]


def _rule_mrr_growth_leader(apps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best: dict[str, Any] | None = None
    best_pct = 0.0
    for app in apps:
        change = _delta_pct(app, "mrr")
        if change is None or change < MRR_GROWTH_PCT_THRESHOLD:
            continue
        if best is None or change > best_pct:
            best, best_pct = app, change
    if best is None:
        return []
    return [
        _insight(
            insight_id="mrr_growth_leader",
            severity="highlight",
            app=best,
            metric_id="mrr",
            headline=f"{best.get('name')} is the fastest-growing app",
            detail=f"MRR grew {_format_pct(best_pct)} versus the comparison period.",
            delta_pct=best_pct,
            impact=best_pct,
        )
    ]


def _rule_conversion_drop(apps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for app in apps:
        current = _value(app, "paid_conversion")
        previous = _previous(app, "paid_conversion")
        if current is None or previous is None:
            continue
        change_pp = current - previous
        if change_pp > CONVERSION_DROP_PP_THRESHOLD:
            continue
        new_users_pct = _delta_pct(app, "new_users")
        if new_users_pct is not None and new_users_pct > 0:
            detail = (
                f"Signups grew {_format_pct(new_users_pct)} but paid conversion "
                f"fell {_format_pp(change_pp)}."
            )
        else:
            detail = f"Paid conversion fell {_format_pp(change_pp)} this period."
        results.append(
            _insight(
                insight_id="conversion_drop",
                severity="attention",
                app=app,
                metric_id="paid_conversion",
                headline=f"{app.get('name')} paid conversion declined",
                detail=detail,
                delta=change_pp,
                impact=abs(change_pp),
            )
        )
    results.sort(key=lambda item: -item["_impact"])
    return results[:1]


def _rule_churn_spike(apps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    worst: dict[str, Any] | None = None
    worst_pp = 0.0
    for app in apps:
        current = _value(app, "churn_rate")
        previous = _previous(app, "churn_rate")
        if current is None or previous is None:
            continue
        change_pp = current - previous
        if change_pp < CHURN_RISE_PP_THRESHOLD:
            continue
        if worst is None or change_pp > worst_pp:
            worst, worst_pp = app, change_pp
    if worst is None:
        return []
    return [
        _insight(
            insight_id="churn_spike",
            severity="attention",
            app=worst,
            metric_id="churn_rate",
            headline=f"{worst.get('name')} churn increased materially",
            detail=f"Churn rose {_format_pp(worst_pp)} versus the comparison period.",
            delta=worst_pp,
            impact=abs(worst_pp),
        )
    ]


def _rule_mrr_milestone(apps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for app in apps:
        current = _value(app, "mrr")
        previous = _previous(app, "mrr")
        if current is None or previous is None:
            continue
        crossed = [m for m in MRR_MILESTONES if previous < m <= current]
        if not crossed:
            continue
        milestone = max(crossed)
        results.append(
            _insight(
                insight_id="mrr_milestone",
                severity="highlight",
                app=app,
                metric_id="mrr",
                headline=f"{app.get('name')} crossed ${milestone:,.0f} MRR",
                detail="A meaningful revenue milestone for this app.",
                delta=current - previous,
                impact=milestone,
            )
        )
    results.sort(key=lambda item: -item["_impact"])
    return results[:1]


def _rule_retention_gain(apps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best: dict[str, Any] | None = None
    best_pp = 0.0
    for app in apps:
        current = _value(app, "retention")
        previous = _previous(app, "retention")
        if current is None or previous is None:
            continue
        change_pp = current - previous
        if change_pp < RETENTION_GAIN_PP_THRESHOLD:
            continue
        if best is None or change_pp > best_pp:
            best, best_pp = app, change_pp
    if best is None:
        return []
    return [
        _insight(
            insight_id="retention_gain",
            severity="watch",
            app=best,
            metric_id="retention",
            headline=f"{best.get('name')} retention improved",
            detail=f"Retention rose {_format_pp(best_pp)} versus the comparison period.",
            delta=best_pp,
            impact=best_pp,
        )
    ]


def _rule_active_users_swing(apps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    biggest: dict[str, Any] | None = None
    biggest_pct = 0.0
    for app in apps:
        change = _delta_pct(app, "active_users")
        if change is None or abs(change) < ACTIVE_USERS_SWING_PCT_THRESHOLD:
            continue
        if biggest is None or abs(change) > abs(biggest_pct):
            biggest, biggest_pct = app, change
    if biggest is None:
        return []
    direction = "grew" if biggest_pct > 0 else "dropped"
    return [
        _insight(
            insight_id="active_users_swing",
            severity="watch",
            app=biggest,
            metric_id="active_users",
            headline=f"{biggest.get('name')} active users {direction} {_format_pct(biggest_pct)}",
            detail="Largest active-user movement in the portfolio this period.",
            delta_pct=biggest_pct,
            impact=abs(biggest_pct),
        )
    ]


_RULES = (
    _rule_mrr_decline,
    _rule_conversion_drop,
    _rule_churn_spike,
    _rule_mrr_growth_leader,
    _rule_mrr_milestone,
    _rule_retention_gain,
    _rule_active_users_swing,
)


def build_portfolio_insights(
    apps: list[dict[str, Any]],
    *,
    max_insights: int = MAX_INSIGHTS,
) -> list[dict[str, Any]]:
    """Evaluate every rule and return the ranked, capped insight list.

    ``apps`` rows carry ``app_id``, ``name``, and a ``metrics`` map of
    metric_id -> ``{value, previous, delta, delta_pct}`` envelopes.
    """

    rows = [app for app in apps if isinstance(app, dict)]
    collected: list[dict[str, Any]] = []
    for rule in _RULES:
        collected.extend(rule(rows))

    # One insight per app: keep its most severe / highest impact finding so a
    # single struggling app does not flood the attention section.
    best_by_app: dict[str, dict[str, Any]] = {}
    for insight in collected:
        app_id = str(insight.get("app_id"))
        current = best_by_app.get(app_id)
        if current is None:
            best_by_app[app_id] = insight
            continue
        candidate_rank = (_SEVERITY_RANK[insight["severity"]], -insight["_impact"])
        current_rank = (_SEVERITY_RANK[current["severity"]], -current["_impact"])
        if candidate_rank < current_rank:
            best_by_app[app_id] = insight

    ranked = sorted(
        best_by_app.values(),
        key=lambda item: (_SEVERITY_RANK[item["severity"]], -item["_impact"], str(item["app_id"])),
    )
    results = []
    for insight in ranked[:max_insights]:
        payload = dict(insight)
        payload.pop("_impact", None)
        results.append(payload)
    return results


__all__ = ["MAX_INSIGHTS", "build_portfolio_insights"]
