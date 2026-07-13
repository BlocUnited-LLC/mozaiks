"""Account data management protocols.

Defines the ``AccountDataHandler`` Protocol that app modules implement to
participate in platform-level account deletion and data portability export.

Architecture
------------
The OSS runtime defines the contract.  App modules (generated or hand-authored)
register a handler for their domain-owned data.  The platform dispatches to all
registered handlers when a user requests account deletion or a data export.

Usage in a generated or hand-authored module::

    from mozaiksai.core.account import AccountDataHandler, account_data_registry

    class MyModuleAccountHandler:
        def __init__(self, db):
            self._db = db

        async def delete_user_data(self, *, app_id: str, user_id: str) -> dict:
            deleted = await self._db["my_collection"].delete_many(
                {"app_id": app_id, "user_id": user_id}
            )
            return {"deleted_count": deleted.deleted_count}

        async def export_user_data(self, *, app_id: str, user_id: str) -> dict:
            cursor = self._db["my_collection"].find(
                {"app_id": app_id, "user_id": user_id}, {"_id": 0}
            )
            return {"my_module_records": await cursor.to_list(length=None)}

    # register at module startup
    account_data_registry.register("my_module", MyModuleAccountHandler)

The platform router calls ``account_data_registry.delete_all`` /
``account_data_registry.export_all`` and aggregates the results.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class AccountDataHandler(Protocol):
    """Protocol for modules that own user-scoped data.

    Implement both methods to participate in GDPR account deletion and
    data portability export.  Either method may be omitted if the module
    has no user-scoped data to delete or export — but do not register a
    handler that is a no-op for both; simply omit the registration.

    Both methods receive ``app_id`` (the tenant/app scope) and ``user_id``
    (the authenticated user).  Implementations must scope all queries
    through both values using ``build_app_scope_filter`` and per-user
    filters.  Never delete or export records across tenants.
    """

    async def delete_user_data(
        self,
        *,
        app_id: str,
        user_id: str,
    ) -> dict[str, Any]:
        """Permanently delete all user-scoped data owned by this module.

        Must be idempotent — calling it twice on the same user must not
        raise an error.

        Returns a dict with at minimum ``{"deleted_count": int}`` so callers
        can log confirmation evidence.  Additional keys are allowed.
        """
        ...

    async def export_user_data(
        self,
        *,
        app_id: str,
        user_id: str,
    ) -> dict[str, Any]:
        """Return all user-scoped data owned by this module as a JSON-safe dict.

        The caller (platform or app export service) merges all module exports
        into a single portable archive.  Keys should use the module_id as a
        namespace prefix to avoid collisions, e.g.::

            return {"messages_threads": [...], "messages_dms": [...]}

        Return an empty dict (not None) when there is no data to export.
        """
        ...
