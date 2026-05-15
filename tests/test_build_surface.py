from __future__ import annotations

from pathlib import Path


def _workspace() -> Path:
    return Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (_workspace() / relative_path).read_text(encoding="utf-8")


def test_runtime_exposes_build_summary_helper() -> None:
    source = _read("mozaiksai/core/runtime/app/console_summary.py")
    assert "def build_build_summary" in source
    assert "def build_apps_summary" in source
    assert "def build_build_section" in source
    assert "async def load_build_state_from_db" in source
    assert "async def save_build_state_to_db" in source
    assert "BUILD_STATE_COLLECTION = PlatformCollections.BUILD_STATE" in source
    assert "StudioBuildState" not in source
    assert "initial_compile_workflow" in source
    assert "refinement_support" in source
    assert "app_validation" in source
    assert "build_app_validation_strategy_summary" in source
    assert '"state_file"' not in source


def test_studio_host_exposes_build_endpoint_and_console_routes() -> None:
    studio_source = _read("mozaiksai/hosts/studio.py")
    manifest_source = _read("factory_app/app/ui/route_manifest.json")
    assert '@app.get("/api/studio/apps")' in studio_source
    assert '@app.post("/api/studio/apps")' in studio_source
    assert '@app.get("/api/studio/build")' in studio_source
    assert '@app.put("/api/studio/build")' in studio_source
    assert 'build_shell_config(surface="studio")' in studio_source
    assert '"path": "/apps/new"' in manifest_source
    assert '"path": "/usage"' in manifest_source
    assert '"path": "/health"' in manifest_source
    assert '"path": "/billing"' in manifest_source
    assert '"path": "/hosting"' in manifest_source
    assert '"path": "/operations"' not in manifest_source
    assert '"path": "/settings"' not in manifest_source
    assert '"path": "/apps"' in manifest_source
    assert '"component": "AppsPage"' in manifest_source
    assert '"path": "/apps/:appId/users"' in manifest_source
    assert '"path": "/apps/:appId/usage"' in manifest_source
    assert '"path": "/apps/:appId/health"' in manifest_source
    assert '"path": "/apps/:appId/billing"' in manifest_source
    assert '"path": "/apps/:appId/hosting"' in manifest_source
    assert '"path": "/apps/:appId/build"' not in manifest_source
    assert '"path": "/apps/:appId/deploy"' not in manifest_source
    assert '"path": "/apps/:appId/operations"' not in manifest_source
    assert '"path": "/apps/:appId/settings"' not in manifest_source
    assert '"surfaces": ["studio"]' in manifest_source
    assert '"requiresRole": "admin"' in manifest_source
    assert '"group": "workspace-console"' in manifest_source
    assert '"group": "app-console"' in manifest_source
    assert '"icon": "apps"' in manifest_source
    assert '"icon": "dashboard"' in manifest_source


def test_factory_app_ui_barrel_registers_hosting_pages_and_omits_removed_pages() -> None:
    source = _read("factory_app/app/ui/index.js")
    admin_source = _read("factory_app/app/admin/index.js")
    assert "registerAdminComponents" in source
    assert "../admin/index.js" in source
    assert "registerAdminComponents(registerComponent)" in source
    assert "registerComponent('AppsPage'" not in source
    assert "registerComponent('WorkspaceHealthPage'" not in source
    assert "registerComponent('AppHostingPage'" not in source
    assert "registerComponent('AppsPage'" in admin_source
    assert "registerComponent('WorkspaceHealthPage'" in admin_source
    assert "registerComponent('WorkspaceHostingPage'" in admin_source
    assert "registerComponent('AppHealthPage'" in admin_source
    assert "registerComponent('AppHostingPage'" in admin_source
    assert "./pages/AppHealthPage.jsx" in admin_source
    assert "./pages/AppHostingPage.jsx" in admin_source
    assert "AppBuildPage" not in source
    assert "AppDeployPage" not in source
    assert "AppOperationsPage" not in source
    assert "AppSettingsPage" not in source
    assert "WorkspaceOperationsPage" not in source
    assert "WorkspaceSettingsPage" not in source


def test_core_components_do_not_register_build_page() -> None:
    source = _read("chat-ui/src/registry/coreComponents.js")
    assert "AppBuildPage" not in source


def test_removed_console_pages_are_deleted_from_factory_app() -> None:
    workspace = _workspace()

    # Old stale pages must not exist anywhere
    for relative_path in (
        "factory_app/app/ui/pages/custom/console/AppBuildPage.jsx",
        "factory_app/app/ui/pages/custom/console/AppDeployPage.jsx",
        "factory_app/app/ui/pages/custom/console/AppOperationsPage.jsx",
        "factory_app/app/ui/pages/custom/console/AppSettingsPage.jsx",
        "factory_app/app/ui/pages/custom/console/WorkspaceOperationsPage.jsx",
        "factory_app/app/ui/pages/custom/console/WorkspaceSettingsPage.jsx",
    ):
        assert not (workspace / relative_path).exists()

    # Entire old console directory has been replaced by admin/pages/
    assert not (workspace / "factory_app/app/ui/pages/custom/console").exists()

    # Active pages live under admin/pages/
    assert (workspace / "factory_app/app/admin/pages/AppHostingPage.jsx").exists()
    assert (workspace / "factory_app/app/admin/pages/AppHealthPage.jsx").exists()
    assert (workspace / "factory_app/app/admin/pages/WorkspaceHostingPage.jsx").exists()
    assert (workspace / "factory_app/app/admin/pages/WorkspaceHealthPage.jsx").exists()


