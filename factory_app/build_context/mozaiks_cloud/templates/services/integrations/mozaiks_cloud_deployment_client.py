from __future__ import annotations

from typing import Any

from .mozaiks_cloud_client import MozaiksCloudTransport


class MozaiksCloudDeploymentClient:
    def __init__(self, transport: MozaiksCloudTransport) -> None:
        self._transport = transport

    async def submit_deployment_request(
        self,
        *,
        app_id: str,
        target_environment: str = "production",
        release_ref: str | None = None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return await self._transport.request(
            "POST",
            "/deployments",
            json={
                "app_id": app_id,
                "target_environment": target_environment,
                "release_ref": release_ref,
            },
            idempotency_key=idempotency_key,
        )

    async def get_operation_status(self, *, operation_id: str) -> dict[str, Any]:
        return await self._transport.request("GET", f"/operations/{operation_id}")

    async def get_environment_endpoints(self, *, app_id: str) -> dict[str, Any]:
        return await self._transport.request("GET", f"/apps/{app_id}/endpoints")

    async def get_deployment_health(self, *, deployment_id: str) -> dict[str, Any]:
        return await self._transport.request("GET", f"/deployments/{deployment_id}/health")

    async def request_rollback(
        self,
        *,
        target_release_id: str,
        expected_current_deployment_id: str | None = None,
        target_environment: str = "production",
        reason: str | None = None,
        approval_reference: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return await self._transport.request(
            "POST",
            "/rollbacks",
            json={
                "target_release_id": target_release_id,
                "expected_current_deployment_id": expected_current_deployment_id,
                "target_environment": target_environment,
                "reason": reason,
                "approval_reference": approval_reference,
            },
            idempotency_key=idempotency_key,
        )
