# ==============================================================================
# FILE: mozaiksai/core/admin/router.py
# DESCRIPTION: First-class admin API. All routes require the "admin" role.
#              Surfaces data already captured by the runtime — no new storage.
# ==============================================================================
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from logs.logging_config import get_core_logger
from mozaiksai.core.admin.contract import (
    DEFAULT_RUNTIME_PANELS,
    build_default_admin_config,
)
from mozaiksai.core.admin.email_promotion import is_admin_by_email
from mozaiksai.core.admin.paths import resolve_admin_app_root as resolve_admin_app_root_path
from mozaiksai.core.admin.registry import load_admin_registry
from mozaiksai.core.auth.adapters.registry import is_auth_enabled
from mozaiksai.core.auth.dependencies import UserPrincipal, require_user
from mozaiksai.core.data.models import WorkflowStatus

logger = get_core_logger("admin.router")

router = APIRouter(prefix="/api/admin", tags=["admin"])


_bearer = HTTPBearer(auto_error=False)


DEFAULT_ADMIN_CONFIG = build_default_admin_config()


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _get_chat_sessions_collection():
    from mozaiksai.core.core_config import get_mongo_client

    client = get_mongo_client()
    return client["mozaiksai"]["ChatSessions"]


def _get_app_registry_collection():
    from mozaiksai.core.core_config import get_mongo_client
    from mozaiksai.core.data.persistence.namespaces import SYSTEM_DATABASE

    client = get_mongo_client()
    return client[SYSTEM_DATABASE]["AppRegistryRecords"]


def _serialize_datetime(value: Any) -> str | None:
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            return None
    return None


async def _load_app_registry_maps() -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    coll = _get_app_registry_collection()
    by_app_id: dict[str, dict[str, Any]] = {}
    by_chat_id: dict[str, dict[str, Any]] = {}
    cursor = coll.find(
        {},
        {
            "_id": 1,
            "app_id": 1,
            "name": 1,
            "name_status": 1,
            "name_source": 1,
            "lifecycle_state": 1,
            "active_chat_id": 1,
        },
    )
    async for doc in cursor:
        app_id = str(doc.get("app_id") or "").strip()
        active_chat_id = str(doc.get("active_chat_id") or "").strip()
        record = {
            "build_registry_id": str(doc.get("_id") or ""),
            "app_id": app_id,
            "app_name": doc.get("name"),
            "name_status": doc.get("name_status") or ("named" if doc.get("name") else "provisional"),
            "name_source": doc.get("name_source") or ("manual" if doc.get("name") else "provisional"),
            "lifecycle_state": doc.get("lifecycle_state"),
        }
        if app_id:
            by_app_id[app_id] = record
        if active_chat_id:
            by_chat_id[active_chat_id] = record
    return by_app_id, by_chat_id


def _usage_app_display_name(record: dict[str, Any] | None, app_id: str | None) -> str | None:
    if record and record.get("name_status") == "named" and record.get("app_name"):
        return str(record["app_name"])
    if app_id:
        suffix = str(app_id).split("-")[-1][:6].upper()
        return f"Draft {suffix}" if suffix else "Draft App"
    return None


async def _enrich_usage_with_app_registry(usage: dict[str, Any]) -> dict[str, Any]:
    by_app_id, by_chat_id = await _load_app_registry_maps()

    def enrich_row(row: dict[str, Any]) -> dict[str, Any]:
        app_id = str(row.get("app_id") or "").strip()
        chat_id = str(row.get("chat_id") or "").strip()
        record = by_app_id.get(app_id) if app_id else None
        if record is None and chat_id:
            record = by_chat_id.get(chat_id)
            if record and record.get("app_id"):
                app_id = str(record["app_id"])
                row["app_id"] = app_id
        if record:
            row["build_registry_id"] = record.get("build_registry_id")
            row["app_name"] = _usage_app_display_name(record, app_id)
            row["name_status"] = record.get("name_status")
            row["name_source"] = record.get("name_source")
            row["lifecycle_state"] = record.get("lifecycle_state")
        elif app_id:
            row["app_name"] = app_id
        return row

    for key in ("by_run", "events"):
        rows = usage.get(key)
        if isinstance(rows, list):
            usage[key] = [enrich_row(dict(row)) for row in rows if isinstance(row, dict)]
    return usage


