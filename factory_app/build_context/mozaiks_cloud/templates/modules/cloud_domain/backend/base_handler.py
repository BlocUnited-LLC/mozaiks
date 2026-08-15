"""
OSS-generated base handler for the cloud_domain module.
DO NOT EDIT — regenerated on framework upgrades.
Put app-specific customizations in handler.py (the workspace subclass).

This module is a managed-capability facade. Action handlers call the Mozaiks
Cloud domain client; reaction handlers update local domain status caches from
normalized provider notifications.
"""

from __future__ import annotations

from typing import Any

from services.integrations.mozaiks_cloud_client import _load_connector_settings
from services.integrations.mozaiks_cloud_domain_client import MozaiksCloudDomainClient

from . import service


class CloudDomainBaseHandler:
    async def connect_domain(self, ctx: Any, **params: Any) -> dict[str, Any]:
        settings = await _load_connector_settings()
        client = MozaiksCloudDomainClient(settings)
        app_id = params.get("app_id") or settings.app_id or ""
        domain = params["domain"]
        result = await client.connect_domain(
            app_id=app_id,
            domain=domain,
            idempotency_key=params.get("idempotency_key", ""),
        )
        await service.record_domain_connected(
            ctx,
            operation_id=result.get("operation_id", ""),
            domain=domain,
            app_id=app_id,
        )
        return result

    async def get_domain_verification(self, ctx: Any, **params: Any) -> dict[str, Any]:
        settings = await _load_connector_settings()
        client = MozaiksCloudDomainClient(settings)
        return await client.get_domain_verification(operation_id=params["operation_id"])

    async def get_dns_instructions(self, ctx: Any, **params: Any) -> dict[str, Any]:
        settings = await _load_connector_settings()
        client = MozaiksCloudDomainClient(settings)
        return await client.get_dns_instructions(operation_id=params["operation_id"])

    async def request_domain_activation(self, ctx: Any, **params: Any) -> dict[str, Any]:
        settings = await _load_connector_settings()
        client = MozaiksCloudDomainClient(settings)
        result = await client.request_domain_activation(
            operation_id=params["operation_id"],
            idempotency_key=params.get("idempotency_key", ""),
        )
        return result

    async def get_domain_status(self, ctx: Any, **params: Any) -> dict[str, Any]:
        settings = await _load_connector_settings()
        client = MozaiksCloudDomainClient(settings)
        return await client.get_domain_status(domain=params["domain"])

    async def disconnect_domain(self, ctx: Any, **params: Any) -> dict[str, Any]:
        settings = await _load_connector_settings()
        client = MozaiksCloudDomainClient(settings)
        result = await client.disconnect_domain(
            domain=params["domain"],
            idempotency_key=params.get("idempotency_key", ""),
        )
        await service.record_domain_disconnected(ctx, domain=params["domain"])
        return result

    async def handle_domain_activated(self, ctx: Any, **params: Any) -> dict[str, Any]:
        """React to hosted.cloud.domain.activated — update local domain status cache."""
        await service.record_domain_status_updated(
            ctx,
            domain=params.get("domain", ""),
            status="active",
            dns_verified=params.get("dns_verified", True),
            tls_status=params.get("tls_status", "active"),
        )
        return {"handled": True}

    async def handle_domain_verification_updated(self, ctx: Any, **params: Any) -> dict[str, Any]:
        """React to hosted.cloud.domain.verification_updated — update verification cache."""
        await service.record_domain_status_updated(
            ctx,
            domain=params.get("domain", ""),
            status=params.get("status", "verified"),
            dns_verified=params.get("dns_verified", False),
            tls_status=params.get("tls_status", "pending"),
        )
        return {"handled": True}

    async def handle_domain_failed(self, ctx: Any, **params: Any) -> dict[str, Any]:
        """React to hosted.cloud.domain.failed — update local domain status cache."""
        await service.record_domain_status_updated(
            ctx,
            domain=params.get("domain", ""),
            status="failed",
            dns_verified=False,
            tls_status=params.get("tls_status", "failed"),
        )
        return {"handled": True}
