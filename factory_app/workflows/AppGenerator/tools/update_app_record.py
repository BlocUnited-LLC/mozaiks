"""
update_app_record — called at the end of the AppGenerator pipeline.

Updates the app lifecycle record to 'review' once the bundle is written, so the
Studio reflects a completed build that is ready for inspection.

Best-effort: never raises. Studio owns app-registry management endpoints; this
tool intentionally does not call the permissioned module action route.
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
    artifact_version_id: str | None = None,
    workflow_sequence: str | None = None,
    active_chat_id: str | None = None,
    active_workflow_id: str | None = None,
    current_build_run: dict[str, object] | None = None,
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
        if artifact_version_id:
            payload["artifact_version_id"] = artifact_version_id
        if workflow_sequence:
            payload["workflow_sequence"] = workflow_sequence
        if active_chat_id:
            payload["active_chat_id"] = active_chat_id
        if active_workflow_id:
            payload["active_workflow_id"] = active_workflow_id
        if current_build_run:
            payload["current_build_run"] = current_build_run
        url = f"{_API_BASE}/api/studio/apps/{record_id}/status"
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.put(url, json=payload)
        logger.info("Updated build registry record %s → %s", record_id, status)
    except Exception as exc:
        logger.debug("Studio app registry status update failed (non-fatal): %s", exc)

