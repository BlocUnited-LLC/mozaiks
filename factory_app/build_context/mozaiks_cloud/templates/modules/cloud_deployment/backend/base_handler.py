"""
OSS-generated base handler for the cloud_deployment module.
DO NOT EDIT — regenerated on framework upgrades.
Put app-specific customizations in handler.py (the workspace subclass).

This module is a managed-capability facade. Action handlers call the Mozaiks
Cloud deployment client; reaction handlers update local status caches from
normalized provider notifications.
"""

from __future__ import annotations

from typing import Any

from services.integrations.mozaiks_cloud_client import _load_connector_settings
from services.integrations.mozaiks_cloud_deployment_client import MozaiksCloudDeploymentClient
from services.integrations.mozaiks_cloud_environment_client import MozaiksCloudEnvironmentClient

from . import service


class CloudDeploymentBaseHandler:
    async def submit_deployment(self, ctx: Any, **params: Any) -> dict[str, Any]:
        settings = await _load_connector_settings()
        client = MozaiksCloudDeploymentClient(settings)
        app_id = params.get("app_id") or settings.app_id or ""
        environment = params.get("environment", "production")
        release_ref = params.get("release_ref", "")
        idempotency_key = params.get("idempotency_key", "")
        result = await client.submit_deployment_request(
            app_id=app_id,
            environment=environment,
            release_ref=release_ref,
            idempotency_key=idempotency_key,
        )
        await service.record_deployment_submitted(
            ctx,
            app_id=app_id,
            environment=environment,
            release_ref=release_ref,
            operation_id=result.get("operation_id", ""),
        )
        return result

    async def get_deployment_status(self, ctx: Any, **params: Any) -> dict[str, Any]:
        settings = await _load_connector_settings()
        client = MozaiksCloudDeploymentClient(settings)
        return await client.get_deployment_status(operation_id=params["operation_id"])

    async def get_environment_endpoints(self, ctx: Any, **params: Any) -> dict[str, Any]:
        settings = await _load_connector_settings()
        client = MozaiksCloudEnvironmentClient(settings)
        app_id = params.get("app_id") or settings.app_id or ""
        environment = params.get("environment", "production")
        return await client.get_environment_endpoints(app_id=app_id, environment=environment)

    async def request_rollback(self, ctx: Any, **params: Any) -> dict[str, Any]:
        settings = await _load_connector_settings()
        client = MozaiksCloudDeploymentClient(settings)
        result = await client.request_rollback(
            operation_id=params["operation_id"],
            target_release_ref=params.get("target_release_ref", ""),
            idempotency_key=params.get("idempotency_key", ""),
        )
        await service.record_rollback_requested(
            ctx,
            operation_id=params["operation_id"],
            target_release_ref=params.get("target_release_ref", ""),
        )
        return result

    async def handle_deployment_completed(self, ctx: Any, **params: Any) -> dict[str, Any]:
        """React to hosted.cloud.deployment.completed — update local status cache."""
        await service.record_deployment_status_updated(
            ctx,
            operation_id=params.get("operation_id", ""),
            status="succeeded",
            message=params.get("message"),
        )
        return {"handled": True}

    async def handle_deployment_failed(self, ctx: Any, **params: Any) -> dict[str, Any]:
        """React to hosted.cloud.deployment.failed — update local status cache."""
        await service.record_deployment_status_updated(
            ctx,
            operation_id=params.get("operation_id", ""),
            status="failed",
            message=params.get("message"),
        )
        return {"handled": True}
