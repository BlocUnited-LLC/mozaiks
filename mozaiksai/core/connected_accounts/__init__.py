"""End users' connected third-party accounts (Google Calendar, Gmail and other plugins).

Plugins declare what they need (:mod:`.definitions`), users connect their own
accounts through the host routes, the app's admins decide which declared
connections the app offers, and plugin actions obtain a token only for the
calling user (:func:`account_access_token`).
"""

from .account_data import ACCOUNT_DATA_HANDLER_ID, ConnectedAccountsDataHandler
from .definitions import (
    AccountConnection,
    AccountConnectionsConfigError,
    load_account_connections,
    load_module_account_connections,
)
from .errors import (
    ACCOUNT_CONNECTION_REQUIRED,
    CONNECTED_ACCOUNTS_ROUTE,
    CONNECTED_ACCOUNTS_TAB,
    AccountAccessDenied,
    AccountConnectionRequired,
    AccountProviderUnavailable,
    UnknownAccountConnection,
    connect_route,
)
from .providers import google_api_base_url
from .runtime import account_access_token, account_request
from .service import (
    ConnectedAccountsService,
    configure_connected_accounts,
    get_connected_accounts_service,
)

__all__ = [
    "ACCOUNT_CONNECTION_REQUIRED",
    "ACCOUNT_DATA_HANDLER_ID",
    "CONNECTED_ACCOUNTS_ROUTE",
    "CONNECTED_ACCOUNTS_TAB",
    "AccountAccessDenied",
    "AccountConnection",
    "AccountConnectionRequired",
    "AccountConnectionsConfigError",
    "AccountProviderUnavailable",
    "ConnectedAccountsDataHandler",
    "ConnectedAccountsService",
    "UnknownAccountConnection",
    "account_access_token",
    "account_request",
    "configure_connected_accounts",
    "connect_route",
    "get_connected_accounts_service",
    "google_api_base_url",
    "load_account_connections",
    "load_module_account_connections",
]
