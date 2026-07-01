from __future__ import annotations

"""Provider-neutral token usage preflight checks.

This module checks whether an app/user/tenant scope has enough runtime token
wallet balance to start an LLM call. It does not know about payment providers,
pricing, checkout sessions, invoices, or hosted-product business rules.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from logs.logging_config import get_core_logger
from mozaiksai.core.runtime.app.entitlements import (
    CollectionResolver,
    ConfiguredEntitlementAdapter,
)
from mozaiksai.core.runtime.app.subscriptions_loader import (
    SubscriptionsConfig,
    TokenWalletDef,
    load_subscriptions_config,
)
from mozaiksai.core.tokens.wallet import TokenWalletLedger, get_token_wallet_ledger
from mozaiksai.core.workflow.paths import resolve_active_app_root

logger = get_core_logger("token_usage_guard")


@dataclass(frozen=True)
class TokenUsageDecision:
    allowed: bool
    reason: str
    error_code: str | None = None
    wallet_id: str | None = None
    balance: int | None = None
    required_tokens: int | None = None


class TokenUsageDenied(RuntimeError):
    def __init__(self, decision: TokenUsageDecision) -> None:
        self.decision = decision
        message = (
            "Insufficient token balance to run this AI action."
            if decision.error_code == "INSUFFICIENT_TOKENS"
            else f"Token usage denied: {decision.reason}"
        )
        super().__init__(message)


def _text(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text.lower() == "none" else text


def _positive_int(value: Any, *, default: int = 1) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, parsed)


class TokenUsageGuard:
    def __init__(
        self,
        *,
        config: SubscriptionsConfig | None = None,
        app_root: str | Path | None = None,
        ledger: TokenWalletLedger | None = None,
        collection_resolver: CollectionResolver | None = None,
    ) -> None:
        self._config = config
        self._app_root = Path(app_root) if app_root is not None else None
        self._ledger = ledger
        self._collection_resolver = collection_resolver

    def _load_config(self) -> SubscriptionsConfig | None:
        if self._config is not None:
            return self._config
        try:
            app_root = self._app_root or resolve_active_app_root()
            return load_subscriptions_config(Path(app_root))
        except Exception as exc:
            logger.debug("token usage guard config load skipped: %s", exc)
            return None

    async def check(
        self,
        *,
        app_id: str | None,
        user_id: str | None = None,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
        required_tokens: int = 1,
    ) -> TokenUsageDecision:
        config = self._load_config()
        if config is None or not config.token_wallets:
            return TokenUsageDecision(allowed=True, reason="not_configured")

        wallets = [wallet for wallet in config.token_wallets if wallet.auto_debit_usage]
        if not wallets:
            return TokenUsageDecision(allowed=True, reason="auto_debit_disabled")

        app_id_text = _text(app_id)
        if not app_id_text:
            return TokenUsageDecision(
                allowed=False,
                reason="missing_app_id",
                error_code="TOKEN_USAGE_SCOPE_MISSING",
            )

        user_id_text = _text(user_id) or None
        tenant_id_text = _text(tenant_id) or None
        workspace_id_text = _text(workspace_id) or None
        required = _positive_int(required_tokens)
        ledger = self._ledger or get_token_wallet_ledger()

        plan_id = await ConfiguredEntitlementAdapter(
            config=config,
            collection_resolver=self._collection_resolver,
        ).current_plan_id(
            app_id=app_id_text,
            user_id=user_id_text,
            tenant_id=tenant_id_text,
            workspace_id=workspace_id_text,
        )

        plan_declared = any(plan.plan_id == plan_id for plan in config.plans)
        if plan_id and plan_declared:
            try:
                await ledger.ensure_plan_allowances(
                    config=config,
                    app_id=app_id_text,
                    plan_id=plan_id,
                    user_id=user_id_text,
                    tenant_id=tenant_id_text,
                )
            except Exception as exc:
                logger.debug("token allowance preflight sync skipped: %s", exc)

        for wallet in wallets:
            scope_decision = self._scope_decision(
                wallet,
                user_id=user_id_text,
                tenant_id=tenant_id_text,
            )
            if scope_decision is not None:
                return scope_decision
            if wallet.allow_negative_balance:
                continue

            balance = await ledger.query_balance(
                app_id=app_id_text,
                wallet_id=wallet.wallet_id,
                user_id=user_id_text,
                tenant_id=tenant_id_text,
                preferred_scope=wallet.scope,
            )
            current_balance = int(balance.get("balance") or 0)
            if current_balance < required:
                return TokenUsageDecision(
                    allowed=False,
                    reason="insufficient_balance",
                    error_code="INSUFFICIENT_TOKENS",
                    wallet_id=wallet.wallet_id,
                    balance=current_balance,
                    required_tokens=required,
                )

        return TokenUsageDecision(allowed=True, reason="sufficient_balance")

    async def check_or_raise(
        self,
        *,
        app_id: str | None,
        user_id: str | None = None,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
        required_tokens: int = 1,
    ) -> TokenUsageDecision:
        decision = await self.check(
            app_id=app_id,
            user_id=user_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            required_tokens=required_tokens,
        )
        if not decision.allowed:
            raise TokenUsageDenied(decision)
        return decision

    @staticmethod
    def _scope_decision(
        wallet: TokenWalletDef,
        *,
        user_id: str | None,
        tenant_id: str | None,
    ) -> TokenUsageDecision | None:
        if wallet.scope == "user" and not user_id:
            return TokenUsageDecision(
                allowed=False,
                reason="missing_user_id",
                error_code="TOKEN_USAGE_SCOPE_MISSING",
                wallet_id=wallet.wallet_id,
            )
        if wallet.scope == "tenant" and not tenant_id:
            return TokenUsageDecision(
                allowed=False,
                reason="missing_tenant_id",
                error_code="TOKEN_USAGE_SCOPE_MISSING",
                wallet_id=wallet.wallet_id,
            )
        return None


__all__ = ["TokenUsageDecision", "TokenUsageDenied", "TokenUsageGuard"]
