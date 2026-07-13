"""Account data handler registry.

Provides a process-global registry that app modules register
``AccountDataHandler`` implementations with.  The platform dispatches
account deletion and export requests to all registered handlers.

Thread-safety: handlers are registered at startup (before any async work
begins) and read-only thereafter, so no locking is required.
"""
from __future__ import annotations

import logging
from typing import Any

from .protocol import AccountDataHandler

logger = logging.getLogger("mozaiks_app.account_data_registry")


class AccountDataRegistry:
    """Registry of per-module account data handlers."""

    def __init__(self) -> None:
        self._handlers: dict[str, type[AccountDataHandler] | AccountDataHandler] = {}

    def register(
        self,
        module_id: str,
        handler: type[AccountDataHandler] | AccountDataHandler,
    ) -> None:
        """Register a handler for *module_id*.

        *handler* may be a class (instantiated per-request with a ``db``
        argument) or a pre-constructed instance.  When a class is registered
        the platform passes the active ``db`` at dispatch time.
        """
        if module_id in self._handlers:
            logger.warning(
                "ACCOUNT_REGISTRY: overwriting existing handler for module_id=%s", module_id
            )
        self._handlers[module_id] = handler
        logger.debug("ACCOUNT_REGISTRY: registered handler for module_id=%s", module_id)

    def registered_module_ids(self) -> list[str]:
        return sorted(self._handlers.keys())

    async def delete_all(
        self,
        *,
        app_id: str,
        user_id: str,
        db: Any,
    ) -> dict[str, Any]:
        """Call ``delete_user_data`` on every registered handler.

        Returns a summary dict ``{module_id: result_or_error}`` for audit
        logging.  A failed handler is logged as a warning but does not
        prevent other handlers from running — all deletions are attempted.
        """
        results: dict[str, Any] = {}
        for module_id, handler in self._handlers.items():
            instance = _resolve_instance(handler, db)
            try:
                result = await instance.delete_user_data(app_id=app_id, user_id=user_id)
                results[module_id] = result
                logger.info(
                    "ACCOUNT_DELETE: module=%s app_id=%s user_id=%s result=%s",
                    module_id,
                    app_id,
                    user_id,
                    result,
                )
            except Exception as exc:
                logger.warning(
                    "ACCOUNT_DELETE_ERROR: module=%s app_id=%s user_id=%s error=%s",
                    module_id,
                    app_id,
                    user_id,
                    exc,
                )
                results[module_id] = {"error": str(exc)}
        return results

    async def export_all(
        self,
        *,
        app_id: str,
        user_id: str,
        db: Any,
    ) -> dict[str, Any]:
        """Call ``export_user_data`` on every registered handler.

        Returns a merged dict of all exported records.  Module IDs are used
        as top-level namespace keys so collisions are avoided.
        """
        export: dict[str, Any] = {}
        for module_id, handler in self._handlers.items():
            instance = _resolve_instance(handler, db)
            try:
                module_export = await instance.export_user_data(app_id=app_id, user_id=user_id)
                export.update(module_export or {})
            except Exception as exc:
                logger.warning(
                    "ACCOUNT_EXPORT_ERROR: module=%s app_id=%s user_id=%s error=%s",
                    module_id,
                    app_id,
                    user_id,
                    exc,
                )
                export[f"{module_id}.__error__"] = str(exc)
        return export


def _resolve_instance(
    handler: type[AccountDataHandler] | AccountDataHandler,
    db: Any,
) -> AccountDataHandler:
    """Return a handler instance.

    If *handler* is a class, construct it with ``db`` as a keyword argument.
    If it's already an instance, return it directly.
    """
    if isinstance(handler, type):
        return handler(db=db)  # type: ignore[call-arg]
    return handler


# Process-global singleton used by platform routes and app modules.
account_data_registry = AccountDataRegistry()
