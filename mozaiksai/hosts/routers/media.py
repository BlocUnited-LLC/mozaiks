from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import RedirectResponse

from mozaiksai.core.auth import UserPrincipal, optional_user, require_any_auth
from mozaiksai.core.auth.dependencies import validate_path_id
from mozaiksai.core.media.store import MediaContentNotFoundError, get_media_asset_store

router = APIRouter(tags=["media"])


def _safe_download_filename(value: str | None) -> str:
    filename = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip()).strip(".-")
    return filename or "generated-media.bin"


def _is_cdn_url(content_ref: str | None) -> bool:
    """Return True when the content_ref is an absolute HTTPS URL (CDN or blob)."""
    return bool(content_ref and str(content_ref).startswith("https://"))


@router.get("/api/media/assets/{app_id}/{asset_id}/content")
async def get_media_asset_content(
    app_id: str,
    asset_id: str,
    download: bool = Query(default=False),
    principal: UserPrincipal = Depends(require_any_auth),
) -> Response:
    """Serve generated media bytes for authenticated users.

    When the asset was stored in Azure Blob with a CDN base URL configured
    (AZURE_STORAGE_CDN_BASE_URL), the content_ref is a full HTTPS URL and
    this endpoint issues a 302 redirect to it instead of proxying bytes.
    This allows the CDN to serve the asset directly with zero API overhead.

    For local and GridFS backends the bytes are proxied through this endpoint.
    """
    _ = principal
    clean_app_id = validate_path_id(app_id, "app_id")
    clean_asset_id = validate_path_id(asset_id, "asset_id")
    store = get_media_asset_store()
    asset = await store.get_asset(app_id=clean_app_id, asset_id=clean_asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Media asset not found")

    # CDN fast-path: redirect instead of proxying bytes
    if _is_cdn_url(asset.content_ref):
        return RedirectResponse(url=asset.content_ref, status_code=302)

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


@router.get("/api/media/public/{app_id}/{asset_id}/content")
async def get_public_media_asset_content(
    app_id: str,
    asset_id: str,
    download: bool = Query(default=False),
    principal: UserPrincipal | None = Depends(optional_user),
) -> Response:
    """Serve promoted media assets without requiring authentication.

    Only assets that have been explicitly promoted (metadata.promotion_status
    == "promoted") are served through this public endpoint. Unpromoted assets
    return 403 regardless of auth state.

    This endpoint exists for publicly visible surfaces — marketplace listing
    logos, campaign hero images on public pages — where requiring a session
    cookie would break unauthenticated visitors.

    CDN redirect applies the same way as the authenticated endpoint.
    """
    _ = principal
    clean_app_id = validate_path_id(app_id, "app_id")
    clean_asset_id = validate_path_id(asset_id, "asset_id")
    store = get_media_asset_store()
    asset = await store.get_asset(app_id=clean_app_id, asset_id=clean_asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Media asset not found")

    # Gate on promotion status — only explicitly promoted assets are public
    promotion_status = (asset.metadata or {}).get("promotion_status", "")
    if promotion_status != "promoted":
        raise HTTPException(
            status_code=403,
            detail="Asset is not promoted and cannot be served publicly",
        )

    # CDN fast-path
    if _is_cdn_url(asset.content_ref):
        return RedirectResponse(url=asset.content_ref, status_code=302)

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
            "Cache-Control": "public, max-age=3600",
            "Content-Disposition": f'{disposition}; filename="{filename}"',
        },
    )


__all__ = ["router"]
