from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_studio_shell_config_injects_studio_routes():
    import studio_app

    shell_config = await studio_app.get_studio_shell_config()
    page_paths = {page.get("path") for page in shell_config.get("pages", [])}
    header_paths = {
        page.get("path")
        for page in (shell_config.get("header") or {}).get("pages", [])
        if isinstance(page, dict)
    }

    assert "/dashboard" in page_paths
    assert "/create" in page_paths
    assert "/admin" in page_paths
    assert "/admin/users" in page_paths
    assert "/admin/billing" in page_paths
    assert "/admin/usage" in page_paths
    assert "/studio" in page_paths
    assert "/studio/build" in page_paths
    assert "/studio" not in header_paths
    assert "/studio/build" not in header_paths

    studio_pages = {page.get("path"): page for page in shell_config.get("pages", [])}
    assert studio_pages["/studio"]["meta"]["requiresRole"] == "admin"
    assert studio_pages["/studio/build"]["meta"]["requiresRole"] == "admin"


def test_mozaiks_app_composes_studio_host():
    import mozaiks_app
    import studio_app

    assert mozaiks_app.app is studio_app.app


def test_runtime_cors_uses_declared_frontend_origins(monkeypatch):
    import runtime_app

    monkeypatch.setenv("FRONTEND_URL", "http://localhost:5173")
    monkeypatch.setenv("REACT_DEV_ORIGIN", "http://localhost:3000")
    monkeypatch.setenv("CORS_ORIGINS", "")
    monkeypatch.setenv("ADDITIONAL_CORS_ORIGINS", "http://localhost:4173")

    assert runtime_app._build_cors_origins() == [
        "http://localhost:5173",
        "http://localhost:3000",
        "http://localhost:4173",
    ]


def test_mozaiks_dashboard_uses_canonical_module_route():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "mozaiks-platform/ui/pages/Dashboard.jsx").read_text(
        encoding="utf-8"
    )

    assert "/api/modules/platform_apps/list_apps" in source
    assert "/api/operations/platform_apps/list_apps" not in source


@pytest.mark.asyncio
async def test_platform_host_loads_modules_through_canonical_loader():
    import platform_app
    from mozaiksai.core.runtime.app.loader import AppLoader

    load_result = await AppLoader.load(str(platform_app.resolve_platform_path()))
    loaded_modules = {module.name: type(module.handler).__name__ for module in load_result.modules}

    assert loaded_modules == {
        "builds": "BuildsOperation",
        "platform_apps": "PlatformAppsOperation",
    }
