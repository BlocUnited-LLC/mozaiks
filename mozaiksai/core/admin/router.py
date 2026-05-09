# ==============================================================================
# FILE: mozaiksai/core/admin/router.py
# DESCRIPTION: First-class admin API. All routes require the "admin" role.
#              Surfaces data already captured by the runtime — no new storage.
# ==============================================================================
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml
from fastapi import APIRouter, Depends, Query, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import JSONResponse

from mozaiksai.core.admin.contract import (
    build_default_admin_shell_config,
    build_default_runtime_panels,
    normalize_admin_section_name,
    normalize_admin_shell_sections,
)
from mozaiksai.core.auth.dependencies import require_user, UserPrincipal
from mozaiksai.core.admin.paths import resolve_admin_app_root as resolve_admin_app_root_path
from mozaiksai.core.auth.adapters.registry import is_auth_enabled
from mozaiksai.core.admin.email_promotion import is_admin_by_email
from mozaiksai.core.observability.performance_manager import get_performance_manager
from mozaiksai.core.data.models import WorkflowStatus
from logs.logging_config import get_core_logger

logger = get_core_logger("admin.router")

router = APIRouter(prefix="/api/admin", tags=["admin"])


_bearer = HTTPBearer(auto_error=False)


DEFAULT_ADMIN_SHELL_CONFIG = build_default_admin_shell_config()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _get_chat_sessions_collection():
    from mozaiksai.core.core_config import get_mongo_client

    client = get_mongo_client()
    return client["mozaiksai"]["ChatSessions"]


def _serialize_datetime(value: Any) -> Optional[str]:
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            return None
    return None


def _count_assistant_turns(messages: Any) -> int:
    if not isinstance(messages, list):
        return 0
    return sum(1 for message in messages if isinstance(message, dict) and message.get("role") == "assistant")


def _compute_session_runtime_sec(doc: dict[str, Any], *, now: datetime) -> float:
    stored_duration = float(doc.get("duration_sec") or 0.0)
    created_at = doc.get("created_at")
    completed_at = doc.get("completed_at")

    if hasattr(created_at, "tzinfo") and created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    if hasattr(completed_at, "tzinfo") and completed_at.tzinfo is None:
        completed_at = completed_at.replace(tzinfo=timezone.utc)

    try:
        if created_at and completed_at:
            return max(stored_duration, float((completed_at - created_at).total_seconds()))
        if created_at and doc.get("status") == int(WorkflowStatus.IN_PROGRESS):
            return max(stored_duration, float((now - created_at).total_seconds()))
    except Exception:
        return stored_duration
    return stored_duration


async def _build_persisted_admin_stats() -> dict[str, Any]:
    coll = _get_chat_sessions_collection()
    pipeline = [
        {
            "$project": {
                "status": 1,
                "usage_prompt_tokens_final": {"$ifNull": ["$usage_prompt_tokens_final", 0]},
                "usage_completion_tokens_final": {"$ifNull": ["$usage_completion_tokens_final", 0]},
                "usage_total_cost_final": {"$ifNull": ["$usage_total_cost_final", 0.0]},
                "tool_calls_final": {"$ifNull": ["$tool_calls_final", 0]},
                "errors_final": {"$ifNull": ["$errors_final", 0]},
                "assistant_turns": {
                    "$size": {
                        "$filter": {
                            "input": {"$ifNull": ["$messages", []]},
                            "as": "message",
                            "cond": {"$eq": ["$$message.role", "assistant"]},
                        }
                    }
                },
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
                "total_agent_turns": {"$sum": "$assistant_turns"},
                "total_tool_calls": {"$sum": "$tool_calls_final"},
                "total_errors": {"$sum": "$errors_final"},
                "total_prompt_tokens": {"$sum": "$usage_prompt_tokens_final"},
                "total_completion_tokens": {"$sum": "$usage_completion_tokens_final"},
                "total_cost": {"$sum": "$usage_total_cost_final"},
            }
        },
    ]
    rows = [doc async for doc in coll.aggregate(pipeline)]
    aggregate = rows[0] if rows else {}
    return {
        "active_chats": int(aggregate.get("active_chats") or 0),
        "tracked_chats": int(aggregate.get("tracked_chats") or 0),
        "total_agent_turns": int(aggregate.get("total_agent_turns") or 0),
        "total_tool_calls": int(aggregate.get("total_tool_calls") or 0),
        "total_errors": int(aggregate.get("total_errors") or 0),
        "total_prompt_tokens": int(aggregate.get("total_prompt_tokens") or 0),
        "total_completion_tokens": int(aggregate.get("total_completion_tokens") or 0),
        "total_cost": float(aggregate.get("total_cost") or 0.0),
    }


