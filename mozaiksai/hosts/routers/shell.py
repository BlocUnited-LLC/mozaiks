"""Shell router — theme config and page schema routes.

Routes:
    GET /api/theme-config
    GET /api/themes/{app_id}
    GET /api/app-assets/{asset_id}
    GET /api/pages/{name}
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from mozaiksai.core.auth.dependencies import validate_path_id
from mozaiksai.core.metrics.usage_instrumentation import record_page_view
from mozaiksai.core.runtime.app.page_schema import (
    PageSchemaValidationError,
    load_and_validate_page_schema,
    safe_page_schema_error_detail,
)
from mozaiksai.core.runtime.app.theme_contract import validate_page_title_theme
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
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to read theme config") from exc
    try:
        validate_page_title_theme(config)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return config


@router.get("/api/themes/{app_id}")
async def get_app_theme(app_id: str):
    # Validate the path segment even though theme config is currently app-agnostic,
    # to prevent malformed IDs (e.g., traversal sequences) from reaching future logic.
    validate_path_id(app_id, "app_id")
    return await get_theme_config()


_APP_ASSET_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".avif": "image/avif",
    ".svg": "image/svg+xml",
}
# Asset files are app-owned; an SVG opened directly must not run script on this origin.
_APP_ASSET_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-cache",
}


def _app_asset_entry(app_root: Path, asset_id: str) -> dict | None:
    manifest_path = app_root / "config" / "asset_manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    assets = manifest.get("assets") if isinstance(manifest, dict) else None
    for entry in assets if isinstance(assets, list) else []:
        if isinstance(entry, dict) and entry.get("asset_id") == asset_id:
            return entry
    return None


@router.get("/api/app-assets/{asset_id}")
async def get_app_asset(asset_id: str):
    """Serve an image the active app's asset_manifest declares with a bundle path."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", asset_id):
        raise HTTPException(status_code=400, detail="Invalid asset id")
    app_root = resolve_active_app_root().resolve()
    entry = _app_asset_entry(app_root, asset_id)
    rel_path = entry.get("path") if entry else None
    if not isinstance(rel_path, str) or not rel_path.strip():
        raise HTTPException(status_code=404, detail="Asset not available")
    file_path = (app_root / rel_path).resolve()
    media_type = _APP_ASSET_MEDIA_TYPES.get(file_path.suffix.lower())
    if not file_path.is_relative_to(app_root) or media_type is None or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Asset not available")
    return FileResponse(file_path, media_type=media_type, headers=_APP_ASSET_HEADERS)


@router.get("/api/pages/{name}")
async def get_page_schema(name: str, request: Request):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise HTTPException(status_code=400, detail="Invalid page name")

    record_page_view(
        app_id=str(request.query_params.get("app_id") or "default"),
        page_name=name,
    )

    validated_pages = getattr(request.app.state, "page_schemas", None)
    if isinstance(validated_pages, dict) and name in validated_pages:
        return JSONResponse(content=validated_pages[name])

    page_path = _resolve_page_schema_path(name)
    if not page_path.exists():
        raise HTTPException(status_code=404, detail=f"Page '{name}' not found")

    try:
        action_index = _page_action_index_from_state(request)
        schema = load_and_validate_page_schema(
            page_path,
            expected_name=name,
            action_index=action_index,
            ask_context_index={
                module: frozenset(action for action, safe in actions.items() if safe is True)
                for module, actions in getattr(request.app.state, "module_ask_context_actions", {}).items()
            },
        )
        return JSONResponse(content=schema.model_dump(mode="json", exclude_none=True))
    except PageSchemaValidationError as exc:
        raise HTTPException(
            status_code=500,
            detail=safe_page_schema_error_detail(exc),
        ) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Invalid page schema") from exc


def _page_action_index_from_state(request: Request) -> dict[str, frozenset[str]] | None:
    surfaces = getattr(request.app.state, "module_action_surfaces", None)
    if not isinstance(surfaces, dict):
        return None
    return {
        str(module): frozenset(str(action) for action in actions)
        for module, actions in surfaces.items()
        if isinstance(actions, dict)
    }
