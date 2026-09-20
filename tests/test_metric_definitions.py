from __future__ import annotations

import pytest

from mozaiksai.core.metrics.definitions import (
    MetricDef,
    MetricDerivation,
    MetricRegistry,
    build_default_metric_registry,
)


def test_default_registry_builds_and_ids_are_unique() -> None:
    registry = build_default_metric_registry()
    ids = registry.ids()
    assert len(ids) == len(set(ids))
    assert "mrr" in ids and "active_users" in ids and "paid_conversion" in ids


def test_default_registry_covers_both_domains_with_headlines() -> None:
    registry = build_default_metric_registry()
    revenue_headlines = [m for m in registry.for_domain("revenue") if m.headline]
    users_headlines = [m for m in registry.for_domain("users") if m.headline]
    assert len(revenue_headlines) >= 4
    assert len(users_headlines) >= 4


def test_snapshot_metrics_expose_kpi_event_names() -> None:
    registry = build_default_metric_registry()
    mrr = registry.require("mrr")
    assert mrr.snapshot_event_name == "kpi.mrr"
    net_new = registry.require("net_new_mrr")
    assert net_new.snapshot_event_name is None


def test_derived_metrics_declare_their_derivations() -> None:
    registry = build_default_metric_registry()
    conversion = registry.require("paid_conversion")
    assert conversion.derivation is not None
    assert conversion.derivation.kind == "ratio"
    assert conversion.derivation.numerator == "paying_users"
    assert conversion.derivation.denominator == "total_users"
    growth = registry.require("mrr_growth")
    assert growth.derivation is not None and growth.derivation.base == "mrr"


def test_registry_rejects_duplicate_metric_ids() -> None:
    metric = MetricDef(
        metric_id="mrr",
        domain="revenue",
        label="MRR",
        description="x",
        unit="currency_usd",
        aggregation="additive",
        temporality="stock",
        source="kpi_snapshot",
    )
    with pytest.raises(ValueError, match="duplicate metric_id"):
        MetricRegistry(metrics=[metric, metric.model_copy()])


def test_registry_rejects_unknown_derivation_reference() -> None:
    broken = MetricDef(
        metric_id="broken_rate",
        domain="users",
        label="Broken",
        description="x",
        unit="percent",
        aggregation="derived",
        temporality="stock",
        source="derived",
        derivation=MetricDerivation(kind="ratio", numerator="nope", denominator="also_nope"),
    )
    with pytest.raises(ValueError, match="references unknown metric"):
        MetricRegistry(metrics=[broken])


def test_derived_metric_requires_derivation() -> None:
    with pytest.raises(ValueError, match="requires a derivation"):
        MetricDef(
            metric_id="bad",
            domain="revenue",
            label="Bad",
            description="x",
            unit="percent",
            aggregation="derived",
            temporality="stock",
            source="derived",
        )


def test_non_derived_metric_rejects_derivation() -> None:
    with pytest.raises(ValueError, match="not derived"):
        MetricDef(
            metric_id="bad",
            domain="revenue",
            label="Bad",
            description="x",
            unit="currency_usd",
            aggregation="additive",
            temporality="stock",
            source="kpi_snapshot",
            derivation=MetricDerivation(kind="growth", base="mrr"),
        )
