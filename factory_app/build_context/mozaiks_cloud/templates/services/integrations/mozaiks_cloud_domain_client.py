from __future__ import annotations

from typing import Any

from .mozaiks_cloud_client import MozaiksCloudTransport


def _query_params(*, tenant_id: str | None = None, app_id: str | None = None) -> dict[str, str]:
    params: dict[str, str] = {}
    if tenant_id:
        params["tenant_id"] = tenant_id
    if app_id:
        params["app_id"] = app_id
    return params


class MozaiksCloudDomainClient:
    def __init__(self, transport: MozaiksCloudTransport) -> None:
        self._transport = transport

    async def connect_domain(
        self,
        *,
        app_id: str,
        domain: str,
        target_environment: str = "production",
        idempotency_key: str,
    ) -> dict[str, Any]:
        return await self._transport.request(
            "POST",
            "/domains",
            json={
                "app_id": app_id,
                "domain": domain,
                "target_environment": target_environment,
            },
            idempotency_key=idempotency_key,
        )

    async def get_domain_verification(
        self,
        *,
        binding_id: str,
        tenant_id: str | None = None,
        app_id: str | None = None,
    ) -> dict[str, Any]:
        return await self._transport.request(
            "GET",
            f"/domains/{binding_id}/verification",
            params=_query_params(tenant_id=tenant_id, app_id=app_id),
        )

    async def get_dns_instructions(
        self,
        *,
        binding_id: str,
        tenant_id: str | None = None,
        app_id: str | None = None,
    ) -> dict[str, Any]:
        return await self._transport.request(
            "GET",
            f"/domains/{binding_id}/instructions",
            params=_query_params(tenant_id=tenant_id, app_id=app_id),
        )

    async def request_domain_activation(
        self,
        *,
        binding_id: str,
        tenant_id: str,
        app_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return await self._transport.request(
            "POST",
            f"/domains/{binding_id}/activate",
            json={"tenant_id": tenant_id, "app_id": app_id},
            idempotency_key=idempotency_key,
        )

    async def get_domain_status(
        self,
        *,
        binding_id: str,
        tenant_id: str | None = None,
        app_id: str | None = None,
    ) -> dict[str, Any]:
        return await self._transport.request(
            "GET",
            f"/domains/{binding_id}",
            params=_query_params(tenant_id=tenant_id, app_id=app_id),
        )

    async def disconnect_domain(
        self,
        *,
        binding_id: str,
        tenant_id: str,
        app_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return await self._transport.request(
            "DELETE",
            f"/domains/{binding_id}",
            json={"tenant_id": tenant_id, "app_id": app_id},
            idempotency_key=idempotency_key,
        )
