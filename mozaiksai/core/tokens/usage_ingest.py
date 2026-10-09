from __future__ import annotations

"""Usage-event to token-wallet bridge.

This bridge is intentionally provider-neutral. It consumes factual
``chat.usage_delta`` payloads and, when the active app's subscriptions config
opts in, materializes plan allowances and debits runtime token wallets.
"""

from pathlib import Path
from typing import Any

from logs.logging_config import get_core_logger
from mozaiksai.core.runtime.app.entitlements import ConfiguredEntitlementAdapter
from mozaiksai.core.runtime.app.subscriptions_loader import (
    SubscriptionsConfig,
    load_subscriptions_config,
)
from mozaiksai.core.tokens.plan_resolution import resolve_v2_wallet_plans
from mozaiksai.core.tokens.wallet import TokenWalletLedger, get_token_wallet_ledger
from mozaiksai.core.workflow.paths import resolve_active_app_root

logger = get_core_logger("token_wallet_usage_ingest")


class TokenWalletUsageIngestClient:
    def __init__(
        self,
        *,
        ledger: TokenWalletLedger | None = None,
        app_root: str | Path | None = None,
    ) -> None:
        self._ledger = ledger
        self._app_root = Path(app_root) if app_root is not None else None
        self._config_cache: SubscriptionsConfig | None | bool = False

    def _load_config(self) -> SubscriptionsConfig | None:
        if self._config_cache is not False:
            return self._config_cache  # type: ignore[return-value]
        try:
            app_root = self._app_root or resolve_active_app_root()
            self._config_cache = load_subscriptions_config(Path(app_root))
        except Exception as exc:
            logger.debug("token wallet usage ingest config load skipped: %s", exc)
            self._config_cache = None
        return self._config_cache

    async def handle_usage_delta(self, payload: dict[str, Any]) -> None:
        config = self._load_config()
        if config is None or not config.effective_token_wallets:
            return

        wallets = [wallet for wallet in config.effective_token_wallets if wallet.auto_debit_usage]
        if not wallets:
            return

        app_id = str(payload.get("app_id") or "").strip()
        user_id = str(payload.get("user_id") or "").strip() or None
        tenant_id = str(payload.get("tenant_id") or "").strip() or None
        workspace_id = str(payload.get("workspace_id") or "").strip() or None
        if not app_id:
            return

        try:
            entitlements = ConfiguredEntitlementAdapter(config=config)
            ledger = self._ledger or get_token_wallet_ledger()
            if config.products:
                wallet_plans = await resolve_v2_wallet_plans(
                    config=config,
                    entitlements=entitlements,
                    app_id=app_id,
                    user_id=user_id,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
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
                        app_id=app_id,
                        wallet_plans=payable_wallet_plans,
                        user_id=user_id,
                        tenant_id=tenant_id,
                    )
            else:
                plan_id = await entitlements.current_plan_id(
                    app_id=app_id,
                    user_id=user_id,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                )
                await ledger.ensure_plan_allowances(
                    config=config,
                    app_id=app_id,
                    plan_id=plan_id,
                    user_id=user_id,
                    tenant_id=tenant_id,
                )
            for wallet in wallets:
                if wallet.scope == "user" and not user_id:
                    continue
                if wallet.scope == "tenant" and not tenant_id:
                    continue
                await ledger.record_usage_debit(payload, wallet=wallet)
        except Exception as exc:  # pragma: no cover - usage spending must not break runs
            logger.debug("token wallet usage ingest skipped: %s", exc)


_global_usage_ingest: TokenWalletUsageIngestClient | None = None


def get_token_wallet_usage_ingest_client() -> TokenWalletUsageIngestClient:
    global _global_usage_ingest
    if _global_usage_ingest is None:
        _global_usage_ingest = TokenWalletUsageIngestClient()
    return _global_usage_ingest


__all__ = [
    "TokenWalletUsageIngestClient",
    "get_token_wallet_usage_ingest_client",
]