def _count_assistant_turns(messages: Any) -> int:
    if not isinstance(messages, list):
        return 0
    return sum(1 for message in messages if isinstance(message, dict) and message.get("role") == "assistant")


def _compute_session_runtime_sec(doc: dict[str, Any], *, now: datetime) -> float:
    stored_duration = float(doc.get("duration_sec") or 0.0)
    created_at = doc.get("created_at")
    completed_at = doc.get("completed_at")

    if hasattr(created_at, "tzinfo") and created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    if hasattr(completed_at, "tzinfo") and completed_at.tzinfo is None:
        completed_at = completed_at.replace(tzinfo=UTC)

    try:
        if created_at and completed_at:
            return max(stored_duration, float((completed_at - created_at).total_seconds()))
        if created_at and doc.get("status") == int(WorkflowStatus.IN_PROGRESS):
            return max(stored_duration, float((now - created_at).total_seconds()))
    except Exception as exc:
        logger.warning(
            "SESSION_RUNTIME_COMPUTE_FAILED chat=%s — using stored_duration: %s",
            doc.get("_id") or doc.get("chat_id"),
            exc,
        )
        return stored_duration
    return stored_duration


async def _build_persisted_admin_stats(*, app_id: str | None = None) -> dict[str, Any]:
    coll = _get_chat_sessions_collection()
    pipeline: list[dict[str, Any]] = []
    if app_id:
        pipeline.append({"$match": {"app_id": app_id}})
    pipeline += [
        {
            "$project": {
                "status": 1,
            }
        },
        {
            "$group": {
                "_id": None,
                "tracked_chats": {"$sum": 1},
                "active_chats": {
                    "$sum": {
                        "$cond": [{"$eq": ["$status", int(WorkflowStatus.IN_PROGRESS)]}, 1, 0]
                    }
                },
                "completed_chats": {
                    "$sum": {
                        "$cond": [{"$eq": ["$status", int(WorkflowStatus.COMPLETED)]}, 1, 0]
                    }
                },
            }
        },
    ]
    rows = [doc async for doc in coll.aggregate(pipeline)]
    aggregate = rows[0] if rows else {}
    return {
        "active_chats": int(aggregate.get("active_chats") or 0),
        "tracked_chats": int(aggregate.get("tracked_chats") or 0),
        "completed_chats": int(aggregate.get("completed_chats") or 0),
        "telemetry_source": "ag2_opentelemetry",
    }


async def _build_persisted_admin_runs(
    *,
    app_id: str | None,
    active_only: bool,
    limit: int,
) -> dict[str, Any]:
    coll = _get_chat_sessions_collection()
    query: dict[str, Any] = {}
    if app_id:
        query["app_id"] = app_id
    if active_only:
        query["status"] = int(WorkflowStatus.IN_PROGRESS)

    cursor = coll.find(
        query,
        {
            "_id": 1,
            "app_id": 1,
            "workflow_name": 1,
            "user_id": 1,
            "status": 1,
            "created_at": 1,
            "completed_at": 1,
            "duration_sec": 1,
            "messages": 1,
        },
    ).sort("created_at", -1).limit(limit)

    now = _utc_now()
    runs = []
    async for doc in cursor:
        runs.append(
            {
                "chat_id": str(doc.get("_id")),
                "app_id": doc.get("app_id"),
                "workflow_name": doc.get("workflow_name"),
                "user_id": doc.get("user_id"),
                "started_at": _serialize_datetime(doc.get("created_at")),
                "ended_at": _serialize_datetime(doc.get("completed_at")),
                "agent_turns": _count_assistant_turns(doc.get("messages")),
                "runtime_sec": _compute_session_runtime_sec(doc, now=now),
                "status": int(doc.get("status")) if doc.get("status") is not None else None,
                "telemetry_source": "ag2_opentelemetry",
            }
        )

    return {"runs": runs, "total": len(runs)}


