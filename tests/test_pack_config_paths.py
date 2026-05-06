from __future__ import annotations

import json
import os
from pathlib import Path

from tests.import_utils import import_module_directly

_config = import_module_directly("mozaiksai.core.workflow.pack.config")
_paths = import_module_directly("mozaiksai.core.workflow.paths")
_resources = import_module_directly("mozaiksai.resources")
get_global_pack_graph_path = _config.get_global_pack_graph_path
get_workflow_pack_graph_path = _config.get_workflow_pack_graph_path
load_global_pack_graph = _config.load_global_pack_graph
load_workflow_pack_graph = _config.load_workflow_pack_graph
list_workflow_sequences = _config.list_workflow_sequences


def _use_repo_factory_workflows(monkeypatch) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    workflows_root = repo_root / "factory_app" / "workflows"
    monkeypatch.setenv("PLATFORM_PATH", str(repo_root / "__no_active_app__"))
    monkeypatch.delenv("MOZAIKS_WORKFLOW_ROOTS", raising=False)
    monkeypatch.setenv("MOZAIKS_WORKFLOWS_PATH", str(workflows_root))
    _config._GLOBAL_CACHE = None
    _config._WORKFLOW_CACHE = {}


def test_global_pack_graph_path_points_to_repo_workflows_pack(monkeypatch) -> None:
    _use_repo_factory_workflows(monkeypatch)
    path = get_global_pack_graph_path()
    assert path.name == "extension_registry.json"
    assert path.parent.name == "extended_orchestration"
    assert path.parent.parent.name == "workflows"


def test_workflow_pack_graph_path_points_to_workflow_pack(monkeypatch) -> None:
    _use_repo_factory_workflows(monkeypatch)
    path = get_workflow_pack_graph_path("AgentGenerator")
    expected_suffix = (
        Path("workflows")
        / "AgentGenerator"
        / "extended_orchestration"
        / "mfj_extension.json"
    )
    assert str(path).endswith(str(expected_suffix))


def test_load_global_pack_graph_uses_canonical_file(monkeypatch) -> None:
    _use_repo_factory_workflows(monkeypatch)
    graph = load_global_pack_graph()
    assert graph is not None
    assert graph.version == 3
    assert {entry.id for entry in graph.workflows} == {
        "ValueEngine",
        "ThemeCapture",
        "DesignDocs",
        "AgentGenerator",
        "AppGenerator",
        "ExistingAppDiscovery",
    }


