from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import RedirectResponse

from mozaiksai.core.auth import UserPrincipal, optional_user, require_any_auth
from mozaiksai.core.auth.dependencies import validate_path_id
from mozaiksai.core.media.store import MediaContentNotFoundError, get_media_asset_store
from mozaiksai.core.media.types import AssetVisibility, GeneratedMediaAsset
from mozaiksai.core.multitenant import build_app_scope_filter

router = APIRouter(tags=["media"])


async def _owns_source_chat(principal: UserPrincipal, asset: GeneratedMediaAsset) -> bool:
    """Authorize draft reads against the persisted runtime chat, never media metadata."""
    if not principal.is_authenticated or not str(principal.user_id or "").strip():
        return False
    if not asset.source_chat_id or not asset.app_id:
        return False
    if not principal.validate_app_id(asset.app_id) or not principal.validate_chat_id(asset.source_chat_id):
        return False

    from mozaiksai.hosts import runtime as runtime_app

    coll = await runtime_app._chat_coll()
    owned = await coll.find_one(
        {
            "_id": asset.source_chat_id,
            "user_id": principal.user_id,
            **build_app_scope_filter(asset.app_id),
        },
        {"_id": 1},
    )
    return bool(owned)


def _has_backer_access(principal: UserPrincipal, app_id: str, asset_id: str) -> bool:
    """Return True when the principal holds a verified-backer claim for this asset.

    The claim ``verified_campaign_backer_assets`` is a list of ``asset_id`` strings
    injected into the session by the hosted product after a campaign backing payment
    confirms (see growth_campaigns.backing.paid reaction). Operators and admin-role
    principals bypass the check entirely.
    """
    roles: list[str] = list(getattr(principal, "roles", None) or [])
    if "admin" in roles or "operator" in roles:
        return True
    raw_claims: dict = dict(getattr(principal, "raw_claims", None) or {})
    backer_assets: list[str] = list(raw_claims.get("verified_campaign_backer_assets") or [])
    return asset_id in backer_assets


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

    Access is gated on ``asset_visibility``:

    - ``private`` — authenticated source-chat owner or operator/admin
    - ``investor_preview`` — verified campaign backers + operators/admins
    - ``public`` — any authenticated user (unauthenticated use the public endpoint)

    When the asset was stored in Azure Blob with a CDN base URL configured
    (AZURE_STORAGE_CDN_BASE_URL), the content_ref is a full HTTPS URL and
    this endpoint issues a 302 redirect to it instead of proxying bytes.
    """
    clean_app_id = validate_path_id(app_id, "app_id")
    clean_asset_id = validate_path_id(asset_id, "asset_id")
    store = get_media_asset_store()
    asset = await store.get_asset(app_id=clean_app_id, asset_id=clean_asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Media asset not found")

    visibility = asset.asset_visibility
    roles: list[str] = list(getattr(principal, "roles", None) or [])
    is_privileged = "admin" in roles or "operator" in roles

    if visibility == AssetVisibility.PRIVATE and not is_privileged:
        if not await _owns_source_chat(principal, asset):
            raise HTTPException(status_code=403, detail="Asset is private")

    if visibility == AssetVisibility.INVESTOR_PREVIEW:
        if not _has_backer_access(principal, clean_app_id, clean_asset_id):
            raise HTTPException(
                status_code=403,
                detail="Asset requires verified campaign backer access",
            )

    # CDN fast-path: redirect instead of proxying bytes
    if _is_cdn_url(asset.content_ref):
        return RedirectResponse(url=asset.content_ref, status_code=302)

    try:
        content = await store.get_asset_content(asset)
    except MediaContentNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Media content not found") from exc

    disposition = "attachment" if download else "inline"
    filename = _safe_download_filename(asset.filename)
    cache_control = "public, max-age=3600" if visibility == AssetVisibility.PUBLIC else "private, max-age=300"
    return Response(
        content=content,
        media_type=asset.media_type,
        headers={
            "Cache-Control": cache_control,
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
    """Serve media with server-authorized public visibility without authentication.

    Promotion publishes through the store's visibility decision. Provider or
    historical promotion metadata cannot override private/backer-only visibility.

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

    if asset.asset_visibility != AssetVisibility.PUBLIC:
        raise HTTPException(
            status_code=403,
            detail="Asset is not public",
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