def test_refinement_ui_moves_into_factory_app() -> None:
    workspace = _workspace()
    assert not (workspace / "chat-ui/src/components/chat/RefinementControls.jsx").exists()
    assert (workspace / "factory_app/app/admin/pages/RefinementControls.jsx").exists()


def test_factory_app_owns_refinement_trigger_payload_helper() -> None:
    source = _read("factory_app/app/admin/pages/refinement.js")
    assert "buildRefinementTriggerPayload" in source
    assert "getRefinementRequestPlaceholder" in source
    assert "REFINEMENT_CHANGE_CLASSES" in source


def test_factory_app_refinement_controls_are_live_and_controlled() -> None:
    source = _read("factory_app/app/admin/pages/RefinementControls.jsx")
    assert "modes = REFINEMENT_CHANGE_CLASSES" in source
    assert "selectedClass" in source
    assert "onSelectClass" in source
    assert "showRequestInput" in source
    assert "Apply refinement" in source
    assert "ActionButton" in source
    assert "StatusPill" in source


def test_app_overview_does_not_link_to_removed_routes() -> None:
    source = _read("factory_app/app/admin/pages/AppOverviewPage.jsx")
    assert 'to={`/apps/${appId}/build`}' not in source
    assert 'to={`/apps/${appId}/deploy`}' not in source
    assert 'to={`/apps/${appId}/operations`}' not in source
    assert 'to={`/apps/${appId}/settings`}' not in source


def test_apps_page_fetches_workspace_apps_endpoint() -> None:
    source = _read("factory_app/app/admin/pages/AppsPage.jsx")
    hook_source = _read("factory_app/app/admin/pages/useWorkspaceApps.js")
    layout_source = _read("chat-ui/src/workspace/WorkspaceLayout.jsx")
    assert "/api/studio/apps" in hook_source
    assert "Mozaiks Console" in layout_source
    assert "Import App" in source
    assert "/apps/new" in source
    assert "row.primaryAction?.href" in source


def test_workspace_layout_links_console_and_hosting_sections() -> None:
    source = _read("chat-ui/src/workspace/WorkspaceLayout.jsx")
    manifest_source = _read("factory_app/app/ui/route_manifest.json")
    assert "Admin Dashboard" not in source
    assert "Mozaiks Console" in source
    assert "Developer" not in source
    assert "Studio Navigation" not in source
    assert "Browse sections" not in source
    assert "Mozaiks Console" in source
    assert "App Console" in source
    assert '"label": "Users"' in manifest_source
    assert '"label": "Billing"' in manifest_source
    assert '"label": "Health"' in manifest_source
    assert '"label": "Hosting"' in manifest_source
    assert '"label": "Usage"' in manifest_source
    assert '"label": "Integrations"' in manifest_source
    # Nav is route-manifest-owned and derived from registered shell pages.
    assert "useNavigation" in source
    assert "buildNavGroupsFromPages" in source
    assert "ICON_MAP" in source
    assert "adminSections" not in source
    assert "APP_NAV_ITEMS" not in source
    assert "WORKSPACE_NAV_ITEMS" not in source
    assert "buildAppNavItems" not in source
    assert "resolvePageNavigation(page).group" in source
    assert "buildPathForPage(page.path, appId)" in source
    # Workspace nav does not include removed app-level sections.
    assert '"path": "/operations"' not in manifest_source
    assert '"path": "/settings"' not in manifest_source
    assert '"path": "/apps/:appId/build"' not in manifest_source
    assert '"path": "/apps/:appId/deploy"' not in manifest_source
    assert '"path": "/apps/:appId/admin"' not in manifest_source
    assert '"path": "/apps/:appId/operations"' not in manifest_source
    assert '"path": "/apps/:appId/settings"' not in manifest_source
    assert "WorkspaceLayout" in source
    assert "Open console navigation" in source
    assert "Console navigation" in source
    assert "lg:hidden" in source
    assert "lg:block" in source
    assert "description:" not in source


def test_integrations_page_uses_integrations_eyebrow() -> None:
    source = _read("factory_app/app/admin/pages/AppIntegrationsPage.jsx")
    assert "Integrations" in source
    assert 'eyebrow="Studio"' not in source


def test_integrations_page_focuses_on_external_integrations() -> None:
    source = _read("factory_app/app/admin/pages/AppIntegrationsPage.jsx")
    assert 'External Integrations' in source
    assert 'Add Integration' in source
    assert 'Connector Secret Backend' not in source
    assert 'Runtime Adapters' not in source
    assert 'Connection State' not in source
