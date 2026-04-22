from __future__ import annotations

from pathlib import Path


def _workspace() -> Path:
    return Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (_workspace() / relative_path).read_text(encoding="utf-8")


def test_runtime_exposes_studio_build_summary_helper() -> None:
    source = _read("mozaiksai/core/runtime/app/studio_home.py")
    assert "def build_studio_build_summary" in source
    assert "def save_studio_build_request" in source
    assert "initial_compile_workflow" in source
    assert "refinement_support" in source


def test_studio_app_exposes_studio_build_endpoint_and_route() -> None:
    studio_source = _read("studio_app.py")
    platform_source = _read("platform_app.py")
    assert '@app.get("/api/studio/build")' in studio_source
    assert '@app.put("/api/studio/build")' in studio_source
    assert '"path": "/studio/build"' in platform_source
    assert '"component": "StudioBuildPage"' in platform_source
    assert '"requiresRole": "admin"' in platform_source
    assert '_inject_header_page(result, path="/studio/build"' not in platform_source


def test_studio_extension_registers_studio_build_page() -> None:
    source = _read("chat-ui/src/studio/index.js")
    assert "StudioBuildPage" in source
    assert "registerComponent('StudioBuildPage'" in source


def test_core_components_do_not_register_studio_build_page() -> None:
    source = _read("chat-ui/src/registry/coreComponents.js")
    assert "StudioBuildPage" not in source


def test_studio_build_page_fetches_endpoint_and_uses_workflow_start() -> None:
    source = _read("chat-ui/src/studio/pages/StudioBuildPage.jsx")
    assert "/api/studio/build" in source
    assert "method: 'PUT'" in source
    assert "useWorkflowStart" in source
    assert "BuilderWorkspaceLayout" in source
    assert "Save Build Draft" in source
    assert "request_kind" in source
    assert "trigger_source: 'refinement'" in source
    assert "artifact_kind: 'app_bundle'" in source


def test_studio_home_links_to_build_surface() -> None:
    source = _read("chat-ui/src/studio/pages/StudioHomePage.jsx")
    assert 'to="/studio/build"' in source


def test_builder_workspace_nav_links_admin_studio_and_build() -> None:
    source = _read("chat-ui/src/studio/components/BuilderWorkspaceNav.jsx")
    assert "Admin Portal" in source
    assert "path: '/admin'" in source
    assert "path: '/studio'" in source
    assert "path: '/studio/build'" in source
    assert "BuilderWorkspaceLayout" in source
    assert "lg:hidden" in source
    assert "lg:block" in source
