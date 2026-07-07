# ==============================================================================
# FILE: mozaiksai/core/audit/audit_logger.py
# DESCRIPTION: Immutable audit trail. Records every workflow start, module
#              action, and admin operation with user identity and input hash.
#              The audit collection is append-only — no update or delete path.
# ==============================================================================
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from logs.logging_config import get_core_logger

logger = get_core_logger("audit")

_AUDIT_COLLECTION = "audit_log"
_AUDIT_DB = os.getenv("MOZAIKS_AUDIT_DATABASE_NAME", "mozaiks_audit")


class AuditEventKind(str, Enum):
    WORKFLOW_START = "workflow.start"
    WORKFLOW_COMPLETE = "workflow.complete"
    WORKFLOW_FAIL = "workflow.fail"
    MODULE_ACTION = "module.action"
    MODULE_ACTION_FAIL = "module.action.fail"
    ADMIN_ACCESS = "admin.access"
    ADMIN_WRITE = "admin.write"
    AUTH_FAIL = "auth.fail"
    ARTIFACT_PROMOTE = "artifact.promote"
    ARTIFACT_LOAD = "artifact.load"


@dataclass
class AuditRecord:
    """Immutable audit record written to the audit log collection."""

    kind: AuditEventKind
    actor_id: str
    app_id: str | None = None
    chat_id: str | None = None
    tenant_id: str | None = None
    workspace_id: str | None = None
    resource: str | None = None          # e.g. module name, workflow name
    action: str | None = None            # e.g. action id, route path
    inputs_hash: str | None = None       # SHA-256 of serialised inputs
    outcome: str = "ok"                  # ok | fail | denied
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    # Populated on write
    record_id: str = field(default_factory=lambda: str(uuid4()))
    occurred_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_document(self) -> dict[str, Any]:
        return {
            "_id": self.record_id,
            "kind": self.kind.value,
            "actor_id": self.actor_id,
            "app_id": self.app_id,
            "chat_id": self.chat_id,
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "resource": self.resource,
            "action": self.action,
            "inputs_hash": self.inputs_hash,
            "outcome": self.outcome,
            "error": self.error,
            "extra": self.extra,
            "occurred_at": self.occurred_at,
        }


def _hash_inputs(inputs: Any) -> str:
    """Return a deterministic SHA-256 hex digest of inputs."""
    try:
        serialised = json.dumps(inputs, sort_keys=True, default=str)
    except Exception:
        serialised = repr(inputs)
    return hashlib.sha256(serialised.encode()).hexdigest()


class AuditLogger:
    """Writes AuditRecord documents to a dedicated MongoDB audit collection.

    The collection is append-only. Call sites must never call update_one or
    delete_one on the audit collection — inserts only.

    Usage:
        audit = get_audit_logger()
        await audit.log(AuditRecord(
            kind=AuditEventKind.MODULE_ACTION,
            actor_id=user.user_id,
            app_id=app_id,
            resource="contacts",
            action="create_contact",
            inputs_hash=_hash_inputs(params),
        ))
    """

    def __init__(self) -> None:
        self._collection: Any | None = None

    def _get_collection(self) -> Any | None:
        """Lazily resolve the audit MongoDB collection."""
        try:
            from mozaiksai.core.core_config import get_mongo_client
            client = get_mongo_client()
            if client is None:
                return None
            db = client[_AUDIT_DB]
            return db[_AUDIT_COLLECTION]
        except Exception as exc:
            logger.debug("Audit collection unavailable: %s", exc)
            return None

    async def log(self, record: AuditRecord) -> None:
        """Persist an audit record. Failures are logged but never raised.

        Audit must never interrupt the primary request path.
        """
        collection = self._get_collection()
        if collection is None:
            # Fallback: write to structured log so records are not lost.
            logger.info(
                "AUDIT kind=%s actor=%s resource=%s action=%s outcome=%s",
                record.kind.value,
                record.actor_id,
                record.resource,
                record.action,
                record.outcome,
                extra={
                    "audit_record": record.to_document(),
                    "event": "AUDIT",
                },
            )
            return

        try:
            await collection.insert_one(record.to_document())
        except Exception as exc:
            # Never raise — log to structured log as fallback.
            logger.error(
                "AUDIT_WRITE_FAIL kind=%s actor=%s error=%s",
                record.kind.value,
                record.actor_id,
                exc,
                extra={"audit_record": record.to_document()},
            )

    async def log_workflow_start(
        self,
        *,
        actor_id: str,
        app_id: str | None,
        chat_id: str | None,
        workflow_name: str,
        inputs: Any = None,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
    ) -> None:
        await self.log(AuditRecord(
            kind=AuditEventKind.WORKFLOW_START,
            actor_id=actor_id,
            app_id=app_id,
            chat_id=chat_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            resource=workflow_name,
            inputs_hash=_hash_inputs(inputs) if inputs is not None else None,
        ))

    async def log_module_action(
        self,
        *,
        actor_id: str,
        app_id: str | None,
        module_id: str,
        action_id: str,
        params: Any = None,
        outcome: str = "ok",
        error: str | None = None,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
    ) -> None:
        await self.log(AuditRecord(
            kind=AuditEventKind.MODULE_ACTION if outcome == "ok" else AuditEventKind.MODULE_ACTION_FAIL,
            actor_id=actor_id,
            app_id=app_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            resource=module_id,
            action=action_id,
            inputs_hash=_hash_inputs(params) if params is not None else None,
            outcome=outcome,
            error=error,
        ))

    async def log_admin_access(
        self,
        *,
        actor_id: str,
        app_id: str | None,
        route: str,
        method: str = "GET",
        outcome: str = "ok",
    ) -> None:
        await self.log(AuditRecord(
            kind=AuditEventKind.ADMIN_ACCESS,
            actor_id=actor_id,
            app_id=app_id,
            resource=route,
            action=method,
            outcome=outcome,
        ))


_audit_logger: AuditLogger | None = None


def get_audit_logger() -> AuditLogger:
    """Return the process-wide audit logger singleton."""
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = AuditLogger()
    return _audit_logger


__all__ = [
    "AuditEventKind",
    "AuditLogger",
    "AuditRecord",
    "get_audit_logger",
    "_hash_inputs",
]
