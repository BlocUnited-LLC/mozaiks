from __future__ import annotations

from mozaiksai.core.metrics.insights import MAX_INSIGHTS, build_portfolio_insights


def _app(app_id: str, name: str, metrics: dict) -> dict:
    return {"app_id": app_id, "name": name, "metrics": metrics}


def _envelope(value, previous):
    delta = None if value is None or previous is None else value - previous
    delta_pct = (
        None
        if value is None or previous in (None, 0)
        else ((value - previous) / abs(previous)) * 100
    )
    return {"value": value, "previous": previous, "delta": delta, "delta_pct": delta_pct}


def test_mrr_decline_flags_worst_app() -> None:
    apps = [
        _app("a", "Alpha", {"mrr": _envelope(920.0, 1000.0)}),   # -8%
        _app("b", "Beta", {"mrr": _envelope(850.0, 1000.0)}),    # -15%
        _app("c", "Gamma", {"mrr": _envelope(990.0, 1000.0)}),   # -1% (below threshold)
    ]
    insights = build_portfolio_insights(apps)
    decline = [i for i in insights if i["insight_id"] == "mrr_decline"]
    assert len(decline) == 1
    assert decline[0]["app_id"] == "b"
    assert decline[0]["severity"] == "attention"
    assert "15.0%" in decline[0]["headline"]


def test_growth_leader_and_milestone_are_highlights() -> None:
    apps = [
        _app("a", "Alpha", {"mrr": _envelope(1200.0, 990.0)}),  # +21%, crosses 1k
        _app("b", "Beta", {"mrr": _envelope(500.0, 480.0)}),    # +4% below threshold
    ]
    insights = build_portfolio_insights(apps)
    ids = {i["insight_id"] for i in insights}
    # One insight per app: Alpha's milestone/growth collapse to its best one.
    assert ids & {"mrr_growth_leader", "mrr_milestone"}
    alpha_insights = [i for i in insights if i["app_id"] == "a"]
    assert len(alpha_insights) == 1
    assert alpha_insights[0]["severity"] == "highlight"


def test_conversion_drop_mentions_signup_growth_when_present() -> None:
    apps = [
        _app(
            "a",
            "Alpha",
            {
                "paid_conversion": _envelope(6.0, 15.0),
                "new_users": _envelope(131.0, 100.0),
            },
        ),
    ]
    insights = build_portfolio_insights(apps)
    drop = [i for i in insights if i["insight_id"] == "conversion_drop"]
    assert len(drop) == 1
    assert "Signups grew 31.0%" in drop[0]["detail"]
    assert "9.0 points" in drop[0]["detail"]


def test_churn_spike_and_retention_gain() -> None:
    apps = [
        _app("a", "Alpha", {"churn_rate": _envelope(9.0, 4.0)}),
        _app("b", "Beta", {"retention": _envelope(97.0, 93.0)}),
    ]
    insights = build_portfolio_insights(apps)
    ids = {i["insight_id"] for i in insights}
    assert "churn_spike" in ids
    assert "retention_gain" in ids
    spike = next(i for i in insights if i["insight_id"] == "churn_spike")
    assert spike["severity"] == "attention"


def test_active_users_swing_requires_threshold() -> None:
    apps = [
        _app("a", "Alpha", {"active_users": _envelope(130.0, 100.0)}),  # +30%
        _app("b", "Beta", {"active_users": _envelope(110.0, 100.0)}),   # +10% below
    ]
    insights = build_portfolio_insights(apps)
    swing = [i for i in insights if i["insight_id"] == "active_users_swing"]
    assert len(swing) == 1
    assert swing[0]["app_id"] == "a"


def test_ranking_puts_attention_first_and_caps_output() -> None:
    apps = [
        _app("decline", "Decline", {"mrr": _envelope(700.0, 1000.0)}),
        _app("growth", "Growth", {"mrr": _envelope(2000.0, 1500.0)}),
        _app("swing", "Swing", {"active_users": _envelope(400.0, 200.0)}),
    ]
    insights = build_portfolio_insights(apps)
    assert insights[0]["severity"] == "attention"
    assert len(insights) <= MAX_INSIGHTS
    assert all("_impact" not in insight for insight in insights)


def test_insights_are_deterministic() -> None:
    apps = [
        _app("a", "Alpha", {"mrr": _envelope(700.0, 1000.0)}),
        _app("b", "Beta", {"mrr": _envelope(2000.0, 1500.0)}),
    ]
    assert build_portfolio_insights(apps) == build_portfolio_insights(apps)


def test_no_data_produces_no_insights() -> None:
    apps = [_app("a", "Alpha", {"mrr": _envelope(None, None)})]
    assert build_portfolio_insights(apps) == []
