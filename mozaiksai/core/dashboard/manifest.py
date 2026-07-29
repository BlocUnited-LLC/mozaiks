"""Canonical App Dashboard manifest contract.

The dashboard manifest describes owner/operator portals for a Mozaiks app. It is
separate from route manifests, admin registries, and workflow sequence routing.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

logger = logging.getLogger(__name__)

DASHBOARD_SCHEMA_VERSION = "mozaiks.dashboard.v1"
DASHBOARD_MANIFEST_RELATIVE_PATH = Path("dashboard") / "dashboard.yaml"

DashboardScope = Literal["workspace", "app"]
DashboardActionType = Literal["route", "workflow_sequence", "workflow", "module_action", "external_url"]
DashboardPanelSource = Literal["runtime", "module", "workflow", "media", "custom"]
DashboardRouteValidationCode = Literal[
    "enabled_portal_route_missing",
    "enabled_portal_route_hidden",
    "enabled_portal_route_group_mismatch",
    "visible_route_missing_enabled_portal",
]
DashboardPanelType = Literal[
    "summary_strip",
    "kpi_grid",
    "next_step",
    "portal_link_grid",
    "app_portfolio_table",
    "workspace_usage_summary",
    "workspace_integrations",
    "build_threads",
    "artifact_timeline",
    "approval_queue",
    "approval_votes",
    "workflow_launcher",
    "brand_kit",
    "media_asset_gallery",
    "landing_page_status",
    "campaign_assets",
    "hosting_status",
    "domain_status",
    "integration_status",
    "user_access_table",
    "usage_breakdown",
    "support_threads",
    "settings_form",
    "module_panel_ref",
    "custom_component",
]

_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*$")


def _clean_text(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _clean_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("value must be a string")
    text = value.strip()
    return text or None


def _clean_id(value: Any, *, field_name: str) -> str:
    text = _clean_text(value, field_name=field_name)
    if not _ID_RE.match(text):
        raise ValueError(
            f"{field_name} must start with a lowercase letter and contain only lowercase letters, "
            "numbers, underscores, dots, or hyphens"
        )
    return text


def _unique_ids(items: Iterable[Any], *, owner: str) -> None:
    seen: set[str] = set()
    for item in items:
        item_id = getattr(item, "id", None)
        if not isinstance(item_id, str):
            raise ValueError(f"{owner} contains an item without a string id")
        if item_id in seen:
            raise ValueError(f"{owner} contains duplicate id '{item_id}'")
        seen.add(item_id)


class DashboardAction(BaseModel):
    """A deterministic action exposed from a dashboard portal or panel."""

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    type: DashboardActionType
    target: str
    order: int = 0
    variant: Literal["primary", "secondary", "outline", "ghost", "danger"] = "secondary"
    params: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    requires_confirmation: bool = False

    @field_validator("id", mode="before")
    @classmethod
    def _id(cls, value: Any) -> str:
        return _clean_id(value, field_name="dashboard action id")

    @field_validator("label", "target", mode="before")
    @classmethod
    def _required_text(cls, value: Any, info: Any) -> str:
        return _clean_text(value, field_name=f"dashboard action {info.field_name}")

    @model_validator(mode="after")
    def _validate_target_shape(self) -> DashboardAction:
        if self.type == "route" and not self.target.startswith("/"):
            raise ValueError("route dashboard actions must target an app-local route path")
        if self.type == "external_url" and not self.target.startswith(("https://", "mailto:")):
            raise ValueError("external_url dashboard actions must target https:// or mailto: URLs")
        if self.type == "module_action" and "." not in self.target:
            raise ValueError("module_action dashboard actions must target '<module_id>.<action_id>'")
        return self


class DashboardPanel(BaseModel):
    """A deterministic panel mounted inside a dashboard portal."""

    model_config = ConfigDict(extra="forbid")

    id: str
    type: DashboardPanelType
    title: str | None = None
    description: str | None = None
    order: int = 0
    source: DashboardPanelSource = "runtime"
    module_id: str | None = None
    workflow_id: str | None = None
    component: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)
    actions: list[DashboardAction] = Field(default_factory=list)

    @field_validator("id", mode="before")
    @classmethod
    def _id(cls, value: Any) -> str:
        return _clean_id(value, field_name="dashboard panel id")

    @field_validator("title", "description", "module_id", "workflow_id", "component", mode="before")
    @classmethod
    def _optional_text(cls, value: Any) -> str | None:
        return _clean_optional_text(value)

    @field_validator("capabilities", mode="before")
    @classmethod
    def _capabilities(cls, value: Any) -> list[str]:
        values = [] if value is None else ([value] if isinstance(value, str) else value)
        if not isinstance(values, list):
            raise ValueError("dashboard panel capabilities must be a list")
        cleaned: list[str] = []
        for item in values:
            capability = _clean_id(item, field_name="dashboard panel capability")
            if capability not in cleaned:
                cleaned.append(capability)
        return cleaned

    @model_validator(mode="after")
    def _validate_panel_contract(self) -> DashboardPanel:
        _unique_ids(self.actions, owner=f"dashboard panel '{self.id}' actions")
        if self.type == "custom_component" and not self.component:
            raise ValueError("custom_component dashboard panels must declare component")
        if self.type == "module_panel_ref" and not self.module_id:
            raise ValueError("module_panel_ref dashboard panels must declare module_id")
        if self.source == "workflow" and not self.workflow_id:
            raise ValueError("workflow-sourced dashboard panels must declare workflow_id")
        return self


class DashboardPortal(BaseModel):
    """A route-level lane inside a dashboard surface."""

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    route: str
    description: str | None = None
    icon: str = "dashboard"
    order: int = 0
    enabled: bool = True
    show_in_navigation: bool = True
    capabilities: list[str] = Field(default_factory=list)
    panels: list[DashboardPanel] = Field(default_factory=list)
    actions: list[DashboardAction] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id", mode="before")
    @classmethod
    def _id(cls, value: Any) -> str:
        return _clean_id(value, field_name="dashboard portal id")

    @field_validator("label", "route", "icon", mode="before")
    @classmethod
    def _required_text(cls, value: Any, info: Any) -> str:
        return _clean_text(value, field_name=f"dashboard portal {info.field_name}")

    @field_validator("description", mode="before")
    @classmethod
    def _description(cls, value: Any) -> str | None:
        return _clean_optional_text(value)

    @field_validator("capabilities", mode="before")
    @classmethod
    def _capabilities(cls, value: Any) -> list[str]:
        values = [] if value is None else ([value] if isinstance(value, str) else value)
        if not isinstance(values, list):
            raise ValueError("dashboard portal capabilities must be a list")
        cleaned: list[str] = []
        for item in values:
            capability = _clean_id(item, field_name="dashboard portal capability")
            if capability not in cleaned:
                cleaned.append(capability)
        return cleaned

    @model_validator(mode="after")
    def _validate_portal_contract(self) -> DashboardPortal:
        if not self.route.startswith("/"):
            raise ValueError("dashboard portal routes must start with '/'")
        _unique_ids(self.panels, owner=f"dashboard portal '{self.id}' panels")
        _unique_ids(self.actions, owner=f"dashboard portal '{self.id}' actions")
        return self


class DashboardSurface(BaseModel):
    """Workspace-level or app-level dashboard surface."""

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    scope: DashboardScope
    route_pattern: str
    default_portal: str
    portals: list[DashboardPortal] = Field(default_factory=list)

    @field_validator("id", "default_portal", mode="before")
    @classmethod
    def _id_fields(cls, value: Any, info: Any) -> str:
        return _clean_id(value, field_name=f"dashboard surface {info.field_name}")

    @field_validator("label", "route_pattern", mode="before")
    @classmethod
    def _required_text(cls, value: Any, info: Any) -> str:
        return _clean_text(value, field_name=f"dashboard surface {info.field_name}")

    @model_validator(mode="after")
    def _validate_surface_contract(self) -> DashboardSurface:
        if not self.route_pattern.startswith("/"):
            raise ValueError("dashboard surface route_pattern must start with '/'")
        if self.scope == "app" and ":appId" not in self.route_pattern:
            raise ValueError("app dashboard route_pattern must include ':appId'")
        if self.scope == "workspace" and ":appId" in self.route_pattern:
            raise ValueError("workspace dashboard route_pattern must not include ':appId'")
        _unique_ids(self.portals, owner=f"dashboard surface '{self.id}' portals")
        enabled_ids = {portal.id for portal in self.portals if portal.enabled}
        if self.default_portal not in enabled_ids:
            raise ValueError(
                f"dashboard surface '{self.id}' default_portal must reference an enabled portal"
            )
        return self

    def enabled_portals(self) -> list[DashboardPortal]:
        return sorted((portal for portal in self.portals if portal.enabled), key=lambda item: (item.order, item.id))


class DashboardManifest(BaseModel):
    """Validated dashboard manifest."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = DASHBOARD_SCHEMA_VERSION
    extends: Literal["default"] | None = "default"
    workspace: DashboardSurface
    app: DashboardSurface

    @field_validator("schema_version", mode="before")
    @classmethod
    def _schema_version(cls, value: Any) -> str:
        text = _clean_text(value, field_name="dashboard schema_version")
        if text != DASHBOARD_SCHEMA_VERSION:
            raise ValueError(f"dashboard schema_version must be {DASHBOARD_SCHEMA_VERSION}")
        return text

    @model_validator(mode="after")
    def _validate_surface_scopes(self) -> DashboardManifest:
        if self.workspace.scope != "workspace":
            raise ValueError("dashboard.workspace must have scope 'workspace'")
        if self.app.scope != "app":
            raise ValueError("dashboard.app must have scope 'app'")
        return self

    def surface_for_scope(self, scope: DashboardScope) -> DashboardSurface:
        return self.workspace if scope == "workspace" else self.app


