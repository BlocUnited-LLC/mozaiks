from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

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
