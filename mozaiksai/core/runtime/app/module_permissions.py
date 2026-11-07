"""Resolve ordinary module permissions from the loaded app's authorization contract."""

from __future__ import annotations

from typing import Any

from mozaiksai.core.admin.email_promotion import is_admin_by_email
from mozaiksai.core.auth.dependencies import UserPrincipal
from mozaiksai.core.auth.websocket_auth import WebSocketUser
from mozaiksai.core.runtime.app.auth_contract import AppAuthContract


def resolve_module_permissions(principal: Any, *, app: Any) -> list[str]:
    """Combine token scopes and held-role grants before deployer scope hooks.

    App grants never manufacture identity, tenant membership, or a dispatch
    bypass. Anonymous visitor roles cannot grant privileges. Admin email
    promotion uses the same verified-email rule as the admin portal and the
    allowlist belonging to this loaded app, never another process-global root.
    """
    if principal is None:
        return []
    permissions = list(principal.scopes)
    if not isinstance(principal, (UserPrincipal, WebSocketUser)):
        return list(dict.fromkeys(permissions))
    development_access = principal.has_local_development_access
    if not (principal.is_authenticated or development_access):
        return list(dict.fromkeys(permissions))
    state = getattr(app, "state", None)
    contract = getattr(state, "auth_contract", None)
    if not isinstance(contract, AppAuthContract):
        return list(dict.fromkeys(permissions))
    roles = set(principal.roles)
    if (principal.email_verified or development_access) and is_admin_by_email(
        principal.email, admin_emails=getattr(state, "app_admin_emails", ()),
    ):
        roles.add("admin")
    for role in contract.authorization.roles:
        if role.id in roles:
            permissions.extend(role.permissions)
    return list(dict.fromkeys(permissions))
