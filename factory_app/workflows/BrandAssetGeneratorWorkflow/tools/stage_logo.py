"""Stage the selected chat image in a new version of the owner's app bundle."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

from factory_app.workflows._shared.brand_revision import stage_brand_revision
from mozaiksai.core.media.store import get_media_asset_store
from mozaiksai.core.media.types import GeneratedMediaAsset
from mozaiksai.core.runtime.app.theme_contract import APP_THEME_PATH

_EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


async def stage_logo(
    context: Any, *, asset: GeneratedMediaAsset, run: tuple[str, str, str, str], guard: Callable[[], None],
) -> dict[str, Any]:
    workflow, host, chat, _ = run
    if asset.app_id != host or asset.source_chat_id != chat or asset.source_workflow != workflow:
        raise ValueError("The logo must come from this workflow chat")
    if asset.media_type not in _EXTENSIONS:
        raise ValueError("The selected logo must be a PNG, JPEG, or WebP image")

    async def apply_logo(files: dict[str, str | bytes]) -> dict[str, Any]:
        theme = json.loads(files.get(APP_THEME_PATH, "{}"))
        if not isinstance(theme, dict) or not isinstance(theme.setdefault("assets", {}), dict):
            raise ValueError("The source bundle does not have a valid theme")
        data = await get_media_asset_store().get_asset_content(asset)
        digest = hashlib.sha256(data).hexdigest()
        if not data or digest != asset.sha256 or len(data) != asset.size_bytes:
            raise ValueError("The selected logo failed its integrity check")
        filename = f"logo-{digest[:24]}.{_EXTENSIONS[asset.media_type]}"
        asset_path = f"brand/assets/{filename}"
        if asset_path in files and files[asset_path] != data:
            raise ValueError("A different existing asset uses the selected logo path")
        files[asset_path] = data
        theme["assets"]["logo"] = filename
        files[APP_THEME_PATH] = json.dumps(theme, ensure_ascii=False, indent=2) + "\n"
        return {"sha256": digest, "path": asset_path}

    child = await stage_brand_revision(
        context, run=run, workflow_sequence="logo_revision", selection={"selected_logo": asset.asset_id},
        apply=apply_logo, guard=guard,
    )
    return {"ok": True, "asset_id": asset.asset_id, "role": "logo", "draft_build_record_id": child.id}
