from __future__ import annotations

from pathlib import Path

import pytest


def _workspace() -> Path:
    return Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (_workspace() / relative_path).read_text(encoding="utf-8")


def test_studio_host_exposes_local_app_overview_endpoint() -> None:
    source = _read("mozaiksai/hosts/studio.py")
    assert '@app.get("/api/studio/overview")' in source
    assert 'build_app_overview_summary(' in source
    assert 'app_record=record' in source


@pytest.mark.asyncio
async def test_platform_app_exposes_console_routes_only_on_studio_surface(monkeypatch) -> None:
    from mozaiksai.hosts import platform as platform_app

    monkeypatch.setenv("PLATFORM_PATH", "factory_app/app")
    studio_source = _read("mozaiksai/hosts/studio.py")
    platform_source = _read("mozaiksai/hosts/platform.py")
    studio_shell = await platform_app.build_shell_config(surface="studio")
    platform_shell = await platform_app.build_shell_config(surface="platform")

    console_pages = {page.get("path"): page for page in studio_shell.get("pages", [])}
    platform_paths = {page.get("path") for page in platform_shell.get("pages", [])}

    assert 'build_shell_config(surface="studio")' in studio_source
    assert "os.getenv" not in studio_source
    assert "STUDIO_SHELL_ROUTES" not in platform_source
    assert "/usage" in console_pages
    assert "/operations" in console_pages
    assert "/billing" in console_pages
    assert "/settings" in console_pages
    assert "/apps/:appId/overview" in console_pages
    assert console_pages["/apps/:appId/overview"]["component"] == "AppOverviewPage"
    assert console_pages["/apps/:appId/overview"]["meta"]["requiresRole"] == "admin"
    assert "/apps/:appId/overview" not in platform_paths
    assert '_inject_header_page(result, path="/apps/:appId/overview"' not in platform_source


def test_factory_app_ui_barrel_registers_app_overview_page() -> None:
    source = _read("factory_app/app/ui/index.js")
    assert "AppOverviewPage" in source
    assert "registerComponent('AppOverviewPage'" in source


def test_core_components_do_not_register_app_overview_page() -> None:
    source = _read("chat-ui/src/registry/coreComponents.js")
    assert "AppOverviewPage" not in source


def test_app_shell_uses_single_app_ui_registration_barrel() -> None:
    source = _read("web_shell/App.jsx")
    assert "registerStudioComponents" not in source
    assert "@studio/extensions" not in source
    assert "register(componentRegistry.registerComponent.bind(componentRegistry));" in source


def test_app_overview_page_fetches_summary_endpoint() -> None:
    source = _read("factory_app/app/ui/pages/custom/console/AppOverviewPage.jsx")
    assert "/api/studio/overview" in source
    assert "App Overview" in source
    assert "next_step" in source
    assert "AdminWorkspaceLayout" in source
