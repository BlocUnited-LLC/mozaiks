from __future__ import annotations

import os
from pathlib import Path

from mozaiksai.hosts.bootstrap import configure_repo_host_defaults


def test_runtime_host_does_not_inject_repo_defaults(monkeypatch) -> None:
    monkeypatch.delenv("PLATFORM_PATH", raising=False)
    monkeypatch.delenv("MOZAIKS_WORKFLOW_ROOTS", raising=False)
    monkeypatch.delenv("MOZAIKS_WORKFLOWS_PATH", raising=False)

    configure_repo_host_defaults("runtime")

    assert os.getenv("PLATFORM_PATH") is None
    assert os.getenv("MOZAIKS_WORKFLOW_ROOTS") is None


def test_studio_host_without_workspace_leaves_platform_path_unset(monkeypatch) -> None:
    """When no external workspace is configured, bootstrap does not inject a default PLATFORM_PATH.

    App Zero now lives in a separate repo (mozaiks-app). Developers must set
    MOZAIKS_APP_WORKSPACE_PATH or PLATFORM_PATH explicitly.  The bootstrap
    still injects MOZAIKS_WORKFLOW_ROOTS pointing to factory_app workflows.
    """
    monkeypatch.delenv("PLATFORM_PATH", raising=False)
    monkeypatch.delenv("MOZAIKS_APP_WORKSPACE_PATH", raising=False)
    monkeypatch.delenv("MOZAIKS_WORKFLOW_ROOTS", raising=False)
    monkeypatch.delenv("MOZAIKS_WORKFLOWS_PATH", raising=False)

    configure_repo_host_defaults("studio")

    assert os.getenv("PLATFORM_PATH") is None

    # Factory workflows root is still injected when in the repo checkout
    workflow_roots_str = os.getenv("MOZAIKS_WORKFLOW_ROOTS") or ""
    if workflow_roots_str:
        workflow_roots = [Path(p).resolve() for p in workflow_roots_str.split(os.pathsep) if p]
        assert any(
            path.name == "workflows"
            and path.parent.name == "app"
            and path.parent.parent.name == "factory_app"
            for path in workflow_roots
        )


def test_studio_host_uses_external_workspace_root_when_provided(monkeypatch, tmp_path) -> None:
    workspace_root = tmp_path / "app-zero-repo"
    app_root = workspace_root / "app"
    (app_root / "workflows").mkdir(parents=True)
    (app_root / "app.json").write_text('{"appName": "External App Zero"}', encoding="utf-8")

    monkeypatch.delenv("PLATFORM_PATH", raising=False)
    monkeypatch.setenv("MOZAIKS_APP_WORKSPACE_PATH", str(workspace_root))
    monkeypatch.delenv("MOZAIKS_WORKFLOW_ROOTS", raising=False)
    monkeypatch.delenv("MOZAIKS_WORKFLOWS_PATH", raising=False)

    configure_repo_host_defaults("studio")

    platform_path = Path(os.environ["PLATFORM_PATH"]).resolve()
    workflow_roots = [Path(part).resolve() for part in os.environ["MOZAIKS_WORKFLOW_ROOTS"].split(os.pathsep) if part]

    assert platform_path == app_root.resolve()
    assert workflow_roots[0] == (app_root / "workflows").resolve()


def test_studio_host_resolves_workspace_root_platform_path_for_workflow_roots(monkeypatch, tmp_path) -> None:
    workspace_root = tmp_path / "external-workspace"
    app_root = workspace_root / "app"
    (app_root / "workflows").mkdir(parents=True)
    (app_root / "app.json").write_text('{"appName": "External App Zero"}', encoding="utf-8")

    monkeypatch.setenv("PLATFORM_PATH", str(workspace_root))
    monkeypatch.delenv("MOZAIKS_APP_WORKSPACE_PATH", raising=False)
    monkeypatch.delenv("MOZAIKS_WORKFLOW_ROOTS", raising=False)
    monkeypatch.delenv("MOZAIKS_WORKFLOWS_PATH", raising=False)

    configure_repo_host_defaults("studio")

    workflow_roots = [Path(part).resolve() for part in os.environ["MOZAIKS_WORKFLOW_ROOTS"].split(os.pathsep) if part]
    assert workflow_roots[0] == (app_root / "workflows").resolve()