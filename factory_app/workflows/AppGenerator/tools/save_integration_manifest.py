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

from factory_app.app.modules.workspace_integrations.backend.policy import derive_status
from factory_app.app.modules.workspace_integrations.backend.repo import IntegrationDeclarationsRepo
from factory_app.app.modules.workspace_integrations.backend.schemas import (
    CATALOG_BY_ID,
    build_declaration_document,
)

logger = logging.getLogger(__name__)

_VALID_CONNECTOR_STATUSES = frozenset({"ready", "not_configured", "partial"})


def _context_get(context_variables: Any, key: str, default: Any = None) -> Any:
    if context_variables is None:
        return default
    if hasattr(context_variables, "get"):
        return context_variables.get(key, default)
    return default


def _resolve_connector_status(service: str, connector_inventory: dict[str, Any]) -> str:
    """Derive per-service connector vault status from the inventory dict."""
    ready = set(connector_inventory.get("ready_services") or [])
    missing = set(connector_inventory.get("missing_required_services") or [])
    if service in ready:
        return "ready"
    if service in missing:
        return "not_configured"
    # partial: in required_services but not in either bucket
    required = set(connector_inventory.get("required_services") or [])
    if service in required:
        return "not_configured"
    return "not_configured"


def _resolve_workspace_status(catalog_id: str | None) -> str | None:
    """Derive current workspace catalog status for a catalog-matched service."""
    if not catalog_id or catalog_id not in CATALOG_BY_ID:
        return None
    spec = CATALOG_BY_ID[catalog_id]
    status, _ = derive_status(spec["required_secrets"])
    return status


async def save_integration_manifest(
    context_variables: Any = None,
) -> dict[str, Any]:
    """Persist the resolved integration manifest from this build to the declarations store.

    Reads from context_variables:
    - app_id: the app being built
    - integration_needs: list of {service, kind, purpose, required_at, optional, ...}
    - connector_inventory: result from collect_missing_connector_needs
    """
    app_id = str(_context_get(context_variables, "app_id") or "").strip()
    if not app_id:
        logger.debug("save_integration_manifest: no app_id in context, skipping")
        return {"saved": 0, "skipped": True, "reason": "no_app_id"}

    integration_needs: list[dict[str, Any]] = _context_get(context_variables, "integration_needs") or []
    connector_inventory: dict[str, Any] = _context_get(context_variables, "connector_inventory") or {}
    declared_at = datetime.now(UTC).isoformat()

    declarations: list[dict[str, Any]] = []
    for need in integration_needs:
        if not isinstance(need, dict):
            continue
        service = str(need.get("service") or "").strip()
        if not service:
            continue

        # Resolve catalog match — service names map directly to catalog IDs
        # (e.g. "twilio" → catalog entry "twilio"). Unrecognized services have no catalog_id.
        catalog_id = service if service in CATALOG_BY_ID else None
        workspace_status = _resolve_workspace_status(catalog_id)
        connector_status = _resolve_connector_status(service, connector_inventory)

        declarations.append(
            build_declaration_document(
                app_id=app_id,
                service=service,
                catalog_id=catalog_id,
                display_name=need.get("display_name") or need.get("provider") or service,
                kind=str(need.get("kind") or "api_key"),
                purpose=need.get("purpose"),
                required_at=str(need.get("required_at") or "runtime"),
                optional=bool(need.get("optional", False)),
                workspace_status=workspace_status,
                connector_status=connector_status,
                declared_at=declared_at,
            )
        )

    if not declarations:
        return {"saved": 0, "app_id": app_id}

    try:
        repo = IntegrationDeclarationsRepo()
        saved = await repo.upsert_declarations(app_id=app_id, declarations=declarations)
        logger.info("save_integration_manifest: saved %d declarations for app %s", len(saved), app_id)
        return {"saved": len(saved), "app_id": app_id}
    except Exception as exc:
        # Best-effort — never block the build pipeline over a manifest write failure.
        logger.warning("save_integration_manifest: non-fatal error for app %s: %s", app_id, exc)
        return {"saved": 0, "app_id": app_id, "error": str(exc)}


__all__ = ["save_integration_manifest"]
