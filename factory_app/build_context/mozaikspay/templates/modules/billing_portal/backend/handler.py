from __future__ import annotations

from typing import Any

from .service import BillingPortalService


class BillingPortalHandler:
    def __init__(self) -> None:
        self.service = BillingPortalService()

    async def get_subscription_status(
        self,
        ctx: Any,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self.service.get_subscription_status(ctx)

    async def get_usage_status(
        self,
        ctx: Any,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self.service.get_usage_status(ctx, **(payload or {}))

    async def open_billing_portal(
        self,
        ctx: Any,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self.service.open_billing_portal(ctx, **(payload or {}))

    async def handle_subscription_activated(
        self,
        ctx: Any,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self.service.handle_subscription_event(ctx, "activated", payload or {})

    async def handle_subscription_updated(
        self,
        ctx: Any,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self.service.handle_subscription_event(ctx, "updated", payload or {})

    async def handle_subscription_cancelled(
        self,
        ctx: Any,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self.service.handle_subscription_event(ctx, "cancelled", payload or {})
