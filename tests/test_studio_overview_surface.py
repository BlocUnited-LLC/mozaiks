from __future__ import annotations

import json
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
    assert "/integrations" in console_pages
    assert "/health" not in console_pages
    assert "/billing" not in console_pages
    assert "/hosting" not in console_pages
    assert "/operations" not in console_pages
    assert "/settings" not in console_pages
    assert "/apps/:appId/overview" in console_pages
    assert "/apps/:appId/health" in console_pages
    assert "/apps/:appId/support" in console_pages
    assert "/apps/:appId/billing" not in console_pages
    assert "/apps/:appId/hosting" not in console_pages
    assert "/apps/:appId/build" not in console_pages
    assert "/apps/:appId/deploy" not in console_pages
    assert "/apps/:appId/admin" not in console_pages
    # admin_registry.yaml declares operations and settings as app-scope admin pages
    assert "/apps/:appId/operations" in console_pages
    assert "/apps/:appId/settings" in console_pages
    assert console_pages["/integrations"]["component"] == "WorkspaceIntegrationsPage"
    assert console_pages["/apps/:appId/overview"]["component"] == "AppOverviewPage"
    assert console_pages["/apps/:appId/health"]["component"] == "AppHealthPage"
    assert console_pages["/apps/:appId/support"]["component"] == "AppSupportPage"
    assert console_pages["/apps/:appId/overview"]["meta"]["requiresRole"] == "admin"
    assert "/apps/:appId/overview" not in platform_paths
    assert "/apps/:appId/health" not in platform_paths
    assert '_inject_header_page(result, path="/apps/:appId/overview"' not in platform_source


def test_factory_app_ui_barrel_registers_app_overview_page() -> None:
    # ui/index.js delegates to admin/index.js; component registration lives in the admin barrel
    ui_source = _read("factory_app/app/ui/index.js")
    assert "registerAdminComponents" in ui_source
    admin_source = _read("factory_app/app/admin/index.js")
    assert "AppOverviewPage" in admin_source
    assert "registerComponent('AppOverviewPage'" in admin_source


def test_core_components_do_not_register_app_overview_page() -> None:
    source = _read("chat-ui/src/registry/coreComponents.js")
    assert "AppOverviewPage" not in source


def test_app_shell_uses_single_app_ui_registration_barrel() -> None:
    source = _read("web_shell/App.jsx")
    assert "registerStudioComponents" not in source
    assert "@studio/extensions" not in source
    assert "register(componentRegistry.registerComponent.bind(componentRegistry));" in source


def test_app_overview_page_fetches_summary_endpoint() -> None:
    source = _read("factory_app/app/admin/pages/AppOverviewPage.jsx")
    chrome_source = _read("factory_app/app/admin/pages/AppStudioChrome.jsx")
    hook_source = _read("factory_app/app/admin/pages/useAppStudioData.js")
    assert "/api/studio/overview" in hook_source
    assert "App Overview" in source
    assert "buildDashboardMetrics" in source
    assert "BusinessSnapshotPanel" not in source
    assert "OperationalHealthPanel" not in source
    assert "ConnectedServicesPanel" not in source
    assert "useAppIntegrationDeclarations" not in source
    assert "getAppJourneyLabel" not in source
    assert "getLifecycleGuidance" in source
    assert "WorkspaceLayout" in source
    assert "nextStep={nextStep}" in source
    assert "nextStepAction={primaryAction}" in source
    assert "SurfaceCard accent" not in source
    assert "AppIdentityMark" in chrome_source
    assert "getAppLogoSrc" in chrome_source
    assert "getAppDescription" in chrome_source
    assert "AppNextStep" in chrome_source
    assert "AppDashboardBanner" in chrome_source
    assert "App description will appear after the concept brief is captured." in chrome_source
    assert "showBanner" in source


def test_app_support_page_is_registered() -> None:
    admin_source = _read("factory_app/app/admin/index.js")
    manifest_source = _read("factory_app/app/ui/route_manifest.json")
    support_source = _read("factory_app/app/admin/pages/AppSupportPage.jsx")

    assert "AppSupportPage" in admin_source
    assert "registerComponent('AppSupportPage'" in admin_source
    assert '"/apps/:appId/support"' in manifest_source
    assert "Help desk" in support_source
    assert "Run review" in support_source


def test_app_overview_summary_reads_app_identity_metadata(tmp_path: Path) -> None:
    from mozaiksai.core.runtime.app.studio_summary import build_app_overview_summary

    (tmp_path / "config").mkdir()
    (tmp_path / "brand").mkdir()
    (tmp_path / "ui").mkdir()
    (tmp_path / "app.json").write_text(
        json.dumps(
            {
                "appId": "ops-portal",
                "appName": "Ops Portal",
                "description": "Coordinates customer follow-up, account review, and team handoffs.",
                "tagline": "Customer operations in one place",
                "value_proposition": "A focused workspace for teams to act on customer signals faster.",
                "startup": {"landing_spot": "/dashboard"},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "config" / "ai.json").write_text(
        json.dumps({"llm": {"provider": "openai", "model": "gpt-4o-mini"}}),
        encoding="utf-8",
    )
    (tmp_path / "config" / "shell.json").write_text(
        json.dumps({"header": {"logo": {"alt": "Ops Portal"}}}),
        encoding="utf-8",
    )
    (tmp_path / "brand" / "theme_config.json").write_text(
        json.dumps({"identity": {"tagline": "Theme tagline"}, "theme": {}}),
        encoding="utf-8",
    )
    (tmp_path / "ui" / "route_manifest.json").write_text(json.dumps({"pages": []}), encoding="utf-8")

    summary = build_app_overview_summary(tmp_path)

    assert summary["app"]["name"] == "Ops Portal"
    assert summary["app"]["description"] == "Coordinates customer follow-up, account review, and team handoffs."
    assert summary["app"]["tagline"] == "Customer operations in one place"
    assert summary["app"]["value_proposition"] == "A focused workspace for teams to act on customer signals faster."