def test_single_root_registry_path_uses_explicit_override_without_factory_merge(monkeypatch, tmp_path) -> None:
    _use_repo_factory_workflows(monkeypatch)

    app_root = tmp_path / "app"
    registry_dir = app_root / "workflows" / "extended_orchestration"
    registry_dir.mkdir(parents=True)
    (app_root / "app.json").write_text('{"appName":"test-app"}', encoding="utf-8")
    target_registry = registry_dir / "extension_registry.json"
    target_registry.write_text(
        json.dumps(
            {
                "pack_name": "TestOverlay",
                "version": 3,
                "workflows": [
                    {"id": "HostedOnly", "description": "Overlay workflow"},
                ],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.delenv("MOZAIKS_WORKFLOW_ROOTS", raising=False)
    monkeypatch.setenv("MOZAIKS_WORKFLOWS_PATH", str(app_root / "workflows"))
    _config._GLOBAL_CACHE = None

    path = get_global_pack_graph_path()
    assert path == target_registry.resolve()

    graph = load_global_pack_graph()
    assert graph is not None
    assert {workflow.id for workflow in graph.workflows} == {"HostedOnly"}
    assert graph.journeys == []


def test_load_workflow_pack_graph_uses_canonical_file(monkeypatch) -> None:
    _use_repo_factory_workflows(monkeypatch)
    graph = load_workflow_pack_graph("AgentGenerator")
    assert graph is not None
    assert graph.version == 3
    assert len(graph.mid_flight_journeys) >= 1
    assert graph.mid_flight_journeys[0].id.startswith("workflow_generation")


def test_declared_global_workflows_match_physical_workflow_folders(monkeypatch) -> None:
    _use_repo_factory_workflows(monkeypatch)
    graph = load_global_pack_graph()
    assert graph is not None

    declared = {w.id for w in graph.workflows}
    workflows_root = get_global_pack_graph_path().parent.parent

    physical = {
        p.name
        for p in workflows_root.iterdir()
        if p.is_dir() and p.name not in {"_pack", "__pycache__", "extended_orchestration"} and not p.name.startswith(".")
    }

    assert declared.issubset(physical)


def test_list_workflow_sequences_returns_declared_sequences(monkeypatch) -> None:
    _use_repo_factory_workflows(monkeypatch)
    graph = load_global_pack_graph()
    assert graph is not None
    assert [item.id for item in list_workflow_sequences(graph)] == [
        item.id for item in graph.journeys
    ]


def test_legacy_multi_root_env_uses_first_declared_root_only(monkeypatch, tmp_path: Path) -> None:
    local_root = tmp_path / "local"
    shared_root = tmp_path / "shared"
    local_graph_dir = local_root / "LocalOnly" / "extended_orchestration"
    shared_graph_dir = shared_root / "SharedOnly" / "extended_orchestration"
    local_graph_dir.mkdir(parents=True)
    shared_graph_dir.mkdir(parents=True)

    local_graph = local_graph_dir / "mfj_extension.json"
    local_graph.write_text(json.dumps({"version": 3, "mid_flight_journeys": []}), encoding="utf-8")
    (shared_graph_dir / "mfj_extension.json").write_text(
        json.dumps({"version": 3, "mid_flight_journeys": []}),
        encoding="utf-8",
    )
    (local_root / "LocalOnly" / "orchestrator.yaml").write_text("workflow_name: LocalOnly\n", encoding="utf-8")
    (shared_root / "SharedOnly" / "orchestrator.yaml").write_text("workflow_name: SharedOnly\n", encoding="utf-8")

    monkeypatch.setenv("MOZAIKS_WORKFLOW_ROOTS", f"{local_root}{os.pathsep}{shared_root}")
    monkeypatch.delenv("MOZAIKS_WORKFLOWS_PATH", raising=False)
    monkeypatch.delenv("PLATFORM_PATH", raising=False)
    _config._GLOBAL_CACHE = None
    _config._WORKFLOW_CACHE = {}

    assert _paths.normalize_workflow_roots() == [local_root.resolve()]
    assert get_workflow_pack_graph_path("LocalOnly") == local_graph.resolve()


def test_single_root_override_does_not_inject_factory_fallback(monkeypatch, tmp_path: Path) -> None:
    local_root = tmp_path / "local-workflows"
    local_root.mkdir(parents=True)
    monkeypatch.delenv("MOZAIKS_WORKFLOW_ROOTS", raising=False)
    monkeypatch.setenv("MOZAIKS_WORKFLOWS_PATH", str(local_root))

    roots = _paths.normalize_workflow_roots()
    assert roots
    assert roots[0] == local_root.resolve()
    assert roots == [local_root.resolve()]


def test_default_roots_use_repo_factory_workflows_when_no_active_app(monkeypatch) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    monkeypatch.chdir(repo_root)
    monkeypatch.delenv("MOZAIKS_WORKFLOW_ROOTS", raising=False)
    monkeypatch.delenv("MOZAIKS_WORKFLOWS_PATH", raising=False)
    monkeypatch.delenv("PLATFORM_PATH", raising=False)

    roots = _paths.normalize_workflow_roots()

    assert roots
    assert roots == [(repo_root / "factory_app" / "workflows").resolve()]
    assert all("__no_active_app__" not in str(root) for root in roots)


def test_active_app_root_uses_workspace_env_when_platform_path_missing(monkeypatch, tmp_path: Path) -> None:
    workspace_root = tmp_path / "external-workspace"
    app_root = workspace_root / "app"
    app_root.mkdir(parents=True)
    (app_root / "app.json").write_text('{"appName":"External Workspace"}', encoding="utf-8")

    monkeypatch.delenv("PLATFORM_PATH", raising=False)
    monkeypatch.setenv("MOZAIKS_APP_WORKSPACE_PATH", str(workspace_root))

    assert _paths.resolve_active_app_root() == app_root.resolve()


def test_chat_ui_src_root_defaults_to_repo_checkout(monkeypatch) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    monkeypatch.chdir(repo_root)
    monkeypatch.delenv("MOZAIKS_CHAT_UI_PATH", raising=False)

    root = _resources.resolve_chat_ui_src_root()

    assert root == (repo_root / "chat-ui" / "src").resolve()
