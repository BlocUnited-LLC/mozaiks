from __future__ import annotations

from pathlib import Path

from tests.import_utils import import_module_directly

_config = import_module_directly("mozaiksai.core.workflow.pack.config")
get_global_pack_graph_path = _config.get_global_pack_graph_path
get_workflow_pack_graph_path = _config.get_workflow_pack_graph_path
load_global_pack_graph = _config.load_global_pack_graph
load_workflow_pack_graph = _config.load_workflow_pack_graph
list_workflow_sequences = _config.list_workflow_sequences


def _use_repo_platform_workflows(monkeypatch) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    workflows_root = repo_root / "platform" / "workflows"
    monkeypatch.setenv("MOZAIKS_WORKFLOWS_PATH", str(workflows_root))
    _config._GLOBAL_CACHE = None
    _config._WORKFLOW_CACHE = {}


def test_global_pack_graph_path_points_to_repo_workflows_pack(monkeypatch) -> None:
    _use_repo_platform_workflows(monkeypatch)
    path = get_global_pack_graph_path()
    assert path.name == "extension_registry.json"
    assert path.parent.name == "extended_orchestration"
    assert path.parent.parent.name == "workflows"


def test_workflow_pack_graph_path_points_to_workflow_pack(monkeypatch) -> None:
    _use_repo_platform_workflows(monkeypatch)
    path = get_workflow_pack_graph_path("JokeFactory")
    expected_suffix = (
        Path("workflows")
        / "JokeFactory"
        / "extended_orchestration"
        / "mfj_extension.json"
    )
    assert str(path).endswith(str(expected_suffix))


def test_load_global_pack_graph_uses_canonical_file(monkeypatch) -> None:
    _use_repo_platform_workflows(monkeypatch)
    graph = load_global_pack_graph()
    assert graph is not None
    assert graph.version == 3
    assert any(w.id == "JokeFactory" for w in graph.workflows)
    assert any(w.id == "JokeWorker" for w in graph.workflows)


def test_load_workflow_pack_graph_uses_canonical_file(monkeypatch) -> None:
    _use_repo_platform_workflows(monkeypatch)
    graph = load_workflow_pack_graph("JokeFactory")
    assert graph is not None
    assert graph.version == 3
    assert len(graph.mid_flight_journeys) >= 1
    assert graph.mid_flight_journeys[0].id == "parallel-joke-generation"


def test_declared_global_workflows_match_physical_workflow_folders(monkeypatch) -> None:
    _use_repo_platform_workflows(monkeypatch)
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
    _use_repo_platform_workflows(monkeypatch)
    graph = load_global_pack_graph()
    assert graph is not None
    assert [item.id for item in list_workflow_sequences(graph)] == [
        item.id for item in graph.journeys
    ]
