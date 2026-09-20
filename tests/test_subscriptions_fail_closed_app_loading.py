"""Fail-closed app loading for present-but-invalid subscription contracts.

The security contract:

- ABSENT ``config/subscriptions.yaml`` → valid non-SaaS app, config None,
  NoOpEntitlementAdapter permitted.
- PRESENT AND VALID (v1 or v2) → app loads, ConfiguredEntitlementAdapter.
- PRESENT BUT INVALID (malformed YAML, wrong schema, unknown field, broken
  reference, invalid default, invalid product/plan or wallet/top-up
  relationship) → ``AppLoadError``; the app never boots with entitlement
  enforcement silently disabled.

Previously an invalid present contract was downgraded to ``None`` with a
warning, which wired NoOpEntitlementAdapter and granted every
``entitlement_gate`` unconditionally.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from mozaiksai.core.ports.entitlement import NoOpEntitlementAdapter
from mozaiksai.core.runtime.app.entitlements import ConfiguredEntitlementAdapter
from mozaiksai.core.runtime.app.loader import AppLoader, AppLoadError
from mozaiksai.core.runtime.app.subscriptions_loader import (
    SubscriptionsLoadError,
    load_subscriptions_config,
)

_VALID_V1 = """schema_version: mozaiks.subscriptions.v1
label: Test Plans
default_plan_id: free
plans:
  - plan_id: free
    label: Free
    capabilities: []
  - plan_id: pro
    label: Pro
    capabilities:
      - reports.export
"""

# Generic v2 structure exercising exactly the shape the hosted product (App
# Zero) depends on — multi-product, default_product_id, per-product
# assignment stores/plans, root wallets/top-ups/pricing catalog — with no
# proprietary plan data.
_VALID_V2 = """schema_version: mozaiks.subscriptions.v2
label: Test Multi-Product Plans
default_product_id: platform
products:
  - product_id: platform
    label: Platform
    default_plan_id: starter
    assignment_store:
      data_alias: billing.platform_subscriptions
    plans:
      - plan_id: starter
        label: Starter
        capabilities: []
      - plan_id: builder
        label: Builder
        capabilities:
          - reports.export
    pricing_catalog_group:
      group_id: platform
      label: Platform
      plan_ids: [starter, builder]
  - product_id: ai
    label: AI
    default_plan_id: ai_starter
    assignment_store:
      data_alias: billing.ai_subscriptions
    plans:
      - plan_id: ai_starter
        label: AI Starter
        capabilities: []
token_wallets:
  - wallet_id: ai_tokens
    unit: tokens
    usage_meter_id: ai_tokens
    scope: user
    auto_debit_usage: true
top_up_products:
  - product_id: tokens_10k
    label: 10K tokens
    wallet_id: ai_tokens
    token_amount: 10000
    price:
      amount_cents: 500
      currency: usd
"""

_INVALID_CONFIGS = {
    "malformed_yaml": "schema_version: [unclosed\nplans:\n  - {",
    "wrong_schema_version": (
        "schema_version: mozaiks.subscriptions.v99\nlabel: X\n"
        "default_plan_id: free\nplans:\n  - plan_id: free\n    label: Free\n"
    ),
    "unknown_root_field": (
        "schema_version: mozaiks.subscriptions.v1\nlabel: X\n"
        "default_plan_id: free\nsurprise_field: true\n"
        "plans:\n  - plan_id: free\n    label: Free\n"
    ),
    "broken_default_reference": (
        "schema_version: mozaiks.subscriptions.v1\nlabel: X\n"
        "default_plan_id: nonexistent\nplans:\n  - plan_id: free\n    label: Free\n"
    ),
    "missing_default": (
        "schema_version: mozaiks.subscriptions.v1\nlabel: X\n"
        "plans:\n  - plan_id: free\n    label: Free\n"
    ),
    "duplicate_plan_ids": (
        "schema_version: mozaiks.subscriptions.v1\nlabel: X\n"
        "default_plan_id: free\nplans:\n"
        "  - plan_id: free\n    label: Free\n"
        "  - plan_id: free\n    label: Also Free\n"
    ),
    "invalid_product_relationship": (
        "schema_version: mozaiks.subscriptions.v2\nlabel: X\n"
        "products:\n  - product_id: platform\n    label: Platform\n"
        "    default_plan_id: nonexistent\n"
        "    plans:\n      - plan_id: starter\n        label: Starter\n"
    ),
    "v1_with_products": (
        "schema_version: mozaiks.subscriptions.v1\nlabel: X\n"
        "default_plan_id: free\nplans:\n  - plan_id: free\n    label: Free\n"
        "products:\n  - product_id: p\n    label: P\n    default_plan_id: a\n"
        "    plans:\n      - plan_id: a\n        label: A\n"
    ),
    "invalid_wallet_topup_relationship": (
        "schema_version: mozaiks.subscriptions.v1\nlabel: X\n"
        "default_plan_id: free\nplans:\n  - plan_id: free\n    label: Free\n"
        "token_wallets:\n  - wallet_id: ai_tokens\n"
        "top_up_products:\n"
        "  - product_id: pack\n    label: Pack\n    wallet_id: unknown_wallet\n"
        "    token_amount: 100\n    price:\n      amount_cents: 100\n"
    ),
}


def _write_app(root: Path, subscriptions_yaml: str | None) -> Path:
    (root / "app.json").write_text('{"appName": "Fail Closed App"}', encoding="utf-8")
    if subscriptions_yaml is not None:
        config_dir = root / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "subscriptions.yaml").write_text(subscriptions_yaml, encoding="utf-8")
    return root


def _write_gated_module(root: Path) -> None:
    module_dir = root / "modules" / "reports"
    backend = module_dir / "backend"
    backend.mkdir(parents=True, exist_ok=True)
    (module_dir / "module.yaml").write_text(
        """schema_version: mozaiks.module.v1
