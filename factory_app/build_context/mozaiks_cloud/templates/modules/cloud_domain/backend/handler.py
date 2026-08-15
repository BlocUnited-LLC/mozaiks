from __future__ import annotations

from typing import Any

from .service import CloudDomainService


class CloudDomainHandler:
    def __init__(self) -> None:
        self._service = CloudDomainService()

    async def connect_domain(self, ctx: Any, **params: Any) -> dict[str, Any]:
        return await self._service.connect_domain(ctx, **params)

    async def get_domain_verification(self, ctx: Any, **params: Any) -> dict[str, Any]:
        return await self._service.get_domain_verification(ctx, **params)

    async def get_dns_instructions(self, ctx: Any, **params: Any) -> dict[str, Any]:
        return await self._service.get_dns_instructions(ctx, **params)

    async def request_domain_activation(self, ctx: Any, **params: Any) -> dict[str, Any]:
        return await self._service.request_domain_activation(ctx, **params)

    async def get_domain_status(self, ctx: Any, **params: Any) -> dict[str, Any]:
        return await self._service.get_domain_status(ctx, **params)

    async def disconnect_domain(self, ctx: Any, **params: Any) -> dict[str, Any]:
        return await self._service.disconnect_domain(ctx, **params)
