# ==============================================================================
# FILE: mozaikscore/core/auth.py
# DESCRIPTION: Authentication and authorization dependencies for mozaikscore.
#              Provides admin-guard and internal API key validation for
#              /__mozaiks/admin/* routes and internal service-to-service calls.
# ==============================================================================
import os
import hmac
import logging

from fastapi import Depends, HTTPException, Request
from starlette import status

logger = logging.getLogger("mozaikscore.auth")

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
ADMIN_KEY = os.getenv("MOZAIKS_APP_ADMIN_KEY", "")
INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY", "")
ENV = os.getenv("ENV", "development")


# ---------------------------------------------------------------------------
# Auth dependency — import from mozaiksai with dev fallback
# ---------------------------------------------------------------------------
def _get_auth_dependency():
    try:
        from mozaiksai.core.auth.dependencies import require_user
        return require_user
    except ImportError:
        logger.warning("mozaiksai auth not available — using dev stub")

        async def _dev_user():
            return {
                "user_id": "dev_user",
                "username": "dev",
                "roles": ["admin"],
                "is_superadmin": True,
            }

        return _dev_user


get_current_user = _get_auth_dependency()


# ---------------------------------------------------------------------------
# Admin key guard — validates X-Mozaiks-App-Admin-Key header
# ---------------------------------------------------------------------------
async def require_admin_key(request: Request) -> None:
    """Validate the admin API key header. Raises 403 on failure."""
    if ENV == "development" and not ADMIN_KEY:
        return  # Skip in dev when no key is configured

    provided = request.headers.get("X-Mozaiks-App-Admin-Key", "")
    if not ADMIN_KEY or not provided:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin key required",
        )
    if not hmac.compare_digest(provided, ADMIN_KEY):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid admin key",
        )


# ---------------------------------------------------------------------------
# Internal API key guard — validates X-Internal-API-Key header
# ---------------------------------------------------------------------------
async def require_internal_api_key(request: Request) -> None:
    """Validate the internal API key header for service-to-service calls."""
    if ENV == "development" and not INTERNAL_API_KEY:
        return  # Skip in dev when no key is configured

    provided = request.headers.get("X-Internal-API-Key", "")
    if not INTERNAL_API_KEY or not provided:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Internal API key required",
        )
    if not hmac.compare_digest(provided, INTERNAL_API_KEY):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid internal API key",
        )


# ---------------------------------------------------------------------------
# Combined guard — admin key OR internal key
# ---------------------------------------------------------------------------
async def require_admin_or_internal(request: Request) -> None:
    """Accept either admin key or internal API key. Raises 403 if neither valid."""
    if ENV == "development" and not ADMIN_KEY and not INTERNAL_API_KEY:
        return  # Skip in dev when no keys configured

    admin_key = request.headers.get("X-Mozaiks-App-Admin-Key", "")
    internal_key = request.headers.get("X-Internal-API-Key", "")

    admin_ok = bool(ADMIN_KEY and admin_key and hmac.compare_digest(admin_key, ADMIN_KEY))
    internal_ok = bool(
        INTERNAL_API_KEY
        and internal_key
        and hmac.compare_digest(internal_key, INTERNAL_API_KEY)
    )

    if not admin_ok and not internal_ok:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin or internal API key required",
        )


# ---------------------------------------------------------------------------
# Role-based admin check
# ---------------------------------------------------------------------------
async def require_admin_user(user: dict = Depends(get_current_user)) -> dict:
    """Require authenticated user with admin role or superadmin flag."""
    roles = user.get("roles", [])
    if user.get("is_superadmin") or "admin" in roles:
        return user
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Admin access required",
    )
