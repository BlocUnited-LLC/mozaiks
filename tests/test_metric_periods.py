from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from mozaiksai.core.metrics.periods import PERIOD_IDS, PeriodError, resolve_period

NOW = datetime(2026, 9, 12, 15, 30, tzinfo=UTC)


def test_rolling_window_shapes_are_contiguous() -> None:
    window = resolve_period("30d", now=NOW)
    assert window.until == NOW
    assert window.since == NOW - timedelta(days=30)
    assert window.previous_until == window.since
    assert window.previous_since == window.since - timedelta(days=30)
    assert window.label == "Last 30 days"


def test_month_to_date_compares_same_elapsed_slice_of_previous_month() -> None:
    window = resolve_period("mtd", now=NOW)
    assert window.since == datetime(2026, 9, 1, tzinfo=UTC)
    assert window.previous_since == datetime(2026, 8, 1, tzinfo=UTC)
    assert window.previous_until == datetime(2026, 8, 12, 15, 30, tzinfo=UTC)


def test_month_to_date_clamps_day_overflow() -> None:
    end_of_march = datetime(2026, 3, 31, 12, 0, tzinfo=UTC)
    window = resolve_period("mtd", now=end_of_march)
    # February 2026 has 28 days: the comparison point clamps to Feb 28.
    assert window.previous_until == datetime(2026, 2, 28, 12, 0, tzinfo=UTC)


def test_quarter_and_year_to_date_windows() -> None:
    qtd = resolve_period("qtd", now=NOW)
    assert qtd.since == datetime(2026, 7, 1, tzinfo=UTC)
    assert qtd.previous_since == datetime(2026, 4, 1, tzinfo=UTC)
    ytd = resolve_period("ytd", now=NOW)
    assert ytd.since == datetime(2026, 1, 1, tzinfo=UTC)
    assert ytd.previous_since == datetime(2025, 1, 1, tzinfo=UTC)
    assert ytd.previous_until == datetime(2025, 9, 12, 15, 30, tzinfo=UTC)


def test_every_declared_period_resolves() -> None:
    for period_id in PERIOD_IDS:
        window = resolve_period(period_id, now=NOW)
        assert window.since < window.until
        assert window.previous_since < window.previous_until


def test_unknown_period_raises() -> None:
    with pytest.raises(PeriodError, match="unknown period"):
        resolve_period("14d", now=NOW)


def test_payload_serializes_iso_strings() -> None:
    payload = resolve_period("7d", now=NOW).to_payload()
    assert payload["id"] == "7d"
    assert payload["since"].startswith("2026-09-05")
    assert payload["comparison_label"] == "vs previous 7 days"
