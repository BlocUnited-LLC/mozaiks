from __future__ import annotations

import asyncio
import json
from pathlib import Path


def _workspace() -> Path:
    return Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (_workspace() / relative_path).read_text(encoding="utf-8")


def test_mozaiks_platform_has_platform_scoped_ai_config() -> None:
    ai_path = _workspace() / "mozaiks-platform" / "app" / "config" / "ai.json"
    assert ai_path.exists()

    data = json.loads(ai_path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert isinstance(data.get("chat"), dict)
    assert data["chat"].get("chat_startup_mode") in {"ask", "workflow"}
    assert isinstance(data.get("workflows"), dict)
    assert "entry_point" in data["workflows"]


def test_shell_config_uses_active_platform_path_first() -> None:
    source = _read("platform_app.py")
    assert 'platform_root = resolve_platform_path()' in source
    assert 'ai_path = platform_root / "config" / "ai.json"' in source
    assert 'app_manifest_path = _resolve_app_manifest_path()' in source
    assert 'platform_root / "app.json"' in source
    assert 'shell_config_path = _resolve_shell_config_path()' in source
    assert 'platform_root / "config" / "shell.json"' in source
    assert "_load_ui_extension_pages" in source
    assert "_load_page_schema_routes" in source
    assert "_load_workflow_entrypoint_pages" in source
    assert 'platform_root / "app.yaml"' not in source
    assert 'platform_root / "config" / "navigation.json"' not in source


def test_mozaiks_platform_app_yaml_is_removed() -> None:
    app_yaml_path = _workspace() / "mozaiks-platform" / "app" / "app.yaml"
    assert not app_yaml_path.exists()


def test_app_loader_discovers_platform_bundle_without_app_yaml() -> None:
    from mozaiksai.core.runtime.app.loader import AppLoader

    platform_root = _workspace() / "mozaiks-platform" / "app"
    result = asyncio.run(AppLoader.load(str(platform_root)))

    assert result.definition.name == "Mozaiks Platform"
    assert "platform_apps" in [module.name for module in result.definition.modules]
    assert "builds" in [module.name for module in result.definition.modules]
    assert "ValueEngine" in [workflow.name for workflow in result.definition.workflows]


def test_mozaiks_platform_ui_extension_owns_dashboard_route() -> None:
    extension_path = _workspace() / "mozaiks-platform" / "ui" / "extension.json"
    data = json.loads(extension_path.read_text(encoding="utf-8"))

    dashboard_route = next(item for item in data["pages"] if item["path"] == "/dashboard")
    assert dashboard_route["component"] == "PlatformDashboard"
    assert dashboard_route["requiresAuth"] is False
    assert "showInHeader" not in dashboard_route


def test_mozaiks_platform_workflow_registry_owns_create_route() -> None:
    registry_path = _workspace() / "mozaiks-platform" / "app" / "workflows" / "extended_orchestration" / "extension_registry.json"
    data = json.loads(registry_path.read_text(encoding="utf-8"))

    create_route = next(item for item in data["entrypoints"] if item["path"] == "/create")
    assert create_route["transition"] == "app_type_selector"
    assert create_route["sequence"] == "build"
    assert create_route["requiresAuth"] is False


def test_mozaiks_platform_navigation_json_is_removed() -> None:
    nav_path = _workspace() / "mozaiks-platform" / "app" / "config" / "navigation.json"
    assert not nav_path.exists()


def test_mozaiks_platform_app_manifest_owns_startup() -> None:
    app_path = _workspace() / "mozaiks-platform" / "app" / "app.json"
    data = json.loads(app_path.read_text(encoding="utf-8"))

    assert data["appName"] == "Mozaiks Platform"
    assert data["startup"]["landing_spot"] == "/dashboard"


def test_mozaiks_platform_shell_config_owns_shell_ui() -> None:
    shell_path = _workspace() / "mozaiks-platform" / "app" / "config" / "shell.json"
    data = json.loads(shell_path.read_text(encoding="utf-8"))

    assert "landing_spot" not in data
    assert data["header"]["logo"]["href"] == "/dashboard"
    assert data["header"]["pages"] == []
    assert "notifications" not in data
    assert "profile" not in data
    assert data["footer"]["visible"] is True


def test_mozaiks_platform_theme_config_keeps_chat_ui_only() -> None:
    theme_path = _workspace() / "mozaiks-platform" / "brand" / "theme_config.json"
    data = json.loads(theme_path.read_text(encoding="utf-8"))
    ui = data["ui"]

    assert "chat" in ui
    assert "header" not in ui
    assert "profile" not in ui
    assert "notifications" not in ui
    assert "footer" not in ui


def test_workflow_manager_prefers_platform_scoped_ai_config() -> None:
    source = _read("mozaiksai/core/workflow/workflow_manager.py")
    assert 'platform_scoped = self.workflows_base_path.resolve().parent / "config" / "ai.json"' in source
    assert "legacy_fallback" in source
