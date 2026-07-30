from __future__ import annotations

from pathlib import Path


def _workspace() -> Path:
    return Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (_workspace() / relative_path).read_text(encoding="utf-8")


def test_runtime_exposes_build_summary_helper() -> None:
    source = _read("mozaiksai/core/runtime/app/studio_summary.py")
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
    assert '@app.put("/api/studio/apps/{build_registry_id}/status")' in studio_source
    assert '@app.get("/api/studio/build")' in studio_source
    assert '@app.put("/api/studio/build")' in studio_source
    assert 'build_shell_config(surface="studio")' in studio_source
    assert '"path": "/apps/new"' in manifest_source
    assert '"path": "/usage"' in manifest_source
    assert '"path": "/integrations"' in manifest_source
    assert '"path": "/billing"' not in manifest_source
    assert '"path": "/hosting"' not in manifest_source
    assert '"path": "/operations"' not in manifest_source
    assert '"path": "/settings"' not in manifest_source
    assert '"path": "/apps"' in manifest_source
    assert '"component": "AppsPage"' in manifest_source
    assert '"path": "/apps/:appId/access"' in manifest_source
    assert '"component": "AppAccessPage"' in manifest_source
    assert '"path": "/apps/:appId/building"' in manifest_source
    assert '"component": "DashboardPortalPage"' in manifest_source
    assert '"path": "/apps/:appId/users"' not in manifest_source
    assert '"path": "/apps/:appId/usage"' in manifest_source
    assert '"path": "/apps/:appId/health"' in manifest_source
    assert '"path": "/apps/:appId/activity"' in manifest_source
    assert '"component": "AppBuildHistoryPage"' in manifest_source
    assert '"path": "/apps/:appId/billing"' not in manifest_source
    assert '"path": "/apps/:appId/hosting"' not in manifest_source
    assert '"path": "/apps/:appId/build"' not in manifest_source
    assert '"path": "/apps/:appId/deploy"' not in manifest_source
    assert '"path": "/apps/:appId/operations"' not in manifest_source
    assert '"path": "/apps/:appId/settings"' not in manifest_source
    assert '"surfaces": ["studio"]' in manifest_source
    assert '"requiresRole": "admin"' in manifest_source
    assert '"group": "workspace-studio"' in manifest_source
    assert '"group": "app-studio"' in manifest_source
    assert '"icon": "apps"' in manifest_source
    assert '"icon": "dashboard"' in manifest_source


def test_factory_app_ui_barrel_registers_admin_pages_and_omits_removed_pages() -> None:
    source = _read("factory_app/app/ui/index.js")
    admin_source = _read("factory_app/app/admin/index.js")
    assert "registerAdminComponents" in source
    assert "../admin/index.js" in source
    assert "registerAdminComponents(registerComponent)" in source
    assert "registerComponent('AppsPage'" not in source
    assert "registerComponent('WorkspaceHealthPage'" not in source
    assert "registerComponent('AppHostingPage'" not in source
    assert "registerComponent('WorkspaceHostingPage'" not in source
    assert "registerComponent('AppsPage'" in admin_source
    assert "registerComponent('WorkspaceIntegrationsPage'" in admin_source
    assert "registerComponent('DashboardPortalPage'" in admin_source
    assert "registerComponent('WorkspaceHostingPage'" not in admin_source
    assert "registerComponent('AppHealthPage'" in admin_source
    assert "registerComponent('AppAccessPage'" in admin_source
    assert "registerComponent('ProfilePage', ProfilePage, {" in admin_source
    assert "User profile surface" in admin_source
    assert "override: true" in admin_source
    assert "registerComponent('AppHostingPage'" not in admin_source
    assert "./pages/AppHealthPage.jsx" in admin_source
    assert "./pages/AppAccessPage.jsx" in admin_source
    assert "./pages/DashboardPortalPage.jsx" in admin_source
    assert "./pages/AppHostingPage.jsx" not in admin_source
    assert "AppBuildPage" not in source
    assert "AppDeployPage" not in source
    assert "AppOperationsPage" not in source
    assert "AppSettingsPage" not in source
    assert "WorkspaceOperationsPage" not in source
    assert "WorkspaceSettingsPage" not in source


def test_core_components_do_not_register_build_page() -> None:
    source = _read("chat-ui/src/registry/coreComponents.js")
    assert "AppBuildPage" not in source


