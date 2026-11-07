"""Errors a plugin sees when it asks for the calling user's connected account."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

ACCOUNT_CONNECTION_REQUIRED = "ACCOUNT_CONNECTION_REQUIRED"
#: The built-in profile page where users connect and disconnect their accounts.
CONNECTED_ACCOUNTS_TAB = "connected-accounts"
CONNECTED_ACCOUNTS_ROUTE = f"/me?tab={CONNECTED_ACCOUNTS_TAB}"


def connect_route(connection_id: str) -> str:
    """Where to send the user to connect *connection_id*."""
    return "/me?" + urlencode({"tab": CONNECTED_ACCOUNTS_TAB, "connect": connection_id})


class AccountConnectionRequired(Exception):
    """The calling user has not connected (or must reconnect) the account an action needs.

    ``reason`` is one of ``not_connected``, ``reconnect_required``,
    ``missing_scopes`` or ``unavailable`` (the app does not offer this
    connection right now). The module executor reports it as
    ``ACCOUNT_CONNECTION_REQUIRED`` with :meth:`to_error_metadata` as extra
    data, so a page can offer a Connect button and a workflow can tell the user
    where to connect.
    """

    error_code = ACCOUNT_CONNECTION_REQUIRED

    def __init__(
        self,
        *,
        connection_id: str,
        provider: str,
        label: str,
        reason: str,
        missing_scopes: tuple[str, ...] = (),
    ) -> None:
        self.connection_id = connection_id
        self.provider = provider
        self.label = label
        self.reason = reason
        self.missing_scopes = missing_scopes
        messages = {
            "not_connected": f"Connect your {label} account to use this.",
            "reconnect_required": f"Your {label} connection has expired or was revoked. Connect it again to use this.",
            "missing_scopes": f"Connect {label} again to allow this app the additional access it needs.",
            "unavailable": f"This app does not offer {label} right now.",
        }
        super().__init__(messages.get(reason, f"Connect your {label} account to use this."))

    def to_error_metadata(self) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "error_code": self.error_code,
            "connection_id": self.connection_id,
            "provider": self.provider,
            "label": self.label,
            "reason": self.reason,
            "recovery_message": str(self),
        }
        if self.reason != "unavailable":
            metadata["connect_route"] = connect_route(self.connection_id)
        if self.missing_scopes:
            metadata["missing_scopes"] = list(self.missing_scopes)
        return metadata


class AccountAccessDenied(PermissionError):
    """Only the signed-in user's own dispatch may use their connected account.

    Raised for anonymous visitors, server-owned dispatches (event reactions,
    framework or operator work) and any request whose actor is not the user
    the context names. It is a PermissionError, so the executor reports
    ``PERMISSION_DENIED``.
    """


class AccountProviderUnavailable(RuntimeError):
    """The provider could not be reached or answered with an error; nothing about the connection changed."""


class UnknownAccountConnection(LookupError):
    """The app does not declare (or offer) the requested connection."""


__all__ = [
    "ACCOUNT_CONNECTION_REQUIRED",
    "CONNECTED_ACCOUNTS_ROUTE",
    "CONNECTED_ACCOUNTS_TAB",
    "AccountAccessDenied",
    "AccountConnectionRequired",
    "AccountProviderUnavailable",
    "UnknownAccountConnection",
    "connect_route",
]
