"""Account management router.

Routes:
    DELETE /api/account          — request account deletion (queued, async)
    GET    /api/account/export   — request a portable data export (JSON)

Both routes require an authenticated user session.  The platform dispatches to
all ``AccountDataHandler`` implementations registered in ``account_data_registry``
and then calls the platform hook for post-processing (auth provider cleanup,
billing cancellation, audit events, etc.).

Deletion is intentionally synchronous for OSS simplicity — the caller blocks
until all module handlers have run.  Production-grade platforms (e.g. Mozaiks
hosted) should override via the ``on_account_delete_complete`` platform hook to
queue deferred cleanup (e.g. OIDC account removal, subscription cancellation).
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from mozaiksai.core.account import account_data_registry
from mozaiksai.core.auth import UserPrincipal, require_user_scope
from mozaiksai.core.auth.dependencies import validate_user_id_against_principal
from mozaiksai.core.runtime.composition.platform_hooks import get_platform_hooks
from mozaiksai.hosts import runtime as runtime_app

router = APIRouter(tags=["account"])
logger = logging.getLogger("mozaiks_app.account_router")

persistence_manager = runtime_app.persistence_manager


def _get_db() -> Any:
    return persistence_manager.db if hasattr(persistence_manager, "db") else None


# ---------------------------------------------------------------------------
# DELETE /api/account
# ---------------------------------------------------------------------------

@router.delete(
    "/api/account",
    summary="Delete the authenticated user's account and all owned data",
    status_code=200,
)
async def delete_account(
    principal: UserPrincipal = Depends(require_user_scope),
    _: None = Depends(validate_user_id_against_principal),
) -> JSONResponse:
    """Permanently delete the authenticated user's account.

    Calls ``delete_user_data`` on every registered ``AccountDataHandler`` in
    registration order.  A module-level failure is captured and logged as a
    warning; other modules continue.  After all handlers run, the platform
    hook ``on_account_delete_complete`` is called for any post-processing the
    host needs to perform (e.g. cancel subscriptions, revoke OIDC sessions).

    Returns a summary of per-module deletion results for audit purposes.
    The response intentionally omits personal data — it contains only counts
    and success/error status per module.
    """
    app_id = principal.app_id
    user_id = principal.user_id

    logger.info("ACCOUNT_DELETE_STARTED: app_id=%s user_id=%s", app_id, user_id)

    db = _get_db()
    deletion_results = await account_data_registry.delete_all(
        app_id=app_id,
        user_id=user_id,
        db=db,
    )

    # Platform hook — hosted product can cancel subscriptions, revoke OIDC, etc.
    hooks = get_platform_hooks()
    on_complete = getattr(hooks, "call_on_account_delete_complete", None)
    if callable(on_complete):
        try:
            await on_complete(
                app_id=app_id,
                user_id=user_id,
                deletion_results=deletion_results,
            )
        except Exception as exc:
            logger.warning("ACCOUNT_DELETE_HOOK_ERROR: %s", exc)

    logger.info(
        "ACCOUNT_DELETE_COMPLETE: app_id=%s user_id=%s modules=%s",
        app_id,
        user_id,
        list(deletion_results.keys()),
    )

    return JSONResponse(
        {
            "success": True,
            "user_id": user_id,
            "modules_processed": list(deletion_results.keys()),
            "results": deletion_results,
        }
    )


# ---------------------------------------------------------------------------
# GET /api/account/export
# ---------------------------------------------------------------------------

@router.get(
    "/api/account/export",
    summary="Export a portable copy of all data owned by the authenticated user",
    status_code=200,
)
async def export_account_data(
    principal: UserPrincipal = Depends(require_user_scope),
    _: None = Depends(validate_user_id_against_principal),
) -> JSONResponse:
    """Return a machine-readable JSON archive of all user-owned data.

    Calls ``export_user_data`` on every registered ``AccountDataHandler``.
    The platform hook ``on_account_export_ready`` is called after all module
    exports are collected — the host can inject platform-owned records
    (billing history, subscription snapshots, auth identity) at that point.
    """
    app_id = principal.app_id
    user_id = principal.user_id

    logger.info("ACCOUNT_EXPORT_STARTED: app_id=%s user_id=%s", app_id, user_id)

    db = _get_db()
    export_payload: dict[str, Any] = {
        "_meta": {
            "app_id": app_id,
            "user_id": user_id,
            "schema_version": "mozaiks.account_export.v1",
        }
    }

    module_export = await account_data_registry.export_all(
        app_id=app_id,
        user_id=user_id,
        db=db,
    )
    export_payload.update(module_export)

    # Platform hook — hosted product can inject billing/subscription records.
    hooks = get_platform_hooks()
    on_ready = getattr(hooks, "call_on_account_export_ready", None)
    if callable(on_ready):
        try:
            export_payload = await on_ready(
                app_id=app_id,
                user_id=user_id,
                export_payload=export_payload,
            )
        except Exception as exc:
            logger.warning("ACCOUNT_EXPORT_HOOK_ERROR: %s", exc)

    logger.info(
        "ACCOUNT_EXPORT_COMPLETE: app_id=%s user_id=%s keys=%s",
        app_id,
        user_id,
        [k for k in export_payload if not k.startswith("_")],
    )

    return JSONResponse(export_payload)
