"""AppGenerator tool: check which integrations are already configured at workspace level.

Called by AppPlanAgent early in planning. Returns structured availability data so the
agent can:
  - Wire already-configured integrations automatically (no user prompt needed)
  - Pass missing required integrations to record_integration_need so the downstream
    IntegrationReadinessAgent can collect credentials inline

This tool is read-only and never returns secret values — only presence booleans.
"""

from __future__ import annotations

from typing import Any

from factory_app.app.modules.workspace_integrations.backend.policy import derive_status
from factory_app.app.modules.workspace_integrations.backend.schemas import (
    CATALOG_BY_ID,
    INTEGRATIONS_CATALOG,
)
from mozaiksai.core.data.persistence import ConnectorStore
from mozaiksai.core.workflow.generator_support.connector_service import get_connector_inventory


def _context_get(context_variables: Any, key: str, default: Any = None) -> Any:
    if context_variables is None:
        return default
    getter = getattr(context_variables, "get", None)
    if callable(getter):
        try:
            return getter(key, default)
        except TypeError:
            value = getter(key)
            return default if value is None else value
    data = getattr(context_variables, "data", None)
    if isinstance(data, dict):
        return data.get(key, default)
    if isinstance(context_variables, dict):
        return context_variables.get(key, default)
    return default


async def check_workspace_integrations(
    integration_ids: list[str] | None = None,
    context_variables: Any = None,
) -> dict[str, Any]:
    """Return workspace availability for the requested integration IDs.

    Args:
        integration_ids: List of catalog IDs to check (e.g. ["mozaikspay", "twilio"]).
                         Pass None or an empty list to return all catalog entries.

    Returns:
        {
            "available": [{"id": "mozaikspay", "name": "MozaiksPay", "status": "configured"}],
            "partial":   [{"id": "s3", "name": "AWS S3", "status": "partial",
                           "missing_secrets": ["AWS_S3_BUCKET"]}],
            "missing":   [{"id": "twilio", "name": "Twilio", "status": "missing",
                           "missing_secrets": [...],
                           "setup_url": "/integrations/twilio"}],
            "unknown":   [],
            "not_in_catalog": ["my_custom_service"],
        }
    """
    check_ids = [i.strip() for i in (integration_ids or []) if i and i.strip()]
    catalog_scope = (
        [CATALOG_BY_ID[i] for i in check_ids if i in CATALOG_BY_ID]
        if check_ids
        else INTEGRATIONS_CATALOG
    )
    not_in_catalog = [i for i in check_ids if i not in CATALOG_BY_ID]

    available: list[dict[str, Any]] = []
    partial: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []
    connector_by_service: dict[str, dict[str, Any]] = {}

    workspace_id = (
        _context_get(context_variables, "workspace_id")
        or _context_get(context_variables, "tenant_id")
    )
    if workspace_id and catalog_scope:
        try:
            inventory = await get_connector_inventory(
                scope=ConnectorStore.SCOPE_WORKSPACE,
                scope_id=str(workspace_id),
                required_services=[str(spec["id"]) for spec in catalog_scope],
            )
            connector_by_service = {
                str(connector.get("service") or ""): connector
                for connector in inventory.get("connectors", [])
                if isinstance(connector, dict) and connector.get("service")
            }
        except Exception:
            connector_by_service = {}

    for spec in catalog_scope:
        status, missing_secrets = derive_status(spec["required_secrets"])
        connector = connector_by_service.get(str(spec["id"]))
        if connector and connector.get("ready"):
            status = "configured"
            missing_secrets = []
        elif connector and status in {"missing", "unknown"}:
            status = "partial"
        entry: dict[str, Any] = {
            "id": spec["id"],
            "name": spec["name"],
            "category": spec["category"],
            "status": status,
        }
        if connector:
            health = connector.get("health") if isinstance(connector.get("health"), dict) else {}
            entry["source"] = "workspace_connector"
            entry["connector_status"] = "ready" if connector.get("ready") else "partial"
            entry["health_status"] = health.get("status") or "unknown"
            entry["health_check_supported"] = bool(health.get("health_check_supported"))
        if status == "configured":
            if "source" not in entry:
                entry["source"] = "environment"
            available.append(entry)
        elif status == "partial":
            partial.append({**entry, "missing_secrets": missing_secrets})
        elif status == "missing":
            missing.append(
                {
                    **entry,
                    "missing_secrets": missing_secrets,
                    "setup_url": f"/integrations/{spec['id']}",
                }
            )
        else:
            unknown.append(entry)

    return {
        "available": available,
        "partial": partial,
        "missing": missing,
        "unknown": unknown,
        "not_in_catalog": not_in_catalog,
    }
