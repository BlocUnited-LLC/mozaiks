from __future__ import annotations

from pathlib import Path


def _workspace() -> Path:
    return Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (_workspace() / relative_path).read_text(encoding="utf-8")


def test_studio_app_exposes_local_studio_home_endpoint() -> None:
    source = _read("studio_app.py")
    assert '@app.get("/api/studio/home")' in source
    assert 'build_studio_home_summary(platform_root, surface="shell-home", local_only=True)' in source


def test_platform_app_exposes_studio_route_without_header_link() -> None:
    studio_source = _read("studio_app.py")
    platform_source = _read("platform_app.py")
    assert 'build_shell_config(include_studio=True)' in studio_source
    assert "os.getenv" not in studio_source
    assert '"path": "/studio"' in platform_source
    assert '"component": "StudioHomePage"' in platform_source
    assert '"requiresRole": "admin"' in platform_source
    assert '_inject_header_page(result, path="/studio"' not in platform_source


def test_studio_extension_registers_studio_home_page() -> None:
    source = _read("chat-ui/src/studio/index.js")
    assert "StudioHomePage" in source
    assert "registerComponent('StudioHomePage'" in source


def test_core_components_do_not_register_studio_home_page() -> None:
    source = _read("chat-ui/src/registry/coreComponents.js")
    assert "StudioHomePage" not in source


def test_app_shell_registers_studio_extension_by_host_mode() -> None:
    source = _read("app/App.jsx")
    assert "registerStudioComponents" in source
    assert "import.meta.env.MOZAIKS_HOST" in source
    assert "hostMode === 'studio'" in source
    assert "hostMode === 'mozaiks'" in source


def test_studio_home_page_fetches_summary_endpoint() -> None:
    source = _read("chat-ui/src/studio/pages/StudioHomePage.jsx")
    assert "/api/studio/home" in source
    assert "Studio Home" in source
    assert "next_step" in source
    assert "AdminWorkspaceLayout" in source
