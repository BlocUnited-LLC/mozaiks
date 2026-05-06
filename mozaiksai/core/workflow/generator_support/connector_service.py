"""App-connector metadata contract used by workflow API-key tools and Studio.

The local generator runtime does not own a production secret vault. It does,
however, own sanitized connector metadata so Studio/Admin surfaces can show
which integrations were requested for an app and operators can manage those
records. Secrets remain ephemeral unless a vault-backed implementation is
provided later.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Dict, List, Optional, Sequence

from mozaiksai.core.data.persistence import AppConnectorStore
from mozaiksai.core.secrets import describe_connector_vault_backend, get_connector_vault_backend


def _get_store(store: Optional[AppConnectorStore] = None) -> AppConnectorStore:
    return store or AppConnectorStore()


def _normalize_service(service: str) -> str:
    return str(service or "").strip().lower().replace(" ", "_")


def _display_service(service: str) -> str:
    normalized = _normalize_service(service)
    return normalized.replace("_", " ").title()


def _classify_connector_status(record: Optional[Dict[str, Any]]) -> Dict[str, Any]:
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
    connectors: Sequence[Dict[str, Any]],
    *,
    required_services: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    normalized_required = [_normalize_service(service) for service in (required_services or []) if str(service or "").strip()]
    required_set = set(normalized_required)

    by_status: Dict[str, List[str]] = {
        "active": [],
        "expiring": [],
        "expired": [],
        "revoked": [],
        "metadata_only": [],
    }
    display_names: Dict[str, str] = {}

    for connector in connectors:
        if not isinstance(connector, dict):
            continue
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

    ready_services = sorted(set(by_status.get("active", [])) | set(by_status.get("expiring", [])))
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
        "connectors": list(connectors),
    }


async def record_connector_metadata(
    *,
    app_id: str,
    user_id: Optional[str],
    service: str,
    display_name: Optional[str],
    key_length: int,
    workflow_name: Optional[str],
    chat_id: Optional[str],
    agent_message_id: Optional[str],
    ui_event_id: Optional[str],
    logger: Optional[Any] = None,
    status_reason: Optional[str] = None,
    store: Optional[AppConnectorStore] = None,
) -> Dict[str, Any]:
    normalized_service = _normalize_service(service)
    connector_store = _get_store(store)
    now = datetime.now(UTC).isoformat()
    record = await connector_store.upsert_connector(
        app_id=str(app_id),
        service=normalized_service,
        display_name=display_name,
        user_id=str(user_id) if user_id else None,
        status="metadata_only",
        secret_storage="unmanaged",
        secret_available=False,
        key_length=key_length,
        source={
            "origin": "workflow_ui",
            "workflow": workflow_name,
            "chat_id": chat_id,
            "agent_message_id": agent_message_id,
            "ui_event_id": ui_event_id,
            "submitted_at": now,
        },
        status_reason=status_reason or "Secret captured for the current workflow run only; no vault-backed connector store is configured.",
    )
    if logger:
        logger.info("Saved connector metadata for %s (app %s)", normalized_service, app_id)
    return {
        "saved": True,
        "connector": record,
    }


async def get_connector_status(
    app_id: str,
    service: str,
    logger: Optional[Any] = None,
    store: Optional[AppConnectorStore] = None,
) -> Dict[str, Any]:
    connector_store = _get_store(store)
    record = await connector_store.get_connector(app_id=str(app_id), service=_normalize_service(service))
    classified = _classify_connector_status(record)
    if logger:
        if classified["exists"]:
            logger.info(
                "Connector metadata found for %s on app %s with status=%s",
                service,
                app_id,
                classified["status"],
            )
        else:
            logger.info("No connector metadata found for %s on app %s", service, app_id)
    return classified


async def get_secret_for_e2b(app_id: str, service: str, logger: Optional[Any] = None) -> Dict[str, Any]:
    backend = get_connector_vault_backend()
    result = await backend.get_secret(app_id=str(app_id), service=_normalize_service(service))
    if logger:
        if result.get("success"):
            logger.info("Connector secret resolved for %s via %s", service, result.get("provider"))
        else:
            logger.info(
                "Connector secret lookup unavailable for %s via %s: %s",
                service,
                result.get("provider"),
                result.get("error"),
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


async def store_connector(
    *,
    app_id: str,
    user_id: str,
    service: str,
    secret_value: str,
    display_name: Optional[str] = None,
    ttl_days: int = 30,
    logger: Optional[Any] = None,
    store: Optional[AppConnectorStore] = None,
) -> Dict[str, Any]:
    connector_store = _get_store(store)
    normalized_service = _normalize_service(service)
    now = datetime.now(UTC)
    backend = get_connector_vault_backend()
    backend_result = await backend.store_secret(
        app_id=str(app_id),
        service=normalized_service,
        secret_value=secret_value,
        display_name=display_name,
        ttl_days=ttl_days,
    )
    success = bool(backend_result.get("success"))
    provider = str(backend_result.get("provider") or "unmanaged")
    expires_at = backend_result.get("expires_at")
    error = backend_result.get("error")

    record = await connector_store.upsert_connector(
        app_id=str(app_id),
        service=normalized_service,
        display_name=display_name,
        user_id=str(user_id) if user_id else None,
        status="active" if success else "metadata_only",
        secret_storage=provider,
        secret_available=success,
        key_length=len(secret_value or ""),
        expires_at=expires_at,
        status_reason=(
            f"Connector secret persisted via {provider}."
            if success
            else "Connector metadata saved, but secret storage is not available for the current runtime."
        ),
        extra_fields={
            "ttl_days_requested": int(ttl_days),
            "last_submitted_at": now.isoformat(),
            "secret_name": backend_result.get("secret_name"),
            "vault_provider": provider,
        },
    )
    if logger:
        if success:
            logger.info("Connector secret persisted for %s on app %s via %s", normalized_service, app_id, provider)
        else:
            logger.info(
                "Connector metadata saved for %s on app %s, but secret storage failed via %s: %s",
                normalized_service,
                app_id,
                provider,
                error,
            )
    return {
        "success": success,
        "metadata_saved": True,
        "app_id": app_id,
        "user_id": user_id,
        "service": normalized_service,
        "display_name": display_name or normalized_service,
        "expires_at": expires_at,
        "created_at_utc": now.isoformat(),
        "connector_status": "active" if success else "metadata_only",
        "secret_available": success,
        "provider": provider,
        "secret_name": backend_result.get("secret_name"),
        "record": record,
        "error": error,
    }


async def list_connectors(app_id: str, store: Optional[AppConnectorStore] = None) -> List[Dict[str, Any]]:
    connector_store = _get_store(store)
    return await connector_store.list_connectors(app_id=str(app_id))


async def get_connector_inventory(
    app_id: str,
    *,
    required_services: Optional[Sequence[str]] = None,
    store: Optional[AppConnectorStore] = None,
) -> Dict[str, Any]:
    connector_store = _get_store(store)
    connectors = await connector_store.list_connectors(app_id=str(app_id))
    return _summarize_connector_inventory(connectors, required_services=required_services)


async def update_connector_metadata(
    *,
    app_id: str,
    service: str,
    user_id: Optional[str] = None,
    display_name: Optional[str] = None,
    notes: Optional[str] = None,
    status: Optional[str] = None,
    expires_at: Optional[str] = None,
    store: Optional[AppConnectorStore] = None,
) -> Optional[Dict[str, Any]]:
    connector_store = _get_store(store)
    return await connector_store.patch_connector(
        app_id=str(app_id),
        service=_normalize_service(service),
        user_id=str(user_id) if user_id else None,
        display_name=display_name,
        notes=notes,
        status=status,
        expires_at=expires_at,
    )


async def delete_connector_metadata(
    *,
    app_id: str,
    service: str,
    store: Optional[AppConnectorStore] = None,
) -> bool:
    connector_store = _get_store(store)
    return await connector_store.delete_connector(app_id=str(app_id), service=_normalize_service(service))


async def delete_connector(
    *,
    app_id: str,
    service: str,
    store: Optional[AppConnectorStore] = None,
) -> Dict[str, Any]:
    connector_store = _get_store(store)
    normalized_service = _normalize_service(service)
    existing = await connector_store.get_connector(app_id=str(app_id), service=normalized_service)

    provider = str((existing or {}).get("secret_storage") or "")
    secret_result: Optional[Dict[str, Any]] = None
    if existing and existing.get("secret_available"):
        secret_result = await get_connector_vault_backend().delete_secret(app_id=str(app_id), service=normalized_service)
        provider = str(secret_result.get("provider") or provider or "unknown")

    metadata_deleted = await connector_store.delete_connector(app_id=str(app_id), service=normalized_service)
    return {
        "deleted": metadata_deleted,
        "service": normalized_service,
        "provider": provider or None,
        "secret_deleted": bool(secret_result and secret_result.get("success")),
        "secret_error": secret_result.get("error") if secret_result and not secret_result.get("success") else None,
    }


async def get_connector_backend_summary() -> Dict[str, Any]:
    return await describe_connector_vault_backend()


__all__ = [
    "delete_connector_metadata",
    "delete_connector",
    "get_connector_inventory",
    "get_connector_status",
    "get_connector_backend_summary",
    "get_secret_for_e2b",
    "list_connectors",
    "record_connector_metadata",
    "store_connector",
    "update_connector_metadata",
]