async def _build_persisted_admin_runs(
    *,
    app_id: Optional[str],
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
            "usage_prompt_tokens_final": 1,
            "usage_completion_tokens_final": 1,
            "usage_total_cost_final": 1,
            "tool_calls_final": 1,
            "errors_final": 1,
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
                "tool_calls": int(doc.get("tool_calls_final") or 0),
                "errors": int(doc.get("errors_final") or 0),
                "prompt_tokens": int(doc.get("usage_prompt_tokens_final") or 0),
                "completion_tokens": int(doc.get("usage_completion_tokens_final") or 0),
                "cost": float(doc.get("usage_total_cost_final") or 0.0),
                "runtime_sec": _compute_session_runtime_sec(doc, now=now),
                "status": int(doc.get("status")) if doc.get("status") is not None else None,
            }
        )

    return {"runs": runs, "total": len(runs)}


async def _require_admin(
    request: Request,
    authorization: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
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
        logger.debug(f"[admin] email-promoted to admin: {principal.email}")
        principal.roles = list(principal.roles) + ["admin"]
        return principal

    # Dev mode with no roles configured — let it through so devs aren't locked out
    if not is_auth_enabled() and not principal.roles:
        logger.debug("[admin] auth disabled + no roles — passing through for dev")
        return principal

    raise HTTPException(status_code=403, detail="Admin access required")


# ---------------------------------------------------------------------------
# Stats — aggregate across persisted chat sessions (fallback to in-memory)
# ---------------------------------------------------------------------------

@router.get("/stats")
async def get_admin_stats(
    user: UserPrincipal = Depends(_require_admin),
):
    """Aggregate runtime stats: active chats, token usage, errors."""
    try:
        return await _build_persisted_admin_stats()
    except Exception as e:
        logger.warning(f"[admin] stats query failed, falling back to live runtime state: {e}")
        pm = await get_performance_manager()
        agg = await pm.aggregate()
        return {
            "active_chats": agg["active_chats"],
            "tracked_chats": agg["tracked_chats"],
            "total_agent_turns": agg["total_agent_turns"],
            "total_tool_calls": agg["total_tool_calls"],
            "total_errors": agg["total_errors"],
            "total_prompt_tokens": agg["total_prompt_tokens"],
            "total_completion_tokens": agg["total_completion_tokens"],
            "total_cost": agg["total_cost"],
        }


# ---------------------------------------------------------------------------
# Runs — recent persisted workflow sessions (fallback to live snapshots)
# ---------------------------------------------------------------------------

@router.get("/runs")
async def get_admin_runs(
    app_id: Optional[str] = Query(None, description="Filter by app_id"),
    active_only: bool = Query(False, description="Only return runs still in progress"),
    limit: int = Query(100, ge=1, le=500, description="Max runs to return"),
    user: UserPrincipal = Depends(_require_admin),
):
    """List current and recent workflow runs, preferring persisted chat sessions."""
    try:
        return await _build_persisted_admin_runs(app_id=app_id, active_only=active_only, limit=limit)
    except Exception as e:
        logger.warning(f"[admin] runs query failed, falling back to live runtime state: {e}")
        pm = await get_performance_manager()
        runs = await pm.snapshot_all()
        if app_id:
            runs = [r for r in runs if r.get("app_id") == app_id]
        if active_only:
            runs = [r for r in runs if r.get("ended_at") is None]
        return {"runs": runs[:limit], "total": min(len(runs), limit)}


# ---------------------------------------------------------------------------
# Sessions — persisted chat sessions from MongoDB
# ---------------------------------------------------------------------------

@router.get("/sessions")
async def get_admin_sessions(
    app_id: Optional[str] = Query(None, description="Filter by app_id"),
    workflow: Optional[str] = Query(None, description="Filter by workflow name"),
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
                "usage_prompt_tokens_final": 1,
                "usage_completion_tokens_final": 1,
                "usage_total_cost_final": 1,
            },
        ).sort("created_at", -1).limit(limit)

        sessions = []
        async for doc in cursor:
            doc["id"] = str(doc.pop("_id"))
            sessions.append(doc)

        return {"sessions": sessions, "total": len(sessions)}
    except Exception as e:
        logger.warning(f"[admin] sessions query failed: {e}")
        return {"sessions": [], "total": 0, "error": str(e)}


# ---------------------------------------------------------------------------
# Config — read the framework-owned admin shell config
# ---------------------------------------------------------------------------

