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
    assert "BuilderWorkspaceLayout" in source
    assert "<AppAdminPanels embedded />" in source
    assert "BuilderWorkspacePanel" in source
    assert "Builder Workspace" in source
    assert "'/studio/build'" in source
    assert "ModuleAdminPanels" in source
    assert "normalizeModulePanels" in source
    assert "Runtime Operations" in source
    assert "normalizeRuntimePanels" in source


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
    assert "modules" in source


def test_runtime_admin_config_uses_panel_groups() -> None:
    source = _read("mozaiksai/core/admin/router.py")

    assert "DEFAULT_ADMIN_CONFIG" in source
    assert "_load_module_admin_panels" in source
    assert '"runtime"' in source
    assert '"modules"' in source


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
            "renderer": "schema",
            "data_source": "module:crm:list_contacts",
            "module_id": "crm",
            "source": "module",
        }
    ]
