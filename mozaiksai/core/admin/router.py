# ==============================================================================
# FILE: mozaiksai/core/admin/router.py
# DESCRIPTION: First-class admin API. All routes require the "admin" role.
#              Surfaces data already captured by the runtime — no new storage.
# ==============================================================================
from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Optional

import yaml
from fastapi import APIRouter, Depends, Query, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import JSONResponse

from mozaiksai.core.auth.dependencies import require_user, UserPrincipal
from mozaiksai.core.auth.adapters.registry import is_auth_enabled
from mozaiksai.core.admin.email_promotion import is_admin_by_email
from mozaiksai.core.observability.performance_manager import get_performance_manager
from logs.logging_config import get_core_logger

logger = get_core_logger("admin.router")

router = APIRouter(prefix="/api/admin", tags=["admin"])


_bearer = HTTPBearer(auto_error=False)


DEFAULT_ADMIN_CONFIG = {
    "enabled": True,
    "panels": {
        "app": [
            {"id": "stats", "label": "App Overview"},
            {"id": "users", "label": "Users"},
        ],
        "modules": [],
        "runtime": [
            {"id": "stats", "label": "Runtime Stats"},
            {"id": "runs", "label": "Active Runs"},
            {"id": "sessions", "label": "Recent Sessions"},
        ],
    },
    "features": {},
}


async def _require_admin(
    request: Request,
    authorization: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> UserPrincipal:
    """
    Admin gate with three escalating checks:

    1. Auth provider granted "admin" role in the JWT          (production)
    2. User's email is in admin.json admin_emails allowlist   (default path)
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
# Stats — aggregate across all in-memory tracked chats
# ---------------------------------------------------------------------------

@router.get("/stats")
async def get_admin_stats(
    user: UserPrincipal = Depends(_require_admin),
):
    """Aggregate runtime stats: active chats, token usage, errors."""
    pm = await get_performance_manager()
    agg = await pm.aggregate()
    # Omit per-chat detail from the summary endpoint
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
# Runs — live in-memory workflow snapshots
# ---------------------------------------------------------------------------

@router.get("/runs")
async def get_admin_runs(
    app_id: Optional[str] = Query(None, description="Filter by app_id"),
    active_only: bool = Query(False, description="Only return runs still in progress"),
    user: UserPrincipal = Depends(_require_admin),
):
    """List current and recent workflow runs (in-memory snapshots)."""
    pm = await get_performance_manager()
    runs = await pm.snapshot_all()

    if app_id:
        runs = [r for r in runs if r.get("app_id") == app_id]
    if active_only:
        runs = [r for r in runs if r.get("ended_at") is None]

    return {"runs": runs, "total": len(runs)}


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
# Config — read the declarative admin.json
# ---------------------------------------------------------------------------

@router.get("/config")
async def get_admin_config(
    user: UserPrincipal = Depends(_require_admin),
):
    """Return the active admin config, including module admin panel manifests."""
    config_path = _resolve_admin_config_path()
    platform_root = _resolve_platform_root()
    if not config_path.exists():
        return _merge_module_admin_panels(deepcopy(DEFAULT_ADMIN_CONFIG), platform_root)
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            config = deepcopy(DEFAULT_ADMIN_CONFIG)
        return _merge_module_admin_panels(config, platform_root)
    except Exception as e:
        logger.warning(f"[admin] failed to read admin.json: {e}")
        return _merge_module_admin_panels(deepcopy(DEFAULT_ADMIN_CONFIG), platform_root)


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

def _resolve_platform_root() -> Path:
    """Find the active platform root without importing the platform host."""
    platform_path = os.environ.get("PLATFORM_PATH", "")
    if platform_path:
        candidate = Path(platform_path)
        if candidate.is_absolute():
            return candidate
        return (Path(__file__).parents[3] / candidate).resolve()

    monorepo = Path(__file__).parents[3] / "mozaiks-platform" / "app"
    if monorepo.is_dir():
        return monorepo

    return Path(__file__).parents[3] / "platform"


def _resolve_admin_config_path() -> Path:
    """Find admin.json relative to the active platform root."""
    return _resolve_platform_root() / "config" / "admin.json"


def _load_module_admin_panels(platform_root: Path) -> list[dict]:
    """Load module-owned admin panels declared by modules/{module}/admin.yaml."""
    modules_dir = platform_root / "modules"
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
        except Exception as exc:
            logger.warning("[admin] failed to read %s: %s", admin_path, exc)
            continue
        if not isinstance(raw, dict):
            continue
        raw_panels = raw.get("panels")
        if not isinstance(raw_panels, list):
            continue
        for panel in raw_panels:
            if not isinstance(panel, dict):
                continue
            panel_id = panel.get("id")
            if not isinstance(panel_id, str) or not panel_id.strip():
                continue
            normalized = dict(panel)
            normalized["id"] = panel_id.strip()
            normalized.setdefault("module_id", module_dir.name)
            normalized.setdefault("label", normalized["id"])
            normalized.setdefault("source", "module")
            panels.append(normalized)
    return panels


def _normalize_panel_groups(config: dict) -> dict:
    panels = config.get("panels")
    if isinstance(panels, dict):
        config["panels"] = {
            "app": panels.get("app") if isinstance(panels.get("app"), list) else [],
            "modules": panels.get("modules") if isinstance(panels.get("modules"), list) else [],
            "runtime": panels.get("runtime") if isinstance(panels.get("runtime"), list) else [],
        }
        return config

    if isinstance(panels, list):
        config["panels"] = {"app": [], "modules": [], "runtime": panels}
        return config

    config["panels"] = {"app": [], "modules": [], "runtime": []}
    return config


def _merge_module_admin_panels(config: dict, platform_root: Path) -> dict:
    config = _normalize_panel_groups(config)
    module_panels = _load_module_admin_panels(platform_root)
    if not module_panels:
        return config

    existing = config["panels"]["modules"]
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
