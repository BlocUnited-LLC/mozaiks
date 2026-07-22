"""
OSS-generated base handler for the entitlement_dispatch module.
DO NOT EDIT — regenerated on framework upgrades.
Put app-specific customizations in handler.py (the workspace subclass).
"""
from __future__ import annotations

from typing import Any

from .service import EntitlementDispatchService


class EntitlementDispatchBaseHandler:
    def __init__(self, service: EntitlementDispatchService | None = None) -> None:
        self._service = service or EntitlementDispatchService()

    async def activate_subscription(self, ctx: Any, **kwargs: Any) -> dict[str, Any]:
        return await self._service.activate_subscription(ctx, **kwargs)

    async def deactivate_subscription(self, ctx: Any, **kwargs: Any) -> dict[str, Any]:
        return await self._service.deactivate_subscription(ctx, **kwargs)
