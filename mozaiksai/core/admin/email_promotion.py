# ==============================================================================
# FILE: mozaiksai/core/admin/email_promotion.py
# DESCRIPTION: Email-based admin role promotion.
#
# If a logged-in user's email is listed in app.json "admins" and the identity
# provider verified that email (a JSON-true email_verified claim, see
# UserClaims.email_verified), they receive the "admin" role at request time.
# This is the primary way app builders access the admin portal. An unverified
# email is never promoted: with self-registration, anyone could sign up with an
# admin's address first.
#
# Priority order for admin access:
#   1. Auth provider already issued the "admin" role in the JWT  (production)
#   2. Verified email matches app.json admins                   (default path)
#   3. AUTH_ANON_ROLES=admin,user in .env                        (dev/no-auth)
#
# is_app_admin is shared by the admin gate and the authenticated profile's role
# projection so backend access and frontend navigation use the same decision.
#
# Matching is ASCII-only. Case folding non-ASCII text can turn one address into
# another (the Kelvin sign U+212A lowercases to "k"), so a non-ASCII address,
# in the token or in app.json, never matches.
# ==============================================================================
from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mozaiksai.core.auth import UserPrincipal

from logs.logging_config import get_core_logger
from mozaiksai.core.admin.paths import resolve_admin_app_root

logger = get_core_logger("admin.email_promotion")


def get_admin_emails() -> list[str]:
    """
    Return the normalised admin email list from app.json.

    Checks the active app root, defaulting to the local App Zero app root.
    Returns an empty list if app.json is missing or malformed — callers
    treat an empty list as "no email promotion configured".
    """
    path = _resolve_app_config_path()
    if not path.exists():
        return []
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
        raw: list[str] = config.get("admins") or []
        return [e.lower().strip() for e in raw if isinstance(e, str) and e.strip() and e.isascii()]
    except Exception as e:
        logger.warning("[admin] Could not read admins from app.json: %s", e)
        return []


def is_admin_by_email(email: str | None, *, admin_emails: Iterable[str] | None = None) -> bool:
    """Return True if email is in the app.json admins allowlist.

    Membership alone grants nothing: the caller must also require that the
    identity provider verified the email (``UserPrincipal.email_verified``).
    A non-ASCII address never matches (see the module header).
    """
    if not email or not email.isascii():
        return False
    allowed = get_admin_emails() if admin_emails is None else admin_emails
    return email.lower().strip() in {
        value.lower().strip() for value in allowed if isinstance(value, str) and value.strip() and value.isascii()
    }


def _resolve_app_config_path() -> Path:
    """Find app.json from the active platform root."""
    return resolve_admin_app_root() / "app.json"


def is_app_admin(principal: UserPrincipal) -> bool:
    """Evaluate app admin access using the authenticated runtime principal."""
    return principal.has_role("admin") or (
        (principal.email_verified or principal.has_local_development_access)
        and is_admin_by_email(principal.email)
    )
