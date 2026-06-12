"""
update_app_record — called at the end of the AppGenerator pipeline.

Updates the app lifecycle record to 'review' once the bundle is written, so the
Studio reflects a completed build that is ready for inspection.

Best-effort: never raises.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_API_BASE = os.getenv("MOZAIKSAI_URL") or os.getenv("VITE_API_URL") or "http://localhost:8000"


async def update_build_status(
    *,
    build_registry_id: str,
    status: str,
    bundle_path: str | None = None,
) -> None:
    """Update a hosted build-registry record status. Best-effort — never raises."""
    record_id = str(build_registry_id or "")
    if not record_id:
        return
    try:
        import httpx
        payload: dict = {"build_registry_id": record_id, "status": status}
        if bundle_path:
            payload["bundle_path"] = bundle_path
        url = f"{_API_BASE}/api/modules/app_registry/update_build_status"
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(url, json=payload)
        logger.info("Updated build registry record %s → %s", record_id, status)
    except Exception as exc:
        logger.debug("app_registry status update failed (non-fatal): %s", exc)

