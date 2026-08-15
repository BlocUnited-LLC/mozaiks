from __future__ import annotations

from typing import Any

from .service import CloudDeploymentService


class CloudDeploymentHandler:
    def __init__(self) -> None:
        self._service = CloudDeploymentService()

    async def submit_deployment(self, ctx: Any, **params: Any) -> dict[str, Any]:
        return await self._service.submit_deployment(ctx, **params)

    async def get_deployment_status(self, ctx: Any, **params: Any) -> dict[str, Any]:
        return await self._service.get_deployment_status(ctx, **params)

    async def get_environment_endpoints(self, ctx: Any, **params: Any) -> dict[str, Any]:
        return await self._service.get_environment_endpoints(ctx, **params)

    async def get_deployment_health(self, ctx: Any, **params: Any) -> dict[str, Any]:
        return await self._service.get_deployment_health(ctx, **params)

    async def request_rollback(self, ctx: Any, **params: Any) -> dict[str, Any]:
        return await self._service.request_rollback(ctx, **params)
