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

    for spec in catalog_scope:
        status, missing_secrets = derive_status(spec["required_secrets"])
        entry: dict[str, Any] = {
            "id": spec["id"],
            "name": spec["name"],
            "category": spec["category"],
            "status": status,
        }
        if status == "configured":
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
