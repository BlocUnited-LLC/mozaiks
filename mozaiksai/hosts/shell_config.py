"""Shell configuration and navigation assembly for the platform host.

Pure config building: reads manifests and page declarations, returns the shell
payload. The host owns the routes; nothing here touches the FastAPI app or
request state.

Extracted from hosts/platform.py, where 83 helpers shared a file with 20 routes.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml
from fastapi import HTTPException

from logs.logging_config import get_workflow_logger
from mozaiksai.core.admin.registry import build_admin_shell_routes, load_admin_registry
from mozaiksai.core.runtime.app.ai_config import resolve_runtime_ai_config
from mozaiksai.core.runtime.app.auth_contract import (
    build_app_auth_projection,
    load_app_auth_contract,
)
from mozaiksai.core.runtime.app.page_schema import discover_page_schema_paths
from mozaiksai.core.workflow.paths import resolve_active_app_root
from mozaiksai.resources import resolve_factory_app_root

logger = get_workflow_logger("platform")


def resolve_app_root() -> Path:
    return resolve_active_app_root()


_DEFAULT_NAVIGATION_POLICY: dict[str, Any] = {
    "desktop": {"global": "header", "local": "sidebar", "footer": "visible"},
    "mobile": {"global": "bottomBar", "local": "sheet", "footer": "hidden"},
    "maxMobileItems": 5,
    "autoFromPages": False,
}


_DEFAULT_CHROME_POLICY: dict[str, Any] = {
    "defaultMode": "standard",
    "modes": {
        "standard": {
            "desktop": {"header": True, "footer": True, "bottomBar": False, "localNav": True},
            "mobile": {"header": True, "footer": False, "bottomBar": True, "localNav": "sheet"},
        },
        "workspace": {
            "desktop": {"header": True, "footer": False, "bottomBar": False, "localNav": True},
            "mobile": {"header": True, "footer": False, "bottomBar": True, "localNav": "sheet"},
        },
        "conversation": {
            "desktop": {"header": True, "footer": False, "bottomBar": False, "localNav": False},
            "mobile": {"header": True, "footer": False, "bottomBar": False, "localNav": False},
        },
        "focused": {
            "desktop": {"header": True, "footer": False, "bottomBar": False, "localNav": False},
            "mobile": {"header": True, "footer": False, "bottomBar": False, "localNav": False},
        },
        "immersive": {
            "desktop": {"header": False, "footer": False, "bottomBar": False, "localNav": False},
            "mobile": {"header": False, "footer": False, "bottomBar": False, "localNav": False},
        },
        "public": {
            "desktop": {"header": True, "footer": True, "bottomBar": False, "localNav": False},
            "mobile": {"header": True, "footer": False, "bottomBar": False, "localNav": False},
        },
    },
}


def _resolve_app_manifest_path() -> Path:
    app_root = resolve_app_root()
    return (app_root / "app.json").resolve()


def _coerce_requires_role(value: Any) -> str | None:
    """Normalize route-role metadata for shell visibility and deep-link gating.

    Current shell route metadata is single-role only. If route or page schema
    content provides a role list, keep the first non-empty role as
    declaration and visibility intent only. Frontend role checks are UX gates;
    module policy remains the authoritative security boundary for
    resource-scoped authorization.
    """

    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, str):
                continue
            normalized = item.strip()
            if normalized:
                return normalized
    return None


def _normalize_chrome_viewport_policy(raw: Any, defaults: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return dict(defaults)

    result = dict(defaults)
    for field in ("header", "footer", "bottomBar", "localNav"):
        value = raw.get(field)
        if value is None:
            continue
        if field == "localNav":
            if isinstance(value, bool):
                result[field] = value
            elif isinstance(value, str) and value.strip():
                result[field] = value.strip()
            continue
        if isinstance(value, bool):
            result[field] = value
    return result


PROFILE_SHELL_ROUTE = {
    "path": "/me",
    "component": "ProfilePage",
    "label": "Profile",
    "order": 998,
    "title": "Profile",
    "shellMode": "social",
}


_ADMIN_PORTAL_MENU_ITEM = {
    "id": "admin-portal",
    "label": "Admin Portal",
    "action": "navigate",
    "path": "/apps",
    "requiresRole": "admin",
}


_NAVIGATION_ITEM_FIELDS = {
    "id",
    "label",
    "action",
    "path",
    "href",
    "icon",
    "iconLabel",
    "requiresRole",
    "visible",
    "order",
    "scope",
    "group",
    "placement",
}


_SHELL_MODE_VALUES = {"standard", "workspace", "conversation", "focused", "immersive", "public"}


def _clean_string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _title_from_id(value: str) -> str:
    return " ".join(part.capitalize() for part in re.split(r"[-_]+", value) if part)


def _normalize_shell_surface(surface: str | None) -> str:
    candidate = str(surface or "platform").strip().lower()
    return candidate if candidate in {"platform", "studio", "user"} else "platform"


def _normalize_shell_mode(value: Any) -> str | None:
    mode = _clean_string(value)
    if not mode:
        return None
    normalized = mode.replace("_", "-").lower()
    return normalized if normalized in _SHELL_MODE_VALUES else None


def _shell_mode_from_entry(entry: dict[str, Any]) -> str | None:
    meta = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
    return (
        _normalize_shell_mode(entry.get("shellMode"))
        or _normalize_shell_mode(entry.get("shell_mode"))
        or _normalize_shell_mode(meta.get("shellMode"))
        or _normalize_shell_mode(meta.get("shell_mode"))
    )


def _resolve_shell_config_path() -> Path:
    app_root = resolve_app_root()
    return (app_root / "config" / "shell.json").resolve()


def _load_app_manifest() -> dict[str, Any]:
    app_manifest_path = _resolve_app_manifest_path()
    if not app_manifest_path.exists():
        return {}
    try:
        raw = json.loads(app_manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("APP_MANIFEST_LOAD_FAILED %s: %s", app_manifest_path, exc)
        return {}
    return raw if isinstance(raw, dict) else {}


def _normalize_chrome_mode_policy(raw: Any, defaults: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return deepcopy(defaults)

    desktop_defaults = defaults.get("desktop", {})
    mobile_defaults = defaults.get("mobile", {})
    shared = {
        key: raw[key]
        for key in ("header", "footer", "bottomBar", "localNav")
        if key in raw
    }
    desktop_raw = {**shared, **(raw.get("desktop") if isinstance(raw.get("desktop"), dict) else {})}
    mobile_raw = {**shared, **(raw.get("mobile") if isinstance(raw.get("mobile"), dict) else {})}
    return {
        "desktop": _normalize_chrome_viewport_policy(desktop_raw, desktop_defaults),
        "mobile": _normalize_chrome_viewport_policy(mobile_raw, mobile_defaults),
    }


def _normalize_chrome_policy(chrome: Any) -> dict[str, Any]:
    policy = deepcopy(_DEFAULT_CHROME_POLICY)
    if not isinstance(chrome, dict):
        return policy

    default_mode = _normalize_shell_mode(chrome.get("defaultMode"))
    if default_mode:
        policy["defaultMode"] = default_mode

    raw_modes = chrome.get("modes") if isinstance(chrome.get("modes"), dict) else {}
    for mode, default_mode_policy in list(policy["modes"].items()):
        raw_mode = raw_modes.get(mode)
        if raw_mode is None:
            raw_mode = chrome.get(mode)
        policy["modes"][mode] = _normalize_chrome_mode_policy(raw_mode, default_mode_policy)
    return policy


def _normalize_route_requires_role_meta(meta: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(meta)
    requires_role = _coerce_requires_role(normalized.get("requiresRole"))
    if not requires_role:
        requires_role = _coerce_requires_role(normalized.get("roles"))
    normalized.pop("roles", None)
    if requires_role:
        normalized["requiresRole"] = requires_role
    return normalized


def _normalize_navigation_policy(navigation: Any) -> dict[str, Any]:
    if not isinstance(navigation, dict):
        return dict(_DEFAULT_NAVIGATION_POLICY)

    raw_policy = navigation.get("policy") if isinstance(navigation.get("policy"), dict) else navigation

    def viewport_policy(name: str) -> dict[str, str]:
        defaults = _DEFAULT_NAVIGATION_POLICY[name]
        raw = raw_policy.get(name) if isinstance(raw_policy.get(name), dict) else {}
        return {
            "global": _clean_string(raw.get("global")) or defaults["global"],
            "local": _clean_string(raw.get("local")) or defaults["local"],
            "footer": _clean_string(raw.get("footer")) or defaults["footer"],
        }

    try:
        max_mobile = int(raw_policy.get("maxMobileItems") or _DEFAULT_NAVIGATION_POLICY["maxMobileItems"])
    except Exception:
        max_mobile = int(_DEFAULT_NAVIGATION_POLICY["maxMobileItems"])

    return {
        "desktop": viewport_policy("desktop"),
        "mobile": viewport_policy("mobile"),
        "maxMobileItems": max(1, min(max_mobile, 5)),
        "autoFromPages": bool(raw_policy.get("autoFromPages") or False),
    }


def _public_nav_item(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if key in _NAVIGATION_ITEM_FIELDS and key != "scope"}


def _placement_for_item(item: dict[str, Any], *, viewport: str, policy: dict[str, Any]) -> str:
    placement = item.get("placement") if isinstance(item.get("placement"), dict) else {}
    explicit = _clean_string(placement.get(viewport))
    if explicit:
        return explicit
    scope = item.get("scope")
    if scope == "local":
        return policy[viewport]["local"]
    if scope == "footer":
        return policy[viewport]["footer"]
    if scope == "profile":
        return "profile"
    return policy[viewport]["global"]


def _shortcut_ids(shortcuts: dict[str, Any], key: str) -> list[str]:
    value = shortcuts.get(key)
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str) and item.strip()]
    return []


def _expand_shortcut_items(ids: list[str], catalog: dict[str, dict[str, Any]], *, footer: bool = False) -> list[dict[str, Any]]:
    expanded: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item_id in ids:
        item = catalog.get(item_id)
        if not item:
            continue
        output = dict(item)
        key = str(output.get("href") or output.get("path") or output.get("id") or item_id)
        if key in seen:
            continue
        seen.add(key)
        if footer:
            href = output.get("href") or output.get("path")
            if not isinstance(href, str) or not href:
                continue
            expanded.append({"label": output.get("label") or _title_from_id(item_id), "href": href})
            continue
        if output.get("href") and not output.get("action"):
            output["action"] = "navigate"
        expanded.append(output)
    return expanded


def _footer_link_from_item(item: dict[str, Any]) -> dict[str, Any] | None:
    href = _clean_string(item.get("href")) or _clean_string(item.get("path"))
    if not href:
        return None
    link = {"label": item.get("label") or _title_from_id(str(item.get("id") or "link")), "href": href}
    requires_role = _clean_string(item.get("requiresRole"))
    if requires_role:
        link["requiresRole"] = requires_role
    if isinstance(item.get("visible"), bool):
        link["visible"] = item["visible"]
    return link


def _header_action_targets(header: Any) -> set[str]:
    if not isinstance(header, dict):
        return set()

    actions = header.get("actions")
    if not isinstance(actions, list):
        return set()

    targets: set[str] = set()
    for action in actions:
        if not isinstance(action, dict):
            continue
        for key in ("path", "href"):
            target = _clean_string(action.get(key))
            if target:
                targets.add(target)
    return targets


def _sanitize_navigation_item(item: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(item, dict) or item.get("visible") is False:
        return None
    item_id = _clean_string(item.get("id"))
    path = _clean_string(item.get("path"))
    href = _clean_string(item.get("href"))
    action = _clean_string(item.get("action"))
    if not item_id:
        item_id = (path or href or "").strip("/").replace("/", "-")
    if not item_id:
        return None

    label = _clean_string(item.get("label")) or _title_from_id(item_id)
    scope = _clean_string(item.get("scope")) or "global"
    if scope not in {"global", "local", "profile", "footer"}:
        scope = "global"

    output: dict[str, Any] = {
        "id": item_id,
        "label": label,
        "scope": scope,
    }
    if action:
        output["action"] = action
    elif path:
        output["action"] = "navigate"
    if path:
        output["path"] = path
    if href:
        output["href"] = href
    for key in ("icon", "iconLabel", "requiresRole", "group"):
        value = _clean_string(item.get(key))
        if value:
            output[key] = value
    if isinstance(item.get("visible"), bool):
        output["visible"] = item["visible"]
    if isinstance(item.get("order"), int):
        output["order"] = item["order"]
    elif isinstance(item.get("priority"), int):
        output["order"] = item["priority"]
    placement = item.get("placement")
    if isinstance(placement, dict):
        clean_placement = {
            key: value.strip()
            for key, value in placement.items()
            if key in {"desktop", "mobile"} and isinstance(value, str) and value.strip()
        }
        if clean_placement:
            output["placement"] = clean_placement
    elif isinstance(placement, str) and placement.strip():
        output["placement"] = {"desktop": placement.strip(), "mobile": placement.strip()}
    return output


def _dedupe_navigation_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for item in items:
        item_id = _clean_string(item.get("id"))
        if not item_id:
            continue
        placement = item.get("placement")
        placement_key = json.dumps(placement, sort_keys=True) if isinstance(placement, dict) else str(placement or "")
        key = f"{item_id}:{item.get('scope', 'global')}:{placement_key}"
        current = by_id.get(key)
        if current is None:
            by_id[key] = item
        else:
            merged_placement = {}
            if isinstance(current.get("placement"), dict):
                merged_placement.update(current["placement"])
            if isinstance(item.get("placement"), dict):
                merged_placement.update(item["placement"])
            merged = {**current, **item}
            if merged_placement:
                merged["placement"] = merged_placement
            by_id[key] = merged
    return sorted(
        by_id.values(),
        key=lambda item: (item.get("order", 500), str(item.get("label") or item.get("id") or "")),
    )


def _navigation_item_from_page(page: dict, *, auto_from_pages: bool) -> dict[str, Any] | None:
    nav = _navigation_config_from_page(page)
    if nav is None and not auto_from_pages:
        return None
    if isinstance(nav, dict) and nav.get("visible") is False:
        return None
    if isinstance(nav, dict) and nav.get("include") is False:
        return None

    meta = page.get("meta") if isinstance(page.get("meta"), dict) else {}
    if nav is None and (
        page.get("path") == PROFILE_SHELL_ROUTE["path"]
        or page.get("component") == "AdminPortal"
        or meta.get("adminSection")
    ):
        return None

    base = _route_item_from_page(page)
    if not base:
        return None
    if isinstance(nav, dict):
        base.update({key: value for key, value in nav.items() if key in _NAVIGATION_ITEM_FIELDS or key in {"include", "priority"}})
    base.setdefault("scope", "global")
    return _sanitize_navigation_item(base)


def _navigation_config_from_page(page: dict) -> dict[str, Any] | None:
    if not isinstance(page, dict):
        return None
    direct = page.get("navigation")
    if isinstance(direct, dict):
        return direct
    meta = page.get("meta") if isinstance(page.get("meta"), dict) else {}
    nav = meta.get("navigation")
    return nav if isinstance(nav, dict) else None


def _navigation_items_from_config(
    navigation: Any,
    catalog: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(navigation, dict):
        return []
    raw_items = navigation.get("items")
    if isinstance(raw_items, dict):
        iterable: list[Any] = [{**value, "id": key} for key, value in raw_items.items() if isinstance(value, dict)]
    elif isinstance(raw_items, list):
        iterable = raw_items
    else:
        iterable = []

    items: list[dict[str, Any]] = []
    for raw in iterable:
        if isinstance(raw, str):
            base = catalog.get(raw)
            if base:
                items.append(_sanitize_navigation_item(base))
            continue
        if not isinstance(raw, dict):
            continue
        reference = _clean_string(raw.get("shortcut")) or _clean_string(raw.get("id"))
        base = catalog.get(reference) if reference else None
        item = {**(base or {}), **raw}
        if reference and not item.get("id"):
            item["id"] = reference
        normalized = _sanitize_navigation_item(item)
        if normalized:
            items.append(normalized)
    return items


def _route_item_from_page(page: dict) -> dict[str, Any] | None:
    path = page.get("path")
    if not isinstance(path, str) or not path.startswith("/"):
        return None
    meta = page.get("meta") if isinstance(page.get("meta"), dict) else {}
    nav = meta.get("navigation") if isinstance(meta.get("navigation"), dict) else {}
    item_id = (
        _clean_string(nav.get("id"))
        or _clean_string(page.get("id"))
        or _clean_string(page.get("schema"))
        or path.strip("/").replace("/", "-")
    )
    label = _clean_string(nav.get("label")) or _clean_string(page.get("label")) or _title_from_id(item_id)
    item: dict[str, Any] = {
        "id": item_id,
        "label": label,
        "action": "navigate",
        "path": path,
    }
    if isinstance(page.get("order"), int):
        item["order"] = page["order"]
    requires_role = nav.get("requiresRole") or meta.get("requiresRole")
    if isinstance(requires_role, str) and requires_role.strip():
        item["requiresRole"] = requires_role.strip()
    for field in ("icon", "iconLabel", "scope", "group", "visible", "placement"):
        if field in nav:
            item[field] = nav[field]
    return item


def _shortcut_navigation_items(shortcuts: Any, catalog: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(shortcuts, dict):
        return []

    items: list[dict[str, Any]] = []
    for index, item in enumerate(_expand_shortcut_items(_shortcut_ids(shortcuts, "header"), catalog)):
        items.append({**item, "order": index, "scope": item.get("scope", "global"), "placement": {"desktop": "header", "mobile": "hidden"}})
    for index, item in enumerate(_expand_shortcut_items(_shortcut_ids(shortcuts, "mobile"), catalog)):
        items.append({**item, "order": index, "scope": item.get("scope", "global"), "placement": {"desktop": "hidden", "mobile": "bottomBar"}})
    for index, item in enumerate(_expand_shortcut_items(_shortcut_ids(shortcuts, "profile"), catalog)):
        items.append({**item, "order": index, "scope": "profile"})
    for index, item in enumerate(_expand_shortcut_items(_shortcut_ids(shortcuts, "footer"), catalog, footer=True)):
        item_id = str(item.get("href") or item.get("label") or "").strip("/").replace("/", "-")
        items.append({"id": item_id or "footer-link", **item, "order": index, "scope": "footer"})
    return [_sanitize_navigation_item(item) for item in items if _sanitize_navigation_item(item)]


def _shell_shortcut_catalog(pages: list[dict], shortcuts: dict[str, Any]) -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {
        "home": {"id": "home", "label": "Home", "action": "navigate", "path": "/"},
        "apps": {"id": "apps", "label": "Apps", "action": "navigate", "path": "/apps"},
        "workspace": {"id": "workspace", "label": "Workspace", "action": "navigate", "path": "/apps"},
        "profile": {"id": "profile", "label": "Profile", "action": "navigate", "path": "/me"},
        "account": {"id": "profile", "label": "Profile", "action": "navigate", "path": "/me"},
        "notifications": {"id": "notifications", "label": "Alerts", "action": "navigate", "path": "/notifications"},
        "marketplace": {"id": "marketplace", "label": "Marketplace", "action": "navigate", "path": "/marketplace"},
        "wallet": {"id": "wallet", "label": "Wallet", "action": "navigate", "path": "/wallet"},
        "create": {"id": "create", "label": "Create", "action": "navigate", "path": "/create?new=1"},
        "admin": {"id": "admin", "label": "Admin", "action": "navigate", "path": "/admin", "requiresRole": "admin"},
        "support": {"id": "support", "label": "Support", "action": "navigate", "path": "/me?tab=support-tickets"},
        "signin": {"id": "signin", "label": "Sign In", "action": "signin"},
        "signout": {"id": "signout", "label": "Sign Out", "action": "signout"},
        "legal": {"id": "legal", "label": "Legal Notice", "href": "/legal"},
        "terms": {"id": "terms", "label": "Terms of Service", "href": "/terms"},
        "cookies": {"id": "cookies", "label": "Cookie Policy", "href": "/cookies"},
        "privacy": {"id": "privacy", "label": "Privacy Policy", "href": "/privacy"},
    }

    for page in pages:
        if not isinstance(page, dict):
            continue
        item = _route_item_from_page(page)
        if not item:
            continue
        catalog[item["id"]] = item
        path_key = str(item["path"]).strip("/").replace("/", "-")
        if path_key:
            catalog.setdefault(path_key, item)

    return catalog


def _inject_admin_portal(result: dict) -> None:
    """Guarantee Admin Portal appears in the Studio profile menu for admin users.

    Called after the full shell config pipeline so nothing can suppress it.
    Inserts before signout, or appends if signout is absent.
    """
    profile = result.get("profile")
    if not isinstance(profile, dict):
        profile = {"show": True, "menu": []}
        result["profile"] = profile

    menu = profile.get("menu")
    if not isinstance(menu, list):
        menu = []
        profile["menu"] = menu

    if any(isinstance(item, dict) and item.get("id") == "admin-portal" for item in menu):
        return

    signout_idx = next(
        (
            i
            for i, item in enumerate(menu)
            if isinstance(item, dict)
            and (item.get("id") == "signout" or item.get("action") == "signout")
        ),
        None,
    )
    if signout_idx is not None:
        menu.insert(signout_idx, dict(_ADMIN_PORTAL_MENU_ITEM))
    else:
        menu.append(dict(_ADMIN_PORTAL_MENU_ITEM))


def _page_targets_surface(page: dict, *, surface: str) -> bool:
    meta = page.get("meta") if isinstance(page.get("meta"), dict) else {}
    declared_surfaces = meta.get("surfaces", meta.get("surface"))

    normalized_surfaces: list[str] = []
    if isinstance(declared_surfaces, str):
        value = declared_surfaces.strip().lower()
        if value:
            normalized_surfaces.append(value)
    elif isinstance(declared_surfaces, list):
        for item in declared_surfaces:
            if isinstance(item, str) and item.strip():
                normalized_surfaces.append(item.strip().lower())

    if not normalized_surfaces:
        return True
    return surface in normalized_surfaces


def _normalize_shell_page_entry(entry: dict, *, order_fallback: int) -> dict | None:
    if not isinstance(entry, dict):
        return None
    path = entry.get("path")
    if not isinstance(path, str) or not path.startswith("/"):
        return None
    component = entry.get("component")
    transition = entry.get("transition")
    workflow = entry.get("workflow")
    if not any(isinstance(value, str) and value.strip() for value in (component, transition, workflow)):
        return None

    meta = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
    normalized_meta = _normalize_route_requires_role_meta(meta)
    requires_auth = (
        entry["requiresAuth"]
        if "requiresAuth" in entry
        else normalized_meta.get("requiresAuth", True)
    )
    page: dict = {
        "path": path,
        "label": entry.get("label", ""),
        "order": entry.get("order", order_fallback),
        "meta": {
            **normalized_meta,
            "requiresAuth": requires_auth,
        },
    }
    shell_mode = _shell_mode_from_entry(entry)
    if shell_mode:
        page["meta"]["shellMode"] = shell_mode
    if isinstance(component, str) and component.strip():
        page["component"] = component.strip()
    if isinstance(transition, str) and transition.strip():
        page["transition"] = transition.strip()
    if isinstance(workflow, str) and workflow.strip():
        page["workflow"] = workflow.strip()
    if isinstance(entry.get("sequence"), str) and entry["sequence"].strip():
        page["sequence"] = entry["sequence"].strip()
    if isinstance(entry.get("schema"), str) and entry["schema"].strip():
        page["schema"] = entry["schema"].strip()
    if isinstance(entry.get("navigation"), dict):
        nav = entry["navigation"]
        page["meta"]["navigation"] = nav
        # If the page declares a navigation group (workspace-studio, app-studio,
        # etc.), it must participate in shell navigation — mark appShell=True so
        # WorkspaceLayout and other layout-aware components can find it.
        if isinstance(nav.get("group"), str) and nav["group"].strip():
            page["meta"].setdefault("appShell", True)
    if isinstance(meta.get("ai_context"), str) and meta["ai_context"].strip():
        page["meta"]["ai_context"] = meta["ai_context"].strip()
    return page


def _append_page_once(pages: list[dict], page: dict) -> None:
    path = page.get("path")
    if not isinstance(path, str) or any(existing.get("path") == path for existing in pages):
        return
    pages.append(page)


def _dedupe_and_sort_pages(pages: list[dict]) -> list[dict]:
    by_path: dict[str, dict] = {}
    for page in pages:
        path = page.get("path")
        if isinstance(path, str) and path not in by_path:
            by_path[path] = page
    return sorted(
        by_path.values(),
        key=lambda page: (page.get("order", 0), str(page.get("label") or page.get("path") or "")),
    )


def _load_ui_route_manifest_pages(app_root: Path) -> list[dict]:
    manifest_path = (app_root / "ui" / "route_manifest.json").resolve()
    if manifest_path is None:
        return []
    if not manifest_path.exists():
        return []
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = raw.get("pages") if isinstance(raw, dict) else []
    if not isinstance(entries, list):
        return []
    pages: list[dict] = []
    for index, entry in enumerate(entries):
        page = _normalize_shell_page_entry(entry, order_fallback=index)
        if page:
            pages.append(page)
    return pages


def _load_workflow_entrypoint_pages(app_root: Path) -> list[dict]:
    from mozaiksai.core.workflow.pack.config import list_entrypoints, load_global_pack_graph

    _ = app_root
    pack = load_global_pack_graph()
    if pack is None:
        return []

    transition_shell_modes = {
        transition.id: transition.ui.shell_mode
        for transition in getattr(pack, "transitions", [])
        if getattr(transition, "ui", None) is not None and transition.ui.shell_mode
    }
    pages: list[dict] = []
    for index, entry in enumerate(list_entrypoints(pack)):
        raw_entry = entry.model_dump(exclude_none=True)
        transition_id = raw_entry.get("transition")
        if (
            isinstance(transition_id, str)
            and transition_id in transition_shell_modes
            and not _shell_mode_from_entry(raw_entry)
        ):
            meta = raw_entry.get("meta") if isinstance(raw_entry.get("meta"), dict) else {}
            raw_entry["meta"] = {**meta, "shellMode": transition_shell_modes[transition_id]}
        page = _normalize_shell_page_entry(raw_entry, order_fallback=200 + index)
        if page:
            pages.append(page)
    return pages


def _load_page_schema_routes(app_root: Path) -> list[dict]:
    pages: list[dict] = []
    for index, (page_key, page_path) in enumerate(discover_page_schema_paths(app_root).items()):
        raw = yaml.safe_load(page_path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            continue
        route = raw.get("route")
        if not isinstance(route, str) or not route.startswith("/"):
            continue
        name = str(raw.get("name") or page_key).strip() or page_key
        title = str(raw.get("title") or name).strip()
        raw_meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
        raw_requires_role = raw_meta.get("requiresRole")
        if raw_requires_role is None:
            raw_requires_role = raw_meta.get("roles")
        if raw_requires_role is None:
            raw_requires_role = raw.get("roles")
        meta_seed: dict[str, Any] = {
            "title": title,
            "appShell": True,
            "requiresAuth": True,
        }
        for key in ("authRedirect", "routeAuth", "requiresAuth", "shellMode", "shell_mode", "ai_context", "ask_context"):
            if key in raw_meta:
                meta_seed[key] = raw_meta[key]
        if raw_requires_role is not None:
            meta_seed["requiresRole"] = raw_requires_role
        meta: dict = _normalize_route_requires_role_meta(meta_seed)
        if isinstance(raw.get("navigation"), dict):
            meta["navigation"] = raw["navigation"]
        shell_mode = (
            _normalize_shell_mode(raw.get("shell_mode"))
            or _normalize_shell_mode(raw.get("shellMode"))
            or _normalize_shell_mode(raw_meta.get("shellMode"))
            or _normalize_shell_mode(raw_meta.get("shell_mode"))
        )
        if shell_mode:
            meta["shellMode"] = shell_mode
        pages.append({
            "path": route,
            "label": title,
            "component": "SchemaPage",
            "schema": page_key,
            "order": 100 + index,
            "meta": meta,
        })
    return pages


def _apply_dynamic_shell_navigation(
    result: dict,
    *,
    pages: list[dict],
    navigation: Any,
    shortcuts: Any,
) -> None:
    policy = _normalize_navigation_policy(navigation)
    catalog = _shell_shortcut_catalog(pages, shortcuts if isinstance(shortcuts, dict) else {})
    all_items = _dedupe_navigation_items([
        *[
            item
            for page in pages
            for item in [_navigation_item_from_page(page, auto_from_pages=policy["autoFromPages"])]
            if item
        ],
        *_navigation_items_from_config(navigation, catalog),
        *_shortcut_navigation_items(shortcuts, catalog),
    ])

    resolved: dict[str, Any] = {
        "desktop": {"header": [], "sidebar": [], "rail": []},
        "mobile": {"bottomBar": [], "sheet": [], "more": []},
        "local": {"desktop": [], "mobile": []},
        "profile": [],
        "footer": [],
    }

    for item in all_items:
        scope = item.get("scope", "global")
        desktop_placement = _placement_for_item(item, viewport="desktop", policy=policy)
        mobile_placement = _placement_for_item(item, viewport="mobile", policy=policy)
        public_item = _public_nav_item(item)

        if scope == "profile" or desktop_placement == "profile" or mobile_placement == "profile":
            resolved["profile"].append(public_item)
        if scope == "footer" or desktop_placement == "visible" or mobile_placement == "visible":
            footer_link = _footer_link_from_item(item)
            if footer_link:
                resolved["footer"].append(footer_link)

        if scope == "local":
            resolved["local"]["desktop"].append(public_item)
            resolved["local"]["mobile"].append(public_item)
        elif desktop_placement in resolved["desktop"]:
            resolved["desktop"][desktop_placement].append(public_item)

        if mobile_placement == "bottomBar":
            resolved["mobile"]["bottomBar"].append(public_item)
        elif mobile_placement in {"sheet", "more"}:
            resolved["mobile"][mobile_placement].append(public_item)

    max_mobile = policy["maxMobileItems"]
    resolved["mobile"]["bottomBar"] = resolved["mobile"]["bottomBar"][:max_mobile]

    if resolved["desktop"]["header"]:
        header = result.get("header") if isinstance(result.get("header"), dict) else {}
        if not isinstance(header.get("pages"), list) or not header["pages"]:
            header_action_targets = _header_action_targets(header)
            header["pages"] = [
                {key: value for key, value in item.items() if key in {"id", "label", "path", "icon", "requiresRole", "visible"}}
                for item in resolved["desktop"]["header"]
                if item.get("path") and item.get("path") not in header_action_targets
            ]
            result["header"] = header

    if resolved["mobile"]["bottomBar"]:
        mobile = result.get("mobile") if isinstance(result.get("mobile"), dict) else {}
        bottom_bar = mobile.get("bottomBar") if isinstance(mobile.get("bottomBar"), dict) else {}
        if not isinstance(bottom_bar.get("items"), list) or not bottom_bar["items"]:
            bottom_bar["visible"] = bottom_bar.get("visible", "auto")
            bottom_bar["items"] = resolved["mobile"]["bottomBar"]
            mobile["bottomBar"] = bottom_bar
            result["mobile"] = mobile

    if resolved["profile"]:
        profile = result.get("profile") if isinstance(result.get("profile"), dict) else {}
        if not isinstance(profile.get("menu"), list) or not profile["menu"]:
            profile["show"] = profile.get("show", True)
            profile["menu"] = resolved["profile"]
            result["profile"] = profile


    if resolved["footer"]:
        footer = result.get("footer") if isinstance(result.get("footer"), dict) else {}
        if not isinstance(footer.get("links"), list) or not footer["links"]:
            footer["visible"] = footer.get("visible", True)
            footer["links"] = resolved["footer"]
        if "hideOnMobile" not in footer:
            footer["hideOnMobile"] = policy["mobile"]["footer"] == "hidden"
        result["footer"] = footer

    if isinstance(shortcuts, dict) and isinstance(shortcuts.get("footerHideOnMobile"), bool):
        footer = result.get("footer") if isinstance(result.get("footer"), dict) else {}
        footer["hideOnMobile"] = shortcuts["footerHideOnMobile"]
        result["footer"] = footer

    result["navigation"] = {
        "policy": policy,
        "items": all_items,
        "resolved": resolved,
    }


async def build_shell_config(*, surface: str = "platform") -> dict:
    """Compose app-shell config from platform-owned manifests."""
    app_root = resolve_app_root()
    ai_path = app_root / "config" / "ai.json"

    shell_surface = _normalize_shell_surface(surface)
    is_studio = shell_surface == "studio"
    is_user = shell_surface == "user"
    result: dict = {
        "chat_startup_mode": "ask",
        "landing_spot": "/apps" if is_studio else "/me" if is_user else "/",
    }
    app_manifest = _load_app_manifest()
    auth_contract = load_app_auth_contract(app_root, auth_required=app_manifest.get("authRequired", False))
    result["auth"] = await build_app_auth_projection(auth_contract)
    shell_shortcuts: dict[str, Any] | None = None
    shell_navigation: dict[str, Any] | None = None
    shell_chrome: dict[str, Any] | None = None

    try:
        if app_manifest:
            for key in ("appName", "app_name"):
                value = app_manifest.get(key)
                if isinstance(value, str) and value.strip():
                    result["appName"] = value.strip()
                    break
            for key in ("appId", "app_id"):
                value = app_manifest.get(key)
                if isinstance(value, str) and value.strip():
                    result["appId"] = value.strip()
                    break
            if not is_studio and not is_user:
                startup = app_manifest.get("startup") if isinstance(app_manifest.get("startup"), dict) else {}
                landing_spot = startup.get("landing_spot")
                if isinstance(landing_spot, str) and landing_spot.startswith("/"):
                    result["landing_spot"] = landing_spot
    except Exception as exc:
        logger.warning("[shell-config] Could not read app startup config: %s", exc)

    try:
        ai = json.loads(ai_path.read_text(encoding="utf-8")) if ai_path.exists() else {}
        ai = resolve_runtime_ai_config(ai, app_root=app_root)
        chat = ai.get("chat") or {}
        workflows = ai.get("workflows") or {}
        result["chat_startup_mode"] = chat.get("chat_startup_mode") or chat.get("startup_mode") or "ask"
        result["entry_point"] = workflows.get("entry_point")
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to read shell config") from exc

    try:
        shell_config_path = _resolve_shell_config_path()
        if shell_config_path.exists():
            shell_config = json.loads(shell_config_path.read_text(encoding="utf-8"))
            for key in ("header", "profile", "notifications", "footer", "mobile"):
                value = shell_config.get(key)
                if value is not None:
                    result[key] = value
            if isinstance(shell_config.get("shortcuts"), dict):
                shell_shortcuts = shell_config["shortcuts"]
            if isinstance(shell_config.get("navigation"), dict):
                shell_navigation = shell_config["navigation"]
            if isinstance(shell_config.get("chrome"), dict):
                shell_chrome = shell_config["chrome"]
    except Exception as exc:
        logger.warning("[shell-config] Could not read shell config: %s", exc)

    pages: list[dict] = []
    for loader, label in (
        (_load_ui_route_manifest_pages, "UI route manifest pages"),
        (_load_page_schema_routes, "page schema routes"),
        (_load_workflow_entrypoint_pages, "workflow entrypoint routes"),
    ):
        try:
            pages.extend(loader(app_root))
        except Exception as exc:
            logger.warning("[shell-config] Could not read %s: %s", label, exc)

    # In Studio mode, also load routes from the factory_app bundle when the
    # active workspace is a different app root. Studio routes (surfaces: [studio])
    # are declared in factory_app/app/ui/route_manifest.json and are not present
    # in a freshly scaffolded workspace's route manifest.
    if is_studio:
        factory_root = resolve_factory_app_root()
        if factory_root is not None:
            factory_app_bundle = factory_root / "app"
            if factory_app_bundle.resolve() != app_root.resolve():
                try:
                    pages.extend(_load_ui_route_manifest_pages(factory_app_bundle))
                except Exception as exc:
                    logger.warning("[shell-config] Could not read factory route manifest: %s", exc)

    if pages:
        result["pages"] = _dedupe_and_sort_pages([
            page for page in pages if _page_targets_surface(page, surface=shell_surface)
        ])

    pages = result.get("pages", [])
    _append_page_once(
        pages,
        {
            "path": PROFILE_SHELL_ROUTE["path"],
            "component": PROFILE_SHELL_ROUTE["component"],
            "label": PROFILE_SHELL_ROUTE["label"],
            "order": PROFILE_SHELL_ROUTE["order"],
            "meta": {
                "requiresAuth": True,
                "title": PROFILE_SHELL_ROUTE["title"],
                "appShell": True,
                "shellMode": "social",
                "ai_context": "The user is on their Profile page — their account identity, preferences, and module-contributed profile panels.",
            },
        },
    )
    _append_page_once(
        pages,
        {
            "path": "/u/:username",
            "component": "ProfilePage",
            "label": "User Profile",
            "order": 999,
            "meta": {
                "requiresAuth": True,
                "title": "Profile",
                "appShell": True,
                "shellMode": "social",
                "ai_context": "The user is viewing another user's public profile.",
            },
        },
    )
    result["pages"] = _dedupe_and_sort_pages(pages)

    pages = result.get("pages", [])
    _admin_registry = load_admin_registry(resolve_active_app_root())
    for route in build_admin_shell_routes(_admin_registry):
        route_surfaces = route.get("surfaces")
        if isinstance(route_surfaces, list) and shell_surface not in route_surfaces:
            continue
        _append_page_once(pages, {
            "path": route["path"],
            "component": "AdminPortal",
            "label": route["label"],
            "order": route["order"],
            "meta": {
                "requiresAuth": True,
                "requiresRole": "admin",
                "title": route["title"],
                "appShell": True,
                "adminPage": route["admin_page"],
                "shellMode": "workspace",
                **({"surfaces": route_surfaces} if isinstance(route_surfaces, list) else {}),
            },
        })
    result["pages"] = _dedupe_and_sort_pages(pages)

    _apply_dynamic_shell_navigation(
        result,
        pages=result["pages"],
        navigation=shell_navigation,
        shortcuts=shell_shortcuts,
    )
    result["chrome"] = _normalize_chrome_policy(shell_chrome)

    # Admin Portal is a Studio guarantee — not injected into app or user surfaces.
    if is_studio:
        _inject_admin_portal(result)

    result["surface"] = shell_surface

    return result
