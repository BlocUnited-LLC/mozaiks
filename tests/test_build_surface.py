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


def test_studio_host_exposes_build_endpoint_and_route() -> None:
    studio_source = _read("mozaiksai/hosts/studio.py")
    manifest_source = _read("factory_app/app/ui/route_manifest.json")
    assert '@app.get("/api/studio/apps")' in studio_source
    assert '@app.post("/api/studio/apps")' in studio_source
    assert '@app.get("/api/studio/build")' in studio_source
    assert '@app.put("/api/studio/build")' in studio_source
    assert 'build_shell_config(surface="studio")' in studio_source
    assert '"path": "/apps/new"' in manifest_source
    assert '"path": "/usage"' in manifest_source
    assert '"path": "/operations"' in manifest_source
    assert '"path": "/billing"' in manifest_source
    assert '"path": "/settings"' in manifest_source
    assert '"path": "/apps"' in manifest_source
    assert '"component": "AppsPage"' in manifest_source
    assert '"path": "/apps/:appId/build"' in manifest_source
    assert '"component": "AppBuildPage"' in manifest_source
    assert '"path": "/apps/:appId/deploy"' in manifest_source
    assert '"path": "/apps/:appId/users"' in manifest_source
    assert '"path": "/apps/:appId/usage"' in manifest_source
    assert '"path": "/apps/:appId/operations"' in manifest_source
    assert '"path": "/apps/:appId/settings"' in manifest_source
    assert '"surfaces": ["studio"]' in manifest_source
    assert '"requiresRole": "admin"' in manifest_source


def test_factory_app_ui_barrel_registers_build_page() -> None:
    source = _read("factory_app/app/ui/index.js")
    assert "AppsPage" in source
    assert "registerComponent('AppsPage'" in source
    assert "AppBuildPage" in source
    assert "registerComponent('AppBuildPage'" in source
    assert "./pages/custom/console/AppBuildPage.jsx" in source


def test_core_components_do_not_register_build_page() -> None:
    source = _read("chat-ui/src/registry/coreComponents.js")
    assert "AppBuildPage" not in source


def test_build_page_fetches_endpoint_and_uses_workflow_start() -> None:
    source = _read("factory_app/app/ui/pages/custom/console/AppBuildPage.jsx")
    assert "/api/studio/build?app_id=" in source
    assert "/api/studio/build/history?app_id=" in source
    assert "method: 'PUT'" in source
    assert "useWorkflowStart" in source
    assert "buildRefinementTriggerPayload" in source
    assert "RefinementControls" in source
    assert "ConsoleSlideOver" in source
    assert "AdminWorkspaceLayout" in source
    assert "Save Draft" in source
    assert "Start Build Conversation" in source
    assert "App Validation" in source
    assert "app_validation_strategy" in source
    assert "request_kind" in source
    assert "trigger_source: 'action'" in source
    assert "action_id: 'build_request'" in source
    assert "app_id: appId" in source
    assert "build_registry_id" in source
    assert "trigger_source: 'refinement'" in source
    assert "trigger_payload:" in source


def test_refinement_ui_moves_into_factory_app() -> None:
    workspace = _workspace()
    assert not (workspace / "chat-ui/src/components/chat/RefinementControls.jsx").exists()
    assert (workspace / "factory_app/app/ui/pages/custom/console/RefinementControls.jsx").exists()


def test_factory_app_owns_refinement_trigger_payload_helper() -> None:
    source = _read("factory_app/app/ui/pages/custom/console/refinement.js")
    assert "buildRefinementTriggerPayload" in source
    assert "getRefinementRequestPlaceholder" in source
    assert "REFINEMENT_CHANGE_CLASSES" in source


def test_factory_app_refinement_controls_are_live_and_controlled() -> None:
    source = _read("factory_app/app/ui/pages/custom/console/RefinementControls.jsx")
    assert "modes = REFINEMENT_CHANGE_CLASSES" in source
    assert "selectedClass" in source
    assert "onSelectClass" in source
    assert "showRequestInput" in source
    assert "Apply refinement" in source
    assert "ActionButton" in source
    assert "StatusPill" in source


def test_app_overview_links_to_build_surface() -> None:
    source = _read("factory_app/app/ui/pages/custom/console/AppOverviewPage.jsx")
    assert 'to={`/apps/${appId}/build`}' in source


def test_apps_page_fetches_workspace_apps_endpoint() -> None:
    source = _read("factory_app/app/ui/pages/custom/console/AppsPage.jsx")
    assert "/api/studio/apps" in source
    assert "Mozaiks Console" in source
    assert "Start New App" in source
    assert "/apps/new" in source
    assert "app.destination" in source


def test_admin_workspace_layout_links_admin_console_and_build() -> None:
    source = _read("chat-ui/src/admin/components/AdminWorkspaceLayout.jsx")
    assert "Admin Dashboard" not in source
    assert "Mozaiks Console" in source
    assert "Developer" not in source
    assert "Studio Navigation" not in source
    assert "Browse sections" not in source
    assert "label: 'Console'" in source
    assert "label: 'App Console'" in source
    assert "Users" in source
    assert "Billing & Hosting" in source
    assert "Usage" in source
    assert "Integrations" in source
    assert "buildAppPath(appId, item.suffix)" in source
    assert "path: '/apps'" in source
    assert "path: '/usage'" in source
    assert "path: '/operations'" in source
    assert "path: '/billing'" in source
    assert "path: '/settings'" in source
    assert "suffix: '/build'" in source
    assert "suffix: '/deploy'" in source
    assert "suffix: '/admin'" in source
    assert "AdminWorkspaceLayout" in source
    assert "Open admin navigation" in source
    assert "lg:hidden" in source
    assert "lg:block" in source
    assert "description:" not in source


def test_integrations_page_uses_integrations_eyebrow() -> None:
    source = _read("factory_app/app/ui/pages/custom/console/AppIntegrationsPage.jsx")
    assert 'eyebrow="Integrations"' in source
    assert 'eyebrow="Studio"' not in source


def test_integrations_page_focuses_on_external_integrations() -> None:
    source = _read("factory_app/app/ui/pages/custom/console/AppIntegrationsPage.jsx")
    assert 'External Integrations' in source
    assert 'Add Integration' in source
    assert 'Connector Secret Backend' not in source
    assert 'Runtime Adapters' not in source
    assert 'Connection State' not in source
