from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from urllib.parse import quote

from .types import GeneratedMediaAsset, MediaPromotionTarget

_PROMOTION_LABELS: dict[MediaPromotionTarget, str] = {
    MediaPromotionTarget.BRAND_ASSET: "Promote to Brand",
    MediaPromotionTarget.APP_ASSET: "Promote to App",
    MediaPromotionTarget.PAGE_ASSET: "Promote to Page",
    MediaPromotionTarget.CAMPAIGN_ASSET: "Promote to Campaign",
    MediaPromotionTarget.ARTIFACT: "Keep as Artifact",
}


def media_asset_content_url(*, app_id: str, asset_id: str, download: bool = False) -> str:
    suffix = "?download=1" if download else ""
    return (
        "/api/media/assets/"
        f"{quote(str(app_id), safe='')}/"
        f"{quote(str(asset_id), safe='')}/content"
        f"{suffix}"
    )


def _normalize_targets(values: Iterable[MediaPromotionTarget | str] | None) -> list[MediaPromotionTarget]:
    targets: list[MediaPromotionTarget] = []
    seen: set[str] = set()
    for value in values or []:
        try:
            target = value if isinstance(value, MediaPromotionTarget) else MediaPromotionTarget(str(value))
        except ValueError:
            continue
        if target.value in seen:
            continue
        seen.add(target.value)
        targets.append(target)
    return targets or [MediaPromotionTarget.ARTIFACT]


def generated_media_artifact_payload(
    assets: Iterable[GeneratedMediaAsset],
    *,
    app_id: str,
    workflow_name: str | None = None,
    chat_id: str | None = None,
    agent_name: str | None = None,
) -> dict[str, Any]:
    """Build the core UI artifact payload for generated media proposals."""

    asset_entries: list[dict[str, Any]] = []
    for asset in assets:
        targets = _normalize_targets(asset.promotion_targets)
        entry = asset.model_dump(by_alias=False, mode="json")
        entry["preview_url"] = media_asset_content_url(app_id=app_id, asset_id=asset.asset_id)
        entry["download_url"] = media_asset_content_url(app_id=app_id, asset_id=asset.asset_id, download=True)
        entry["display_name"] = asset.filename or asset.asset_id
        entry["actions"] = [
            {
                "tool": "media.promote",
                "label": _PROMOTION_LABELS[target],
                "style": "primary" if target is MediaPromotionTarget.BRAND_ASSET else "secondary",
                "scope": "row",
                "params": {
                    "asset_id": "{{asset.asset_id}}",
                    "promotion_target": target.value,
                },
            }
            for target in targets
        ]
        asset_entries.append(entry)

    first_asset_id = asset_entries[0]["asset_id"] if asset_entries else "empty"
    count = len(asset_entries)
    noun = "asset" if count == 1 else "assets"
    return {
        "artifact_type": "core.media.generated_asset",
        "artifact_id": f"media:{chat_id or 'chat'}:{first_asset_id}",
        "title": "Generated Media",
        "subtitle": f"{agent_name or 'Agent'} created {count} {noun}.",
        "summary": "Review generated media and choose where it should be promoted.",
        "workflow_name": workflow_name,
        "chat_id": chat_id,
        "app_id": app_id,
        "assets": asset_entries,
        "persist_ui_state": True,
        "display": "artifact",
        "interaction_type": "media_artifact",
    }


__all__ = [
    "generated_media_artifact_payload",
    "media_asset_content_url",
]
