"""Connected accounts in the platform's account deletion and data export."""

from __future__ import annotations

from typing import Any

from logs.logging_config import get_core_logger

from .service import ConnectedAccountsService, get_connected_accounts_service

logger = get_core_logger("connected_accounts")

#: The ``account_data_registry`` key; dotted so it can never collide with a module id.
ACCOUNT_DATA_HANDLER_ID = "platform.connected_accounts"


class ConnectedAccountsDataHandler:
    """Deleting an account revokes every grant its user gave the app; an export lists them, never a token.

    The host serves one loaded app, and every grant here belongs to it, so the
    user's grants in that app are what an account deletion removes. Without a
    *service*, the host's current service is used at call time.
    """

    def __init__(self, service: ConnectedAccountsService | None = None) -> None:
        self._service = service

    def _resolve(self, app_id: str) -> ConnectedAccountsService | None:
        service = self._service or get_connected_accounts_service()
        if service is not None and app_id and app_id != service.app_id:
            logger.info(
                "CONNECTED_ACCOUNTS_ACCOUNT_SCOPE: request app_id=%s, grants are held for loaded app_id=%s",
                app_id, service.app_id,
            )
        return service

    async def delete_user_data(self, *, app_id: str, user_id: str) -> dict[str, Any]:
        service = self._resolve(app_id)
        if service is None:
            return {"deleted_count": 0}
        return await service.delete_user_data(user_id=user_id)

    async def export_user_data(self, *, app_id: str, user_id: str) -> dict[str, Any]:
        service = self._resolve(app_id)
        if service is None:
            return {}
        return {"connected_accounts": await service.export_user_data(user_id=user_id)}


__all__ = ["ACCOUNT_DATA_HANDLER_ID", "ConnectedAccountsDataHandler"]
