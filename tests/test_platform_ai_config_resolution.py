from __future__ import annotations

import asyncio
import json
from pathlib import Path

from tests.import_utils import active_app_root


def _workspace() -> Path:
    return Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (_workspace() / relative_path).read_text(encoding="utf-8")


def test_mozaiks_platform_has_platform_scoped_ai_config() -> None:
    app_root = active_app_root()
    ai_path = app_root / "config" / "ai.json"
    assert ai_path.exists()

    data = json.loads(ai_path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert isinstance(data.get("chat"), dict)
    assert data["chat"].get("chat_startup_mode") in {"ask", "workflow"}
    assert isinstance(data.get("workflows"), dict)
    assert "entry_point" in data["workflows"]


def test_shell_config_uses_active_platform_path_first() -> None:
    source = _read("mozaiksai/hosts/platform.py")
    assert 'def resolve_app_root() -> Path:' in source
    assert 'def resolve_platform_path() -> Path:' in source
    assert 'app_root = resolve_app_root()' in source
    assert 'ai_path = app_root / "config" / "ai.json"' in source
    assert 'Path(__file__).parent / "platform" / "config" / "ai.json"' not in source
    assert 'app_manifest_path = _resolve_app_manifest_path()' in source
    assert 'app_root / "app.json"' in source
    assert 'shell_config_path = _resolve_shell_config_path()' in source
    assert 'app_root / "config" / "shell.json"' in source
    assert "_load_ui_route_manifest_pages" in source
    assert "_load_page_schema_routes" in source
    assert "_load_workflow_entrypoint_pages" in source
    assert 'app_root / "app.yaml"' not in source
    assert 'app_root / "config" / "navigation.json"' not in source


def test_mozaiks_platform_app_yaml_is_removed() -> None:
    app_root = active_app_root()
    assert not (app_root / "app.yaml").exists()


def test_app_loader_discovers_platform_bundle_without_app_yaml() -> None:
    from mozaiksai.core.runtime.app.loader import AppLoader

    app_root = active_app_root()
    # Skip if running against the factory app workspace (not a product workspace)
    if not (app_root / "modules" / "investor_marketplace").is_dir():
        pytest.skip("Product workspace not configured — investor_marketplace module missing")
    result = asyncio.run(AppLoader.load(str(app_root)))

    assert result.definition.name == "Mozaiks Platform"
    assert "investor_marketplace" in [module.name for module in result.definition.modules]
    assert "communications" in [module.name for module in result.definition.modules]
    workflow_names = [workflow.name for workflow in result.definition.workflows]
    assert "CreateLauncher" not in workflow_names
    assert "AppMarketing" in workflow_names
    assert "InvestorMarketplace" in workflow_names
    assert "ValueEngine" not in workflow_names


def test_mozaiks_platform_route_manifest_owns_dashboard_route() -> None:
    app_root = active_app_root()
    manifest_path = app_root / "ui" / "route_manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))

    dashboard_route = next(item for item in data["pages"] if item["path"] == "/dashboard")
    assert dashboard_route["component"] == "PlatformDashboard"
    assert dashboard_route["requiresAuth"] is False
    assert "showInHeader" not in dashboard_route


def test_mozaiks_platform_workflow_registry_owns_create_route() -> None:
    registry_path = _workspace() / "factory_app" / "workflows" / "extended_orchestration" / "extension_registry.json"
    data = json.loads(registry_path.read_text(encoding="utf-8"))

    create_route = next(item for item in data["entrypoints"] if item["path"] == "/create")
    assert create_route["transition"] == "app_type_selector"
    assert create_route["sequence"] == "build"
    assert create_route["requiresAuth"] is False


def test_mozaiks_platform_workflow_registry_uses_shared_base_and_local_overlay() -> None:
    app_root = active_app_root()
    shared_registry_path = _workspace() / "factory_app" / "workflows" / "extended_orchestration" / "extension_registry.json"
    overlay_registry_path = app_root / "workflows" / "extended_orchestration" / "extension_registry.json"
    shared = json.loads(shared_registry_path.read_text(encoding="utf-8"))
    overlay = json.loads(overlay_registry_path.read_text(encoding="utf-8"))

    shared_workflow_ids = {item["id"] for item in shared["workflows"]}
    overlay_workflow_ids = {item["id"] for item in overlay["workflows"]}

    assert {"ValueEngine", "ThemeCapture", "DesignDocs", "AgentGenerator", "AppGenerator"}.issubset(shared_workflow_ids)
    assert {"AppMarketing", "InvestorMarketplace"}.issubset(overlay_workflow_ids)
    assert "CreateLauncher" not in overlay_workflow_ids
    assert "entrypoints" not in overlay
    assert "workflow_sequences" not in overlay
    assert "transitions" not in overlay


def test_mozaiks_platform_navigation_json_is_removed() -> None:
    app_root = active_app_root()
    assert not (app_root / "config" / "navigation.json").exists()


def test_mozaiks_platform_app_manifest_owns_startup() -> None:
    app_root = active_app_root()
    data = json.loads((app_root / "app.json").read_text(encoding="utf-8"))

    assert data["appName"] == "Mozaiks Platform"
    assert data["startup"]["landing_spot"] == "/marketplace"


def test_mozaiks_platform_shell_config_owns_shell_ui() -> None:
    app_root = active_app_root()
    data = json.loads((app_root / "config" / "shell.json").read_text(encoding="utf-8"))

    header_actions = {item["id"]: item for item in data["header"]["actions"]}
    assert "landing_spot" not in data
    assert data["header"]["logo"]["href"] == "/marketplace"
    assert data["header"]["pages"] == []
    assert "notifications" not in data
    assert "profile" not in data
    assert data["footer"]["visible"] is True
    assert header_actions["create-app"]["path"] == "/create"


def test_mozaiks_platform_theme_config_keeps_chat_ui_only() -> None:
    app_root = active_app_root()
    data = json.loads((app_root / "brand" / "theme_config.json").read_text(encoding="utf-8"))
    ui = data["ui"]

    assert "chat" in ui
    assert "header" not in ui
    assert "profile" not in ui
    assert "notifications" not in ui
    assert "footer" not in ui


def test_workflow_manager_prefers_platform_scoped_ai_config() -> None:
    source = _read("mozaiksai/core/workflow/workflow_manager.py")
    assert 'platform_scoped = self.workflows_base_path.resolve().parent / "config" / "ai.json"' in source
    assert 'return (resolve_active_app_root() / "config" / "ai.json").resolve()' in source