module:
  id: reports
  display_name: Reports
  version: 1.0.0
  description: Gated reports
  handler: backend.handler:ReportsHandler
actions:
  - id: export_report
    description: Export a report.
    handler_method: export_report
    entitlement_gate: reports.export
    input_schema: {type: object, properties: {}}
    output_schema: {type: object}
""",
        encoding="utf-8",
    )
    (backend / "__init__.py").write_text("", encoding="utf-8")
    (backend / "handler.py").write_text(
        "class ReportsHandler:\n"
        "    async def export_report(self, ctx, **params):\n"
        "        return {\"exported\": True}\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Absent / valid v1 / valid v2
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_absent_config_is_a_valid_non_saas_app(tmp_path: Path) -> None:
    _write_app(tmp_path, None)
    result = await AppLoader.load(str(tmp_path))
    assert result.subscriptions_config is None
    # NoOp is permitted only for genuinely non-SaaS apps.
    adapter = NoOpEntitlementAdapter()
    granted = await adapter.check("anything", app_id="app-x")
    assert granted.granted is True


@pytest.mark.asyncio
async def test_valid_v1_loads_and_configures_enforcement(tmp_path: Path) -> None:
    _write_app(tmp_path, _VALID_V1)
    result = await AppLoader.load(str(tmp_path))
    assert result.subscriptions_config is not None
    assert result.subscriptions_config.schema_version == "mozaiks.subscriptions.v1"
    adapter = ConfiguredEntitlementAdapter(config=result.subscriptions_config)
    denied = await adapter.check("reports.export", app_id="app-x")
    assert denied.granted is False  # default plan grants nothing


@pytest.mark.asyncio
async def test_valid_v2_loads_and_configures_enforcement(tmp_path: Path) -> None:
    _write_app(tmp_path, _VALID_V2)
    result = await AppLoader.load(str(tmp_path))
    assert result.subscriptions_config is not None
    assert result.subscriptions_config.schema_version == "mozaiks.subscriptions.v2"
    assert [p.product_id for p in result.subscriptions_config.products] == ["platform", "ai"]
    adapter = ConfiguredEntitlementAdapter(
        config=result.subscriptions_config,
        collection_resolver=lambda _alias: None,  # no store hits in this test
    )
    assert isinstance(adapter, ConfiguredEntitlementAdapter)


# ---------------------------------------------------------------------------
# Present but invalid → AppLoadError, never NoOp
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("case", sorted(_INVALID_CONFIGS))
async def test_invalid_present_config_fails_app_loading(tmp_path: Path, case: str) -> None:
    _write_app(tmp_path, _INVALID_CONFIGS[case])
    with pytest.raises(AppLoadError, match="Invalid config/subscriptions.yaml"):
        await AppLoader.load(str(tmp_path))


@pytest.mark.parametrize("case", sorted(_INVALID_CONFIGS))
def test_loader_remains_sole_schema_authority(tmp_path: Path, case: str) -> None:
    """AppLoader adds no second parser: the same inputs fail the one loader."""
    _write_app(tmp_path, _INVALID_CONFIGS[case])
    with pytest.raises(SubscriptionsLoadError):
        load_subscriptions_config(tmp_path)


# ---------------------------------------------------------------------------
# Security regression: gated action + malformed config
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gated_action_never_exposed_under_disabled_enforcement(tmp_path: Path) -> None:
    """The core security proof. An app declares an entitlement-gated action
    and ships a present-but-malformed subscriptions.yaml.

    Before this correction: the config became None, NoOpEntitlementAdapter
    was wired, and the gated action was granted to everyone. After: the
    application fails loading — the gated action is never exposed under
    disabled enforcement.
    """
    _write_app(tmp_path, _INVALID_CONFIGS["malformed_yaml"])
    _write_gated_module(tmp_path)

    with pytest.raises(AppLoadError, match="Invalid config/subscriptions.yaml"):
        await AppLoader.load(str(tmp_path))

    # Control: the same app with a VALID config loads, and enforcement is
    # configured (the gate denies without an active grant).
    (tmp_path / "config" / "subscriptions.yaml").write_text(_VALID_V1, encoding="utf-8")
    result = await AppLoader.load(str(tmp_path))
    assert result.subscriptions_config is not None
    adapter = ConfiguredEntitlementAdapter(config=result.subscriptions_config)
    outcome = await adapter.check("reports.export", app_id="app-x")
    assert outcome.granted is False