async def _require_admin(
    request: Request,
    authorization: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> UserPrincipal:
    """
    Admin gate with three escalating checks:

    1. Auth provider granted "admin" role in the JWT          (production)
    2. User's email is in app.json admins allowlist           (default path)
    3. Auth is disabled (dev mode) — pass through             (local dev)

    Any of the three is sufficient. All three can coexist.
    """
    principal = await require_user(request, authorization)

    # Already has admin role (auth provider assigned it)
    if principal.has_role("admin"):
        return principal

    # Email allowlist promotion
    if is_admin_by_email(principal.email):
        logger.debug("[admin] email-promoted to admin: %s", principal.email)
        principal.roles = list(principal.roles) + ["admin"]
        return principal

    # Dev mode with no roles configured — let it through so devs aren't locked out
    if not is_auth_enabled() and not principal.roles:
        logger.debug("[admin] auth disabled + no roles — passing through for dev")
        return principal

    raise HTTPException(status_code=403, detail="Admin access required")


# ---------------------------------------------------------------------------
# Stats — aggregate across persisted chat sessions
# ---------------------------------------------------------------------------

@router.get("/stats")
async def get_admin_stats(
    app_id: str | None = Query(None, description="Filter stats to a specific app"),
    user: UserPrincipal = Depends(_require_admin),
):
    """Aggregate lifecycle stats. Token metering is served by /api/admin/usage."""
    try:
        return await _build_persisted_admin_stats(app_id=app_id)
    except Exception as e:
        logger.warning("[admin] stats query failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to load persisted admin stats") from e


# ---------------------------------------------------------------------------
# Runs — recent persisted workflow sessions
# ---------------------------------------------------------------------------

@router.get("/runs")
async def get_admin_runs(
    app_id: str | None = Query(None, description="Filter by app_id"),
    active_only: bool = Query(False, description="Only return runs still in progress"),
    limit: int = Query(100, ge=1, le=500, description="Max runs to return"),
    user: UserPrincipal = Depends(_require_admin),
):
    """List current and recent workflow runs, preferring persisted chat sessions."""
    try:
        return await _build_persisted_admin_runs(app_id=app_id, active_only=active_only, limit=limit)
    except Exception as e:
        logger.warning("[admin] runs query failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to load persisted admin runs") from e


# ---------------------------------------------------------------------------
# Usage — token metering from runtime usage events
# ---------------------------------------------------------------------------

@router.get("/usage")
async def get_admin_usage(
    app_id: str | None = Query(None, description="Filter usage to a specific app"),
    user_id: str | None = Query(None, description="Filter usage to a specific end user"),
    limit: int = Query(500, ge=1, le=1000, description="Max usage events to aggregate"),
    user: UserPrincipal = Depends(_require_admin),
):
    """Return neutral token usage measurements from the runtime usage ledger."""
    try:
        from mozaiksai.core.usage import (
            get_runtime_token_budget_alert_ledger,
            get_runtime_usage_ledger,
        )

        ledger = get_runtime_usage_ledger()
        alert_ledger = get_runtime_token_budget_alert_ledger()
        usage = await ledger.query_usage(app_id=app_id, user_id=user_id, limit=limit)
        usage = await _enrich_usage_with_app_registry(usage)
        return {
            **usage,
            "token_budget_alerts": await alert_ledger.query_alerts(
                app_id=app_id,
                user_id=user_id,
                limit=min(limit, 100),
            ),
        }
    except Exception as e:
        logger.warning("[admin] usage query failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to load runtime usage") from e


@router.delete("/usage/orphaned")
async def purge_orphaned_usage(
    user: UserPrincipal = Depends(_require_admin),
):
    """Delete usage events for apps that no longer exist in the app registry."""
    try:
        from mozaiksai.core.core_config import get_mongo_client
        from mozaiksai.core.data.persistence.namespaces import SYSTEM_DATABASE
        from mozaiksai.core.usage import get_runtime_usage_ledger

        client = get_mongo_client()
        app_records = await client[SYSTEM_DATABASE]["AppRegistryRecords"].distinct("app_id")
        known_app_ids = [str(aid) for aid in app_records if aid]
        deleted = await get_runtime_usage_ledger().purge_orphaned_usage(known_app_ids=known_app_ids)
        return {"deleted": deleted, "known_apps": len(known_app_ids)}
    except Exception as e:
        logger.warning("[admin] orphaned usage purge failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to purge orphaned usage") from e


# ---------------------------------------------------------------------------
# Sessions — persisted chat sessions from MongoDB
# ---------------------------------------------------------------------------

@router.get("/sessions")
async def get_admin_sessions(
    app_id: str | None = Query(None, description="Filter by app_id"),
    workflow: str | None = Query(None, description="Filter by workflow name"),
    limit: int = Query(50, ge=1, le=200),
    user: UserPrincipal = Depends(_require_admin),
):
    """List persisted chat sessions from MongoDB (most recent first)."""
    try:
        from mozaiksai.core.core_config import get_mongo_client
        client = get_mongo_client()
        coll = client["mozaiksai"]["ChatSessions"]

        query: dict = {}
        if app_id:
            query["app_id"] = app_id
        if workflow:
            query["workflow_name"] = workflow

        cursor = coll.find(
            query,
            {
                "_id": 1,
                "app_id": 1,
                "workflow_name": 1,
                "user_id": 1,
                "status": 1,
                "created_at": 1,
                "ended_at": 1,
                "duration_sec": 1,
            },
        ).sort("created_at", -1).limit(limit)

        sessions = []
        async for doc in cursor:
            doc["id"] = str(doc.pop("_id"))
            sessions.append(doc)

        return {"sessions": sessions, "total": len(sessions)}
    except Exception as e:
        logger.warning("[admin] sessions query failed: %s", e)
        return {"sessions": [], "total": 0, "error": "Sessions query failed"}


# ---------------------------------------------------------------------------
# Config — read the framework-owned admin shell config
# ---------------------------------------------------------------------------

@router.get("/config")
async def get_admin_config(
    user: UserPrincipal = Depends(_require_admin),
):
    """Return the active admin shell config: registry pages + runtime + module panels."""
    app_root = _resolve_admin_app_root()
    registry = load_admin_registry(app_root)
    config = {
        "pages": [p.model_dump() for p in registry.enabled_pages()],
        "runtime_panels": list(DEFAULT_RUNTIME_PANELS),
        "module_panels": _load_module_admin_panels(app_root),
    }
    return config


# ---------------------------------------------------------------------------
# Health — quick liveness check (no data, just confirms admin API is up)
# ---------------------------------------------------------------------------

@router.get("/health")
async def get_admin_health(
    user: UserPrincipal = Depends(_require_admin),
):
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_admin_app_root() -> Path:
    """Find the active app root without importing the platform host."""
    return resolve_admin_app_root_path()


def _load_module_admin_panels(app_root: Path) -> list[dict]:
    """Discover module panels from modules/{module}/contracts/admin.yaml."""
    from mozaiksai.core.runtime.app.module_loader import ModuleAdminManifest

    modules_dir = app_root / "modules"
    if not modules_dir.is_dir():
        return []

    panels: list[dict] = []
    for module_dir in sorted(modules_dir.iterdir(), key=lambda d: d.name.lower()):
        if not module_dir.is_dir():
            continue
        admin_path = module_dir / "contracts" / "admin.yaml"
        if not admin_path.exists():
            continue
        try:
            raw = yaml.safe_load(admin_path.read_text(encoding="utf-8")) or {}
            manifest = ModuleAdminManifest.model_validate(raw)
        except Exception as exc:
            logger.warning("[admin] failed to read %s: %s", admin_path, exc)
            continue
        for manifest_panel in manifest.panels:
            panel = manifest_panel.model_dump(mode="python")
            panel_id = panel.get("id")
            if not isinstance(panel_id, str) or not panel_id.strip():
                continue
            panel["module_id"] = module_dir.name
            panel["source"] = "module"
            panels.append(panel)
    return panels
