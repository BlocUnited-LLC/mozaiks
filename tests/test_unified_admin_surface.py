from __future__ import annotations

from pathlib import Path

import yaml


def _workspace() -> Path:
    return Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (_workspace() / relative_path).read_text(encoding="utf-8")


def test_admin_portal_is_the_only_registered_admin_page() -> None:
    core_source = _read("chat-ui/src/registry/coreComponents.js")
    # ui/index.js delegates to admin/index.js; AdminPortal registration lives in the admin barrel
    ui_source = _read("factory_app/app/ui/index.js")
    admin_source = _read("factory_app/app/admin/index.js")

    assert "registerComponent('AdminPortal'" not in core_source
    assert "registerAdminComponents" in ui_source
    assert "registerComponent('AdminPortal'" in admin_source
    assert "AppAdminDashboard" not in core_source
    assert "AppAdminDashboard" not in ui_source
    assert "AppAdminDashboard" not in admin_source


def test_admin_portal_embeds_app_admin_panels() -> None:
    source = _read("chat-ui/src/pages/AdminPage.jsx")
    users_source = _read("chat-ui/src/admin/pages/UsersSection.jsx")

    assert "WorkspaceLayout" in source
    assert "AdminOverviewPanel" in source
    assert "AdminSectionRoute" in source
    # Routing now handles all canonical sections, not just users/usage
    assert "^\\/apps\\/[^/]+\\/([^/]+)\\/?$" in source
    assert "raw === 'activity'" not in source
    assert "raw === 'audit'" not in source
    assert "raw === 'logs'" not in source
    assert "AdminSectionRoute" in source
    assert "useLocation" in source
    assert 'title="Usage"' in source
    # page-based routing: sections reference page ids, not section= attributes
    assert "pagePanels" in source
    assert "BillingSection" not in source
    assert "IntegrationsSection" not in source
    assert "SupportSection" not in source
    assert "AdminExtensionPanels" in source
    assert "normalizeRuntimePanels" in source
    assert "BuilderWorkspacePanel" not in source
    assert "AppAdminPanels" in users_source


def test_platform_shell_registers_admin_section_routes() -> None:
    platform_source = _read("mozaiksai/hosts/platform.py")
    registry_source = _read("factory_app/app/admin/admin_registry.yaml")

    # Admin portal pages are now declared in admin_registry.yaml, not hardcoded in contract.py
    for path in ["/apps/:appId/overview", "/apps/:appId/users", "/apps/:appId/usage"]:
        assert path in registry_source

    # The first-party Studio does not expose a standalone /admin page.
    assert "path: /admin" not in registry_source
    assert "surfaces: [studio]" in registry_source
    # /apps/:appId/admin is not a valid path — overview uses the app Studio route.
    assert "/apps/:appId/admin" not in registry_source

    assert '"component": "AdminPortal"' in platform_source
    assert "build_admin_shell_routes" in platform_source
    assert "load_admin_registry" in platform_source
    # Admin portal routes are separate from custom app routes in route_manifest.json
    assert "/apps/:appId/users" in _read("factory_app/app/ui/route_manifest.json")
    assert "/apps/:appId/usage" in _read("factory_app/app/ui/route_manifest.json")
    assert "/apps/:appId/operations" not in _read("factory_app/app/ui/route_manifest.json")
    assert "/apps/:appId/settings" not in _read("factory_app/app/ui/route_manifest.json")


def test_platform_host_mounts_admin_api_routes() -> None:
    import importlib

    platform_host = importlib.import_module("mozaiksai.hosts.platform")
    routes = {route.path for route in platform_host.app.routes}

    assert "/api/admin/config" in routes
    assert "/api/admin/stats" in routes


def test_profile_menu_uses_framework_defaults() -> None:
    source = _read("chat-ui/src/components/layout/Header.js")

    assert "getDefaultProfileMenu" in source
    assert '"profile"' in source
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
    from mozaiksai.hosts import runtime as runtime_app  # noqa: F401 - initializes persistence dependencies before admin imports

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
            "module_id": "crm",
            "source": "module",
        }
    ]

