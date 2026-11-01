"""Shared reads of the app media plan inside an app bundle."""

from __future__ import annotations

import json
from typing import Any

ASSET_MANIFEST_PATH = "config/asset_manifest.json"
# Ceiling on images made per build; planned entries past it keep their placeholder.
MAX_GENERATED_IMAGES = 8
ERROR_PREFIX = "app_media_source"


def parse_asset_manifest(files: dict[str, Any]) -> dict[str, Any] | None:
    raw = files.get(ASSET_MANIFEST_PATH)
    if not isinstance(raw, str):
        return None
    try:
        manifest = json.loads(raw)
    except ValueError as exc:
        raise ValueError(f"{ERROR_PREFIX}_asset_manifest_invalid") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("assets"), list):
        raise ValueError(f"{ERROR_PREFIX}_asset_manifest_invalid")
    return manifest


def is_planned(entry: Any) -> bool:
    """A manifest entry whose file the media step still has to make or reserve."""
    return (
        isinstance(entry, dict)
        and isinstance(entry.get("asset_id"), str)
        and bool(entry["asset_id"].strip())
        and not str(entry.get("path") or "").strip()
        and not str(entry.get("url") or "").strip()
        and bool(str(entry.get("brief") or "").strip())
    )


def planned_generated_entries(manifest: dict[str, Any] | None) -> list[dict[str, Any]]:
    entries = [
        entry for entry in (manifest or {}).get("assets", [])
        if is_planned(entry) and entry.get("source") == "generated"
    ]
    return entries[:MAX_GENERATED_IMAGES]
