from __future__ import annotations

import json
import re
from pathlib import Path

import yaml


def _workspace() -> Path:
    return Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (_workspace() / relative_path).read_text(encoding="utf-8")


def test_studio_workspace_queue_keeps_operator_guard_and_app_queue_uses_resource_authority() -> None:
    manifest = json.loads(_read("factory_app/app/ui/route_manifest.json"))
    routes = {route["path"]: route for route in manifest["pages"]}
    assert routes["/support"]["meta"]["requiresRole"] == "admin"
    assert "requiresRole" not in routes["/apps/:appId/support"]["meta"]


def test_admin_portal_is_the_only_registered_admin_page() -> None:
    core_source = _read("chat-ui/src/registry/coreComponents.js")
    # The framework owns AdminPortal; app barrels register only app extensions.
    ui_source = _read("factory_app/app/ui/index.js")
    admin_source = _read("factory_app/app/admin/index.js")

    assert "registerComponent('AdminPortal'" in core_source
    assert "registerAdminComponents" in ui_source
    assert "registerComponent('AdminPortal'" not in admin_source
    assert "AppAdminDashboard" not in core_source
    assert "AppAdminDashboard" not in ui_source
    assert "AppAdminDashboard" not in admin_source


def test_admin_portal_renders_every_page_through_one_layout() -> None:
    source = _read("chat-ui/src/pages/AdminPage.jsx")

    assert "WorkspaceLayout" in source
    assert "AdminSectionRoute" in source
    # Routing handles any page id, not a fixed few.
    assert "^\\/apps\\/[^/]+\\/([^/]+)\\/?$" in source
    assert "raw === 'activity'" not in source
    assert "raw === 'audit'" not in source
    assert "raw === 'logs'" not in source
    assert "useLocation" in source
    assert "pagePanels" in source
    assert "normalizeRuntimePanels" in source
    assert "BuilderWorkspacePanel" not in source

    # Only pages the framework has content for get a section component.
    framework_pages = source[source.index("const FRAMEWORK_PAGES = {"):]
    framework_pages = framework_pages[: framework_pages.index("}")]
    assert re.findall(r"^\s*([a-z]+):", framework_pages, flags=re.MULTILINE) == [
        "overview",
        "usage",
        "operations",
        "settings",
    ]

    # Every page, not only Access, shows app-backend panels and module panels.
    body = source[source.index("<AdminWorkspaceLayout"):]
    assert "<AppAdminPanels panels={backendPanels}" in body
    assert "<AdminExtensionPanels panels={modulePanels} />" in body
    assert "<EmptyState>" in body
    assert "p.section === activePage" in source


def test_admin_portal_has_no_unrendered_section_components() -> None:
    """Each file in admin/pages is a section AdminPage actually mounts."""
    source = _read("chat-ui/src/pages/AdminPage.jsx")
    section_files = sorted(path.name for path in (_workspace() / "chat-ui/src/admin/pages").glob("*.jsx"))

    assert section_files == [
        "OperationsSection.jsx",
        "OverviewSection.jsx",
        "SettingsSection.jsx",
        "UsageSection.jsx",
    ]
    for name in section_files:
        assert f"'../admin/pages/{name}'" in source


def test_admin_sections_read_token_numbers_only_from_the_usage_endpoint() -> None:
    """/api/admin/stats, /runs, and /sessions carry no token or cost fields."""
    removed_fields = (
        "total_prompt_tokens",
        "total_completion_tokens",
        "total_agent_turns",
        "total_errors",
        "total_cost",
        "usage_prompt_tokens_final",
        "usage_completion_tokens_final",
        "usage_total_cost_final",
    )
    for path in sorted((_workspace() / "chat-ui/src/admin").rglob("*.js*")):
        text = path.read_text(encoding="utf-8")
        for field in removed_fields:
            assert field not in text, f"{path.name} reads {field}, which no admin endpoint returns"

    usage_helpers = _read("chat-ui/src/admin/adminUsage.js")
    assert "export const USAGE_ENDPOINT = `/api/admin/usage?limit=${USAGE_EVENT_LIMIT}`" in usage_helpers
    assert "USAGE_ENDPOINT" in _read("chat-ui/src/admin/pages/OverviewSection.jsx")
    assert "USAGE_ENDPOINT" in _read("chat-ui/src/admin/pages/UsageSection.jsx")


def test_admin_portal_usage_window_fits_the_server_limit() -> None:
    """The portal asks for a number of usage events the endpoint accepts.

    The portal labels its totals "Latest N model calls" from its own constant.
    If N were above the endpoint's maximum the request would be refused (422)
    and every usage card would show an error.
    """
    import inspect

    from mozaiksai.core.admin import router as admin_router

    limit = inspect.signature(admin_router.get_admin_usage).parameters["limit"].default
    bounds = {type(item).__name__: item for item in limit.metadata}
    server_minimum, server_maximum = bounds["Ge"].ge, bounds["Le"].le

    declared = re.search(r"^export const USAGE_EVENT_LIMIT = (\d+);$", _read("chat-ui/src/admin/adminUsage.js"), re.MULTILINE)
    assert declared, "adminUsage.js must declare USAGE_EVENT_LIMIT as a number"
    assert server_minimum <= int(declared.group(1)) <= server_maximum


