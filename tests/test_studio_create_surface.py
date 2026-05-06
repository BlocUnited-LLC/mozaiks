from __future__ import annotations

from pathlib import Path


def _workspace() -> Path:
    return Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (_workspace() / relative_path).read_text(encoding="utf-8")


def test_runtime_exposes_studio_create_summary_helper() -> None:
    source = _read("mozaiksai/core/runtime/app/studio_home.py")
    assert "def build_studio_create_summary" in source
    assert "def build_studio_apps_summary" in source
    assert "def build_create_section" in source
    assert "async def load_studio_create_state_from_db" in source
    assert "async def save_studio_create_state_to_db" in source
    assert "STUDIO_CREATE_STATE_COLLECTION = PlatformCollections.STUDIO_CREATE_STATE" in source
    assert "StudioBuildState" not in source
    assert "initial_compile_workflow" in source
    assert "refinement_support" in source
    assert "app_validation" in source
    assert "build_app_validation_strategy_summary" in source
    assert '"state_file"' not in source


def test_studio_app_exposes_studio_create_endpoint_and_route() -> None:
    studio_source = _read("mozaiksai/hosts/studio.py")
    manifest_source = _read("factory_app/app/ui/route_manifest.json")
    assert '@app.get("/api/studio/apps")' in studio_source
    assert '@app.get("/api/studio/create")' in studio_source
    assert '@app.put("/api/studio/create")' in studio_source
    assert 'build_shell_config(surface="studio")' in studio_source
    assert '"path": "/hub"' in manifest_source
    assert '"component": "HubPage"' in manifest_source
    assert '"path": "/studio/create"' in manifest_source
    assert '"component": "StudioCreatePage"' in manifest_source
    assert '"surfaces": ["studio"]' in manifest_source
    assert '"requiresRole": "admin"' in manifest_source


def test_factory_app_ui_barrel_registers_studio_create_page() -> None:
    source = _read("factory_app/app/ui/index.js")
    assert "HubPage" in source
    assert "registerComponent('HubPage'" in source
    assert "StudioCreatePage" in source
    assert "registerComponent('StudioCreatePage'" in source
    assert "./pages/custom/StudioCreatePage.jsx" in source


def test_core_components_do_not_register_studio_create_page() -> None:
    source = _read("chat-ui/src/registry/coreComponents.js")
    assert "StudioCreatePage" not in source


def test_studio_create_page_fetches_endpoint_and_uses_workflow_start() -> None:
    source = _read("factory_app/app/ui/pages/custom/StudioCreatePage.jsx")
    assert "/api/studio/create" in source
    assert "method: 'PUT'" in source
    assert "useWorkflowStart" in source
    assert "buildRefinementTriggerPayload" in source
    assert "RefinementControls" in source
    assert "StudioSlideOver" in source
    assert "AdminWorkspaceLayout" in source
    assert "Save Draft" in source
    assert "Start Create Conversation" in source
    assert "App Validation" in source
    assert "app_validation_strategy" in source
    assert "request_kind" in source
    assert "trigger_source: 'action'" in source
    assert "action_id: 'studio_create'" in source
    assert "trigger_source: 'refinement'" in source
    assert "trigger_payload:" in source


def test_refinement_ui_moves_into_factory_app() -> None:
    workspace = _workspace()
    assert not (workspace / "chat-ui/src/components/chat/RefinementControls.jsx").exists()
    assert (workspace / "factory_app/app/ui/pages/custom/studio/RefinementControls.jsx").exists()


def test_factory_app_owns_refinement_trigger_payload_helper() -> None:
    source = _read("factory_app/app/ui/pages/custom/studio/refinement.js")
    assert "buildRefinementTriggerPayload" in source
    assert "getRefinementRequestPlaceholder" in source
    assert "REFINEMENT_CHANGE_CLASSES" in source


def test_factory_app_refinement_controls_are_live_and_controlled() -> None:
    source = _read("factory_app/app/ui/pages/custom/studio/RefinementControls.jsx")
    assert "modes = REFINEMENT_CHANGE_CLASSES" in source
    assert "selectedClass" in source
    assert "onSelectClass" in source
    assert "showRequestInput" in source
    assert "Apply refinement" in source
    assert "ActionButton" in source
    assert "StatusPill" in source


def test_studio_home_links_to_create_surface() -> None:
    source = _read("factory_app/app/ui/pages/custom/studio/StudioHomePage.jsx")
    assert 'to="/studio/create"' in source


def test_hub_page_fetches_studio_apps_endpoint() -> None:
    source = _read("factory_app/app/ui/pages/custom/studio/HubPage.jsx")
    assert "/api/studio/apps" in source
    assert "Workspace Catalog" in source
    assert "Start New App" in source
    assert "app.destination" in source


def test_admin_workspace_layout_links_admin_studio_and_create() -> None:
    source = _read("chat-ui/src/admin/components/AdminWorkspaceLayout.jsx")
    assert "Admin Dashboard" not in source
    assert "Mozaiks Admin" in source
    assert "Developer" not in source
    assert "Studio Navigation" not in source
    assert "Browse sections" not in source
    assert "label: 'Studio'" in source
    assert "label: 'Builder'" not in source
    assert "Users" in source
    assert "Billing" in source
    assert "Usage" in source
    assert "Integrations" in source
    assert "path: '/admin'" in source
    assert "path: '/admin/users'" in source
    assert "path: '/admin/usage'" in source
    assert "path: '/studio'" in source
    assert "path: '/studio/create'" in source
    assert "AdminWorkspaceLayout" in source
    assert "Open admin navigation" in source
    assert "lg:hidden" in source
    assert "lg:block" in source
    assert "description:" not in source


def test_studio_adapters_page_uses_studio_eyebrow() -> None:
    source = _read("factory_app/app/ui/pages/custom/studio/StudioAdaptersPage.jsx")
    assert 'eyebrow="Studio"' in source
    assert 'eyebrow="Builder"' not in source


def test_studio_adapters_page_focuses_on_external_adapters() -> None:
    source = _read("factory_app/app/ui/pages/custom/studio/StudioAdaptersPage.jsx")
    assert 'External Adapters' in source
    assert 'Add Adapter' in source
    assert 'Connector Secret Backend' not in source
    assert 'Runtime Adapters' not in source
    assert 'Connection State' not in source
