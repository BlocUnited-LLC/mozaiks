from __future__ import annotations

import os
from importlib import import_module
from pathlib import Path

from mozaiksai.hosts.bootstrap import configure_repo_host_defaults


def _delenv_restoring(monkeypatch, name: str) -> None:
    """Delete ``name`` from the environment and guarantee it is restored.

    ``monkeypatch.delenv(name, raising=False)`` records nothing when the key
    is already absent, so a value written afterwards by production code
    (``configure_repo_host_defaults`` sets ``PLATFORM_PATH`` and
    ``MOZAIKS_WORKFLOWS_PATH``) would survive teardown and leak the temporary
    workspace into every later test in the process.  Setting the key first
    forces monkeypatch to record a restore entry for it; undo replays in
    reverse, ending at the original value or original absence.
    """
    monkeypatch.setenv(name, "")
    monkeypatch.delenv(name)


def test_host_composition_contract_documents_supported_app_local_studio_composition() -> None:
    doc = Path("docs/architecture/hosts/host-composition-contract.md").read_text(encoding="utf-8")

    assert "mozaiksai.hosts.studio" in doc
    assert "app = studio_app.app" in doc
    assert "MOZAIKS_APP_WORKSPACE_PATH" in doc
    assert "PLATFORM_PATH" in doc
    assert "MOZAIKS_WORKFLOWS_PATH" in doc
    assert "RUNTIME_PLATFORM_EXTENSIONS" in doc
    assert "private helper" in doc


def test_host_composition_contract_resolves_workspace_environment(monkeypatch, tmp_path) -> None:
    workspace = tmp_path / "canonical-app"
    app_root = workspace / "app"
    workflows_root = workspace / "workflows"
    app_root.mkdir(parents=True)
    workflows_root.mkdir()
    (app_root / "app.json").write_text('{"appName":"Contract App","appId":"contract-app"}', encoding="utf-8")

    monkeypatch.setenv("MOZAIKS_APP_WORKSPACE_PATH", str(workspace))
    _delenv_restoring(monkeypatch, "PLATFORM_PATH")
    monkeypatch.setenv("MOZAIKS_WORKFLOWS_PATH", str(workflows_root))

    configure_repo_host_defaults("studio")

    assert Path(os.environ["PLATFORM_PATH"]) == app_root.resolve()
    assert Path(os.environ["MOZAIKS_WORKFLOWS_PATH"]) == workflows_root.resolve()


def test_app_local_host_can_attach_routes_to_composed_studio_app() -> None:
    studio_host = import_module("mozaiksai.hosts.studio")
    route_path = "/__host_composition_contract_probe"

    if not any(getattr(route, "path", None) == route_path for route in studio_host.app.routes):
        @studio_host.app.get(route_path)
        async def _host_composition_contract_probe() -> dict[str, bool]:
            return {"ok": True}

    assert any(getattr(route, "path", None) == route_path for route in studio_host.app.routes)