def test_removed_custom_studio_pages_are_deleted_from_factory_app() -> None:
    workspace = _workspace()

    # Studio management pages live under admin/pages/, not custom app UI routes.
    for relative_path in (
        "factory_app/app/ui/pages/custom/studio/AppBuildPage.jsx",
        "factory_app/app/ui/pages/custom/studio/AppDeployPage.jsx",
        "factory_app/app/ui/pages/custom/studio/AppOperationsPage.jsx",
        "factory_app/app/ui/pages/custom/studio/AppSettingsPage.jsx",
        "factory_app/app/ui/pages/custom/studio/WorkspaceOperationsPage.jsx",
        "factory_app/app/ui/pages/custom/studio/WorkspaceSettingsPage.jsx",
    ):
        assert not (workspace / relative_path).exists()

    # The custom app UI route tree must not own Studio management pages.
    assert not (workspace / "factory_app/app/ui/pages/custom/studio").exists()

    # Active pages live under admin/pages/
    assert (workspace / "factory_app/app/admin/pages/AppHealthPage.jsx").exists()
    assert (workspace / "factory_app/app/admin/pages/WorkspaceIntegrationsPage.jsx").exists()
    # Hosting pages moved to mozaiks-app (hosted-only capability)
    assert not (workspace / "factory_app/app/admin/pages/AppHostingPage.jsx").exists()
    assert not (workspace / "factory_app/app/admin/pages/WorkspaceHostingPage.jsx").exists()


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


def test_dashboard_portal_page_renders_manifest_build_panels() -> None:
    source = _read("factory_app/app/admin/pages/DashboardPortalPage.jsx")
    manifest_source = _read("factory_app/app/ui/route_manifest.json")

    assert "fetchDashboardConfig({ scope, appId" in source
    assert "routePatternMatches(item.route, pathname)" in source
    assert "case 'build_threads':" in source
    assert "case 'artifact_timeline':" in source
    assert "case 'approval_queue':" in source
    assert "case 'workflow_launcher':" in source
    assert "GenericPanelFallback" in source
    assert '"/apps/:appId/building"' in manifest_source
    assert '"component": "DashboardPortalPage"' in manifest_source


def test_apps_page_fetches_workspace_apps_endpoint() -> None:
    source = _read("factory_app/app/admin/pages/AppsPage.jsx")
    studio_page_source = _read("factory_app/app/admin/pages/StudioPage.jsx")
    model_source = _read("factory_app/app/admin/pages/workspaceStudioModel.js")
    hook_source = _read("factory_app/app/admin/pages/useWorkspaceApps.js")
    create_hook_source = _read("factory_app/workflows/ValueEngine/tools/create_app_record.py")
    update_hook_source = _read("factory_app/workflows/AppGenerator/tools/update_app_record.py")
    layout_source = _read("chat-ui/src/workspace/WorkspaceLayout.jsx")
    assert "/api/studio/apps" in hook_source
    assert "/api/studio/dashboard" in _read("factory_app/app/admin/pages/dashboardRoutes.js")
    assert "getDefaultPortalRoute(dashboardConfig, 'app')" in source
    assert "buildWorkspacePortfolio(apps, { appDashboardRoute })" in source
    assert "buildAppDashboardHref(options.appDashboardRoute, appId)" in model_source
    assert "/apps/${encodeURIComponent(appId)}/overview" not in source
    assert "/apps/${encodeURIComponent(appId)}/overview" not in model_source
    assert "manifest-declared default App Dashboard portal" in studio_page_source
    assert "getDefaultPortalRoute(payload, 'app')" in studio_page_source
    assert "location.pathname}/overview" not in studio_page_source
    assert "/api/studio/apps" in create_hook_source
    assert "_provisional_build_app_id" in create_hook_source
    assert "No persisted user build intent" not in create_hook_source
    assert "/api/modules/app_registry" not in create_hook_source
    assert "/api/studio/apps/{record_id}/status" in update_hook_source
    assert "/api/modules/app_registry" not in update_hook_source
    assert "Mozaiks Studio" in layout_source
    assert "Import App" in source
    assert "const CREATE_APP_PATH = '/create'" in source
    assert "/chat?workflow=ValueEngine&mode=workflow&defer_start=1" not in source
    assert "/chat?workflow=ValueEngine&mode=workflow&new=1" not in source
    assert "row.primaryAction?.href" in source
    assert "active_chat_id" in _read("chat-ui/src/admin/appStudioModel.js")
    assert "active_chat_id" in _read("mozaiksai/core/runtime/app/studio_summary.py")


def test_chat_page_defers_new_app_workflow_until_first_user_message() -> None:
    source = _read("chat-ui/src/pages/ChatPage.js")
    assert "queryDeferStart" in source
    assert "searchParams.get('defer_start')" in source
    assert "if (queryDeferStart)" in source
    assert "Deferred workflow launch" in source
    assert "nextParams.delete('defer_start')" in source


def test_building_app_list_entry_routes_to_active_chat() -> None:
    source = _read("mozaiksai/core/runtime/app/studio_summary.py")
    assert '"chat_app_id": chat_app_id or None' in source
    assert '"app_id": chat_scope' in source
    assert 'f"/chat?{urlencode(resume_query)}"' in source
    assert '"active_chat_id": active_chat_id or None' in source
    assert '"active_workflow_id": active_workflow_id if active_chat_id else None' in source


