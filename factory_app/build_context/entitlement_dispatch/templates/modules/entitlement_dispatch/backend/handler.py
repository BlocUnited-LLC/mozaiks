from __future__ import annotations

from typing import Any

from .service import EntitlementDispatchService


class EntitlementDispatchHandler:
    def __init__(self) -> None:
        self.service: EntitlementDispatchService = EntitlementDispatchService()

    async def activate_subscription(self, ctx: Any, **kwargs: Any) -> dict[str, Any]:
        return await self.service.activate_subscription(ctx, **kwargs)

    async def deactivate_subscription(self, ctx: Any, **kwargs: Any) -> dict[str, Any]:
        return await self.service.deactivate_subscription(ctx, **kwargs)
