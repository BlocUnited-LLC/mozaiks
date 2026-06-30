from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from services.integrations.mozaikspay_client import MozaiksPayClient

from .schemas import SubscriptionStatus, safe_portal_response


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

    async def open_billing_portal(
        self,
        ctx: Any,
        *,
        return_url: str,
        **_: Any,
    ) -> dict[str, Any]:
        if not return_url:
            return {"success": False, "error_code": "INVALID_INPUT", "detail": "return_url is required"}
        parsed = urlparse(return_url)
        if parsed.scheme != "https" or not parsed.netloc:
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
