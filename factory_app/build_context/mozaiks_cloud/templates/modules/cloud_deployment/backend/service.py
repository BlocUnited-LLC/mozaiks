from __future__ import annotations

from typing import Any

from services.integrations.mozaiks_cloud_client import MozaiksCloudTransport
from services.integrations.mozaiks_cloud_deployment_client import (
    MozaiksCloudDeploymentClient,
)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _require(value: Any, field: str) -> str:
    cleaned = _clean(value)
    if not cleaned:
        raise ValueError(f"{field} is required")
    return cleaned


class CloudDeploymentService:
    def _client(self, *, app_id: str | None = None) -> MozaiksCloudDeploymentClient:
        return MozaiksCloudDeploymentClient(MozaiksCloudTransport(app_id=app_id))

    async def submit_deployment(self, ctx: Any, **params: Any) -> dict[str, Any]:
        del ctx
        app_id = _require(params.get("app_id"), "app_id")
        return await self._client(app_id=app_id).submit_deployment_request(
            app_id=app_id,
            target_environment=_clean(params.get("target_environment")) or "production",
            release_ref=_clean(params.get("release_ref")) or None,
            idempotency_key=_require(params.get("idempotency_key"), "idempotency_key"),
        )

    async def get_deployment_status(self, ctx: Any, **params: Any) -> dict[str, Any]:
        del ctx
        return await self._client(app_id=_clean(params.get("app_id")) or None).get_operation_status(
            operation_id=_require(params.get("operation_id"), "operation_id"),
        )

    async def get_environment_endpoints(self, ctx: Any, **params: Any) -> dict[str, Any]:
        del ctx
        app_id = _require(params.get("app_id"), "app_id")
        return await self._client(app_id=app_id).get_environment_endpoints(app_id=app_id)

    async def get_deployment_health(self, ctx: Any, **params: Any) -> dict[str, Any]:
        del ctx
        return await self._client(app_id=_clean(params.get("app_id")) or None).get_deployment_health(
            deployment_id=_require(params.get("deployment_id"), "deployment_id"),
        )

    async def request_rollback(self, ctx: Any, **params: Any) -> dict[str, Any]:
        del ctx
        return await self._client(app_id=_clean(params.get("app_id")) or None).request_rollback(
            target_release_id=_require(params.get("target_release_id"), "target_release_id"),
            expected_current_deployment_id=_clean(params.get("expected_current_deployment_id")) or None,
            target_environment=_clean(params.get("target_environment")) or "production",
            reason=_clean(params.get("reason")) or None,
            approval_reference=_require(params.get("approval_reference"), "approval_reference"),
            idempotency_key=_require(params.get("idempotency_key"), "idempotency_key"),
        )