class DashboardRouteValidationIssue(BaseModel):
    """A dashboard/route-manifest alignment issue."""

    model_config = ConfigDict(extra="forbid")

    code: DashboardRouteValidationCode
    scope: DashboardScope
    route: str
    message: str
    portal_id: str | None = None
    page_id: str | None = None


class DashboardRouteValidationResult(BaseModel):
    """Result from validating a dashboard manifest against mounted routes."""

    model_config = ConfigDict(extra="forbid")

    issues: list[DashboardRouteValidationIssue] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def messages(self) -> list[str]:
        return [issue.message for issue in self.issues]


def _default_manifest_dict() -> dict[str, Any]:
    return {
        "schema_version": DASHBOARD_SCHEMA_VERSION,
        "extends": "default",
        "workspace": {
            "id": "workspace",
            "label": "Workspace Dashboard",
            "scope": "workspace",
            "route_pattern": "/apps",
            "default_portal": "portfolio",
            "portals": [
                {
                    "id": "portfolio",
                    "label": "Apps",
                    "route": "/apps",
                    "description": "Portfolio-level list of apps in the workspace.",
                    "icon": "apps",
                    "order": 0,
                    "panels": [
                        {"id": "portfolio", "type": "app_portfolio_table", "title": "Apps"},
                    ],
                },
                {
                    "id": "usage",
                    "label": "Usage",
                    "route": "/usage",
                    "description": "Workspace-level usage, cost, and activity signals.",
                    "icon": "chart",
                    "order": 20,
                    "panels": [
                        {"id": "usage", "type": "workspace_usage_summary", "title": "Usage"},
                    ],
                },
                {
                    "id": "integrations",
                    "label": "Integrations",
                    "route": "/integrations",
                    "description": "Shared integration readiness across workspace apps.",
                    "icon": "plug",
                    "order": 30,
                    "panels": [
                        {
                            "id": "integrations",
                            "type": "workspace_integrations",
                            "title": "Integrations",
                        },
                    ],
                },
            ],
        },
        "app": {
            "id": "app",
            "label": "App Dashboard",
            "scope": "app",
            "route_pattern": "/apps/:appId",
            "default_portal": "overview",
            "portals": [
                {
                    "id": "overview",
                    "label": "Overview",
                    "route": "/apps/:appId/overview",
                    "description": "App identity, lifecycle, metrics, alerts, and next step.",
                    "icon": "dashboard",
                    "order": 0,
                    "panels": [
                        {"id": "summary", "type": "summary_strip", "title": "Summary"},
                        {"id": "kpis", "type": "kpi_grid", "title": "Metrics"},
                        {"id": "next_step", "type": "next_step", "title": "Next step"},
                    ],
                },
                {
                    "id": "building",
                    "label": "Building",
                    "route": "/apps/:appId/building",
                    "description": "Build threads, artifact versions, approvals, and workflow launch actions.",
                    "icon": "hammer",
                    "order": 10,
                    "capabilities": ["build_threads", "artifact_versions", "approval_votes"],
                    "panels": [
                        {"id": "threads", "type": "build_threads", "title": "Threads"},
                        {"id": "artifacts", "type": "artifact_timeline", "title": "Artifacts"},
                        {"id": "approvals", "type": "approval_queue", "title": "Approvals"},
                        {
                            "id": "continue_build",
                            "type": "workflow_launcher",
                            "title": "Continue build",
                            "source": "workflow",
                            "workflow_id": "extended_orchestration",
                            "actions": [
                                {
                                    "id": "continue_build",
                                    "label": "Continue Build",
                                    "type": "workflow_sequence",
                                    "target": "build",
                                    "variant": "primary",
                                }
                            ],
                        },
                    ],
                },
                {
                    "id": "branding",
                    "label": "Branding",
                    "route": "/apps/:appId/branding",
                    "description": "Brand kit, generated media, themes, and promoted brand assets.",
                    "icon": "palette",
                    "order": 20,
                    "capabilities": ["brand_kit", "media_assets"],
                    "panels": [
                        {"id": "brand_kit", "type": "brand_kit", "title": "Brand kit"},
                        {"id": "media", "type": "media_asset_gallery", "title": "Media assets", "source": "media"},
                    ],
                },
                {
                    "id": "launch",
                    "label": "Launch",
                    "route": "/apps/:appId/launch",
                    "description": "Landing page status, hosting, domains, and launch readiness.",
                    "icon": "rocket",
                    "order": 30,
                    "capabilities": ["landing_page", "hosting", "domains"],
                    "panels": [
                        {"id": "landing_page", "type": "landing_page_status", "title": "Landing page"},
                        {"id": "hosting", "type": "hosting_status", "title": "Hosting"},
                        {"id": "domains", "type": "domain_status", "title": "Domains"},
                    ],
                },
                {
                    "id": "growth",
                    "label": "Growth",
                    "route": "/apps/:appId/growth",
                    "description": "Marketing campaigns, growth experiments, and campaign assets.",
                    "icon": "megaphone",
                    "order": 40,
                    "capabilities": ["marketing_campaigns"],
                    "panels": [
                        {"id": "campaigns", "type": "campaign_assets", "title": "Campaigns"},
                    ],
                },
                {
                    "id": "users",
                    "label": "Users",
                    "route": "/apps/:appId/access",
                    "description": "App users, roles, invitations, and access policy summaries.",
                    "icon": "users",
                    "order": 50,
                    "panels": [
                        {"id": "access", "type": "user_access_table", "title": "Access"},
                    ],
                },
                {
                    "id": "usage",
                    "label": "Usage",
                    "route": "/apps/:appId/usage",
                    "description": "App-scoped chats, workflow usage, token consumption, and cost.",
                    "icon": "chart",
                    "order": 60,
                    "panels": [
                        {"id": "usage", "type": "usage_breakdown", "title": "Usage"},
                    ],
                },
                {
                    "id": "support",
                    "label": "Support",
                    "route": "/apps/:appId/support",
                    "description": "App-specific support threads, follow-up, and service diagnostics.",
                    "icon": "support",
                    "order": 70,
                    "panels": [
                        {"id": "threads", "type": "support_threads", "title": "Support"},
                    ],
                },
                {
                    "id": "settings",
                    "label": "Settings",
                    "route": "/apps/:appId/settings",
                    "description": "App-level settings and configuration forms.",
                    "icon": "settings",
                    "order": 90,
                    "panels": [
                        {"id": "settings", "type": "settings_form", "title": "Settings"},
                    ],
                },
            ],
        },
    }


