from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pymongo import ReturnDocument

from mozaiksai.core.core_config import get_mongo_client
from mozaiksai.core.runtime.app.paths import APP_DATA_MIGRATIONS_DIR

from .indexes import (
    DatabaseIndexApplyError,
    _ensure_raw_collection_indexes,
    _normalize_index_spec,
)
from .mongo import MongoPersistenceContext

SYSTEM_DATABASE = "mozaiksai"
APP_DATA_MIGRATIONS_COLLECTION = "AppDataMigrations"
MIGRATION_HEALTH_MAX_LIMIT = 500
SUPPORTED_MIGRATION_OPERATIONS = {"ensure_collection", "ensure_index"}
DESTRUCTIVE_MIGRATION_OPERATIONS = {
    "delete_field",
    "drop_collection",
    "drop_field",
    "remove_collection",
    "remove_field",
    "rename_collection",
    "rename_field",
}

DatabaseMigration = dict[str, Any]


class DatabaseMigrationError(ValueError):
    """Raised when an app data migration cannot be loaded or applied."""


class DatabaseMigrationOperationError(DatabaseMigrationError):
    """Raised when a specific migration operation fails."""

    def __init__(
        self,
        message: str,
        *,
        operation_index: int,
        operation_summary: dict[str, str],
    ) -> None:
        super().__init__(message)
        self.operation_index = operation_index
        self.operation_summary = operation_summary


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _require_object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DatabaseMigrationError(f"{path} must be an object")
    return value


