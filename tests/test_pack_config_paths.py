from __future__ import annotations

from pathlib import Path

from tests.import_utils import import_module_directly

_config = import_module_directly("mozaiksai.core.workflow.pack.config")
get_global_pack_graph_path = _config.get_global_pack_graph_path
get_workflow_pack_graph_path = _config.get_workflow_pack_graph_path
load_global_pack_graph = _config.load_global_pack_graph
load_workflow_pack_graph = _config.load_workflow_pack_graph


def test_global_pack_graph_path_points_to_repo_workflows_pack() -> None:
    path = get_global_pack_graph_path()
    assert path.name == "workflow_graph.json"
    assert path.parent.name == "_pack"
    assert path.parent.parent.name == "workflows"


def test_workflow_pack_graph_path_points_to_workflow_pack() -> None:
    path = get_workflow_pack_graph_path("RoastChat")
    expected_suffix = Path("workflows") / "RoastChat" / "_pack" / "workflow_graph.json"
    assert str(path).endswith(str(expected_suffix))


def test_load_global_pack_graph_uses_canonical_file() -> None:
    graph = load_global_pack_graph()
    assert graph is not None
    assert graph.version == 2
    assert any(w.id == "RoastChat" for w in graph.workflows)


def test_load_workflow_pack_graph_uses_canonical_file() -> None:
    graph = load_workflow_pack_graph("RoastChat")
    assert graph is not None
    assert graph.version == 3
    assert len(graph.mid_flight_journeys) >= 1
    assert graph.mid_flight_journeys[0].id == "roast_cycle"
