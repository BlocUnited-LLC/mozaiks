"""Deterministic portfolio aggregation math for owner analytics.

Pure functions only — no I/O. These implement the aggregation semantics the
metric registry declares:

- additive metrics sum across apps, treating "no data anywhere" as ``None``
  rather than a fabricated zero;
- derived rates are recomputed from underlying totals (portfolio conversion =
  total paying / total users), never averaged across apps;
- benchmarks are medians across per-app values and are labelled as such by
  consumers — they are context, not the portfolio operating value.
"""

from __future__ import annotations

from statistics import median
from typing import Any

from mozaiksai.core.metrics.definitions import MetricDef, MetricRegistry


def sum_available(values: list[float | None]) -> float | None:
    """Sum per-app values; ``None`` when no app reported a value at all.

    Apps with missing data are excluded from the sum rather than treated as
    zero, so a portfolio total is only ``None`` when *nothing* reported.
    """

    present = [value for value in values if value is not None]
    if not present:
        return None
    return float(sum(present))


def derive_ratio(numerator: float | None, denominator: float | None) -> float | None:
    """A rate from two totals, as a percentage. ``None`` when undefined."""

    if numerator is None or denominator is None or denominator == 0:
        return None
    return (numerator / denominator) * 100.0


def derive_unit_ratio(numerator: float | None, denominator: float | None) -> float | None:
    """A per-unit value from two totals (for example MRR per paying user)."""

    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def derive_growth(current: float | None, previous: float | None) -> float | None:
    """Percent change versus the comparison period. ``None`` when undefined."""

    if current is None or previous is None or previous == 0:
        return None
    return ((current - previous) / abs(previous)) * 100.0


