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
_CORE_MOZAIKSPAY_REVENUE_MODELS = frozenset(
    {"subscriptions", "subscription", "usage_based", "usage", "metered", "pay_per_use"}
)
_MOZAIKSPAY_REQUIRED_FIELDS = [
    {
        "name": "api_base",
        "label": "Mozaiks Pay API Base URL",
        "type": "url",
        "required": True,
        "frontend_safe": True,
    },
    {
        "name": "client_id",
        "label": "Client ID",
        "type": "text",
        "required": True,
        "frontend_safe": True,
    },
    {
        "name": "client_secret",
        "label": "Client Secret",
        "type": "secret",
        "required": True,
        "frontend_safe": False,
    },
]


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


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _requires_default_mozaikspay_context(context_variables: Any) -> bool:
    """Return true when upstream state requires first-class subscription/usage billing."""
    subscription_contract = _context_get(context_variables, "subscription_contract")
    if isinstance(subscription_contract, dict) and bool(subscription_contract.get("contract_required")):
        return True

    app_build_plan = _context_get(context_variables, "app_build_plan")
    if isinstance(app_build_plan, dict):
        revenue_model = str(app_build_plan.get("revenue_model") or "").strip().lower()
        if revenue_model in _CORE_MOZAIKSPAY_REVENUE_MODELS:
            return True
        monetization_plan = app_build_plan.get("monetization_plan")
        if isinstance(monetization_plan, dict):
            plan_revenue_model = str(monetization_plan.get("revenue_model") or "").strip().lower()
            if plan_revenue_model in _CORE_MOZAIKSPAY_REVENUE_MODELS:
                return True
            requirement = str(monetization_plan.get("subscription_contract_requirement") or "").strip().lower()
            if requirement == "required":
                return True

    subscription_requirement = str(
        _context_get(context_variables, "subscription_contract_requirement") or ""
    ).strip().lower()
    if subscription_requirement == "required":
        return True

    if _is_truthy(_context_get(context_variables, "subscription_contract_required")):
        return True

    if _is_truthy(_context_get(context_variables, "token_wallet_required")):
        return True

    if _is_truthy(_context_get(context_variables, "usage_billing_required")):
        return True

    return False


def _with_default_mozaikspay_need(
    integration_needs: list[dict[str, Any]],
    *,
    context_variables: Any,
) -> list[dict[str, Any]]:
    """Add a removable Mozaiks Pay declaration for first-class billing unless explicit."""
    normalized_services = {
        str(need.get("service") or need.get("integration_id") or "").strip().lower()
        for need in integration_needs
        if isinstance(need, dict)
    }
    if "mozaikspay" in normalized_services or not _requires_default_mozaikspay_context(context_variables):
        return integration_needs
    return [
        *integration_needs,
        {
            "service": "mozaikspay",
            "catalog_id": "mozaikspay",
            "provider": "mozaikspay",
            "display_name": "Mozaiks Pay",
            "kind": "api_key",
            "purpose": (
                "Default connector for subscription, usage, and token billing. Remove it from "
                "app integrations if this app should use a different billing path."
            ),
            "required_at": "runtime",
            "optional": True,
            "defaulted": True,
            "removable": True,
            "source": "monetization_default",
            "required_fields": _MOZAIKSPAY_REQUIRED_FIELDS,
        },
    ]


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
    integration_needs = _with_default_mozaikspay_need(
        [need for need in integration_needs if isinstance(need, dict)],
        context_variables=context_variables,
    )
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
                defaulted=bool(need.get("defaulted", False)),
                removable=bool(need.get("removable", False)),
                source=str(need.get("source") or "build"),
                required_fields=need.get("required_fields") if isinstance(need.get("required_fields"), list) else [],
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
