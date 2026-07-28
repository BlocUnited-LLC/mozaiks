from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from mozaiksai.core.auth import UserPrincipal, require_any_auth
from mozaiksai.core.auth.dependencies import validate_path_id
from mozaiksai.core.media.store import MediaContentNotFoundError, get_media_asset_store

router = APIRouter(tags=["media"])


def _safe_download_filename(value: str | None) -> str:
    filename = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip()).strip(".-")
    return filename or "generated-media.bin"


@router.get("/api/media/assets/{app_id}/{asset_id}/content")
async def get_media_asset_content(
    app_id: str,
    asset_id: str,
    download: bool = Query(default=False),
    principal: UserPrincipal = Depends(require_any_auth),
) -> Response:
    _ = principal
    clean_app_id = validate_path_id(app_id, "app_id")
    clean_asset_id = validate_path_id(asset_id, "asset_id")
    store = get_media_asset_store()
    asset = await store.get_asset(app_id=clean_app_id, asset_id=clean_asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Media asset not found")
    try:
        content = await store.get_asset_content(asset)
    except MediaContentNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Media content not found") from exc

    disposition = "attachment" if download else "inline"
    filename = _safe_download_filename(asset.filename)
    return Response(
        content=content,
        media_type=asset.media_type,
        headers={
            "Cache-Control": "private, max-age=300",
            "Content-Disposition": f'{disposition}; filename="{filename}"',
        },
    )


__all__ = ["router"]
