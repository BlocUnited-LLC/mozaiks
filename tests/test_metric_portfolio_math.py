from __future__ import annotations

from mozaiksai.core.metrics.definitions import build_default_metric_registry
from mozaiksai.core.metrics.portfolio import (
    derive_growth,
    derive_grr,
    derive_metric_value,
    derive_net_new_mrr,
    derive_nrr,
    derive_ratio,
    derive_retention,
    metric_value_payload,
    portfolio_median,
    sum_available,
)

REGISTRY = build_default_metric_registry()


def test_sum_available_excludes_missing_but_keeps_partial() -> None:
    assert sum_available([100.0, None, 50.0]) == 150.0
    assert sum_available([None, None]) is None
    assert sum_available([]) is None


def test_portfolio_rate_derives_from_totals_not_app_averages() -> None:
    # App A: 90 paying / 100 total (90%); App B: 10 paying / 900 total (1.1%).
    # A naive average of rates says ~45.6%; the correct portfolio conversion
    # is 100 / 1000 = 10%.
    total_paying = sum_available([90.0, 10.0])
    total_users = sum_available([100.0, 900.0])
    portfolio_conversion = derive_ratio(total_paying, total_users)
    assert portfolio_conversion == 10.0
    naive_average = (90.0 + (10.0 / 900.0) * 100.0) / 2
    assert abs(portfolio_conversion - naive_average) > 30


def test_ratio_and_growth_handle_undefined_inputs() -> None:
    assert derive_ratio(None, 10.0) is None
    assert derive_ratio(5.0, 0.0) is None
    assert derive_growth(None, 10.0) is None
    assert derive_growth(10.0, 0.0) is None
    assert derive_growth(110.0, 100.0) == 10.0
    assert derive_growth(90.0, 100.0) == -10.0


def test_revenue_retention_formulas() -> None:
    # start 1000, +expansion 100, -contraction 50, -churn 150 → NRR 90%, GRR 80%.
    assert derive_nrr(1000.0, 100.0, 50.0, 150.0) == 90.0
    assert derive_grr(1000.0, 50.0, 150.0) == 80.0
    assert derive_nrr(None, 100.0, 50.0, 150.0) is None
    assert derive_nrr(0.0, 100.0, 50.0, 150.0) is None


def test_net_new_mrr_composes_components() -> None:
    assert derive_net_new_mrr(500.0, 200.0, 100.0, 250.0) == 350.0
    assert derive_net_new_mrr(None, None, None, None) is None
    # Partial components still compose (missing treated as zero once any exist).
    assert derive_net_new_mrr(500.0, None, None, None) == 500.0


def test_retention_inverts_churn() -> None:
    assert derive_retention(4.0) == 96.0
    assert derive_retention(None) is None


def test_portfolio_median_is_a_benchmark_over_present_values() -> None:
    assert portfolio_median([10.0, None, 30.0, 20.0]) == 20.0
    assert portfolio_median([10.0, 30.0]) == 20.0
    assert portfolio_median([None, None]) is None


def test_derive_metric_value_ratio_currency_uses_unit_ratio() -> None:
    arppu = REGISTRY.require("arppu")
    current, previous = derive_metric_value(
        arppu,
        current_totals={"mrr": 1000.0, "paying_users": 40.0},
        previous_totals={"mrr": 800.0, "paying_users": 40.0},
    )
    assert current == 25.0
    assert previous == 20.0


def test_derive_metric_value_growth_compares_base_totals() -> None:
    growth = REGISTRY.require("mrr_growth")
    current, previous = derive_metric_value(
        growth,
        current_totals={"mrr": 1100.0},
        previous_totals={"mrr": 1000.0},
    )
    assert current is not None and abs(current - 10.0) < 1e-9
    assert previous is None


def test_derive_metric_value_nrr_uses_starting_levels() -> None:
    nrr = REGISTRY.require("nrr")
    current, previous = derive_metric_value(
        nrr,
        current_totals={"expansion_mrr": 100.0, "contraction_mrr": 50.0, "churned_mrr": 150.0},
        previous_totals={"expansion_mrr": 80.0, "contraction_mrr": 60.0, "churned_mrr": 120.0},
        starting_mrr=1000.0,
        previous_starting_mrr=800.0,
    )
    assert current == 90.0
    assert previous == 87.5


def test_metric_value_payload_marks_availability() -> None:
    mrr = REGISTRY.require("mrr")
    present = metric_value_payload(mrr, 100.0, 80.0)
    assert present["available"] is True
    assert present["delta"] == 20.0
    assert present["delta_pct"] == 25.0
    absent = metric_value_payload(mrr, None, None)
    assert absent["available"] is False
    assert absent["delta"] is None
