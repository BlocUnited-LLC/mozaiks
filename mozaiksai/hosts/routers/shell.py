"""Shell router — theme config and page schema routes.

Routes:
    GET /api/theme-config
    GET /api/themes/{app_id}
    GET /api/pages/{name}
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from mozaiksai.core.auth.dependencies import validate_path_id
from mozaiksai.core.workflow.paths import resolve_active_app_root
from mozaiksai.resources import resolve_factory_brand_root

router = APIRouter(tags=["shell"])


# ---------------------------------------------------------------------------
# Path helpers (local to this router)
# ---------------------------------------------------------------------------

def _resolve_default_brand_root() -> Path:
    resolved = resolve_factory_brand_root()
    if resolved is not None:
        return resolved
    return (Path(__file__).resolve().parents[3] / "factory_app" / "app" / "brand").resolve()


def _resolve_theme_config_path() -> Path:
    app_root = resolve_active_app_root()
    candidates = [
        (app_root / "brand" / "theme_config.json").resolve(),
        (_resolve_default_brand_root() / "theme_config.json").resolve(),
    ]
    return next((c for c in candidates if c.exists()), candidates[0])


def _resolve_pages_dir() -> Path:
    return (resolve_active_app_root() / "ui" / "pages").resolve()


def _resolve_page_schema_path(name: str) -> Path:
    pages_dir = _resolve_pages_dir()
    candidates = (
        pages_dir / f"{name}.yaml",
        pages_dir / f"{name}.yml",
        pages_dir / name / "page.yaml",
        pages_dir / name / "page.yml",
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/api/theme-config")
async def get_theme_config():
    config_path = _resolve_theme_config_path()
    if not config_path.exists():
        raise HTTPException(status_code=404, detail="Theme config not found")
    try:
        return json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to read theme config") from exc


@router.get("/api/themes/{app_id}")
async def get_app_theme(app_id: str):
    # Validate the path segment even though theme config is currently app-agnostic,
    # to prevent malformed IDs (e.g., traversal sequences) from reaching future logic.
    validate_path_id(app_id, "app_id")
    return await get_theme_config()


@router.get("/api/pages/{name}")
async def get_page_schema(name: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise HTTPException(status_code=400, detail="Invalid page name")

    page_path = _resolve_page_schema_path(name)
    if not page_path.exists():
        raise HTTPException(status_code=404, detail=f"Page '{name}' not found")

    try:
        schema = yaml.safe_load(page_path.read_text(encoding="utf-8"))
        if not isinstance(schema, dict):
            raise ValueError("Page schema must be a YAML mapping")
        return JSONResponse(content=schema)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to read page schema") from exc
