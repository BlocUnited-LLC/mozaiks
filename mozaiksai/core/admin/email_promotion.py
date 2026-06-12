# ==============================================================================
# FILE: mozaiksai/core/admin/email_promotion.py
# DESCRIPTION: Email-based admin role promotion.
#
# If a logged-in user's email is listed in app.json "admins", they
# receive the "admin" role at request time — no auth provider configuration
# required. This is the primary way app builders access the admin portal.
#
# Priority order for admin access:
#   1. Auth provider already issued the "admin" role in the JWT  (production)
#   2. Email matches app.json admins                            (default path)
#   3. AUTH_ANON_ROLES=admin,user in .env                        (dev/no-auth)
# ==============================================================================
from __future__ import annotations

import json
from pathlib import Path

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
        return [e.lower().strip() for e in raw if isinstance(e, str) and e.strip()]
    except Exception as e:
        logger.warning(f"[admin] Could not read admins from app.json: {e}")
        return []


def is_admin_by_email(email: str | None) -> bool:
    """Return True if email is in the app.json admins allowlist."""
    if not email:
        return False
    return email.lower().strip() in get_admin_emails()


def _resolve_app_config_path() -> Path:
    """Find app.json from the active platform root."""
    return resolve_admin_app_root() / "app.json"
