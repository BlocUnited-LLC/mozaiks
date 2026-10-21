from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from mozaiksai.core.runtime.app.entitlements import ProductPlanSelection
from mozaiksai.core.tokens.usage_ingest import TokenWalletUsageIngestClient


def _write_subscriptions(tmp_path: Path, *, auto_debit_usage: bool = True) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_dir.joinpath("subscriptions.yaml").write_text(
        textwrap.dedent(
            f"""
            schema_version: mozaiks.subscriptions.v1
            label: Token SaaS
            default_plan_id: pro
            token_wallets:
              - wallet_id: ai_tokens
                label: AI tokens
                unit: tokens
                usage_meter_id: ai_tokens
                scope: user
                auto_debit_usage: {str(auto_debit_usage).lower()}
            plans:
              - plan_id: pro
                label: Pro
                capabilities: [ai.chat]
                token_allowances:
                  - wallet_id: ai_tokens
                    amount: 1000
                    cadence: monthly
            """
        ),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_usage_ingest_materializes_allowance_and_debits_wallet(tmp_path: Path) -> None:
    _write_subscriptions(tmp_path, auto_debit_usage=True)
    calls: list[tuple[str, dict]] = []

    class _Ledger:
        async def ensure_plan_allowances(self, **kwargs):
            calls.append(("ensure", kwargs))
            return []

        async def record_usage_debit(self, payload, *, wallet):
            calls.append(("debit", {"payload": payload, "wallet_id": wallet.wallet_id}))
            return None

    client = TokenWalletUsageIngestClient(ledger=_Ledger(), app_root=tmp_path)

    await client.handle_usage_delta(
        {
            "event_id": "usage_evt_1",
            "app_id": "app_1",
            "user_id": "user_1",
            "chat_id": "chat_1",
            "workflow_name": "Chat",
            "total_tokens": 25,
        }
    )

    assert calls[0][0] == "ensure"
    assert calls[0][1]["app_id"] == "app_1"
    assert calls[0][1]["user_id"] == "user_1"
    assert calls[0][1]["plan_id"] == "pro"
    assert calls[1] == (
        "debit",
        {
            "payload": {
                "event_id": "usage_evt_1",
                "app_id": "app_1",
                "user_id": "user_1",
                "chat_id": "chat_1",
                "workflow_name": "Chat",
                "total_tokens": 25,
            },
            "wallet_id": "ai_tokens",
        },
    )


@pytest.mark.asyncio
async def test_v2_usage_ingest_uses_wallet_product_not_primary(
    tmp_path: Path, monkeypatch,
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_dir.joinpath("subscriptions.yaml").write_text(
        textwrap.dedent("""
            schema_version: mozaiks.subscriptions.v2
            label: Multi-product SaaS
            default_product_id: platform
            token_wallets:
              - wallet_id: ai_tokens
                scope: user
                auto_debit_usage: true
            products:
              - product_id: platform
                label: Platform
                default_plan_id: builder
                plans:
                  - plan_id: builder
                    label: Builder
              - product_id: ai
                label: AI
                default_plan_id: ai_starter
                plans:
                  - plan_id: ai_starter
                    label: Starter
                  - plan_id: ai_pro
                    label: Pro
                    token_allowances:
                      - wallet_id: ai_tokens
                        amount: 500
                        cadence: monthly
        """),
        encoding="utf-8",
    )
    calls: list[tuple[str, dict]] = []

    class _Entitlements:
        def __init__(self, *, config):
            assert config.products

        async def current_product_plan(self, *, product_id=None, **kwargs):
            assert product_id == "ai"
            return ProductPlanSelection("ai", "ai_pro", "active_assignment")

    class _Ledger:
        async def ensure_resolved_wallet_allowances(self, **kwargs):
            calls.append(("ensure", kwargs))
            return []

        async def record_usage_debit(self, payload, *, wallet):
            calls.append(("debit", {"wallet_id": wallet.wallet_id}))

    monkeypatch.setattr(
        "mozaiksai.core.tokens.usage_ingest.ConfiguredEntitlementAdapter", _Entitlements
    )
    client = TokenWalletUsageIngestClient(ledger=_Ledger(), app_root=tmp_path)
    await client.handle_usage_delta({
        "event_id": "usage_evt_1", "app_id": "app_1", "user_id": "user_1",
        "total_tokens": 25,
    })

    assert calls == [("debit", {"wallet_id": "ai_tokens"})]


@pytest.mark.asyncio
async def test_v2_chat_usage_debits_only_its_meter_wallet(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_dir.joinpath("subscriptions.yaml").write_text(
        textwrap.dedent("""
            schema_version: mozaiks.subscriptions.v2
            label: Metered products
            default_product_id: ai
            products:
              - product_id: ai
                label: AI
                default_plan_id: free
                token_wallets:
                  - wallet_id: ai_tokens
                    usage_meter_id: ai_tokens
                    auto_debit_usage: true
                plans:
                  - plan_id: free
                    label: Free
              - product_id: tools
                label: Tools
                default_plan_id: free
                token_wallets:
                  - wallet_id: tool_credits
                    usage_meter_id: tool_calls
                    auto_debit_usage: true
                plans:
                  - plan_id: free
                    label: Free
        """),
        encoding="utf-8",
    )
    debited: list[str] = []

    class _Ledger:
        async def record_usage_debit(self, payload, *, wallet):
            debited.append(wallet.wallet_id)

    client = TokenWalletUsageIngestClient(ledger=_Ledger(), app_root=tmp_path)
    await client.handle_usage_delta({
        "event_id": "usage_evt_1", "app_id": "app_1", "user_id": "user_1",
        "total_tokens": 25,
    })
    assert debited == ["ai_tokens"]


@pytest.mark.asyncio
async def test_usage_ingest_is_noop_when_auto_debit_disabled(tmp_path: Path) -> None:
    _write_subscriptions(tmp_path, auto_debit_usage=False)
    calls: list[str] = []

    class _Ledger:
        async def ensure_plan_allowances(self, **kwargs):
            calls.append("ensure")
            return []

        async def record_usage_debit(self, payload, *, wallet):
            calls.append("debit")
            return None

    client = TokenWalletUsageIngestClient(ledger=_Ledger(), app_root=tmp_path)

    await client.handle_usage_delta(
        {
            "event_id": "usage_evt_1",
            "app_id": "app_1",
            "user_id": "user_1",
            "total_tokens": 25,
        }
    )

    assert calls == []
