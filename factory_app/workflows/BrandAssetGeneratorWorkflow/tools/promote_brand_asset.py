"""promote_brand_asset — promote a generated brand asset to asset_manifest.json."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated, Any


async def promote_brand_asset(
    asset_id: Annotated[str, "asset_id from generated_brand_assets to promote"],
    role: Annotated[str, "Asset role: logo, icon, hero, splash, or og_image"],
    context_variables: Annotated[Any | None, "Runtime context"] = None,
    alt_text: Annotated[str | None, "Optional alt text for the asset"] = None,
) -> dict[str, Any]:
    """Promote a generated brand asset to asset_manifest.json."""
    workspace = os.getenv("MOZAIKS_APP_WORKSPACE_PATH", "").strip()
    if not workspace:
        return {"ok": False, "error": "MOZAIKS_APP_WORKSPACE_PATH not set"}

    app_id = ""
    if context_variables is not None:
        if isinstance(context_variables, dict):
            app_id = context_variables.get("app_id", "") or ""
        else:
            app_id = getattr(context_variables, "app_id", "") or ""

    # idempotency guard
    if context_variables is not None:
        promoted: list = []
        if isinstance(context_variables, dict):
            promoted = context_variables.get("promoted_brand_assets", []) or []
        else:
            promoted = getattr(context_variables, "promoted_brand_assets", []) or []
        if any(e.get("asset_id") == asset_id and e.get("role") == role for e in promoted):
            content_url = (
                f"/api/media/assets/{app_id}/{asset_id}/content"
                if app_id
                else f"/api/media/assets/{asset_id}/content"
            )
            return {
                "ok": True,
                "already_promoted": True,
                "asset_id": asset_id,
                "role": role,
                "content_url": content_url,
            }

    content_url = (
        f"/api/media/assets/{app_id}/{asset_id}/content"
        if app_id
        else f"/api/media/assets/{asset_id}/content"
    )

    # write asset_manifest.json
    config_dir = Path(workspace) / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = config_dir / "asset_manifest.json"

    manifest: dict = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}

    manifest.setdefault("assets", {})
    manifest["assets"][role] = {
        "asset_id": asset_id,
        "content_url": content_url,
        "alt_text": alt_text or "",
        "promoted": True,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # mark asset promoted in store (best-effort)
    try:
        from mozaiksai.core.media.store import get_media_asset_store
        from mozaiksai.core.media import MediaPromotionTarget

        store = get_media_asset_store()
        await store.mark_asset_promoted(asset_id, MediaPromotionTarget.BRAND_ASSET)
    except Exception:
        pass

    # update context_variables
    if context_variables is not None:
        entry = {"asset_id": asset_id, "role": role, "content_url": content_url}
        if isinstance(context_variables, dict):
            context_variables.setdefault("promoted_brand_assets", []).append(entry)
        else:
            lst = getattr(context_variables, "promoted_brand_assets", None) or []
            lst.append(entry)
            try:
                context_variables.promoted_brand_assets = lst
            except AttributeError:
                pass

    return {
        "ok": True,
        "asset_id": asset_id,
        "role": role,
        "content_url": content_url,
        "manifest_path": str(manifest_path),
    }