def derive_delta(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None:
        return None
    return current - previous


def derive_arr(mrr: float | None) -> float | None:
    if mrr is None:
        return None
    return mrr * 12.0


def derive_net_new_mrr(
    new_mrr: float | None,
    expansion_mrr: float | None,
    contraction_mrr: float | None,
    churned_mrr: float | None,
) -> float | None:
    components = [new_mrr, expansion_mrr, contraction_mrr, churned_mrr]
    if all(component is None for component in components):
        return None
    return (
        (new_mrr or 0.0)
        + (expansion_mrr or 0.0)
        - (contraction_mrr or 0.0)
        - (churned_mrr or 0.0)
    )


def derive_nrr(
    starting_mrr: float | None,
    expansion_mrr: float | None,
    contraction_mrr: float | None,
    churned_mrr: float | None,
) -> float | None:
    """Net revenue retention from portfolio totals, as a percentage."""

    if starting_mrr is None or starting_mrr == 0:
        return None
    retained = (
        starting_mrr
        + (expansion_mrr or 0.0)
        - (contraction_mrr or 0.0)
        - (churned_mrr or 0.0)
    )
    return (retained / starting_mrr) * 100.0


def derive_grr(
    starting_mrr: float | None,
    contraction_mrr: float | None,
    churned_mrr: float | None,
) -> float | None:
    """Gross revenue retention from portfolio totals, as a percentage."""

    if starting_mrr is None or starting_mrr == 0:
        return None
    retained = starting_mrr - (contraction_mrr or 0.0) - (churned_mrr or 0.0)
    return (retained / starting_mrr) * 100.0


def derive_retention(churn_rate: float | None) -> float | None:
    if churn_rate is None:
        return None
    return 100.0 - churn_rate


def portfolio_median(values: list[float | None]) -> float | None:
    """Median across per-app values — a benchmark, not an operating metric."""

    present = [value for value in values if value is not None]
    if not present:
        return None
    return float(median(present))


def _resolve_base_totals(
    metric_id: str,
    current_totals: dict[str, float | None],
    previous_totals: dict[str, float | None],
) -> tuple[float | None, float | None]:
    return current_totals.get(metric_id), previous_totals.get(metric_id)


def derive_metric_value(
    metric: MetricDef,
    *,
    current_totals: dict[str, float | None],
    previous_totals: dict[str, float | None],
    starting_mrr: float | None = None,
    previous_starting_mrr: float | None = None,
) -> tuple[float | None, float | None]:
    """Compute a derived metric's (current, previous) from base totals.

    ``current_totals`` / ``previous_totals`` map base metric ids to totals for
    the scope being computed — one app, or the portfolio-wide sums. Because
    derivation runs *after* summation, portfolio rates are derived from
    portfolio totals, never averaged across apps.

    ``starting_mrr`` is the MRR level at the start of the window, needed by
    the retention formulas (NRR/GRR).
    """

    derivation = metric.derivation
    if derivation is None:
        return _resolve_base_totals(metric.metric_id, current_totals, previous_totals)

    if derivation.kind == "ratio":
        numerator_now, numerator_prev = _resolve_base_totals(
            derivation.numerator or "", current_totals, previous_totals
        )
        denominator_now, denominator_prev = _resolve_base_totals(
            derivation.denominator or "", current_totals, previous_totals
        )
        if metric.unit == "currency_usd":
            return (
                derive_unit_ratio(numerator_now, denominator_now),
                derive_unit_ratio(numerator_prev, denominator_prev),
            )
        return (
            derive_ratio(numerator_now, denominator_now),
            derive_ratio(numerator_prev, denominator_prev),
        )

    if derivation.kind == "growth":
        base_now, base_prev = _resolve_base_totals(
            derivation.base or "", current_totals, previous_totals
        )
        # Growth's "previous" has no meaningful value without a third window;
        # consumers compare the growth number itself against zero.
        return derive_growth(base_now, base_prev), None

    formula = derivation.formula
    if formula == "arr":
        mrr_now, mrr_prev = _resolve_base_totals("mrr", current_totals, previous_totals)
        return derive_arr(mrr_now), derive_arr(mrr_prev)
    if formula == "net_new_mrr":
        return (
            derive_net_new_mrr(
                current_totals.get("new_mrr"),
                current_totals.get("expansion_mrr"),
                current_totals.get("contraction_mrr"),
                current_totals.get("churned_mrr"),
            ),
            derive_net_new_mrr(
                previous_totals.get("new_mrr"),
                previous_totals.get("expansion_mrr"),
                previous_totals.get("contraction_mrr"),
                previous_totals.get("churned_mrr"),
            ),
        )
    if formula == "nrr":
        return (
            derive_nrr(
                starting_mrr,
                current_totals.get("expansion_mrr"),
                current_totals.get("contraction_mrr"),
                current_totals.get("churned_mrr"),
            ),
            derive_nrr(
                previous_starting_mrr,
                previous_totals.get("expansion_mrr"),
                previous_totals.get("contraction_mrr"),
                previous_totals.get("churned_mrr"),
            ),
        )
    if formula == "grr":
        return (
            derive_grr(
                starting_mrr,
                current_totals.get("contraction_mrr"),
                current_totals.get("churned_mrr"),
            ),
            derive_grr(
                previous_starting_mrr,
                previous_totals.get("contraction_mrr"),
                previous_totals.get("churned_mrr"),
            ),
        )
    if formula == "retention":
        churn_now = derive_ratio(
            current_totals.get("churned_users"), current_totals.get("total_users")
        )
        churn_prev = derive_ratio(
            previous_totals.get("churned_users"), previous_totals.get("total_users")
        )
        return derive_retention(churn_now), derive_retention(churn_prev)

    return None, None


def metric_value_payload(
    metric: MetricDef,
    current: float | None,
    previous: float | None,
) -> dict[str, Any]:
    """The canonical value envelope every surface renders."""

    delta = derive_delta(current, previous)
    delta_pct = derive_growth(current, previous)
    return {
        "metric_id": metric.metric_id,
        "value": current,
        "previous": previous,
        "delta": delta,
        "delta_pct": delta_pct,
        "available": current is not None,
    }


def registry_payload(registry: MetricRegistry) -> dict[str, Any]:
    """Registry metadata serialized for UI consumption, keyed by metric id."""

    return {
        metric.metric_id: {
            "metric_id": metric.metric_id,
            "domain": metric.domain,
            "label": metric.label,
            "short_label": metric.display_label,
            "description": metric.description,
            "unit": metric.unit,
            "aggregation": metric.aggregation,
            "temporality": metric.temporality,
            "polarity": metric.polarity,
            "scopes": list(metric.scopes),
            "source": metric.source,
            "benchmarkable": metric.benchmarkable,
            "headline": metric.headline,
            "related": list(metric.related),
        }
        for metric in registry.metrics
    }


__all__ = [
    "derive_arr",
    "derive_delta",
    "derive_grr",
    "derive_growth",
    "derive_metric_value",
    "derive_net_new_mrr",
    "derive_nrr",
    "derive_ratio",
    "derive_retention",
    "derive_unit_ratio",
    "metric_value_payload",
    "portfolio_median",
    "registry_payload",
    "sum_available",
]
