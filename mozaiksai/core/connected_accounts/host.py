"""What the platform host wires once per loaded app."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from logs.logging_config import get_core_logger
from mozaiksai.core.account import account_data_registry

from .account_data import ACCOUNT_DATA_HANDLER_ID, ConnectedAccountsDataHandler
from .errors import CONNECTED_ACCOUNTS_TAB
from .service import (
    ConnectedAccountsService,
    configure_connected_accounts,
    get_connected_accounts_service,
)

logger = get_core_logger("connected_accounts")


def configure_for_app(*, app_id: str, app_root: str | Path, modules: Iterable[Any]) -> ConnectedAccountsService:
    """Install the service for the loaded app's modules (objects with ``name`` and ``path``).

    The account-data handler is registered even when no plugin declares a
    connection, so deleting an account still revokes grants that outlived an
    uninstalled plugin.
    """
    service = ConnectedAccountsService(
        app_id=app_id,
        app_root=app_root,
        modules=[(str(module.name), Path(module.path)) for module in modules],
    )
    configure_connected_accounts(service)
    if ACCOUNT_DATA_HANDLER_ID not in account_data_registry.registered_module_ids():
        account_data_registry.register(ACCOUNT_DATA_HANDLER_ID, ConnectedAccountsDataHandler())
    if service.declared:
        logger.info(
            "CONNECTED_ACCOUNTS_READY: app_id=%s connections=%s",
            app_id, [connection.id for connection in service.declared],
        )
    return service


def profile_page() -> dict[str, Any] | None:
    """The built-in profile page where users manage their connections, when a plugin declares one."""
    service = get_connected_accounts_service()
    if service is None or not service.declared:
        return None
    return {
        "id": CONNECTED_ACCOUNTS_TAB,
        "label": "Connected accounts",
        "route": CONNECTED_ACCOUNTS_TAB,
        "section": "settings",
        "order": 9000,
        "renderer": "custom_component",
        "component": "ConnectedAccountsTab",
        "visibility": "owner_only",
        "data": None,
        "error": None,
        "source": "platform_builtin",
    }


__all__ = ["configure_for_app", "profile_page"]