def _require_operations(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise DatabaseMigrationError(f"{path} must be a list")
    return value


def load_data_migrations(app_root: Path) -> list[DatabaseMigration]:
    """Load app data migrations in filename order."""

    migrations_dir = Path(app_root) / APP_DATA_MIGRATIONS_DIR
    if not migrations_dir.exists():
        return []
    if not migrations_dir.is_dir():
        raise DatabaseMigrationError(f"{APP_DATA_MIGRATIONS_DIR} must be a directory")

    migrations: list[DatabaseMigration] = []
    for path in sorted(migrations_dir.glob("*.json"), key=lambda item: item.name.lower()):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise DatabaseMigrationError(f"Failed to read {path.relative_to(app_root)}: {exc}") from exc
        migration = _require_object(raw, f"{path.name}")
        _validate_migration(migration, path.name)
        migrations.append(migration)
    return migrations


def migration_hash(migration: DatabaseMigration) -> str:
    payload = json.dumps(migration, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def apply_data_migrations(
    *,
    app_id: str,
    migrations: list[DatabaseMigration],
    persistence: MongoPersistenceContext | None = None,
    history_client: Any | None = None,
) -> int:
    """Apply additive app data migrations and record migration history."""

    resolved_app_id = str(app_id or "").strip()
    if not resolved_app_id:
        raise DatabaseMigrationError("app_id is required to apply data migrations")
    if not migrations:
        return 0

    context = persistence or MongoPersistenceContext(app_id=resolved_app_id)
    history = _history_collection(history_client)
    await _ensure_history_indexes(history)

    applied_count = 0
    for migration in migrations:
        _validate_migration(migration, str(migration.get("migration_id") or "migration"))
        migration_id = str(migration["migration_id"]).strip()
        current_hash = migration_hash(migration)
        operations = list(migration.get("operations") or [])
        operations_summary = _operations_summary(operations)
        lock_owner = f"migration_{uuid4().hex}"
        claim = await _claim_migration(
            history,
            app_id=resolved_app_id,
            migration_id=migration_id,
            migration_hash_value=current_hash,
            operations_summary=operations_summary,
            lock_owner=lock_owner,
        )
        if claim == "skip":
            continue

        try:
            for index, operation in enumerate(operations):
                await _apply_operation(
                    context,
                    operation,
                    path=f"migration {migration_id}.operations[{index}]",
                    app_id=resolved_app_id,
                    migration_id=migration_id,
                    operation_index=index,
                )
        except Exception as exc:
            failed_at = datetime.now(UTC)
            operation_index = getattr(exc, "operation_index", None)
            operation_summary = getattr(exc, "operation_summary", None)
            await history.update_one(
                {"app_id": resolved_app_id, "migration_id": migration_id},
                {
                    "$set": {
                        "app_id": resolved_app_id,
                        "migration_id": migration_id,
                        "migration_hash": current_hash,
                        "status": "failed",
                        "failed_at": failed_at,
                        "completed_at": failed_at,
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                        "failed_operation_index": operation_index,
                        "failed_operation_summary": operation_summary,
                        "operations_summary": operations_summary,
                    }
                },
                upsert=False,
            )
            raise

        completed_at = datetime.now(UTC)
        await history.update_one(
            {"app_id": resolved_app_id, "migration_id": migration_id},
            {
                "$set": {
                    "app_id": resolved_app_id,
                    "migration_id": migration_id,
                    "migration_hash": current_hash,
                    "status": "applied",
                    "applied_at": completed_at,
                    "completed_at": completed_at,
                    "operations_summary": operations_summary,
                },
            },
            upsert=False,
        )
        applied_count += 1

    return applied_count


async def get_migration_health_report(
    *,
    app_id: str | None = None,
    status: str | None = None,
    database_name: str | None = None,
    client: Any | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Return a read-only generated-app migration health report."""

    history = _history_collection(client, database_name=database_name)
    query: dict[str, Any] = {}
    resolved_app_id = str(app_id or "").strip()
    if resolved_app_id:
        query["app_id"] = resolved_app_id
    resolved_status = str(status or "").strip()
    if resolved_status:
        query["status"] = resolved_status

    safe_limit = max(1, min(int(limit), MIGRATION_HEALTH_MAX_LIMIT))
    cursor = history.find(query).sort([("app_id", 1), ("migration_id", 1)]).limit(safe_limit)
    records = await cursor.to_list(length=safe_limit)

    summary: dict[str, int] = {
        "total": 0,
        "applied": 0,
        "in_progress": 0,
        "failed": 0,
        "unknown": 0,
    }
    items: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        item = _migration_health_item(record)
        status_value = item["status"]
        summary["total"] += 1
        if status_value in {"applied", "in_progress", "failed"}:
            summary[status_value] += 1
        else:
            summary["unknown"] += 1
        items.append(item)

    return {
        "summary": summary,
        "items": items,
        "has_blockers": any(item["is_blocker"] for item in items),
        "has_unknown_statuses": any(item["unknown_status"] for item in items),
    }


def _migration_health_item(record: dict[str, Any]) -> dict[str, Any]:
    status = str(record.get("status") or "unknown")
    is_blocker = status in {"failed", "in_progress"}
    unknown_status = status not in {"applied", "in_progress", "failed"}
    return {
        "app_id": record.get("app_id"),
        "migration_id": record.get("migration_id"),
        "status": status,
        "migration_hash": record.get("migration_hash"),
        "claimed_at": record.get("claimed_at"),
        "lock_owner": record.get("lock_owner"),
        "started_at": record.get("started_at"),
        "applied_at": record.get("applied_at"),
        "failed_at": record.get("failed_at"),
        "completed_at": record.get("completed_at"),
        "error_type": record.get("error_type"),
        "error_message": record.get("error_message"),
        "failed_operation_index": record.get("failed_operation_index"),
        "failed_operation_summary": record.get("failed_operation_summary"),
        "operations_summary": record.get("operations_summary"),
        "is_blocker": is_blocker,
        "unknown_status": unknown_status,
    }


def _history_collection(client: Any | None = None, *, database_name: str | None = None):
    resolved_client = client or get_mongo_client()
    return resolved_client[(database_name or SYSTEM_DATABASE)][APP_DATA_MIGRATIONS_COLLECTION]


async def _ensure_history_indexes(history: Any) -> None:
    await _ensure_raw_collection_indexes(
        history,
        [
            {
                "name": "adm_app_migration",
                "keys": [("app_id", 1), ("migration_id", 1)],
                "unique": True,
            }
        ],
        collection_label=APP_DATA_MIGRATIONS_COLLECTION,
    )


async def _claim_migration(
    history: Any,
    *,
    app_id: str,
    migration_id: str,
    migration_hash_value: str,
    operations_summary: list[dict[str, str]],
    lock_owner: str,
) -> str:
    claimed_at = datetime.now(UTC)
    try:
        record = await history.find_one_and_update(
            {"app_id": app_id, "migration_id": migration_id},
            {
                "$setOnInsert": {
                    "app_id": app_id,
                    "migration_id": migration_id,
                    "migration_hash": migration_hash_value,
                    "status": "in_progress",
                    "created_at": claimed_at,
                    "started_at": claimed_at,
                    "claimed_at": claimed_at,
                    "lock_owner": lock_owner,
                    "operations_summary": operations_summary,
                }
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except Exception as exc:
        existing = await history.find_one({"app_id": app_id, "migration_id": migration_id})
        if isinstance(existing, dict):
            result = _existing_migration_result(
                existing,
                app_id=app_id,
                migration_id=migration_id,
                migration_hash_value=migration_hash_value,
            )
            if result == "skip":
                return "skip"
        raise DatabaseMigrationError(
            f"Could not claim migration {migration_id!r} for app_id={app_id!r}: {exc}"
        ) from exc

    if not isinstance(record, dict):
        raise DatabaseMigrationError(f"Could not claim migration {migration_id!r} for app_id={app_id!r}")

    if record.get("status") == "in_progress" and record.get("lock_owner") == lock_owner:
        return "claimed"

    existing_result = _existing_migration_result(
        record,
        app_id=app_id,
        migration_id=migration_id,
        migration_hash_value=migration_hash_value,
    )
    if existing_result == "skip":
        return "skip"
    raise AssertionError("unreachable")


def _existing_migration_result(
    existing: dict[str, Any],
    *,
    app_id: str,
    migration_id: str,
    migration_hash_value: str,
) -> str:
    existing_hash = str(existing.get("migration_hash") or "").strip()
    status = str(existing.get("status") or "").strip()
    if existing_hash == migration_hash_value and status == "applied":
        return "skip"
    detail = (
        f"Migration {migration_id!r} for app_id={app_id!r} already has status={status!r} "
        f"existing_hash={existing_hash!r}"
    )
    if status == "applied":
        raise DatabaseMigrationError(f"{detail}; different hash")
    if status == "in_progress":
        raise DatabaseMigrationError(f"{detail}; migration is already in progress")
    if status == "failed":
        raise DatabaseMigrationError(f"{detail}; clear the failed history record before retrying")
    raise DatabaseMigrationError(f"{detail}; cannot claim migration")


async def _apply_operation(
    context: MongoPersistenceContext,
    operation: Any,
    *,
    path: str,
    app_id: str,
    migration_id: str,
    operation_index: int,
) -> None:
    op = _require_object(operation, path)
    op_type = str(op.get("type") or op.get("operation") or "").strip()
    module_id = str(op.get("module_id") or "").strip()
    entity_name = str(op.get("entity_name") or op.get("name") or "").strip()
    summary = _operation_summary(op)

    try:
        if not op_type:
            raise DatabaseMigrationError(f"{path}.type is required")
        if op_type in DESTRUCTIVE_MIGRATION_OPERATIONS:
            raise DatabaseMigrationError(f"{path}.type {op_type!r} is destructive and unsupported")
        if op_type not in SUPPORTED_MIGRATION_OPERATIONS:
            raise DatabaseMigrationError(f"{path}.type {op_type!r} is unsupported")
        if not module_id:
            raise DatabaseMigrationError(f"{path}.module_id is required")
        if not entity_name:
            raise DatabaseMigrationError(f"{path}.entity_name is required")

        collection_name = (
            context.collection_name(module_id, entity_name)
            if hasattr(context, "collection_name")
            else ""
        )
        collection = context.collection(module_id, entity_name)
        summary["collection_name"] = collection_name
        if op_type == "ensure_collection":
            return
        if op_type == "ensure_index":
            index_spec = op.get("index")
            if index_spec is None:
                index_spec = {
                    key: value
                    for key, value in op.items()
                    if key
                    in {
                        "collation",
                        "expireAfterSeconds",
                        "hidden",
                        "keys",
                        "name",
                        "partialFilterExpression",
                        "sparse",
                        "unique",
                        "wildcardProjection",
                    }
                }
            try:
                normalized = _normalize_index_spec(index_spec, f"{path}.index")
            except DatabaseIndexApplyError as exc:
                raise DatabaseMigrationError(str(exc)) from exc
            index_dict: dict[str, Any] = {
                "keys": normalized.keys,
                "name": normalized.name,
                **normalized.options,
            }
            await collection.ensure_indexes([index_dict])
    except Exception as exc:
        message = (
            f"Migration {migration_id!r} for app_id={app_id!r} failed at operation "
            f"{operation_index} ({summary.get('type') or 'unknown'} "
            f"module_id={summary.get('module_id') or ''} "
            f"entity_name={summary.get('entity_name') or ''} "
            f"collection_name={summary.get('collection_name') or ''}): {exc}"
        )
        raise DatabaseMigrationOperationError(
            message,
            operation_index=operation_index,
            operation_summary=summary,
        ) from exc


def _validate_migration(migration: DatabaseMigration, path: str) -> None:
    if not _is_non_empty_string(migration.get("migration_id")):
        raise DatabaseMigrationError(f"{path}.migration_id is required")
    if not (_is_non_empty_string(migration.get("version")) or _is_non_empty_string(migration.get("schema_version"))):
        raise DatabaseMigrationError(f"{path}.version or {path}.schema_version is required")
    operations = _require_operations(migration.get("operations"), f"{path}.operations")
    for index, operation in enumerate(operations):
        op = _require_object(operation, f"{path}.operations[{index}]")
        op_type = str(op.get("type") or op.get("operation") or "").strip()
        if op_type in DESTRUCTIVE_MIGRATION_OPERATIONS:
            raise DatabaseMigrationError(f"{path}.operations[{index}].type {op_type!r} is destructive and unsupported")


def _operation_summary(operation: dict[str, Any]) -> dict[str, str]:
    return {
        "type": str(operation.get("type") or operation.get("operation") or ""),
        "module_id": str(operation.get("module_id") or ""),
        "entity_name": str(operation.get("entity_name") or operation.get("name") or ""),
    }


def _operations_summary(operations: list[Any]) -> list[dict[str, str]]:
    summary: list[dict[str, str]] = []
    for operation in operations:
        if not isinstance(operation, dict):
            continue
        summary.append(_operation_summary(operation))
    return summary


__all__ = [
    "APP_DATA_MIGRATIONS_COLLECTION",
    "DatabaseMigrationError",
    "DatabaseMigrationOperationError",
    "apply_data_migrations",
    "get_migration_health_report",
    "load_data_migrations",
    "migration_hash",
]
