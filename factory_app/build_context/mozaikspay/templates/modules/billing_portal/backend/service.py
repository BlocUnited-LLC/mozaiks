from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from services.integrations.mozaikspay_client import MozaiksPayClient

from .schemas import SubscriptionStatus, safe_checkout_response, safe_portal_response


def _auth_token_from_ctx(ctx: Any) -> str | None:
    for attr in ("auth_token", "authorization"):
        value = getattr(ctx, attr, None)
        if isinstance(value, str) and value:
            return value
    headers = getattr(ctx, "headers", None)
    if isinstance(headers, dict):
        value = headers.get("Authorization") or headers.get("authorization")
        if isinstance(value, str) and value:
            return value
    return None


def _ctx_scope(ctx: Any) -> dict[str, str | None]:
    return {
        "user_id": getattr(ctx, "user_id", None),
        "tenant_id": getattr(ctx, "tenant_id", None),
        "workspace_id": getattr(ctx, "workspace_id", None),
    }


def _subscriptions_config() -> dict[str, Any]:
    subs_path = Path.cwd() / "app" / "config" / "subscriptions.yaml"
    if not subs_path.exists():
        return {}
    try:
        import yaml

        raw = yaml.safe_load(subs_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def _safe_https_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.netloc)


def _positive_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _safe_plan(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "plan_id": plan.get("plan_id", ""),
        "label": plan.get("label", ""),
        "description": plan.get("description", ""),
        "capabilities": plan.get("capabilities", []),
        "usage_limits": plan.get("usage_limits", {}),
        "token_allowances": plan.get("token_allowances", []),
    }


def _safe_token_wallet(wallet: dict[str, Any]) -> dict[str, Any]:
    return {
        "wallet_id": wallet.get("wallet_id", ""),
        "label": wallet.get("label", ""),
        "unit": wallet.get("unit", ""),
        "usage_meter_id": wallet.get("usage_meter_id", ""),
        "scope": wallet.get("scope", ""),
        "auto_debit_usage": bool(wallet.get("auto_debit_usage", False)),
        "depleted_balance": wallet.get("depleted_balance") or None,
    }


def _safe_top_up_product(product: dict[str, Any]) -> dict[str, Any]:
    raw_price = product.get("price")
    price = raw_price if isinstance(raw_price, dict) else {}
    currency = str(price.get("currency") or "usd").strip().lower()
    return {
        "product_id": product.get("product_id", ""),
        "label": product.get("label", ""),
        "description": product.get("description", ""),
        "wallet_id": product.get("wallet_id", "ai_tokens"),
        "token_amount": _positive_int(product.get("token_amount")),
        "price": {
            "amount_cents": _positive_int(price.get("amount_cents")),
            "currency": currency if len(currency) == 3 else "usd",
            "display": str(price.get("display") or "").strip(),
        },
        "active": bool(product.get("active", True)),
    }


