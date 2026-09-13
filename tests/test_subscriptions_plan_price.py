from __future__ import annotations

import pytest
from pydantic import ValidationError

from mozaiksai.core.runtime.app.subscriptions_loader import PlanDef, PlanPriceDef


def test_plan_without_price_remains_valid() -> None:
    plan = PlanDef(plan_id="free", label="Free")
    assert plan.price is None


def test_plan_price_parses_and_defaults_to_monthly() -> None:
    plan = PlanDef.model_validate(
        {
            "plan_id": "pro",
            "label": "Pro",
            "price": {"amount_cents": 2900, "currency": "usd"},
        }
    )
    assert plan.price is not None
    assert plan.price.interval == "month"
    assert plan.price.monthly_amount_cents() == 2900.0


def test_plan_price_normalizes_intervals_to_monthly() -> None:
    yearly = PlanPriceDef(amount_cents=12000, interval="year")
    assert yearly.monthly_amount_cents() == 1000.0
    weekly = PlanPriceDef(amount_cents=700, interval="week")
    assert abs(weekly.monthly_amount_cents() - 700 * ((365.0 / 12.0) / 7.0)) < 1e-9
    daily = PlanPriceDef(amount_cents=100, interval="day")
    assert abs(daily.monthly_amount_cents() - 100 * (365.0 / 12.0)) < 1e-9


def test_plan_price_rejects_invalid_currency_and_amount() -> None:
    with pytest.raises(ValidationError):
        PlanPriceDef(amount_cents=1000, currency="dollars")
    with pytest.raises(ValidationError):
        PlanPriceDef(amount_cents=0)


def test_plan_price_rejects_one_time_interval() -> None:
    # Recurring revenue requires a recurring interval; one_time is not valid
    # for a subscription plan price.
    with pytest.raises(ValidationError):
        PlanPriceDef(amount_cents=1000, interval="one_time")
