"""Shared identities and references for an app's asset manifest."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

_ASSET_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_RESERVED_FILENAMES = {"con", "prn", "aux", "nul", *(f"{prefix}{n}" for prefix in ("com", "lpt") for n in range(1, 10))}


def validate_asset_id(value: Any) -> str:
    """Require a route and filename identity portable across supported hosts."""
    if (
        not isinstance(value, str)
        or not _ASSET_ID.fullmatch(value)
        or value.endswith(".")
        or value.split(".", 1)[0].lower() in _RESERVED_FILENAMES
    ):
        raise ValueError("asset_id must be 1-128 ASCII letters, digits, dots, underscores or hyphens, start with a letter or digit, and not end with a dot or use a reserved filename")
    return value


def validate_asset_url(value: Any) -> str:
    """Allow browser redirects to HTTP(S) image locations, never credentials."""
    if not isinstance(value, str) or any(
        character.isspace() or ord(character) < 32 or 127 <= ord(character) <= 159 for character in value
    ):
        raise ValueError("asset url must be an absolute HTTP(S) URL without credentials or whitespace")
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme in {"http", "https"} and bool(parsed.hostname)
            and parsed.username is None and parsed.password is None
            and "\\" not in value
        )
        _ = parsed.port
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("asset url must be an absolute HTTP(S) URL without credentials or whitespace")
    return value


def asset_manifest_index(manifest: Any) -> dict[str, dict[str, Any]]:
    """Validate manifest identities and return its entries keyed by exact id."""
    if manifest is None:
        return {}
    if not isinstance(manifest, dict) or not isinstance(manifest.get("assets"), list):
        raise ValueError("asset_manifest.assets must be a list")
    result: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for position, entry in enumerate(manifest["assets"]):
        if not isinstance(entry, dict):
            raise ValueError(f"asset_manifest.assets[{position}] must be an object")
        asset_id = validate_asset_id(entry.get("asset_id"))
        if asset_id.casefold() in seen:
            raise ValueError("asset_manifest.assets must have unique asset_id values (case-insensitive)")
        seen.add(asset_id.casefold())
        result[asset_id] = entry
        if entry.get("url") is not None:
            validate_asset_url(entry["url"])
    return result
