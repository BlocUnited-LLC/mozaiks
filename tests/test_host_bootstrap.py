from __future__ import annotations

import os
from pathlib import Path

from mozaiksai.hosts.bootstrap import configure_repo_host_defaults


def _delenv_restoring(monkeypatch, name: str) -> None:
    """Delete ``name`` from the environment and guarantee it is restored.

    ``monkeypatch.delenv(name, raising=False)`` records nothing when the key
    is already absent, so a value written afterwards by
    ``configure_repo_host_defaults`` (it sets ``PLATFORM_PATH`` and
    ``MOZAIKS_WORKFLOWS_PATH``) would survive teardown and leak host
    configuration into every later test in the process.  Setting the key first
    forces monkeypatch to record a restore entry for it; undo replays in
    reverse, ending at the original value or original absence.
    """
    monkeypatch.setenv(name, "")
    monkeypatch.delenv(name)



def test_runtime_host_does_not_inject_repo_defaults(monkeypatch) -> None:
    _delenv_restoring(monkeypatch, "PLATFORM_PATH")
    _delenv_restoring(monkeypatch, "MOZAIKS_FACTORY_APP_PATH")
    _delenv_restoring(monkeypatch, "MOZAIKS_WORKFLOWS_PATH")

    configure_repo_host_defaults("runtime")

    assert os.getenv("PLATFORM_PATH") is None
    assert os.getenv("MOZAIKS_WORKFLOWS_PATH") is None


def test_studio_host_without_workspace_defaults_platform_path_to_factory_app_bundle(monkeypatch) -> None:
    """Repo-local Studio bootstrap should bind to the shared builder workflow root."""
    _delenv_restoring(monkeypatch, "PLATFORM_PATH")
    _delenv_restoring(monkeypatch, "MOZAIKS_APP_WORKSPACE_PATH")
    _delenv_restoring(monkeypatch, "MOZAIKS_WORKFLOWS_PATH")

    configure_repo_host_defaults("studio")

    platform_path = Path(os.environ["PLATFORM_PATH"]).resolve()
    assert platform_path.name == "app"
    assert platform_path.parent.name == "factory_app"

    workflow_root = Path(os.environ["MOZAIKS_WORKFLOWS_PATH"]).resolve()
    assert workflow_root.name == "workflows"
    assert workflow_root.parent.name == "factory_app"
    # Studio should use the shared factory_app/workflows, not factory_app/app/workflows
    assert workflow_root.parent.name == "factory_app"
    assert workflow_root != (platform_path / "workflows").resolve()


def test_studio_host_uses_external_workspace_root_when_provided(monkeypatch, tmp_path) -> None:
    workspace_root = tmp_path / "app-zero-repo"
    app_root = workspace_root / "app"
    app_root.mkdir(parents=True)
    (workspace_root / "workflows").mkdir(parents=True)
    (app_root / "app.json").write_text('{"appName": "External App Zero"}', encoding="utf-8")

    _delenv_restoring(monkeypatch, "PLATFORM_PATH")
    _delenv_restoring(monkeypatch, "MOZAIKS_FACTORY_APP_PATH")
    monkeypatch.setenv("MOZAIKS_APP_WORKSPACE_PATH", str(workspace_root))
    _delenv_restoring(monkeypatch, "MOZAIKS_WORKFLOWS_PATH")

    configure_repo_host_defaults("studio")

    platform_path = Path(os.environ["PLATFORM_PATH"]).resolve()
    workflow_root = Path(os.environ["MOZAIKS_WORKFLOWS_PATH"]).resolve()

    assert platform_path == app_root.resolve()
    assert workflow_root.name == "workflows"
    assert workflow_root.parent.name == "factory_app"


def test_platform_host_uses_workspace_root_workflows_when_present(monkeypatch, tmp_path) -> None:
    workspace_root = tmp_path / "external-workspace"
    app_root = workspace_root / "app"
    app_root.mkdir(parents=True)
    (workspace_root / "workflows").mkdir(parents=True)
    (app_root / "app.json").write_text('{"appName": "External App Zero"}', encoding="utf-8")

    monkeypatch.setenv("PLATFORM_PATH", str(workspace_root))
    _delenv_restoring(monkeypatch, "MOZAIKS_FACTORY_APP_PATH")
    _delenv_restoring(monkeypatch, "MOZAIKS_APP_WORKSPACE_PATH")
    _delenv_restoring(monkeypatch, "MOZAIKS_WORKFLOWS_PATH")

    configure_repo_host_defaults("platform")

    workflow_root = Path(os.environ["MOZAIKS_WORKFLOWS_PATH"]).resolve()
    assert workflow_root == (workspace_root / "workflows").resolve()


def test_studio_host_normalizes_repo_root_platform_path_to_factory_app_bundle(monkeypatch) -> None:
    monkeypatch.setenv("PLATFORM_PATH", str(Path.cwd().resolve()))
    _delenv_restoring(monkeypatch, "MOZAIKS_APP_WORKSPACE_PATH")
    _delenv_restoring(monkeypatch, "MOZAIKS_WORKFLOWS_PATH")

    configure_repo_host_defaults("studio")

    platform_path = Path(os.environ["PLATFORM_PATH"]).resolve()
    workflow_root = Path(os.environ["MOZAIKS_WORKFLOWS_PATH"]).resolve()
    assert platform_path.name == "app"
    assert platform_path.parent.name == "factory_app"
    assert workflow_root.name == "workflows"
    assert workflow_root.parent.name == "factory_app"

