"""
Shared page-plan utilities used by the runtime task batch runner and the
AppGenerator assembly tools.

These helpers convert AppBuildPlan page descriptors into AppPageSchema dicts
that the generator writes as ui/pages/{stem}.yaml files.
"""
from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from typing import Any

from .code_files import safe_relpath


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", str(value or "").strip()).strip("_").lower()


def _page_stem_from_path(path: str) -> str | None:
    safe = safe_relpath(path)
    if not safe:
        return None
    pure = PurePosixPath(safe)
    if len(pure.parts) == 3 and pure.parts[0] == "ui" and pure.parts[1] == "pages" and pure.suffix in {".yaml", ".yml"}:
        return _slug(pure.stem)
    return None


def _page_stems(page: dict[str, Any]) -> set[str]:
    stems: set[str] = set()
    route = str(page.get("route") or "").strip()
    if route and route != "/":
        stems.add(_slug(route.strip("/").split("/")[-1]))
    for key in ("name", "id", "surface_id"):
        value = str(page.get(key) or "").strip()
        if value:
            stems.add(_slug(value))
    return {stem for stem in stems if stem}


def _decode_config_hint(value: Any) -> dict[str, Any]:
    """Decode a JSON-encoded config_hint string to a dict, or return {} on failure."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, dict) else {}
        except (json.JSONDecodeError, ValueError):
            return {}
    return {}


def _page_from_plan(page: dict[str, Any], stem: str) -> dict[str, Any]:
    title = str(page.get("title") or page.get("name") or stem.replace("_", " ").title()).strip()
    route = str(page.get("route") or f"/{stem.replace('_', '-')}").strip()
    sections: list[dict[str, Any]] = []
    hints = page.get("sections_hint")
    if isinstance(hints, list):
        for index, hint in enumerate(hints):
            if not isinstance(hint, dict):
                continue
            primitive = str(hint.get("primitive") or "PageHeader").strip()
            section_id = str(hint.get("section_id_hint") or f"{stem}-{index + 1}").strip()
            sections.append(
                {
                    "id": section_id,
                    "primitive": primitive,
                    "title": hint.get("title_hint"),
                    "config": _decode_config_hint(hint.get("config_hint")),
                    "event_triggers": [],
                    "roles": None,
                }
            )
    if not sections:
        sections.append(
            {
                "id": f"{stem}-header",
                "primitive": "PageHeader",
                "title": None,
                "config": {"title": title, "subtitle": str(page.get("purpose") or "").strip()},
                "event_triggers": [],
                "roles": None,
            }
        )
    return {
        "name": str(page.get("name") or title).strip(),
        "route": route,
        "title": title,
        "page_type": str(page.get("page_type") or page.get("page_type_hint") or page.get("ui_layout") or "standard").strip(),
        "layout": str(page.get("layout") or "stack").strip(),
        "shell_mode": str(page.get("shell_mode") or page.get("shell_mode_hint") or "workspace").strip(),
        "roles": page.get("roles"),
        "navigation": {
            "id": stem,
            "label": title,
            "icon": None,
            "scope": "global",
            "order": 10,
            "visible": True,
            "placement": None,
        },
        "sections": sections,
        "extensions": None,
    }


__all__ = [
    "_decode_config_hint",
    "_page_from_plan",
    "_page_stem_from_path",
    "_page_stems",
    "_slug",
]
