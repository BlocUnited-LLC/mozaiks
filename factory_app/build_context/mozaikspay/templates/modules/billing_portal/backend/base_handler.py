"""
OSS-generated base handler for the billing_portal module.
DO NOT EDIT — regenerated on framework upgrades.
Put app-specific customizations in handler.py (the workspace subclass).
"""
from __future__ import annotations

from typing import Any

from .service import BillingPortalService


class BillingPortalBaseHandler:
    def __init__(self, service: BillingPortalService | None = None) -> None:
        self._service = service or BillingPortalService()

    async def get_subscription_status(self, ctx: Any, **params: Any) -> dict[str, Any]:
        return await self._service.get_subscription_status(ctx)

    async def get_usage_status(self, ctx: Any, **params: Any) -> dict[str, Any]:
        return await self._service.get_usage_status(ctx, **params)

    async def get_token_status(self, ctx: Any, **params: Any) -> dict[str, Any]:
        return await self._service.get_token_status(ctx, **params)

    async def list_plans(self, ctx: Any, **params: Any) -> dict[str, Any]:
        return await self._service.list_plans(ctx)

    async def start_subscription_checkout(self, ctx: Any, **params: Any) -> dict[str, Any]:
        return await self._service.start_subscription_checkout(ctx, **params)

    async def start_token_top_up(self, ctx: Any, **params: Any) -> dict[str, Any]:
        return await self._service.start_token_top_up(ctx, **params)

    async def open_billing_portal(self, ctx: Any, **params: Any) -> dict[str, Any]:
        return await self._service.open_billing_portal(ctx, **params)

    async def handle_subscription_activated(self, ctx: Any, **params: Any) -> dict[str, Any]:
        return await self._service.handle_subscription_event(ctx, "activated", params)

    async def handle_subscription_updated(self, ctx: Any, **params: Any) -> dict[str, Any]:
        return await self._service.handle_subscription_event(ctx, "updated", params)

    async def handle_subscription_cancelled(self, ctx: Any, **params: Any) -> dict[str, Any]:
        return await self._service.handle_subscription_event(ctx, "cancelled", params)
