from __future__ import annotations

from typing import Any

from services.integrations.mozaiks_cloud_client import MozaiksCloudTransport
from services.integrations.mozaiks_cloud_domain_client import MozaiksCloudDomainClient


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _require(value: Any, field: str) -> str:
    cleaned = _clean(value)
    if not cleaned:
        raise ValueError(f"{field} is required")
    return cleaned


class CloudDomainService:
    def _client(self, *, app_id: str | None = None) -> MozaiksCloudDomainClient:
        return MozaiksCloudDomainClient(MozaiksCloudTransport(app_id=app_id))

    async def connect_domain(self, ctx: Any, **params: Any) -> dict[str, Any]:
        del ctx
        app_id = _require(params.get("app_id"), "app_id")
        return await self._client(app_id=app_id).connect_domain(
            app_id=app_id,
            domain=_require(params.get("domain"), "domain"),
            target_environment=_clean(params.get("target_environment")) or "production",
            idempotency_key=_require(params.get("idempotency_key"), "idempotency_key"),
        )

    async def get_domain_verification(self, ctx: Any, **params: Any) -> dict[str, Any]:
        del ctx
        return await self._client(app_id=_clean(params.get("app_id")) or None).get_domain_verification(
            binding_id=_require(params.get("binding_id"), "binding_id"),
            tenant_id=_clean(params.get("tenant_id")) or None,
            app_id=_clean(params.get("app_id")) or None,
        )

    async def get_dns_instructions(self, ctx: Any, **params: Any) -> dict[str, Any]:
        del ctx
        return await self._client(app_id=_clean(params.get("app_id")) or None).get_dns_instructions(
            binding_id=_require(params.get("binding_id"), "binding_id"),
            tenant_id=_clean(params.get("tenant_id")) or None,
            app_id=_clean(params.get("app_id")) or None,
        )

    async def request_domain_activation(self, ctx: Any, **params: Any) -> dict[str, Any]:
        del ctx
        return await self._client(app_id=_clean(params.get("app_id")) or None).request_domain_activation(
            binding_id=_require(params.get("binding_id"), "binding_id"),
            tenant_id=_require(params.get("tenant_id"), "tenant_id"),
            app_id=_require(params.get("app_id"), "app_id"),
            idempotency_key=_require(params.get("idempotency_key"), "idempotency_key"),
        )

    async def get_domain_status(self, ctx: Any, **params: Any) -> dict[str, Any]:
        del ctx
        return await self._client(app_id=_clean(params.get("app_id")) or None).get_domain_status(
            binding_id=_require(params.get("binding_id"), "binding_id"),
            tenant_id=_clean(params.get("tenant_id")) or None,
            app_id=_clean(params.get("app_id")) or None,
        )

    async def disconnect_domain(self, ctx: Any, **params: Any) -> dict[str, Any]:
        del ctx
        return await self._client(app_id=_clean(params.get("app_id")) or None).disconnect_domain(
            binding_id=_require(params.get("binding_id"), "binding_id"),
            tenant_id=_require(params.get("tenant_id"), "tenant_id"),
            app_id=_require(params.get("app_id"), "app_id"),
            idempotency_key=_require(params.get("idempotency_key"), "idempotency_key"),
        )
