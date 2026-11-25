"""promote_brand_asset — promote an owned generated brand asset to its manifest."""


import json
import logging
import os
from pathlib import Path
from typing import Any

from mozaiksai.core.media import MediaPromotionTarget
from mozaiksai.core.media.store import get_media_asset_store
from mozaiksai.core.workflow.agents.factory import active_workflow_tool_run

from .stage_logo import stage_logo

logger = logging.getLogger(__name__)


async def promote_brand_asset(
    asset_id: str,
    role: str,
    context_variables: Any | None = None,
    alt_text: str | None = None,
) -> dict[str, Any]:
    """Publish an asset from the active workflow chat, then save its role entry.

    Scope comes from the immutable runtime invocation, never tool arguments or
    mutable context identifiers. The existing role-keyed manifest layout is kept.
    """
    try:
        run_identity = active_workflow_tool_run()
    except PermissionError:
        return {"ok": False, "error": "An active brand workflow is required"}
    workflow, app_id, chat_id, user_id = run_identity
    if workflow != "BrandAssetGeneratorWorkflow" or not all((app_id, chat_id, user_id)):
        return {"ok": False, "error": "An active brand workflow is required"}
    if role not in {"logo", "icon", "hero", "splash", "og_image"}:
        return {"ok": False, "error": "Unknown brand asset role"}
    store = get_media_asset_store()
    try:
        asset = await store.get_asset(app_id=app_id, asset_id=asset_id)
    except Exception:
        return {"ok": False, "error": "Media asset could not be read"}
    if asset is None or asset.source_chat_id != chat_id:
        return {"ok": False, "error": "Media asset is not available in this workflow chat"}

    def require_active_run() -> None:
        if active_workflow_tool_run() != run_identity:
            raise PermissionError("Workflow scope changed")

    if context_variables is not None and any(
        context_variables.get(key) is not None for key in ("run_build_binding", "artifact_version_id", "change_request_id")
    ):
        if role != "logo":
            return {"ok": False, "error": "The logo refinement only stages the selected logo"}
        try:
            result = await stage_logo(context_variables, asset=asset, run=run_identity, guard=require_active_run)
            require_active_run()
        except (ValueError, PermissionError, OSError, RuntimeError) as exc:
            logger.warning("Brand logo staging failed: %s", exc)
            return {"ok": False, "error": "The chosen logo could not be staged in this app build"}
        context_variables.set("brand_assets_complete", True)
        return result

    workspace = os.getenv("MOZAIKS_APP_WORKSPACE_PATH", "").strip()
    if not workspace:
        return {"ok": False, "error": "MOZAIKS_APP_WORKSPACE_PATH not set"}
    content_url = (
        asset.content_ref if str(asset.content_ref or "").startswith("https://")
        else f"/api/media/assets/{app_id}/{asset_id}/content"
    )
    manifest_path = Path(workspace) / "config" / "asset_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
        manifest.setdefault("assets", {})[role] = {
            "asset_id": asset_id, "content_url": content_url,
            "alt_text": alt_text or "", "promoted": True,
        }
    except (OSError, ValueError, TypeError, AttributeError):
        return {"ok": False, "error": "Brand asset manifest could not be prepared"}

    try:
        require_active_run()
        promoted = await store.mark_asset_promoted(
            app_id=app_id, asset_id=asset_id,
            promotion_target=MediaPromotionTarget.BRAND_ASSET,
            promoted_by=user_id, expected_source_chat_id=chat_id,
            scope_guard=require_active_run,
        )
        if promoted is None:
            return {"ok": False, "error": "Media asset is no longer available in this workflow chat"}
    except Exception:
        return {"ok": False, "error": "Media promotion failed"}

    try:
        if active_workflow_tool_run() != run_identity:
            raise PermissionError("Workflow scope changed")
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    except OSError:
        return {"ok": False, "error": "Asset was published, but its brand manifest could not be saved"}

    if context_variables is not None:
        entries = list(context_variables.get("promoted_brand_assets", []) or [])
        entry = {"asset_id": asset_id, "role": role, "content_url": content_url}
        if not any(item.get("asset_id") == asset_id and item.get("role") == role for item in entries):
            entries.append(entry)
            context_variables.set("promoted_brand_assets", entries)
        context_variables.set("brand_assets_complete", True)

    return {
        "ok": True, "asset_id": asset_id, "role": role, "content_url": content_url,
        "manifest_path": str(manifest_path),
    }
