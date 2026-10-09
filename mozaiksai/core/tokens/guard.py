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
    PlanDef,
    SubscriptionsConfig,
    TokenWalletDef,
    load_subscriptions_config,
)
from mozaiksai.core.tokens.plan_resolution import resolve_v2_wallet_plans
from mozaiksai.core.tokens.wallet import TokenWalletLedger, get_token_wallet_ledger
from mozaiksai.core.workflow.paths import resolve_active_app_root

logger = get_core_logger("token_usage_guard")


@dataclass(frozen=True)
class TokenUsageDecision:
    allowed: bool
    reason: str
    error_code: str | None = None
    app_id: str | None = None
    wallet_id: str | None = None
    balance: int | None = None
    required_tokens: int | None = None
    scope: str | None = None
    user_id: str | None = None
    tenant_id: str | None = None
    workspace_id: str | None = None
    recovery_action: str | None = None
    billing_route: str | None = None
    top_up_route: str | None = None
    upgrade_route: str | None = None
    contact_route: str | None = None
    recovery_message: str | None = None
    top_up_product_ids: tuple[str, ...] = ()

    def to_error_metadata(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "error_code": self.error_code,
                "app_id": self.app_id,
                "wallet_id": self.wallet_id,
                "balance": self.balance,
                "required_tokens": self.required_tokens,
                "scope": self.scope,
                "user_id": self.user_id,
                "tenant_id": self.tenant_id,
                "workspace_id": self.workspace_id,
                "recovery_action": self.recovery_action,
                "billing_route": self.billing_route,
                "top_up_route": self.top_up_route,
                "upgrade_route": self.upgrade_route,
                "contact_route": self.contact_route,
                "recovery_message": self.recovery_message,
                "top_up_product_ids": list(self.top_up_product_ids),
            }.items()
            if value not in (None, "", [])
        }


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
        if config is None or not config.effective_token_wallets:
            return TokenUsageDecision(allowed=True, reason="not_configured")

        wallets = [wallet for wallet in config.effective_token_wallets if wallet.auto_debit_usage]
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

        entitlements = ConfiguredEntitlementAdapter(
            config=config,
            collection_resolver=self._collection_resolver,
        )
        plan_id = await entitlements.current_plan_id(
            app_id=app_id_text,
            user_id=user_id_text,
            tenant_id=tenant_id_text,
            workspace_id=workspace_id_text,
        )

        if config.products:
            try:
                wallet_plans = await resolve_v2_wallet_plans(
                    config=config,
                    entitlements=entitlements,
                    app_id=app_id_text,
                    user_id=user_id_text,
                    tenant_id=tenant_id_text,
                    workspace_id=workspace_id_text,
                )
                payable_wallet_plans = {
                    wallet.wallet_id: wallet_plans[wallet.wallet_id]
                    for wallet in wallets
                    if wallet.wallet_id in wallet_plans
                    and wallet_plans[wallet.wallet_id].status == "resolved"
                    and wallet_plans[wallet.wallet_id].grant_authority == "runtime_default"
                    and wallet_plans[wallet.wallet_id].allowances
                }
                if payable_wallet_plans:
                    await ledger.ensure_resolved_wallet_allowances(
                        config=config,
                        app_id=app_id_text,
                        wallet_plans=payable_wallet_plans,
                        user_id=user_id_text,
                        tenant_id=tenant_id_text,
                    )
            except Exception as exc:
                logger.debug("token allowance preflight sync skipped: %s", exc)
        elif plan_id and any(plan.plan_id == plan_id for plan in config.plans):
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
                recovery = self._recovery_metadata(config, wallet)
                product_top_up_available = any(
                    top_up.wallet_id == wallet.wallet_id and top_up.active
                    for product in config.products
                    for top_up in product.top_up_products
                )
                if (
                    recovery["recovery_action"] == "upgrade"
                    and not recovery["top_up_product_ids"]
                    and not product_top_up_available
                ):
                    recovery_plan_id = plan_id
                    plans = config.plans
                    if config.products:
                        owning_products = [
                            product
                            for product in config.products
                            if any(
                                allowance.wallet_id == wallet.wallet_id
                                for plan in product.plans
                                for allowance in plan.token_allowances
                            )
                        ]
                        # A shared wallet has no single subscription plan authority.
                        recovery_plan_id = None
                        plans = []
                        if len(owning_products) == 1:
                            product = owning_products[0]
                            plans = product.plans
                            recovery_plan_id = await entitlements.current_plan_id(
                                app_id=app_id_text,
                                user_id=user_id_text,
                                tenant_id=tenant_id_text,
                                workspace_id=workspace_id_text,
                                product_id=product.product_id,
                            )
                    if self._terminal_monthly_plan(plans, wallet.wallet_id, recovery_plan_id):
                        recovery.update(
                            recovery_action="contact_admin",
                            billing_route=None,
                            top_up_route=None,
                            upgrade_route=None,
                            recovery_message=None,
                        )
                return TokenUsageDecision(
                    allowed=False,
                    reason="insufficient_balance",
                    error_code="INSUFFICIENT_TOKENS",
                    app_id=app_id_text,
                    wallet_id=wallet.wallet_id,
                    balance=current_balance,
                    required_tokens=required,
                    scope=wallet.scope,
                    user_id=user_id_text if wallet.scope == "user" else None,
                    tenant_id=tenant_id_text if wallet.scope == "tenant" else None,
                    workspace_id=workspace_id_text,
                    **recovery,
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

    @staticmethod
    def _recovery_metadata(
        config: SubscriptionsConfig,
        wallet: TokenWalletDef,
    ) -> dict[str, Any]:
        configured = wallet.depleted_balance
        top_up_products = config.top_up_products_for_wallet(wallet.wallet_id)
        default_action = "top_up" if top_up_products else "upgrade"
        billing_route = getattr(configured, "billing_route", None) or "/billing"
        top_up_route = getattr(configured, "top_up_route", None) or (billing_route if top_up_products else None)
        upgrade_route = getattr(configured, "upgrade_route", None) or "/pricing"
        return {
            "recovery_action": getattr(configured, "recovery_action", None) or default_action,
            "billing_route": billing_route,
            "top_up_route": top_up_route,
            "upgrade_route": upgrade_route,
            "contact_route": getattr(configured, "contact_route", None),
            "recovery_message": getattr(configured, "message", None),
            "top_up_product_ids": tuple(product.product_id for product in top_up_products),
        }

    @staticmethod
    def _terminal_monthly_plan(
        plans: list[PlanDef], wallet_id: str, plan_id: str | None,
    ) -> bool:
        """Compare declared monthly credits only when the plan ladder is unambiguous."""
        if not plan_id:
            return False
        amounts: dict[str, int] = {}
        for plan in plans:
            allowances = [
                allowance for allowance in plan.token_allowances
                if allowance.wallet_id == wallet_id
            ]
            if len(allowances) > 1 or (allowances and allowances[0].cadence != "monthly"):
                return False
            amounts[plan.plan_id] = allowances[0].amount if allowances else 0
        current_amount = amounts.get(plan_id)
        if current_amount is None or not any(amount > 0 for amount in amounts.values()):
            return False
        return not any(amount > current_amount for amount in amounts.values())


__all__ = ["TokenUsageDecision", "TokenUsageDenied", "TokenUsageGuard"]
