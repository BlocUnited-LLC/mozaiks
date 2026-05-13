from __future__ import annotations

from pathlib import Path

import yaml


def _workspace() -> Path:
    return Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (_workspace() / relative_path).read_text(encoding="utf-8")


def test_admin_portal_is_the_only_registered_admin_page() -> None:
    core_source = _read("chat-ui/src/registry/coreComponents.js")
    studio_source = _read("factory_app/app/ui/index.js")

    assert "registerComponent('AdminPortal'" not in core_source
    assert "registerComponent('AdminPortal'" in studio_source
    assert "AppAdminDashboard" not in core_source
    assert "AppAdminDashboard" not in studio_source


def test_admin_portal_embeds_app_admin_panels() -> None:
    source = _read("chat-ui/src/pages/AdminPage.jsx")
    users_source = _read("chat-ui/src/admin/pages/UsersSection.jsx")

    assert "AdminWorkspaceLayout" in source
    assert "AdminOverviewPanel" in source
    assert "AdminSectionRoute" in source
    assert "^\\/apps\\/[^/]+\\/(?:users|usage)\\/?$" in source
    assert "raw === 'activity'" not in source
    assert "raw === 'audit'" not in source
    assert "raw === 'logs'" not in source
    assert "AdminSectionRoute" in source
    assert "useLocation" in source
    assert 'title="Usage"' in source
    assert 'section="users"' in source
    assert "BillingSection" not in source
    assert "IntegrationsSection" not in source
    assert "SupportSection" not in source
    assert "AdminExtensionPanels" in source
    assert "normalizeRuntimePanels" in source
    assert "BuilderWorkspacePanel" not in source
    assert "AppAdminPanels" in users_source


def test_platform_shell_registers_admin_section_routes() -> None:
    platform_source = _read("mozaiksai/hosts/platform.py")
    contract_source = _read("mozaiksai/core/admin/contract.py")

    for path in ["/apps/:appId/users", "/apps/:appId/usage"]:
        assert path in contract_source

    for path in ["/apps/:appId/admin", "/apps/:appId/operations", "/apps/:appId/settings"]:
        assert path not in contract_source

    assert '"component": "AdminPortal"' in platform_source
    assert "build_admin_shell_routes" in platform_source
    assert "/apps/:appId/users" in _read("factory_app/app/ui/route_manifest.json")
    assert "/apps/:appId/usage" in _read("factory_app/app/ui/route_manifest.json")
    assert "/apps/:appId/operations" not in _read("factory_app/app/ui/route_manifest.json")
    assert "/apps/:appId/settings" not in _read("factory_app/app/ui/route_manifest.json")
    assert '"payments": "billing"' not in contract_source
    assert '"usage-health": "operations"' not in contract_source


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

    assert "DEFAULT_ADMIN_SHELL_CONFIG" in source
    assert "_load_module_admin_panels" in source
    assert '"runtime_panels"' in source
    assert '"module_panels"' in source
    assert '"sections"' in source
    assert "_infer_admin_panel_section" in source


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
                        "section": "settings",
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
    config = admin_router._merge_module_admin_panels(
        {"enabled": True, "sections": {}, "runtime_panels": [], "module_panels": []},
        app_root,
    )

    panels = config["module_panels"]
    assert panels == [
        {
            "id": "crm.contacts",
            "label": "Contacts",
            "description": None,
            "section": "settings",
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