class BillingPortalService:
    def __init__(self, mozaikspay_client: MozaiksPayClient | None = None) -> None:
        self._mozaikspay_client = mozaikspay_client

    def _client(self, ctx: Any) -> MozaiksPayClient:
        if self._mozaikspay_client is not None:
            return self._mozaikspay_client
        return MozaiksPayClient(
            auth_token=_auth_token_from_ctx(ctx),
            app_id=getattr(ctx, "app_id", None),
        )

    async def get_subscription_status(self, ctx: Any) -> dict[str, Any]:
        raw = await self._client(ctx).get_subscription_status_for_scope(**_ctx_scope(ctx))
        return SubscriptionStatus.from_hosted(raw).to_dict()

    async def get_usage_status(
        self,
        ctx: Any,
        *,
        limit: int = 500,
        **_: Any,
    ) -> dict[str, Any]:
        client = self._client(ctx)
        runtime_ai_usage = await client.get_runtime_ai_usage(limit=limit)
        return {
            "success": True,
            "runtime_ai_usage": runtime_ai_usage,
            "source": "mozaikspay_facade",
        }

    async def get_token_status(
        self,
        ctx: Any,
        *,
        limit: int = 500,
        **_: Any,
    ) -> dict[str, Any]:
        config = _subscriptions_config()
        runtime_ai_usage = await self._client(ctx).get_runtime_ai_usage(limit=limit)
        token_wallets = [
            _safe_token_wallet(wallet)
            for wallet in (config.get("token_wallets") or [])
            if isinstance(wallet, dict)
        ]
        top_up_products = [
            _safe_top_up_product(product)
            for product in (config.get("top_up_products") or [])
            if isinstance(product, dict) and product.get("active", True)
        ]
        return {
            "success": True,
            "runtime_ai_usage": runtime_ai_usage,
            "token_wallets": token_wallets,
            "top_up_products": top_up_products,
            "source": "mozaikspay_facade",
        }

    async def list_plans(self, ctx: Any) -> dict[str, Any]:
        raw = _subscriptions_config()
        plans = raw.get("plans", [])
        top_up_products = raw.get("top_up_products", [])
        return {
            "success": True,
            "plans": [_safe_plan(plan) for plan in plans if isinstance(plan, dict)],
            "top_up_products": [
                _safe_top_up_product(product)
                for product in top_up_products
                if isinstance(product, dict) and product.get("active", True)
            ],
            "pricing_catalog": raw.get("pricing_catalog") or {},
        }

    async def start_subscription_checkout(
        self,
        ctx: Any,
        *,
        plan_id: str,
        success_url: str,
        cancel_url: str,
        customer_email: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        if not plan_id:
            return {"success": False, "error_code": "INVALID_INPUT", "detail": "plan_id is required"}
        if not _safe_https_url(success_url) or not _safe_https_url(cancel_url):
            return {
                "success": False,
                "error_code": "INVALID_INPUT",
                "detail": "success_url and cancel_url must be https URLs",
            }
        raw = await self._client(ctx).create_subscription_checkout_session(
            plan_id=plan_id,
            success_url=success_url,
            cancel_url=cancel_url,
            customer_email=customer_email,
            **_ctx_scope(ctx),
        )
        return safe_checkout_response(raw)

    async def start_token_top_up(
        self,
        ctx: Any,
        *,
        product_id: str,
        success_url: str,
        cancel_url: str,
        **_: Any,
    ) -> dict[str, Any]:
        if not product_id:
            return {"success": False, "error_code": "INVALID_INPUT", "detail": "product_id is required"}
        if not _safe_https_url(success_url) or not _safe_https_url(cancel_url):
            return {
                "success": False,
                "error_code": "INVALID_INPUT",
                "detail": "success_url and cancel_url must be https URLs",
            }
        products = [
            product
            for product in (_subscriptions_config().get("top_up_products") or [])
            if isinstance(product, dict) and product.get("product_id") == product_id
        ]
        if not products:
            return {"success": False, "error_code": "TOKEN_TOP_UP_PRODUCT_NOT_FOUND"}
        product = _safe_top_up_product(products[0])
        price = product["price"]
        if product["token_amount"] <= 0 or price["amount_cents"] <= 0:
            return {"success": False, "error_code": "TOKEN_TOP_UP_PRODUCT_NOT_FOUND"}
        raw = await self._client(ctx).create_token_top_up_session(
            product_id=product["product_id"],
            wallet_id=product["wallet_id"],
            token_amount=product["token_amount"],
            amount_cents=price["amount_cents"],
            currency=price["currency"],
            success_url=success_url,
            cancel_url=cancel_url,
            **_ctx_scope(ctx),
        )
        return safe_checkout_response(raw)

    async def open_billing_portal(
        self,
        ctx: Any,
        *,
        return_url: str,
        **_: Any,
    ) -> dict[str, Any]:
        if not return_url:
            return {"success": False, "error_code": "INVALID_INPUT", "detail": "return_url is required"}
        if not _safe_https_url(return_url):
            return {"success": False, "error_code": "INVALID_INPUT", "detail": "return_url must be an https URL"}
        raw = await self._client(ctx).create_billing_portal_session(
            return_url=return_url,
            **_ctx_scope(ctx),
        )
        return safe_portal_response(raw)

    async def handle_subscription_event(
        self,
        ctx: Any,
        status: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "success": True,
            "handled": True,
            "status": status,
            "plan_id": payload.get("plan_id"),
        }
