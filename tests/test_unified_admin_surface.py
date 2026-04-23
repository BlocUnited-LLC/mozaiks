from __future__ import annotations

from pathlib import Path

import yaml


def _workspace() -> Path:
    return Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (_workspace() / relative_path).read_text(encoding="utf-8")


def test_admin_portal_is_the_only_registered_admin_page() -> None:
    source = _read("chat-ui/src/registry/coreComponents.js")

    assert "registerComponent('AdminPortal'" in source
    assert "AppAdminDashboard" not in source
    assert "AppAdminDashboard" not in source[source.find("CORE_COMPONENTS") :]


def test_admin_portal_embeds_app_admin_panels() -> None:
    source = _read("chat-ui/src/pages/AdminPage.jsx")

    assert "import { AppAdminPanels }" in source
    assert "AdminWorkspaceLayout" in source
    assert "AdminOverviewPanel" in source
    assert "ADMIN_SECTION_ROUTES" in source
    assert "'/admin/users': 'users'" in source
    assert "'/admin/usage': 'usage'" in source
    assert "AdminSectionRoute" in source
    assert "useLocation" in source
    assert 'title="Usage"' in source
    assert 'section="users"' in source
    assert 'section="billing"' in source
    assert "AdminExtensionPanels" in source
    assert "normalizeExtensionPanels" in source
    assert "normalizeRuntimePanels" in source
    assert "BuilderWorkspacePanel" not in source


def test_platform_shell_registers_admin_section_routes() -> None:
    source = _read("platform_app.py")

    for path in [
        "/admin",
        "/admin/users",
        "/admin/billing",
        "/admin/usage",
        "/admin/activity",
        "/admin/settings",
        "/admin/integrations",
        "/admin/support",
    ]:
        assert f'"path": "{path}"' in source

    assert '"component": "AdminPortal"' in source
    assert "ADMIN_SHELL_ROUTES" in source


def test_profile_menu_uses_framework_defaults() -> None:
    source = _read("chat-ui/src/components/layout/Header.js")

    assert "getDefaultProfileMenu" in source
    assert '"profile"' in source
    assert '"admin-portal"' in source
    assert '"signin"' in source
    assert '"signout"' in source
    assert "mergeProfileMenu" in source


def test_app_admin_dashboard_is_panel_group_not_registered_route() -> None:
    source = _read("chat-ui/src/pages/AppAdminDashboard.jsx")

    assert "export function AppAdminPanels" in source
    assert "normalizeAppAdminPanels" in source
    assert "normalizeAppPanelSection" in source
    assert "section" in source


def test_runtime_admin_config_uses_panel_groups() -> None:
    source = _read("mozaiksai/core/admin/router.py")

    assert "DEFAULT_ADMIN_CONFIG" in source
    assert "_load_module_admin_panels" in source
    assert '"runtime"' in source
    assert '"modules"' in source
    assert "_infer_admin_panel_section" in source


def test_runtime_admin_config_discovers_module_admin_yaml(tmp_path) -> None:
    import importlib
    import runtime_app  # noqa: F401 - initializes persistence dependencies before admin imports

    platform_root = tmp_path / "platform"
    module_root = platform_root / "modules" / "crm"
    module_root.mkdir(parents=True)
    (module_root / "admin.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "mozaiks.admin.v1",
                "panels": [
                    {
                        "id": "crm.contacts",
                        "label": "Contacts",
                        "section": "integrations",
                        "renderer": "schema",
                        "data_source": "module:crm:list_contacts",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    admin_router = importlib.import_module("mozaiksai.core.admin.router")
    config = admin_router._merge_module_admin_panels(
        {"enabled": True, "panels": {"app": [], "modules": [], "runtime": []}},
        platform_root,
    )

    panels = config["panels"]["modules"]
    assert panels == [
        {
            "id": "crm.contacts",
            "label": "Contacts",
            "section": "integrations",
            "renderer": "schema",
            "data_source": "module:crm:list_contacts",
            "module_id": "crm",
            "source": "module",
        }
    ]
