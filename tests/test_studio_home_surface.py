from __future__ import annotations

from pathlib import Path

import pytest


def _workspace() -> Path:
    return Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (_workspace() / relative_path).read_text(encoding="utf-8")


def test_studio_app_exposes_local_studio_home_endpoint() -> None:
    source = _read("mozaiksai/hosts/studio.py")
    assert '@app.get("/api/studio/home")' in source
    assert 'build_studio_home_summary(app_root, surface="shell-home", local_only=True)' in source


@pytest.mark.asyncio
async def test_platform_app_exposes_studio_routes_only_on_studio_surface(monkeypatch) -> None:
    from mozaiksai.hosts import platform as platform_app

    monkeypatch.setenv("PLATFORM_PATH", "factory_app/app")
    studio_source = _read("mozaiksai/hosts/studio.py")
    platform_source = _read("mozaiksai/hosts/platform.py")
    studio_shell = await platform_app.build_shell_config(surface="studio")
    platform_shell = await platform_app.build_shell_config(surface="platform")

    studio_pages = {page.get("path"): page for page in studio_shell.get("pages", [])}
    platform_paths = {page.get("path") for page in platform_shell.get("pages", [])}

    assert 'build_shell_config(surface="studio")' in studio_source
    assert "os.getenv" not in studio_source
    assert "STUDIO_SHELL_ROUTES" not in platform_source
    assert "/studio" in studio_pages
    assert studio_pages["/studio"]["component"] == "StudioHomePage"
    assert studio_pages["/studio"]["meta"]["requiresRole"] == "admin"
    assert "/studio" not in platform_paths
    assert '_inject_header_page(result, path="/studio"' not in platform_source


def test_factory_app_ui_barrel_registers_studio_home_page() -> None:
    source = _read("factory_app/app/ui/index.js")
    assert "StudioHomePage" in source
    assert "registerComponent('StudioHomePage'" in source


def test_core_components_do_not_register_studio_home_page() -> None:
    source = _read("chat-ui/src/registry/coreComponents.js")
    assert "StudioHomePage" not in source


def test_app_shell_uses_single_app_ui_registration_barrel() -> None:
    source = _read("web_shell/App.jsx")
    assert "registerStudioComponents" not in source
    assert "@studio/extensions" not in source
    assert "register(componentRegistry.registerComponent.bind(componentRegistry));" in source


def test_studio_home_page_fetches_summary_endpoint() -> None:
    source = _read("factory_app/app/ui/pages/custom/studio/StudioHomePage.jsx")
    assert "/api/studio/home" in source
    assert "Studio Home" in source
    assert "next_step" in source
    assert "AdminWorkspaceLayout" in source