@router.get("/config")
async def get_admin_config(
    user: UserPrincipal = Depends(_require_admin),
):
    """Return the active admin shell config, including feature admin panel manifests."""
    app_root = _resolve_admin_app_root()
    return _merge_module_admin_panels(deepcopy(DEFAULT_ADMIN_SHELL_CONFIG), app_root)


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
    """Load feature-owned admin panels declared by modules/{module}/admin.yaml."""
    from mozaiksai.core.runtime.app.module_loader import ModuleAdminManifest

    modules_dir = app_root / "modules"
    if not modules_dir.is_dir():
        return []

    panels: list[dict] = []
    for module_dir in sorted(modules_dir.iterdir(), key=lambda item: item.name.lower()):
        if not module_dir.is_dir():
            continue
        admin_path = module_dir / "admin.yaml"
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
            normalized = _normalize_module_admin_panel(panel, module_id=module_dir.name)
            panels.append(normalized)
    return panels


def _infer_admin_panel_section(panel: dict) -> str:
    explicit = panel.get("section") or panel.get("category") or panel.get("group")
    if isinstance(explicit, str) and explicit.strip():
        return normalize_admin_section_name(explicit)

    text = " ".join(
        str(panel.get(key) or "")
        for key in ("id", "label", "description")
    ).lower()
    if any(token in text for token in ("user", "role", "permission", "auth", "account", "member")):
        return "users"
    if any(token in text for token in ("billing", "bill", "subscription", "invoice", "payment", "stripe", "revenue", "plan")):
        return "billing"
    if any(token in text for token in ("run", "session", "workflow", "usage", "health", "cost", "token", "error", "performance", "telemetry")):
        return "usage"
    if any(token in text for token in ("activity", "audit", "log", "event", "history")):
        return "activity"
    if any(token in text for token in ("setting", "config", "domain", "brand", "theme", "environment")):
        return "settings"
    if any(token in text for token in ("support", "ticket", "notification", "alert", "message")):
        return "support"
    return "integrations"


def _normalize_module_admin_panel(panel: dict, *, module_id: str) -> dict:
    normalized = dict(panel)
    normalized["id"] = str(panel.get("id")).strip()
    normalized["module_id"] = module_id
    normalized["label"] = normalized.get("label") or normalized["id"]
    normalized["source"] = "module"
    normalized["section"] = _infer_admin_panel_section(normalized)

    renderer = normalized.get("renderer")
    normalized["renderer"] = renderer if renderer in {"schema", "custom_component"} else "schema"

    if normalized["renderer"] == "custom_component":
        component = normalized.get("component")
        normalized["component"] = component if isinstance(component, str) and component.strip() else normalized["id"]
        normalized["layout"] = None
        normalized["sections"] = []
    else:
        layout = normalized.get("layout")
        normalized["layout"] = layout if layout in {"grid", "sidebar", "full-width", "split"} else "full-width"
        normalized["component"] = None
        normalized["sections"] = normalized.get("sections") if isinstance(normalized.get("sections"), list) else []

    permissions = normalized.get("permissions")
    normalized["permissions"] = permissions if isinstance(permissions, list) else []
    return normalized


def _normalize_runtime_panel(panel: dict) -> dict | None:
    panel_id = panel.get("id")
    if not isinstance(panel_id, str) or not panel_id.strip():
        return None

    normalized = dict(panel)
    normalized["id"] = panel_id.strip()
    normalized["label"] = normalized.get("label") or normalized["id"]
    section = normalized.get("section")
    normalized["section"] = (
        normalize_admin_section_name(section)
        if isinstance(section, str) and section.strip()
        else "usage"
    )
    normalized["source"] = "runtime"
    return normalized


def _normalize_admin_shell_config(config: dict) -> dict:
    normalized = build_default_admin_shell_config()
    normalized["sections"] = normalize_admin_shell_sections(config.get("sections"))

    runtime_panels = config.get("runtime_panels")
    if isinstance(runtime_panels, list):
        normalized_panels = []
        for panel in runtime_panels:
            if not isinstance(panel, dict):
                continue
            parsed = _normalize_runtime_panel(panel)
            if parsed:
                normalized_panels.append(parsed)
        normalized["runtime_panels"] = normalized_panels or build_default_runtime_panels()

    module_panels = config.get("module_panels")
    normalized["module_panels"] = module_panels if isinstance(module_panels, list) else []
    return normalized


def _merge_module_admin_panels(config: dict, app_root: Path) -> dict:
    config = _normalize_admin_shell_config(config)
    module_panels = _load_module_admin_panels(app_root)
    if not module_panels:
        return config

    existing = config["module_panels"]
    seen = {
        panel.get("id")
        for panel in existing
        if isinstance(panel, dict) and isinstance(panel.get("id"), str)
    }
    for panel in module_panels:
        if panel["id"] not in seen:
            existing.append(panel)
            seen.add(panel["id"])
    return config
