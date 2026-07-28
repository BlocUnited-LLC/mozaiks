"""Scoped connector metadata contract used by workflow API-key tools and Studio.

Connectors are identified by (scope, scope_id, service). Workspace-scoped
credentials can fall back as a default for apps in that workspace via
resolve_connector. Secrets remain in the vault-backed backend; this module
manages metadata in MongoDB via ConnectorStore.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, cast

from mozaiksai.core.data.persistence import ConnectorStore
from mozaiksai.core.secrets import describe_connector_vault_backend, get_connector_vault_backend
from mozaiksai.core.workflow.generator_support.connector_health import (
    connector_health_check_supported,
)

SECRET_FIELD_TYPES = {"secret", "password", "api_key", "token"}


def _get_store(store: ConnectorStore | None = None) -> ConnectorStore:
    return store or ConnectorStore()


async def _vault_store_secret(
    backend: Any,
    *,
    scope_id: str,
    service: str,
    secret_value: str,
    display_name: str | None = None,
    ttl_days: int = 30,
) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        await backend.store_secret(
            scope_id=scope_id,
            service=service,
            secret_value=secret_value,
            display_name=display_name,
            ttl_days=ttl_days,
        ),
    )


async def _vault_get_secret(backend: Any, *, scope_id: str, service: str) -> dict[str, Any]:
    return cast(dict[str, Any], await backend.get_secret(scope_id=scope_id, service=service))


async def _vault_delete_secret(backend: Any, *, scope_id: str, service: str) -> dict[str, Any]:
    return cast(dict[str, Any], await backend.delete_secret(scope_id=scope_id, service=service))


def _normalize_service(service: str) -> str:
    return str(service or "").strip().lower().replace(" ", "_")


def _connector_identity_fields(
    *,
    service: str,
    provider: str | None = None,
    integration_id: str | None = None,
) -> dict[str, str]:
    normalized_service = _normalize_service(service)
    normalized_provider = _normalize_service(provider or normalized_service) or normalized_service
    normalized_integration_id = _normalize_service(integration_id or normalized_provider or normalized_service)
    return {
        "provider": normalized_provider,
        "integration_id": normalized_integration_id or normalized_service,
    }


def _health_supported_for_record(record: dict[str, Any] | None) -> bool:
    if not isinstance(record, dict):
        return False
    service = _normalize_service(str(record.get("service") or ""))
    integration_id = _normalize_service(str(record.get("integration_id") or record.get("provider") or service))
    return connector_health_check_supported(integration_id) or connector_health_check_supported(service)


def _display_service(service: str) -> str:
    normalized = _normalize_service(service)
    return normalized.replace("_", " ").title()


def _normalize_required_fields(required_fields: Sequence[dict[str, Any]] | None) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    if not isinstance(required_fields, Sequence) or isinstance(required_fields, (str, bytes)):
        return normalized
    for field in required_fields:
        if not isinstance(field, dict):
            continue
        name = str(field.get("name") or "").strip()
        if not name:
            continue
        field_type = str(field.get("type") or "text").strip().lower()
        is_secret = bool(field.get("secret")) or field_type in SECRET_FIELD_TYPES
        normalized.append(
            {
                "name": name,
                "label": str(field.get("label") or name.replace("_", " ").title()),
                "type": "secret" if is_secret else field_type,
                "required": bool(field.get("required", True)),
                "frontend_safe": False if is_secret else bool(field.get("frontend_safe", True)),
            }
        )
    return normalized


def compute_connector_health(
    record: dict[str, Any] | None,
    *,
    required_fields: Sequence[dict[str, Any]] | None = None,
    checked_by: str = "readiness",
) -> dict[str, Any]:
    """Compute frontend-safe connector configuration health without network calls."""

    now = datetime.now(UTC).isoformat()
    health_check_supported = _health_supported_for_record(record)
    if not isinstance(record, dict):
        missing = [
            field["name"]
            for field in _normalize_required_fields(required_fields)
            if bool(field.get("required", True))
        ]
        return {
            "status": "not_configured",
            "last_checked_at": now,
            "message": "Connector is not configured.",
            "missing_fields": missing,
            "checked_by": checked_by,
            "health_check_supported": health_check_supported,
            "frontend_safe": True,
        }

    fields = _normalize_required_fields(required_fields or record.get("required_fields"))
    public_config = record.get("public_config") if isinstance(record.get("public_config"), dict) else {}
    missing_fields: list[str] = []
    has_secret = bool(record.get("secret_available")) or int(record.get("key_length") or 0) > 0

    for field in fields:
        if not bool(field.get("required", True)):
            continue
        name = str(field.get("name") or "").strip()
        if not name:
            continue
        field_type = str(field.get("type") or "").lower()
        if field_type in SECRET_FIELD_TYPES:
            if not has_secret:
                missing_fields.append(name)
            continue
        value = public_config.get(name)  # type: ignore[union-attr]
        if value is None or (isinstance(value, str) and not value.strip()):
            missing_fields.append(name)

    if missing_fields:
        status = "not_configured"
        message = "Connector is missing required configuration."
    elif str(record.get("health_status") or "").strip().lower() in {"healthy", "unhealthy"}:
        status = str(record.get("health_status")).strip().lower()
        message = str(record.get("health_message") or "").strip() or None  # type: ignore[assignment]
    elif fields or record.get("secret_available") or public_config:
        if health_check_supported:
            status = "pending_validation"
            message = "Connector has required configuration; run its provider health check before use."
        else:
            status = "configured"
            message = "Connector has required configuration; no provider validation has run."
    else:
        status = "unknown"
        message = "Connector health has not been checked."

    return {
        "status": status,
        "last_checked_at": record.get("last_checked_at") or now,
        "message": message,
        "missing_fields": missing_fields,
        "checked_by": checked_by,
        "safe_details": record.get("health_details") if isinstance(record.get("health_details"), dict) else {},
        "error_code": record.get("health_error_code"),
        "health_check_supported": health_check_supported,
        "frontend_safe": True,
    }


def _with_connector_health(
    record: dict[str, Any] | None,
    *,
    required_fields: Sequence[dict[str, Any]] | None = None,
    checked_by: str = "readiness",
) -> dict[str, Any] | None:
    if not isinstance(record, dict):
        return None
    enriched = dict(record)
    enriched["health"] = compute_connector_health(
        enriched,
        required_fields=required_fields,
        checked_by=checked_by,
    )
    configuration_complete = enriched["health"]["status"] != "not_configured" and not enriched["health"].get("missing_fields")
    classified = _classify_connector_status(enriched)
    lifecycle_status = classified.get("status")
    # Passive readiness (inventory) never runs live provider health checks.
    # Active checks happen explicitly via run_connector_health_check.
    enriched["configured"] = configuration_complete
    enriched["ready"] = lifecycle_status in {"active", "expiring"} and configuration_complete
    enriched["lifecycle_status"] = lifecycle_status
    enriched["days_until_expiry"] = classified.get("days_until_expiry")
    return enriched


def _classify_connector_status(record: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(record, dict):
        return {
            "exists": False,
            "status": "missing",
            "connector": None,
            "days_until_expiry": None,
        }

    expires_at_raw = record.get("expires_at")
    expires_at = None
    if isinstance(expires_at_raw, str):
        try:
            expires_at = datetime.fromisoformat(expires_at_raw.replace("Z", "+00:00"))
        except Exception:
            expires_at = None

    days_until_expiry = None
    if expires_at is not None:
        delta = expires_at - datetime.now(UTC)
        days_until_expiry = max(int(delta.total_seconds() // 86400), 0)

    status = str(record.get("status") or "").strip() or "metadata_only"
    secret_available = bool(record.get("secret_available"))

    if status == "revoked":
        classified = "revoked"
    elif not secret_available:
        classified = "metadata_only"
    elif expires_at is not None and expires_at <= datetime.now(UTC):
        classified = "expired"
    elif days_until_expiry is not None and days_until_expiry <= 7:
        classified = "expiring"
    else:
        classified = "active"

    return {
        "exists": True,
        "status": classified,
        "connector": record,
        "days_until_expiry": days_until_expiry,
    }


def _summarize_connector_inventory(
    connectors: Sequence[dict[str, Any]],
    *,
    required_services: Sequence[str] | None = None,
) -> dict[str, Any]:
    normalized_required = [_normalize_service(service) for service in (required_services or []) if str(service or "").strip()]
    required_set = set(normalized_required)

    by_status: dict[str, list[str]] = {
        "active": [],
        "expiring": [],
        "expired": [],
        "revoked": [],
        "metadata_only": [],
    }
    display_names: dict[str, str] = {}
    ready_candidates: set[str] = set()

    for connector in connectors:
        if not isinstance(connector, dict):
            continue
        connector = _with_connector_health(connector) or connector
        service = _normalize_service(str(connector.get("service") or ""))
        if not service:
            continue
        display_names[service] = str(connector.get("display_name") or _display_service(service))
        classified = _classify_connector_status(connector).get("status") or "metadata_only"
        status_bucket = str(classified)
        if status_bucket not in by_status:
            by_status[status_bucket] = []
        if service not in by_status[status_bucket]:
            by_status[status_bucket].append(service)
        if bool(connector.get("ready")):
            ready_candidates.add(service)

    ready_services = sorted(ready_candidates)
    known_services = sorted({service for values in by_status.values() for service in values})
    missing_required_services = sorted(required_set - set(ready_services))
    known_but_unready_required = sorted(required_set & (set(by_status.get("metadata_only", [])) | set(by_status.get("expired", [])) | set(by_status.get("revoked", []))))
    entirely_missing_required = sorted(required_set - set(known_services))

    return {
        "required_services": normalized_required,
        "ready_services": ready_services,
        "known_services": known_services,
        "missing_required_services": missing_required_services,
        "known_but_unready_required_services": known_but_unready_required,
        "entirely_missing_required_services": entirely_missing_required,
        "status_buckets": by_status,
        "display_names": display_names,
        "connectors": [_with_connector_health(connector) or connector for connector in connectors],
    }


async def save_connector_draft(
    *,
    scope: str,
    scope_id: str,
    service: str,
    display_name: str | None = None,
    user_id: str | None = None,
    key_length: int = 0,
    public_config: dict[str, Any] | None = None,
    required_fields: Sequence[dict[str, Any]] | None = None,
    provider: str | None = None,
    integration_id: str | None = None,
    workflow_name: str | None = None,
    chat_id: str | None = None,
    agent_message_id: str | None = None,
    ui_event_id: str | None = None,
    status_reason: str | None = None,
    logger: Any | None = None,
    store: ConnectorStore | None = None,
) -> dict[str, Any]:
    """Save connector metadata without vault storage (draft/workflow-inline state)."""
    normalized_service = _normalize_service(service)
    connector_store = _get_store(store)
    now = datetime.now(UTC).isoformat()
    record = await connector_store.upsert(
        scope=scope,
        scope_id=str(scope_id),
        service=normalized_service,
        display_name=display_name,
        user_id=str(user_id) if user_id else None,
        status="metadata_only",
        secret_storage="unmanaged",
        secret_available=False,
        key_length=key_length,
        public_config=public_config,
        required_fields=_normalize_required_fields(required_fields),
        source={
            "origin": "workflow_ui",
            "workflow": workflow_name,
            "chat_id": chat_id,
            "agent_message_id": agent_message_id,
            "ui_event_id": ui_event_id,
            "submitted_at": now,
        },
        status_reason=status_reason or "Secret captured for the current workflow run only; no vault-backed connector store is configured.",
        extra=_connector_identity_fields(
            service=normalized_service,
            provider=provider,
            integration_id=integration_id,
        ),
    )
    if logger:
        logger.debug("CONNECTOR_DRAFT_SAVED service=%s scope=%s scope_id=%s", normalized_service, scope, scope_id)
    return {
        "saved": True,
        "connector": record,
    }


async def save_connector(
    *,
    scope: str,
    scope_id: str,
    service: str,
    secret_value: str,
    display_name: str | None = None,
    user_id: str | None = None,
    ttl_days: int = 30,
    public_config: dict[str, Any] | None = None,
    required_fields: Sequence[dict[str, Any]] | None = None,
    provider: str | None = None,
    integration_id: str | None = None,
    logger: Any | None = None,
    store: ConnectorStore | None = None,
) -> dict[str, Any]:
    """Store secret in vault and save connector metadata."""
    connector_store = _get_store(store)
    normalized_service = _normalize_service(service)
    now = datetime.now(UTC)
    backend = get_connector_vault_backend()
    backend_result = await _vault_store_secret(
        backend,
        scope_id=str(scope_id),
        service=normalized_service,
        secret_value=secret_value,
        display_name=display_name,
        ttl_days=ttl_days,
    )
    success = bool(backend_result.get("success"))
    vault_provider = str(backend_result.get("provider") or "unmanaged")
    identity_fields = _connector_identity_fields(
        service=normalized_service,
        provider=provider,
        integration_id=integration_id,
    )
    expires_at = backend_result.get("expires_at")
    error = backend_result.get("error")

    record = await connector_store.upsert(
        scope=scope,
        scope_id=str(scope_id),
        service=normalized_service,
        display_name=display_name,
        user_id=str(user_id) if user_id else None,
        status="active" if success else "metadata_only",
        secret_storage=vault_provider,
        secret_available=success,
        key_length=len(secret_value or ""),
        expires_at=expires_at,
        public_config=public_config,
        required_fields=_normalize_required_fields(required_fields),
        status_reason=(
            f"Connector secret persisted via {vault_provider}."
            if success
            else "Connector metadata saved, but secret storage is not available for the current runtime."
        ),
        extra={
            **identity_fields,
            "ttl_days_requested": int(ttl_days),
            "last_submitted_at": now.isoformat(),
            "secret_name": backend_result.get("secret_name"),
            "vault_provider": vault_provider,
        },
    )
    if logger:
        if success:
            logger.debug("CONNECTOR_SECRET_PERSISTED service=%s scope=%s scope_id=%s provider=%s", normalized_service, scope, scope_id, vault_provider)
        else:
            logger.warning(
                "CONNECTOR_SECRET_PERSIST_FAILED service=%s scope=%s scope_id=%s provider=%s: %s",
                normalized_service,
                scope,
                scope_id,
                vault_provider,
                error,
            )

    return {
        "success": success,
        "connector": record,
        "error": error,
        "health_check_supported": success and connector_health_check_supported(normalized_service),
    }


async def get_connector(
    *,
    scope: str,
    scope_id: str,
    service: str,
    store: ConnectorStore | None = None,
) -> dict[str, Any] | None:
    """Get a single connector with health enrichment."""
    connector_store = _get_store(store)
    record = await connector_store.get(scope=scope, scope_id=str(scope_id), service=_normalize_service(service))
    return _with_connector_health(record)


async def resolve_connector(
    *,
    service: str,
    app_id: str,
    workspace_id: str,
    store: ConnectorStore | None = None,
) -> dict[str, Any] | None:
    """Resolve the effective connector: app-scoped first, workspace fallback.

    Returns the connector record (enriched with health) and adds a
    'resolved_scope' key ('app' or 'workspace') to the result.
    """
    connector_store = _get_store(store)
    normalized_service = _normalize_service(service)

    app_record = await connector_store.get(scope=ConnectorStore.SCOPE_APP, scope_id=str(app_id), service=normalized_service)
    enriched_app = _with_connector_health(app_record)
    if enriched_app and enriched_app.get("ready"):
        enriched_app["resolved_scope"] = ConnectorStore.SCOPE_APP
        return enriched_app

    ws_record = await connector_store.get(scope=ConnectorStore.SCOPE_WORKSPACE, scope_id=str(workspace_id), service=normalized_service)
    enriched_ws = _with_connector_health(ws_record)
    if enriched_ws:
        enriched_ws["resolved_scope"] = ConnectorStore.SCOPE_WORKSPACE
        return enriched_ws

    if enriched_app:
        enriched_app["resolved_scope"] = ConnectorStore.SCOPE_APP
        return enriched_app

    return None


async def list_connectors(
    *,
    scope: str,
    scope_id: str,
    store: ConnectorStore | None = None,
) -> list[dict[str, Any]]:
    """List connectors for a scope with health enrichment."""
    connector_store = _get_store(store)
    connectors = await connector_store.list(scope=scope, scope_id=str(scope_id))
    return [_with_connector_health(connector) or connector for connector in connectors]


async def get_connector_inventory(
    *,
    scope: str,
    scope_id: str,
    required_services: Sequence[str] | None = None,
    store: ConnectorStore | None = None,
) -> dict[str, Any]:
    """Summarize connector inventory for a scope."""
    connector_store = _get_store(store)
    connectors = await connector_store.list(scope=scope, scope_id=str(scope_id))
    return _summarize_connector_inventory(connectors, required_services=required_services)


async def patch_connector(
    *,
    scope: str,
    scope_id: str,
    service: str,
    user_id: str | None = None,
    display_name: str | None = None,
    notes: str | None = None,
    status: str | None = None,
    expires_at: str | None = None,
    public_config: dict[str, Any] | None = None,
    required_fields: Sequence[dict[str, Any]] | None = None,
    store: ConnectorStore | None = None,
) -> dict[str, Any] | None:
    """Update connector metadata fields."""
    connector_store = _get_store(store)
    return await connector_store.patch(
        scope=scope,
        scope_id=str(scope_id),
        service=_normalize_service(service),
        user_id=str(user_id) if user_id else None,
        display_name=display_name,
        notes=notes,
        status=status,
        expires_at=expires_at,
        public_config=public_config,
        required_fields=_normalize_required_fields(required_fields),
    )


async def delete_connector(
    *,
    scope: str,
    scope_id: str,
    service: str,
    store: ConnectorStore | None = None,
) -> dict[str, Any]:
    """Delete connector metadata and vault secret."""
    connector_store = _get_store(store)
    normalized_service = _normalize_service(service)
    existing = await connector_store.get(scope=scope, scope_id=str(scope_id), service=normalized_service)

    secret_result: dict[str, Any] | None = None
    if existing and existing.get("secret_available"):
        secret_result = await _vault_delete_secret(
            get_connector_vault_backend(),
            scope_id=str(scope_id),
            service=normalized_service,
        )

    metadata_deleted = await connector_store.delete(scope=scope, scope_id=str(scope_id), service=normalized_service)
    return {
        "deleted": metadata_deleted,
        "service": normalized_service,
        "secret_deleted": bool(secret_result and secret_result.get("success")),
        "error": secret_result.get("error") if secret_result and not secret_result.get("success") else None,
    }


async def get_secret(*, scope_id: str, service: str) -> dict[str, Any]:
    """Retrieve a vault secret by scope_id and service."""
    backend = get_connector_vault_backend()
    result = await _vault_get_secret(
        backend,
        scope_id=str(scope_id),
        service=_normalize_service(service),
    )
    return {
        "success": bool(result.get("success")),
        "service": service,
        "secret_value": result.get("secret_value"),
        "provider": result.get("provider"),
        "secret_name": result.get("secret_name"),
        "expires_at": result.get("expires_at"),
        "error": result.get("error"),
    }


async def get_connector_backend_summary() -> dict[str, Any]:
    return await describe_connector_vault_backend()


__all__ = [
    "compute_connector_health",
    "delete_connector",
    "get_connector",
    "get_connector_backend_summary",
    "get_connector_inventory",
    "get_secret",
    "list_connectors",
    "patch_connector",
    "resolve_connector",
    "save_connector",
    "save_connector_draft",
]
