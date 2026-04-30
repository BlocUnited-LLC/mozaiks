from __future__ import annotations

from inspect import isawaitable
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, HTTPException

from mozaiksai.core.admin.app_backend_contract import (
    APP_BACKEND_ADMIN_SCHEMA_VERSION,
    AppBackendAdminConfig,
    validate_app_backend_admin_config,
)


AppBackendAdminConfigProvider = Callable[[], Any | Awaitable[Any]]


async def _resolve_admin_config(
    provider: AppBackendAdminConfigProvider,
) -> AppBackendAdminConfig:
    raw = provider()
    if isawaitable(raw):
        raw = await raw
    return validate_app_backend_admin_config(raw)


def build_app_backend_admin_router(
    config_provider: AppBackendAdminConfigProvider,
) -> APIRouter:
    """Build a strict /api/admin/config router for split app backends.

    The provider owns business/admin semantics. This helper owns the HTTP route
    shape and validates that the emitted payload satisfies
    mozaiks.admin.app_backend.v1 before the shell consumes it.
    """

    router = APIRouter(prefix="/api/admin", tags=["app-backend-admin"])

    @router.get(
        "/config",
        response_model=AppBackendAdminConfig,
        response_model_exclude_none=True,
    )
    async def get_admin_config() -> AppBackendAdminConfig:
        try:
            return await _resolve_admin_config(config_provider)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=(
                    "Invalid app backend admin config. "
                    f"Expected schema_version={APP_BACKEND_ADMIN_SCHEMA_VERSION}. {exc}"
                ),
            ) from exc

    return router


__all__ = ["build_app_backend_admin_router"]
