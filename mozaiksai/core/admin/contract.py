from __future__ import annotations

from copy import deepcopy
from typing import Any


ADMIN_SECTION_ORDER = (
    "overview",
    "users",
    "usage",
    "operations",
    "settings",
)


ADMIN_SECTION_META: dict[str, dict[str, Any]] = {
    "overview": {"label": "Admin", "order": 999, "path": "/apps/:appId/admin", "title": "Admin"},
    "users": {"label": "Users", "order": 1000, "path": "/apps/:appId/users", "title": "Users"},
    "usage": {"label": "Usage", "order": 1001, "path": "/apps/:appId/usage", "title": "Usage"},
    "operations": {"label": "Operations", "order": 1002, "path": "/apps/:appId/operations", "title": "Operations"},
    "settings": {"label": "Settings", "order": 1003, "path": "/apps/:appId/settings", "title": "Settings"},
}


DEFAULT_RUNTIME_PANELS: list[dict[str, Any]] = [
    {"id": "stats", "label": "Usage Stats", "section": "usage"},
    {"id": "runs", "label": "Active Runs", "section": "usage"},
    {"id": "sessions", "label": "Recent Sessions", "section": "operations"},
]


def normalize_admin_section_name(value: str) -> str:
    section = value.strip().lower().replace("_", "-")
    return section if section in ADMIN_SECTION_META else "overview"


def build_default_admin_sections() -> dict[str, dict[str, Any]]:
    return {
        section: {
            "label": meta["label"],
            "enabled": True,
            "order": meta["order"],
        }
        for section, meta in ADMIN_SECTION_META.items()
    }


def build_default_runtime_panels() -> list[dict[str, Any]]:
    return deepcopy(DEFAULT_RUNTIME_PANELS)


def build_default_admin_shell_config() -> dict[str, Any]:
    return {
        "sections": build_default_admin_sections(),
        "runtime_panels": build_default_runtime_panels(),
        "module_panels": [],
    }


def normalize_admin_shell_sections(raw_sections: Any) -> dict[str, dict[str, Any]]:
    normalized = build_default_admin_sections()
    if not isinstance(raw_sections, dict):
        return normalized

    for section, defaults in normalized.items():
        candidate = raw_sections.get(section)
        if not isinstance(candidate, dict):
            continue
        if isinstance(candidate.get("label"), str) and candidate["label"].strip():
            defaults["label"] = candidate["label"].strip()
        if "enabled" in candidate:
            defaults["enabled"] = bool(candidate.get("enabled"))
        if isinstance(candidate.get("order"), int):
            defaults["order"] = candidate["order"]
    return normalized


def build_admin_shell_routes(section_config: Any = None) -> tuple[dict[str, Any], ...]:
    sections = normalize_admin_shell_sections(section_config)
    routes: list[dict[str, Any]] = []
    for section in ADMIN_SECTION_ORDER:
        config = sections[section]
        if not config.get("enabled", True):
            continue
        meta = ADMIN_SECTION_META[section]
        routes.append(
            {
                "path": meta["path"],
                "label": config.get("label") or meta["label"],
                "order": config.get("order") if isinstance(config.get("order"), int) else meta["order"],
                "title": config.get("label") or meta["title"],
                "admin_section": section,
            }
        )
    return tuple(routes)
