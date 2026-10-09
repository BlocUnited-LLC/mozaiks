"""Resolve v2 token wallets against their subscription product authority."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError

from mozaiksai.core.runtime.app.entitlements import (
    ConfiguredEntitlementAdapter,
    ProductPlanSelection,
)
from mozaiksai.core.runtime.app.subscriptions_loader import (
    SubscriptionsConfig,
    TokenAllowanceDef,
)

WalletPlanStatus = Literal[
    "resolved", "no_product", "ambiguous_product", "plan_unavailable",
    "unknown_plan", "invalid_snapshot",
]


@dataclass(frozen=True)
class WalletPlanResolution:
    wallet_id: str
    status: WalletPlanStatus
    product_id: str | None = None
    plan_id: str | None = None
    plan_label: str | None = None
    allowances: tuple[TokenAllowanceDef, ...] = ()
    allowance_source: Literal["catalog", "assignment_snapshot"] | None = None
    grant_authority: Literal["runtime_default", "billing_fulfillment"] | None = None


async def resolve_v2_wallet_plans(
    *,
    config: SubscriptionsConfig,
    entitlements: ConfiguredEntitlementAdapter,
    app_id: str,
    user_id: str | None = None,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
) -> dict[str, WalletPlanResolution]:
    """Resolve each wallet through exactly one product, never the primary fallback.

    Products may share a balance, but there is no single authoritative plan in
    that case. Automatic subscription grants and plan display fail closed for
    the ambiguous wallet; explicit billing fulfillment can still credit it.
    """
    if not config.products:
        return {}

    resolutions: dict[str, WalletPlanResolution] = {}
    selected_plans: dict[str, ProductPlanSelection | None] = {}
    for wallet in config.effective_token_wallets:
        owners = [
            product
            for product in config.products
            if any(declared.wallet_id == wallet.wallet_id for declared in product.token_wallets)
            or any(
                allowance.wallet_id == wallet.wallet_id
                for plan in product.plans
                for allowance in plan.token_allowances
            )
        ]
        if not owners:
            resolutions[wallet.wallet_id] = WalletPlanResolution(
                wallet_id=wallet.wallet_id, status="no_product"
            )
            continue
        if len(owners) != 1:
            resolutions[wallet.wallet_id] = WalletPlanResolution(
                wallet_id=wallet.wallet_id, status="ambiguous_product"
            )
            continue

        product = owners[0]
        if product.product_id not in selected_plans:
            selected_plans[product.product_id] = await entitlements.current_product_plan(
                app_id=app_id,
                user_id=user_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                product_id=product.product_id,
            )
        selection = selected_plans[product.product_id]
        if selection is None or not selection.plan_id:
            resolutions[wallet.wallet_id] = WalletPlanResolution(
                wallet_id=wallet.wallet_id,
                status="plan_unavailable",
                product_id=product.product_id,
            )
            continue
        plan = next(
            (item for item in product.plans if item.plan_id == selection.plan_id), None
        )
        if plan is None:
            resolutions[wallet.wallet_id] = WalletPlanResolution(
                wallet_id=wallet.wallet_id,
                status="unknown_plan",
                product_id=product.product_id,
                plan_id=selection.plan_id,
            )
            continue
        source: Literal["catalog", "assignment_snapshot"] = "catalog"
        plan_allowances = plan.token_allowances
        if selection.source == "active_assignment" and selection.allowances_snapshot is not None:
            if not isinstance(selection.allowances_snapshot, list):
                resolutions[wallet.wallet_id] = WalletPlanResolution(
                    wallet_id=wallet.wallet_id,
                    status="invalid_snapshot",
                    product_id=product.product_id,
                    plan_id=plan.plan_id,
                )
                continue
            try:
                plan_allowances = [
                    TokenAllowanceDef.model_validate(item)
                    for item in selection.allowances_snapshot
                ]
            except ValidationError:
                resolutions[wallet.wallet_id] = WalletPlanResolution(
                    wallet_id=wallet.wallet_id,
                    status="invalid_snapshot",
                    product_id=product.product_id,
                    plan_id=plan.plan_id,
                )
                continue
            source = "assignment_snapshot"
        resolutions[wallet.wallet_id] = WalletPlanResolution(
            wallet_id=wallet.wallet_id,
            status="resolved",
            product_id=product.product_id,
            plan_id=plan.plan_id,
            plan_label=plan.label,
            allowances=tuple(
                allowance
                for allowance in plan_allowances
                if allowance.wallet_id == wallet.wallet_id
            ),
            allowance_source=source,
            grant_authority=(
                "billing_fulfillment"
                if selection.source == "active_assignment"
                else "runtime_default"
            ),
        )
    return resolutions