def build_default_dashboard_manifest() -> DashboardManifest:
    """Return the default Workspace Dashboard + App Dashboard contract."""
    return DashboardManifest.model_validate(_default_manifest_dict())


def _merge_named_lists(
    base_items: list[dict[str, Any]],
    overlay_items: list[dict[str, Any]],
    *,
    child_key: str | None = None,
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = [deepcopy(item) for item in base_items]
    positions = {str(item.get("id")): index for index, item in enumerate(merged) if isinstance(item, dict)}
    for overlay in overlay_items:
        if not isinstance(overlay, dict):
            merged.append(deepcopy(overlay))
            continue
        item_id = str(overlay.get("id") or "")
        if item_id and item_id in positions:
            current = merged[positions[item_id]]
            next_item = {**current, **deepcopy(overlay)}
            if child_key and isinstance(current.get(child_key), list) and isinstance(overlay.get(child_key), list):
                next_item[child_key] = _merge_named_lists(current[child_key], overlay[child_key])
            if isinstance(current.get("actions"), list) and isinstance(overlay.get("actions"), list):
                next_item["actions"] = _merge_named_lists(current["actions"], overlay["actions"])
            merged[positions[item_id]] = next_item
        else:
            merged.append(deepcopy(overlay))
            if item_id:
                positions[item_id] = len(merged) - 1
    return merged


def _merge_surface(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = {**deepcopy(base), **deepcopy({k: v for k, v in overlay.items() if k != "portals"})}
    if isinstance(overlay.get("portals"), list):
        result["portals"] = _merge_named_lists(
            list(base.get("portals") or []),
            overlay["portals"],
            child_key="panels",
        )
    return result


def merge_dashboard_manifest_overlay(raw: dict[str, Any]) -> dict[str, Any]:
    """Merge an app dashboard manifest overlay with the canonical defaults.

    The overlay model lets generated apps customize labels, routes, ordering,
    enabled state, panels, and actions without copying the whole default file.
    Set ``extends: null`` to provide a complete manifest with no defaults.
    """
    if not isinstance(raw, dict):
        raise ValueError("dashboard manifest must be a mapping")
    extends = raw.get("extends", "default")
    if extends is None:
        return deepcopy(raw)
    if extends != "default":
        raise ValueError("dashboard manifest extends must be 'default' or null")

    result = _default_manifest_dict()
    for key, value in raw.items():
        if key in {"workspace", "app"} and isinstance(value, dict):
            result[key] = _merge_surface(result[key], value)
        elif key != "extends":
            result[key] = deepcopy(value)
    result["extends"] = "default"
    return result


def load_dashboard_manifest(app_root: Path) -> DashboardManifest:
    """Load ``dashboard/dashboard.yaml`` from an app root.

    Missing manifests return the canonical default. Invalid manifests are logged
    and also fall back to the canonical default so Studio remains available.
    """
    manifest_path = (app_root / DASHBOARD_MANIFEST_RELATIVE_PATH).resolve()
    if not manifest_path.exists():
        return build_default_dashboard_manifest()
    try:
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        merged = merge_dashboard_manifest_overlay(raw)
        return DashboardManifest.model_validate(merged)
    except Exception as exc:
        logger.warning("DASHBOARD_MANIFEST_LOAD_FAILED %s: %s", manifest_path, exc, exc_info=True)
        return build_default_dashboard_manifest()


def _dashboard_route_group(scope: DashboardScope) -> str:
    return "workspace-studio" if scope == "workspace" else "app-studio"


def _route_page_navigation(page: Mapping[str, Any]) -> Mapping[str, Any]:
    navigation = page.get("navigation")
    if isinstance(navigation, Mapping):
        return navigation

    meta = page.get("meta")
    if isinstance(meta, Mapping):
        nested_navigation = meta.get("navigation")
        if isinstance(nested_navigation, Mapping):
            return nested_navigation
    return {}


def _route_page_id(page: Mapping[str, Any]) -> str | None:
    value = page.get("id")
    if isinstance(value, str) and value.strip():
        return value.strip()
    value = page.get("component")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _route_pages_by_path(route_pages: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    pages_by_path: dict[str, Mapping[str, Any]] = {}
    for page in route_pages:
        path = page.get("path")
        if not isinstance(path, str) or not path.startswith("/"):
            continue
        pages_by_path.setdefault(path, page)
    return pages_by_path


def validate_dashboard_manifest_routes(
    manifest: DashboardManifest,
    route_pages: Iterable[Mapping[str, Any]],
    *,
    scopes: Iterable[DashboardScope] | None = None,
    require_visible_route_portals: bool = True,
) -> DashboardRouteValidationResult:
    """Validate dashboard portals against concrete route-manifest pages.

    The dashboard manifest owns Studio portal semantics, while
    ``ui/route_manifest.json`` owns concrete React route mounting. This validator
    makes the boundary executable for CI:

    - every enabled portal must resolve to a mounted route;
    - every enabled portal shown in navigation must be visible in the expected
      Studio navigation group;
    - every visible Studio navigation route must be represented by an enabled
      portal when ``require_visible_route_portals`` is true.

    Route pages may use either top-level ``navigation`` or nested
    ``meta.navigation`` metadata. The returned issue list is deterministic and
    safe to render in CI output.
    """

    selected_scopes = set(scopes or ("workspace", "app"))
    pages_by_path = _route_pages_by_path(route_pages)
    issues: list[DashboardRouteValidationIssue] = []

    for surface in (manifest.workspace, manifest.app):
        if surface.scope not in selected_scopes:
            continue

        expected_group = _dashboard_route_group(surface.scope)
        enabled_portal_routes: set[str] = set()

        for portal in surface.enabled_portals():
            enabled_portal_routes.add(portal.route)
            route_page = pages_by_path.get(portal.route)
            if route_page is None:
                issues.append(
                    DashboardRouteValidationIssue(
                        code="enabled_portal_route_missing",
                        scope=surface.scope,
                        portal_id=portal.id,
                        route=portal.route,
                        message=(
                            f"{surface.scope} dashboard portal '{portal.id}' points to "
                            f"unregistered route '{portal.route}'."
                        ),
                    )
                )
                continue

            if not portal.show_in_navigation:
                continue

            navigation = _route_page_navigation(route_page)
            if navigation.get("include") is False:
                issues.append(
                    DashboardRouteValidationIssue(
                        code="enabled_portal_route_hidden",
                        scope=surface.scope,
                        portal_id=portal.id,
                        page_id=_route_page_id(route_page),
                        route=portal.route,
                        message=(
                            f"{surface.scope} dashboard portal '{portal.id}' points to "
                            f"route '{portal.route}', but that route is hidden from navigation."
                        ),
                    )
                )

            actual_group = navigation.get("group")
            if actual_group != expected_group:
                issues.append(
                    DashboardRouteValidationIssue(
                        code="enabled_portal_route_group_mismatch",
                        scope=surface.scope,
                        portal_id=portal.id,
                        page_id=_route_page_id(route_page),
                        route=portal.route,
                        message=(
                            f"{surface.scope} dashboard portal '{portal.id}' points to "
                            f"route '{portal.route}' in navigation group {actual_group!r}; "
                            f"expected {expected_group!r}."
                        ),
                    )
                )

        if not require_visible_route_portals:
            continue

        for route, route_page in sorted(pages_by_path.items()):
            navigation = _route_page_navigation(route_page)
            if navigation.get("include") is False:
                continue
            if navigation.get("group") != expected_group:
                continue
            if route in enabled_portal_routes:
                continue
            issues.append(
                DashboardRouteValidationIssue(
                    code="visible_route_missing_enabled_portal",
                    scope=surface.scope,
                    page_id=_route_page_id(route_page),
                    route=route,
                    message=(
                        f"visible {surface.scope} Studio route '{route}' is not represented "
                        "by an enabled dashboard portal."
                    ),
                )
            )

    return DashboardRouteValidationResult(issues=issues)


def build_dashboard_shell_routes(
    manifest: DashboardManifest,
    *,
    scopes: Iterable[DashboardScope] | None = None,
    component: str = "DashboardPortalPage",
) -> list[dict[str, Any]]:
    """Build route-manifest-compatible entries from enabled dashboard portals."""
    selected_scopes = set(scopes or ("workspace", "app"))
    routes: list[dict[str, Any]] = []
    for surface in (manifest.workspace, manifest.app):
        if surface.scope not in selected_scopes:
            continue
        group = "workspace-studio" if surface.scope == "workspace" else "app-studio"
        for portal in surface.enabled_portals():
            routes.append(
                {
                    "path": portal.route,
                    "component": component,
                    "label": portal.label,
                    "order": portal.order,
                    "meta": {
                        "surfaces": ["studio"],
                        "requiresAuth": True,
                        "requiresRole": "admin",
                        "title": portal.label,
                        "appShell": True,
                        "shellMode": "workspace",
                        "dashboard": {
                            "surface": surface.id,
                            "scope": surface.scope,
                            "portal": portal.id,
                            "panel_count": len(portal.panels),
                        },
                        "navigation": {
                            "group": group,
                            "scope": "local",
                            "icon": portal.icon,
                            "include": portal.show_in_navigation,
                        },
                    },
                }
            )
    return sorted(routes, key=lambda item: (item["order"], item["path"]))


__all__ = [
    "DASHBOARD_MANIFEST_RELATIVE_PATH",
    "DASHBOARD_SCHEMA_VERSION",
    "DashboardAction",
    "DashboardManifest",
    "DashboardPanel",
    "DashboardPortal",
    "DashboardRouteValidationIssue",
    "DashboardRouteValidationResult",
    "DashboardSurface",
    "build_dashboard_shell_routes",
    "build_default_dashboard_manifest",
    "load_dashboard_manifest",
    "merge_dashboard_manifest_overlay",
    "validate_dashboard_manifest_routes",
]
