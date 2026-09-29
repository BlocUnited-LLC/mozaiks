"""Account data handler registry.

Provides a process-global registry that app modules register
``AccountDataHandler`` implementations with.  The platform dispatches
account deletion and export requests to all registered handlers.

Thread-safety: handlers are registered at startup (before any async work
begins) and read-only thereafter, so no locking is required.
"""
from __future__ import annotations

import inspect
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

        *handler* may be a class (instantiated per request) or a
        pre-constructed instance.  A class receives, by keyword, the resources
        its constructor declares: ``db`` (the app database) and/or
        ``persistence`` (the requesting account's runtime-scoped persistence).
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
        persistence: Any = None,
    ) -> dict[str, Any]:
        """Call ``delete_user_data`` on every registered handler.

        Returns a summary dict ``{module_id: result_or_error}`` for audit
        logging.  A failed handler is logged as a warning but does not
        prevent other handlers from running — all deletions are attempted.
        """
        results: dict[str, Any] = {}
        for module_id, handler in self._handlers.items():
            try:
                instance = _resolve_instance(handler, db, persistence)
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
        persistence: Any = None,
    ) -> dict[str, Any]:
        """Call ``export_user_data`` on every registered handler.

        Returns a merged dict of all exported records.  Module IDs are used
        as top-level namespace keys so collisions are avoided.
        """
        export: dict[str, Any] = {}
        for module_id, handler in self._handlers.items():
            try:
                instance = _resolve_instance(handler, db, persistence)
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


def account_handler_arguments(handler_cls: type, *, db: Any, persistence: Any) -> dict[str, Any]:
    """The keyword arguments a handler class is constructed with.

    ``persistence`` is passed only to a constructor that names it; every other
    constructor, including ``__init__(self, **kwargs)``, receives ``db`` as it
    always has. The loader binds the same arguments to validate a handler.
    """
    parameters = inspect.signature(handler_cls).parameters
    named = {
        name for name, parameter in parameters.items()
        if parameter.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    }
    accepts_any = any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values())
    arguments: dict[str, Any] = {}
    if "persistence" not in named or "db" in named or accepts_any:
        arguments["db"] = db
    if "persistence" in named:
        arguments["persistence"] = persistence
    return arguments


def _resolve_instance(
    handler: type[AccountDataHandler] | AccountDataHandler,
    db: Any,
    persistence: Any = None,
) -> AccountDataHandler:
    """Return a handler instance.

    If *handler* is a class, construct it with ``account_handler_arguments``.
    If it's already an instance, return it directly.
    """
    if isinstance(handler, type):
        arguments = account_handler_arguments(handler, db=db, persistence=persistence)
        if "persistence" in arguments and persistence is None:
            raise RuntimeError("account handler requires runtime persistence, but none was supplied")
        return handler(**arguments)
    return handler


# Process-global singleton used by platform routes and app modules.
account_data_registry = AccountDataRegistry()
