"""Connector contract used by AgentGenerator API-key tools.

The local generator runtime does not own a production secret vault. These
helpers intentionally fail closed so API keys can be used for the current tool
call without silently persisting secrets in MongoDB.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Dict, Optional


async def get_connector_status(app_id: str, service: str, logger: Optional[Any] = None) -> Dict[str, Any]:
    if logger:
        logger.info("Connector vault unavailable; treating %s as unconnected for app %s", service, app_id)
    return {
        "exists": False,
        "status": "missing",
        "connector": None,
        "days_until_expiry": None,
    }


async def get_secret_for_e2b(app_id: str, service: str, logger: Optional[Any] = None) -> Dict[str, Any]:
    if logger:
        logger.info("Connector secret lookup skipped; no local secret vault is configured for %s", service)
    return {
        "success": False,
        "service": service,
        "secret_value": None,
        "error": "Connector secret vault is not configured in the local generator runtime.",
    }


async def store_connector(
    *,
    app_id: str,
    user_id: str,
    service: str,
    secret_value: str,
    display_name: Optional[str] = None,
    ttl_days: int = 30,
    logger: Optional[Any] = None,
) -> Dict[str, Any]:
    if logger:
        logger.info("Connector storage skipped for %s; production secret vault is not configured", service)
    return {
        "success": False,
        "app_id": app_id,
        "user_id": user_id,
        "service": service,
        "display_name": display_name or service,
        "expires_at": None,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "error": "Connector secret vault is not configured in the local generator runtime.",
    }


__all__ = ["get_connector_status", "get_secret_for_e2b", "store_connector"]
