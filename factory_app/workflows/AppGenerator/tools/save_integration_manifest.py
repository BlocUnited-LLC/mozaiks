"""save_integration_manifest — AppGenerator factory tool.

Called by IntegrationReadinessAgent after it resolves integration needs for a build.
Persists declarations to the workspace_integrations module's AppIntegrationDeclarations
collection so the per-app integrations tab and workspace catalog page can surface
what each app declared it needs, even after the build session ends.

Uses direct Python imports (same pattern as check_workspace_integrations) — no HTTP
call needed and no auth issues since the tool runs in-process.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from factory_app.app.modules.workspace_integrations.backend.service import (
    WorkspaceIntegrationsService,
)

logger = logging.getLogger(__name__)

_VALID_CONNECTOR_STATUSES = frozenset({"ready", "not_configured", "partial"})


def _context_get(context_variables: Any, key: str, default: Any = None) -> Any:
    if context_variables is None:
        return default
    if hasattr(context_variables, "get"):
        return context_variables.get(key, default)
    return default


def _connector_status_from_inventory(service: str, connector_inventory: dict[str, Any]) -> str:
    """Derive per-service connector vault status from the readiness inventory."""
    ready = set(connector_inventory.get("ready_services") or [])
    if service in ready:
        return "ready"
    return "not_configured"


async def save_integration_manifest(
    context_variables: Any = None,
) -> dict[str, Any]:
    """Persist the resolved integration manifest from this build to the declarations store.

    Routes through WorkspaceIntegrationsService.declare_app_integration_needs so that
    catalog enrichment (setup lanes, managed defaults, workspace status) is applied
    consistently and the service layer remains the single path to the declarations store.

    Reads from context_variables:
    - app_id: the app being built
    - integration_needs: list of {service, kind, purpose, required_at, optional, ...}
    - connector_inventory: result from collect_missing_connector_needs
    """
    app_id = str(_context_get(context_variables, "app_id") or "").strip()
    if not app_id:
        logger.debug("save_integration_manifest: no app_id in context, skipping")
        return {"saved": 0, "skipped": True, "reason": "no_app_id"}

    integration_needs: list[dict[str, Any]] = [
        need for need in (_context_get(context_variables, "integration_needs") or []) if isinstance(need, dict)
    ]
    connector_inventory: dict[str, Any] = _context_get(context_variables, "connector_inventory") or {}
    declared_at = datetime.now(UTC).isoformat()

    # Enrich each need with its current connector vault status before handing to the service.
    enriched: list[dict[str, Any]] = []
    for need in integration_needs:
        service = str(need.get("service") or "").strip()
        if not service:
            continue
        enriched.append({
            **need,
            "connector_status": _connector_status_from_inventory(service, connector_inventory),
        })

    if not enriched:
        return {"saved": 0, "app_id": app_id}

    try:
        svc = WorkspaceIntegrationsService()
        result = await svc.declare_app_integration_needs(
            app_id=app_id,
            needs=enriched,
            declared_at=declared_at,
        )
        saved_count = result.get("saved", 0)
        logger.info("save_integration_manifest: saved %d declarations for app %s", saved_count, app_id)
        return {"saved": saved_count, "app_id": app_id}
    except Exception as exc:
        # Best-effort — never block the build pipeline over a manifest write failure.
        logger.warning("save_integration_manifest: non-fatal error for app %s: %s", app_id, exc)
        return {"saved": 0, "app_id": app_id, "error": str(exc)}


__all__ = ["save_integration_manifest"]