def test_workspace_layout_links_studio_and_hosting_sections() -> None:
    source = _read("chat-ui/src/workspace/WorkspaceLayout.jsx")
    manifest_source = _read("factory_app/app/ui/route_manifest.json")
    assert "Admin Dashboard" not in source
    assert "Mozaiks Studio" in source
    assert "Developer" not in source
    assert "Studio Navigation" not in source
    assert "Browse sections" not in source
    assert "Mozaiks Studio" in source
    assert "App Studio" in source
    assert '"label": "Access"' in manifest_source
    assert '"label": "Billing"' not in manifest_source
    assert '"label": "Health"' in manifest_source
    assert '"label": "Hosting"' not in manifest_source
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
    assert "Open Studio navigation" in source
    assert "Studio navigation" in source
    assert "lg:hidden" in source
    assert "lg:block" in source
    assert "description:" not in source


def test_admin_studio_pages_use_workspace_layout_not_page_frame() -> None:
    """Every file in factory_app/app/admin/pages/ is a workspace/app Studio surface.
    It must import WorkspaceLayout (or AppStudioLayout) and must NOT use PageFrame
    as the root layout shell, or the sidebar will be missing at runtime."""
    admin_pages_dir = _workspace() / "factory_app" / "app" / "admin" / "pages"
    violations = []
    for jsx_file in sorted(admin_pages_dir.glob("*.jsx")):
        source = jsx_file.read_text(encoding="utf-8")
        has_workspace_layout = "WorkspaceLayout" in source or "AppStudioLayout" in source
        has_page_frame_as_root = (
            "PageFrame" in source and not has_workspace_layout
        )
        if has_page_frame_as_root:
            violations.append(jsx_file.name)
    assert not violations, (
        f"Admin portal pages use PageFrame instead of WorkspaceLayout — "
        f"sidebar will be missing at runtime: {violations}. "
        "Import WorkspaceLayout from @mozaiks/chat-ui/workspace instead."
    )


def test_route_manifest_components_all_registered_in_admin_index() -> None:
    """Every component named in factory_app/app/ui/route_manifest.json must have
    a registerComponent() call in factory_app/app/admin/index.js, or the shell
    will log 'Component Not Registered' and render nothing."""
    import json
    manifest = json.loads(_read("factory_app/app/ui/route_manifest.json"))
    admin_index = _read("factory_app/app/admin/index.js")

    unregistered = []
    for page in manifest["pages"]:
        component = page.get("component")
        if not component or component == "AdminPortal":
            continue
        if f"registerComponent('{component}'" not in admin_index:
            unregistered.append(component)

    assert not unregistered, (
        f"Components in route_manifest.json not registered in admin/index.js: {unregistered}. "
        "Add a registerComponent() call for each or the shell will fail to render the route."
    )


def test_integrations_routes_have_global_management_and_app_detail() -> None:
    manifest_source = _read("factory_app/app/ui/route_manifest.json")
    admin_registry = _read("factory_app/app/admin/admin_registry.yaml")
    playwright_source = _read("web_shell/playwright/apps.responsive.smoke.spec.js")

    assert '"/integrations"' in manifest_source
    assert '"/apps/:appId/integrations"' in manifest_source
    assert '"component": "WorkspaceIntegrationsPage"' in manifest_source
    assert '"include": false' in manifest_source
    assert "pages: []" in admin_registry
    assert "path: /integrations" not in admin_registry
    assert "path: /apps/:appId/integrations" not in admin_registry
    assert "show_in_navigation: false" not in admin_registry
    assert "page.goto(`/apps/${APP_ID}/integrations`)" in playwright_source
    assert "page.goto('/integrations')" in playwright_source


def test_app_integrations_page_is_setup_detail_not_crud_inventory() -> None:
    source = _read("factory_app/app/admin/pages/AppIntegrationsPage.jsx")
    assert "App Integrations" in source
    assert "list_app_integration_needs" in source
    assert "Workspace integrations" in source
    assert "Services this app needs" in source
    assert "Required" in source
    assert "Optional" in source
    assert "App-specific" in source
    assert "Add Integration" not in source
    assert "/api/studio/integrations/connectors/" not in source
    assert "checkConnectorHealth" not in source
    assert 'eyebrow="Studio"' not in source


def test_app_overview_links_out_instead_of_owning_diagnostic_panels() -> None:
    source = _read("factory_app/app/admin/pages/AppOverviewPage.jsx")
    manifest_source = _read("factory_app/app/ui/route_manifest.json")
    assert "BusinessSnapshotPanel" not in source
    assert "OperationalHealthPanel" not in source
    assert "ConnectedServicesPanel" not in source
    assert "list_app_integration_needs" not in source
    assert '"/apps/:appId/health"' in manifest_source
    assert '"/apps/:appId/support"' in manifest_source
    assert '"component": "AppSupportPage"' in manifest_source


def test_integrations_page_uses_shared_primitives_for_health_ui() -> None:
    source = _read("factory_app/app/admin/pages/AppIntegrationsPage.jsx")
    assert "StatusPill" in source
    assert "SummaryStrip" in source
    assert "Panel" in source
    assert "ActionButton" in source
    assert "StudioInlineEmptyState" in source
    assert "StudioLoadingState" in source
    assert "StudioErrorState" in source
    assert "function StatusPill" not in source
    assert "function MetricTile" not in source
    assert "function StatCard" not in source

