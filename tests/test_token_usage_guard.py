from __future__ import annotations

import textwrap
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from mozaiksai.core.tokens.guard import TokenUsageGuard


def _write_subscriptions(tmp_path: Path, *, assignment_store: bool = False) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    assignment_block = (
        """
            assignment_store:
              data_alias: subscriptions.assignments
              user_id_field: user_id
              active_statuses: [active]
        """
        if assignment_store
        else ""
    )
    config_dir.joinpath("subscriptions.yaml").write_text(
        textwrap.dedent(
            f"""
            schema_version: mozaiks.subscriptions.v1
            label: Token SaaS
            default_plan_id: pro
            {assignment_block}
            token_wallets:
              - wallet_id: ai_tokens
                label: AI tokens
                unit: tokens
                usage_meter_id: ai_tokens
                scope: user
                auto_debit_usage: true
                allow_negative_balance: false
            plans:
              - plan_id: pro
                label: Pro
                capabilities: [ai.chat]
            """
        ),
        encoding="utf-8",
    )


class _Ledger:
    def __init__(self, *, balance: int) -> None:
        self.balance = balance
        self.ensure_calls: list[dict[str, Any]] = []
        self.query_calls: list[dict[str, Any]] = []

    async def ensure_plan_allowances(self, **kwargs):
        self.ensure_calls.append(kwargs)
        return [SimpleNamespace(status="applied")]

    async def query_balance(self, **kwargs):
        self.query_calls.append(kwargs)
        return {"balance": self.balance}


class _AssignmentCollection:
    async def find_one(self, query: dict[str, Any], projection: dict[str, int] | None = None):
        if query.get("app_id") == "app_1" and query.get("user_id") == "user_1":
            return {
                "app_id": "app_1",
                "user_id": "user_1",
                "plan_id": "operator_plus",
                "status": "active",
            }
        return None


@pytest.mark.asyncio
async def test_token_usage_guard_allows_when_wallet_has_required_balance(tmp_path: Path) -> None:
    _write_subscriptions(tmp_path)
    ledger = _Ledger(balance=50)
    guard = TokenUsageGuard(app_root=tmp_path, ledger=ledger)

    decision = await guard.check(
        app_id="app_1",
        user_id="user_1",
        required_tokens=25,
    )

    assert decision.allowed is True
    assert decision.reason == "sufficient_balance"
    assert ledger.ensure_calls[0]["plan_id"] == "pro"
    assert ledger.query_calls[0]["wallet_id"] == "ai_tokens"


@pytest.mark.asyncio
async def test_token_usage_guard_denies_before_llm_when_wallet_is_depleted(tmp_path: Path) -> None:
    _write_subscriptions(tmp_path)
    guard = TokenUsageGuard(app_root=tmp_path, ledger=_Ledger(balance=0))

    decision = await guard.check(
        app_id="app_1",
        user_id="user_1",
        required_tokens=1,
    )

    assert decision.allowed is False
    assert decision.error_code == "INSUFFICIENT_TOKENS"
    assert decision.wallet_id == "ai_tokens"
    assert decision.balance == 0


@pytest.mark.asyncio
async def test_token_usage_guard_denies_missing_user_scope(tmp_path: Path) -> None:
    _write_subscriptions(tmp_path)
    guard = TokenUsageGuard(app_root=tmp_path, ledger=_Ledger(balance=100))

    decision = await guard.check(app_id="app_1", required_tokens=1)

    assert decision.allowed is False
    assert decision.error_code == "TOKEN_USAGE_SCOPE_MISSING"
    assert decision.reason == "missing_user_id"


@pytest.mark.asyncio
async def test_token_usage_guard_preserves_dynamic_assignment_without_default_allowance(tmp_path: Path) -> None:
    _write_subscriptions(tmp_path, assignment_store=True)
    ledger = _Ledger(balance=50)
    guard = TokenUsageGuard(
        app_root=tmp_path,
        ledger=ledger,
        collection_resolver=lambda alias: _AssignmentCollection(),
    )

    decision = await guard.check(
        app_id="app_1",
        user_id="user_1",
        required_tokens=25,
    )

    assert decision.allowed is True
    assert ledger.ensure_calls == []
