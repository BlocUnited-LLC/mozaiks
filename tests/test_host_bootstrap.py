from __future__ import annotations

import os
from pathlib import Path

from mozaiksai.hosts.bootstrap import configure_repo_host_defaults


def test_runtime_host_does_not_inject_repo_defaults(monkeypatch) -> None:
    monkeypatch.delenv("PLATFORM_PATH", raising=False)
    monkeypatch.delenv("MOZAIKS_FACTORY_APP_PATH", raising=False)
    monkeypatch.delenv("MOZAIKS_WORKFLOW_ROOTS", raising=False)
    monkeypatch.delenv("MOZAIKS_WORKFLOWS_PATH", raising=False)

    configure_repo_host_defaults("runtime")

    assert os.getenv("PLATFORM_PATH") is None
    assert os.getenv("MOZAIKS_WORKFLOWS_PATH") is None


def test_studio_host_without_workspace_defaults_platform_path_to_factory_app_bundle(monkeypatch) -> None:
    """Repo-local Studio bootstrap should bind to the shared builder workflow root."""
    monkeypatch.delenv("PLATFORM_PATH", raising=False)
    monkeypatch.delenv("MOZAIKS_APP_WORKSPACE_PATH", raising=False)
    monkeypatch.delenv("MOZAIKS_WORKFLOW_ROOTS", raising=False)
    monkeypatch.delenv("MOZAIKS_WORKFLOWS_PATH", raising=False)

    configure_repo_host_defaults("studio")

    platform_path = Path(os.environ["PLATFORM_PATH"]).resolve()
    assert platform_path.name == "app"
    assert platform_path.parent.name == "factory_app"
    assert not (platform_path / "workflows").exists()

    workflow_root = Path(os.environ["MOZAIKS_WORKFLOWS_PATH"]).resolve()
    assert workflow_root.name == "workflows"
    assert workflow_root.parent.name == "factory_app"


def test_studio_host_uses_external_workspace_root_when_provided(monkeypatch, tmp_path) -> None:
    workspace_root = tmp_path / "app-zero-repo"
    app_root = workspace_root / "app"
    (app_root / "workflows").mkdir(parents=True)
    (app_root / "app.json").write_text('{"appName": "External App Zero"}', encoding="utf-8")

    monkeypatch.delenv("PLATFORM_PATH", raising=False)
    monkeypatch.delenv("MOZAIKS_FACTORY_APP_PATH", raising=False)
    monkeypatch.setenv("MOZAIKS_APP_WORKSPACE_PATH", str(workspace_root))
    monkeypatch.delenv("MOZAIKS_WORKFLOW_ROOTS", raising=False)
    monkeypatch.delenv("MOZAIKS_WORKFLOWS_PATH", raising=False)

    configure_repo_host_defaults("studio")

    platform_path = Path(os.environ["PLATFORM_PATH"]).resolve()
    workflow_root = Path(os.environ["MOZAIKS_WORKFLOWS_PATH"]).resolve()

    assert platform_path == app_root.resolve()
    assert workflow_root.name == "workflows"
    assert workflow_root.parent.name == "factory_app"


def test_platform_host_uses_workspace_root_workflows_when_present(monkeypatch, tmp_path) -> None:
    workspace_root = tmp_path / "external-workspace"
    app_root = workspace_root / "app"
    (app_root / "workflows").mkdir(parents=True)
    (app_root / "app.json").write_text('{"appName": "External App Zero"}', encoding="utf-8")

    monkeypatch.setenv("PLATFORM_PATH", str(workspace_root))
    monkeypatch.delenv("MOZAIKS_FACTORY_APP_PATH", raising=False)
    monkeypatch.delenv("MOZAIKS_APP_WORKSPACE_PATH", raising=False)
    monkeypatch.delenv("MOZAIKS_WORKFLOW_ROOTS", raising=False)
    monkeypatch.delenv("MOZAIKS_WORKFLOWS_PATH", raising=False)

    configure_repo_host_defaults("platform")

    workflow_root = Path(os.environ["MOZAIKS_WORKFLOWS_PATH"]).resolve()
    assert workflow_root == (app_root / "workflows").resolve()


def test_studio_host_normalizes_repo_root_platform_path_to_factory_app_bundle(monkeypatch) -> None:
    monkeypatch.setenv("PLATFORM_PATH", str(Path.cwd().resolve()))
    monkeypatch.delenv("MOZAIKS_APP_WORKSPACE_PATH", raising=False)
    monkeypatch.delenv("MOZAIKS_WORKFLOW_ROOTS", raising=False)
    monkeypatch.delenv("MOZAIKS_WORKFLOWS_PATH", raising=False)

    configure_repo_host_defaults("studio")

    platform_path = Path(os.environ["PLATFORM_PATH"]).resolve()
    workflow_root = Path(os.environ["MOZAIKS_WORKFLOWS_PATH"]).resolve()
    assert platform_path.name == "app"
    assert platform_path.parent.name == "factory_app"
    assert workflow_root.name == "workflows"
    assert workflow_root.parent.name == "factory_app"