from __future__ import annotations

"""Tests for mozaiksai.core.runtime.app.subscriptions_loader."""

import textwrap
from pathlib import Path

import pytest

from mozaiksai.core.runtime.app.subscriptions_loader import (
    PlanDef,
    SubscriptionAssignmentStoreDef,
    SubscriptionsConfig,
    SubscriptionsLoadError,
    UsageLimitDef,
    load_subscriptions_config,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_config(tmp_path: Path, content: str) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / "subscriptions.yaml"
    path.write_text(textwrap.dedent(content), encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# load_subscriptions_config — file-absent case
# ---------------------------------------------------------------------------


def test_load_returns_none_when_file_absent(tmp_path: Path) -> None:
    result = load_subscriptions_config(tmp_path)
    assert result is None


# ---------------------------------------------------------------------------
# load_subscriptions_config — valid YAML
# ---------------------------------------------------------------------------


def test_load_valid_minimal_config(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        """
        schema_version: mozaiks.subscriptions.v1
        label: Test App
        default_plan_id: free
        plans:
          - plan_id: free
            label: Free
            capabilities: []
        """,
    )
    config = load_subscriptions_config(tmp_path)
    assert config is not None
    assert config.schema_version == "mozaiks.subscriptions.v1"
    assert config.label == "Test App"
    assert config.default_plan_id == "free"
    assert len(config.plans) == 1
    assert config.plans[0].plan_id == "free"
    assert config.plans[0].capabilities == []


def test_load_multi_plan_with_capabilities(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        """
        schema_version: mozaiks.subscriptions.v1
        label: My SaaS
        default_plan_id: free
        plans:
          - plan_id: free
            label: Free
            capabilities: []
          - plan_id: pro
            label: Pro
            capabilities:
              - wallet.view
              - analytics.dashboard
          - plan_id: enterprise
            label: Enterprise
            capabilities:
              - wallet.view
              - wallet.transfer
              - analytics.dashboard
              - analytics.export
        """,
    )
    config = load_subscriptions_config(tmp_path)
    assert config is not None
    assert len(config.plans) == 3
    pro = next(p for p in config.plans if p.plan_id == "pro")
    assert "wallet.view" in pro.capabilities
    assert "analytics.dashboard" in pro.capabilities


def test_load_plan_usage_limits(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        """
        schema_version: mozaiks.subscriptions.v1
        label: My SaaS
        default_plan_id: free
        plans:
          - plan_id: free
            label: Free
            capabilities: []
            usage_limits:
              - meter_id: ai_tokens
                label: AI tokens
                unit: tokens
                monthly_limit: 100000
                capability_id: ai.chat
        """,
    )

    config = load_subscriptions_config(tmp_path)

    assert config is not None
    limit = config.plans[0].usage_limits[0]
    assert limit.meter_id == "ai_tokens"
    assert limit.unit == "tokens"
    assert limit.monthly_limit == 100000
    assert limit.capability_id == "ai.chat"


def test_load_assignment_store(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        """
        schema_version: mozaiks.subscriptions.v1
        label: My SaaS
        default_plan_id: free
        assignment_store:
          data_alias: billing.subscriptions
          workspace_id_field: workspace_id
          active_statuses: [active, pending]
        plans:
          - plan_id: free
            label: Free
            capabilities: []
          - plan_id: pro
            label: Pro
            capabilities:
              - analytics.dashboard
        """,
    )

    config = load_subscriptions_config(tmp_path)

    assert config is not None
    assert config.assignment_store is not None
    assert config.assignment_store.data_alias == "billing.subscriptions"
    assert config.assignment_store.workspace_id_field == "workspace_id"
    assert config.assignment_store.active_statuses == ["active", "pending"]


def test_invalid_usage_limit_rejected() -> None:
    with pytest.raises(Exception):
        UsageLimitDef.model_validate(
            {
                "meter_id": "Bad Meter",
                "unit": "tokens",
                "monthly_limit": 100,
            }
        )


def test_invalid_assignment_store_alias_rejected() -> None:
    with pytest.raises(Exception):
        SubscriptionAssignmentStoreDef.model_validate(
            {"data_alias": "Billing Subscriptions"}
        )


def test_empty_assignment_store_statuses_rejected() -> None:
    with pytest.raises(Exception):
        SubscriptionAssignmentStoreDef.model_validate(
            {"data_alias": "billing.subscriptions", "active_statuses": []}
        )


# ---------------------------------------------------------------------------
# SubscriptionsConfig — validation
# ---------------------------------------------------------------------------


def test_duplicate_plan_ids_rejected() -> None:
    with pytest.raises(Exception):
        SubscriptionsConfig.model_validate(
            {
                "schema_version": "mozaiks.subscriptions.v1",
                "label": "Test",
                "default_plan_id": "free",
                "plans": [
                    {"plan_id": "free", "label": "Free", "capabilities": []},
                    {"plan_id": "free", "label": "Free Again", "capabilities": []},
                ],
            }
        )


def test_default_plan_id_must_reference_declared_plan() -> None:
    with pytest.raises(Exception):
        SubscriptionsConfig.model_validate(
            {
                "schema_version": "mozaiks.subscriptions.v1",
                "label": "Test",
                "default_plan_id": "nonexistent",
                "plans": [
                    {"plan_id": "free", "label": "Free", "capabilities": []},
                ],
            }
        )


def test_empty_plans_rejected() -> None:
    with pytest.raises(Exception):
        SubscriptionsConfig.model_validate(
            {
                "schema_version": "mozaiks.subscriptions.v1",
                "label": "Test",
                "default_plan_id": "free",
                "plans": [],
            }
        )


def test_invalid_capability_id_format_rejected() -> None:
    with pytest.raises(Exception):
        PlanDef.model_validate(
            {
                "plan_id": "pro",
                "label": "Pro",
                "capabilities": ["UPPERCASE.bad", "has spaces"],
            }
        )


def test_invalid_plan_id_format_rejected() -> None:
    with pytest.raises(Exception):
        PlanDef.model_validate(
            {"plan_id": "UPPERCASE", "label": "Bad", "capabilities": []}
        )


def test_wrong_schema_version_rejected(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        """
        schema_version: mozaiks.subscriptions.v2
        label: Test
        default_plan_id: free
        plans:
          - plan_id: free
            label: Free
            capabilities: []
        """,
    )
    with pytest.raises(SubscriptionsLoadError):
        load_subscriptions_config(tmp_path)


def test_not_a_yaml_object_raises(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "subscriptions.yaml").write_text("- just a list\n", encoding="utf-8")
    with pytest.raises(SubscriptionsLoadError):
        load_subscriptions_config(tmp_path)


# ---------------------------------------------------------------------------
# SubscriptionsConfig.capabilities_for_plan
# ---------------------------------------------------------------------------


def test_capabilities_for_plan_known_plan() -> None:
    config = SubscriptionsConfig.model_validate(
        {
            "schema_version": "mozaiks.subscriptions.v1",
            "label": "Test",
            "default_plan_id": "free",
            "plans": [
                {"plan_id": "free", "label": "Free", "capabilities": []},
                {
                    "plan_id": "pro",
                    "label": "Pro",
                    "capabilities": ["wallet.view", "analytics.dashboard"],
                },
            ],
        }
    )
    caps = config.capabilities_for_plan("pro")
    assert "wallet.view" in caps
    assert "analytics.dashboard" in caps
    assert len(caps) == 2


def test_capabilities_for_plan_falls_back_to_default() -> None:
    config = SubscriptionsConfig.model_validate(
        {
            "schema_version": "mozaiks.subscriptions.v1",
            "label": "Test",
            "default_plan_id": "free",
            "plans": [
                {"plan_id": "free", "label": "Free", "capabilities": []},
                {
                    "plan_id": "pro",
                    "label": "Pro",
                    "capabilities": ["wallet.view"],
                },
            ],
        }
    )
    # Unknown plan — falls back to default (free), which has no capabilities
    caps = config.capabilities_for_plan("unknown_plan")
    assert caps == frozenset()


def test_capabilities_for_plan_free_has_no_capabilities() -> None:
    config = SubscriptionsConfig.model_validate(
        {
            "schema_version": "mozaiks.subscriptions.v1",
            "label": "Test",
            "default_plan_id": "free",
            "plans": [
                {"plan_id": "free", "label": "Free", "capabilities": []},
            ],
        }
    )
    assert config.capabilities_for_plan("free") == frozenset()


# ---------------------------------------------------------------------------
# AppLoadResult carries subscriptions_config
# ---------------------------------------------------------------------------


def test_app_load_result_has_subscriptions_config_field() -> None:
    """AppLoadResult must expose subscriptions_config for platform.py wiring."""
    from mozaiksai.core.runtime.app.loader import AppLoadResult

    result = AppLoadResult.__dataclass_fields__
    assert "subscriptions_config" in result