def test_platform_shell_registers_admin_section_routes() -> None:
    platform_source = _read("mozaiksai/hosts/platform.py") + "\n" + _read("mozaiksai/hosts/shell_config.py")
    registry_source = _read("factory_app/app/admin/admin_registry.yaml")
    route_manifest = _read("factory_app/app/ui/route_manifest.json")

    # First-party Studio product routes live in route_manifest.json.
    assert "pages: []" in registry_source
    for path in [
        "/apps/:appId/overview",
        "/apps/:appId/building",
        "/apps/:appId/usage",
        "/apps/:appId/activity",
    ]:
        assert path in route_manifest
        assert path not in registry_source
    # Pages with no data source behind them are not mounted at all.
    assert "/apps/:appId/access" not in route_manifest
    assert '"path": "/users"' not in route_manifest

    # The first-party Studio does not expose a standalone /admin page.
    assert "path: /admin" not in registry_source
    # /apps/:appId/admin is not a valid path — overview uses the app Studio route.
    assert "/apps/:appId/admin" not in registry_source

    assert '"component": "AdminPortal"' in platform_source
    assert "build_admin_shell_routes" in platform_source
    assert "load_admin_registry" in platform_source
    # Admin portal extension routes stay separate from first-party Studio routes.
    assert "/apps/:appId/users" not in route_manifest
    assert "/apps/:appId/operations" not in route_manifest
    assert "/apps/:appId/settings" not in route_manifest


def _all_route_paths(routes, prefix: str = "") -> set[str]:
    """Recursively collect all route paths.

    Handles both pre-0.116 FastAPI and 0.116+ (_IncludedRouter with
    original_router / include_context.prefix).
    """
    paths: set[str] = set()
    for route in routes:
        path = getattr(route, "path", None)
        if path is not None:
            paths.add(prefix + path)
        elif hasattr(route, "original_router"):
            ctx = getattr(route, "include_context", None)
            sub_prefix = prefix + (getattr(ctx, "prefix", "") or "")
            paths.update(_all_route_paths(route.original_router.routes, sub_prefix))
    return paths


def test_platform_host_mounts_admin_api_routes() -> None:
    import importlib

    platform_host = importlib.import_module("mozaiksai.hosts.platform")
    routes = _all_route_paths(platform_host.app.routes)

    assert "/api/admin/config" in routes
    assert "/api/admin/stats" in routes


def test_profile_menu_uses_framework_defaults() -> None:
    source = _read("chat-ui/src/components/layout/Header.js")

    assert "getDefaultProfileMenu" in source
    assert '"profile"' in source
    assert 'href: "/me"' in source
    assert 'href: "/profile"' not in source
    assert 'href: "/admin"' not in source
    assert '"admin-portal"' not in source
    assert '"signin"' in source
    assert '"signout"' in source
    assert "mergeProfileMenu" in source


def test_app_admin_dashboard_is_panel_group_not_registered_route() -> None:
    source = _read("chat-ui/src/pages/AppAdminDashboard.jsx")

    assert "export function AppAdminPanels" in source
    assert "parseAppBackendAdminConfig" in source
    assert "builtin_panel" in source
    assert "section" in source


def test_runtime_admin_config_uses_flat_panel_collections() -> None:
    source = _read("mozaiksai/core/admin/router.py")

    assert "_load_module_admin_panels" in source
    assert '"runtime_panels"' in source
    assert '"module_panels"' in source
    assert '"pages"' in source
    assert "load_admin_registry" in source
    # Removed constants and helpers stay absent.
    assert "DEFAULT_ADMIN_SHELL_CONFIG" not in source
    assert "_infer_admin_panel_section" not in source


def test_runtime_admin_config_discovers_module_admin_yaml(tmp_path) -> None:
    import importlib

    from mozaiksai.hosts import (
        runtime as runtime_app,  # noqa: F401 - initializes persistence dependencies before admin imports
    )

    app_root = tmp_path / "platform"
    module_root = app_root / "modules" / "crm"
    contracts_root = module_root / "contracts"
    contracts_root.mkdir(parents=True)
    (contracts_root / "admin.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "mozaiks.admin.v2",
                "panels": [
                    {
                        "id": "crm.contacts",
                        "label": "Contacts",
                        "page": "settings",
                        "renderer": "schema",
                        "layout": "full-width",
                        "sections": [
                            {
                                "id": "contacts-table",
                                "primitive": "DataTable",
                                "config": {
                                    "api_endpoint": "/api/modules/crm/list_contacts",
                                    "columns": [{"key": "name", "label": "Name"}],
                                },
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    admin_router = importlib.import_module("mozaiksai.core.admin.router")
    panels = admin_router._load_module_admin_panels(app_root)

    assert panels == [
        {
            "id": "crm.contacts",
            "label": "Contacts",
            "description": None,
            "page": "settings",
            "order": 0,
            "renderer": "schema",
            "layout": "full-width",
            "sections": [
                {
                    "id": "contacts-table",
                    "primitive": "DataTable",
                    "config": {
                        "api_endpoint": "/api/modules/crm/list_contacts",
                        "columns": [{"key": "name", "label": "Name"}],
                    },
                }
            ],
            "component": None,
            "permissions": [],
            "permissions_any": [],
            "module_id": "crm",
            "source": "module",
        }
    ]

