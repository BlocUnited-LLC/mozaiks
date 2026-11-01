"""Load the planned app images and brand context before the art director runs."""

from __future__ import annotations

import json
import logging
from typing import Any

from factory_app.workflows._shared.artifact_bundle import read_artifact_bundle
from factory_app.workflows._shared.platform.current_build import resolve_current_build_artifact
from factory_app.workflows.AppMediaGenerator.tools.media_bundle import (
    ERROR_PREFIX,
    parse_asset_manifest,
    planned_generated_entries,
)
from mozaiksai.core.workflow.context.frozen import detach

logger = logging.getLogger(__name__)

_REQUEST_FIELDS = ("asset_id", "kind", "brief", "aspect", "alt", "usage")


def _json_file(files: dict[str, Any], path: str) -> dict[str, Any]:
    raw = files.get(path)
    if not isinstance(raw, str):
        return {}
    try:
        value = json.loads(raw)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _brand_context(files: dict[str, Any]) -> dict[str, Any]:
    app = _json_file(files, "app.json")
    theme = _json_file(files, "brand/theme_config.json")
    return {
        "app_name": app.get("name") or app.get("appName") or app.get("title"),
        "description": app.get("description") or app.get("tagline"),
        "colors": theme.get("colors") or theme.get("core"),
        "fonts": theme.get("fonts"),
    }


async def load_media_requests(context_variables: Any = None) -> dict[str, Any]:
    if context_variables is None:
        return {"status": "skipped", "reason": "no_context"}
    app_id = str(detach(context_variables.get("app_id")) or "")
    user_id = str(detach(context_variables.get("user_id")) or "")
    try:
        _, _, artifact = await resolve_current_build_artifact(
            context_variables, app_id=app_id, user_id=user_id, error_prefix=ERROR_PREFIX,
        )
        files, diagnostics = await read_artifact_bundle(artifact)
        if any(item.get("blocking") for item in diagnostics):
            raise ValueError(f"{ERROR_PREFIX}_bundle_unreadable")
        requests = [
            {field: entry.get(field) for field in _REQUEST_FIELDS}
            for entry in planned_generated_entries(parse_asset_manifest(files))
        ]
        brand = _brand_context(files)
    except (ValueError, PermissionError) as exc:
        # The publisher re-resolves the source and reports the failure to the user.
        logger.warning("App media requests unavailable: %s", exc)
        requests, brand = [], {}
    context_variables.set("media_requests", requests)
    context_variables.set("media_brand_context", brand)
    return {"status": "loaded", "request_count": len(requests)}
