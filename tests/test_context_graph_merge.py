import pytest

from mozaiksai.core.app_context.context_graph import (
    build_context_graph_from_file_map,
    merge_context_graphs,
)
from mozaiksai.core.app_context.models import (
    AppContextGraph,
    AppContextGraphEdge,
    AppContextGraphNode,
    GraphEdgeType,
    GraphNodeType,
)


def test_structural_and_parser_files_share_identity_diagnostics_and_edges():
    parsed = build_context_graph_from_file_map(
        app_id="app_1",
        artifact_version_id="av_1",
        artifact_kind="app_bundle",
        file_map={
            "pkg/service.py": "from .repo import load\ndef run():\n    return load()\n",
            "pkg/repo.py": "def load():\n    return 1\n",
        },
    )
    original = parsed.model_dump(mode="json")
    parsed_file = next(
        node
        for node in parsed.nodes
        if node.node_type == GraphNodeType.FILE and node.metadata["path"] == "pkg/service.py"
    )
    structural = AppContextGraph(
        graph_id="structural",
        app_id="app_1",
        nodes=[
            AppContextGraphNode(node_id="module:pkg", node_type=GraphNodeType.MODULE),
            AppContextGraphNode(
                node_id="file:structural_service",
                node_type=GraphNodeType.FILE,
                metadata={"path": "pkg\\service.py", "structural_evidence": "module_manifest"},
            ),
        ],
        edges=[
            AppContextGraphEdge(
                edge_type=GraphEdgeType.GENERATED_FROM,
                source_node_id="module:pkg",
                target_node_id="file:structural_service",
            )
        ],
    )
    merged = merge_context_graphs(app_id="app_1", graph_id="combined", graphs=[structural, parsed])

    files = [
        node
        for node in merged.nodes
        if node.node_type == GraphNodeType.FILE and node.metadata["path"] == "pkg/service.py"
    ]
    assert len(files) == 1
    file = files[0]
    assert file.checksum == parsed_file.checksum
    assert file.artifact_version_id == "av_1"
    assert file.source_ref_id == parsed_file.source_ref_id
    assert file.metadata["parser"] == parsed_file.metadata["parser"]
    assert file.metadata["parse_status"] == parsed_file.metadata["parse_status"]
    assert file.metadata["structural_evidence"] == "module_manifest"
    assert any(
        edge.source_node_id == file.node_id
        and edge.edge_type == GraphEdgeType.DEFINES
        and edge.artifact_version_id == "av_1"
        for edge in merged.edges
    )
    assert any(
        edge.source_node_id == file.node_id
        and edge.edge_type == GraphEdgeType.IMPORTS
        and edge.artifact_version_id == "av_1"
        for edge in merged.edges
    )
    assert any(
        edge.source_node_id == "module:pkg" and edge.target_node_id == file.node_id
        for edge in merged.edges
    )
    assert parsed.model_dump(mode="json") == original


def _legacy_projection(graph_id: str, path: str, checksum: str | None = None) -> AppContextGraph:
    return AppContextGraph(
        graph_id=graph_id,
        app_id="app_1",
        nodes=[
            AppContextGraphNode(node_id=f"module:{graph_id}", node_type=GraphNodeType.MODULE),
            AppContextGraphNode(
                node_id="file:old_collision",
                node_type=GraphNodeType.FILE,
                checksum=checksum,
                metadata={"path": path},
            ),
        ],
        edges=[
            AppContextGraphEdge(
                edge_id="edge:old_collision",
                edge_type=GraphEdgeType.CONTAINS,
                source_node_id=f"module:{graph_id}",
                target_node_id="file:old_collision",
                metadata={"projection": graph_id},
            )
        ],
    )


@pytest.mark.parametrize("reverse", [False, True])
def test_legacy_file_aliases_are_scoped_to_their_input_projection(reverse: bool) -> None:
    graphs = [
        _legacy_projection("one", "pkg/foo-bar.py"),
        _legacy_projection("two", "pkg/foo_bar.py"),
    ]
    if reverse:
        graphs.reverse()
    originals = [graph.model_dump(mode="json") for graph in graphs]
    merged = merge_context_graphs(app_id="app_1", graph_id="merged", graphs=graphs)
    nodes = {node.node_id: node for node in merged.nodes}
    files = [node for node in merged.nodes if node.node_type == GraphNodeType.FILE]
    assert len(files) == 2
    assert len({node.node_id for node in files}) == 2
    assert {
        (edge.source_node_id, nodes[edge.target_node_id].metadata["path"]) for edge in merged.edges
    } == {("module:one", "pkg/foo-bar.py"), ("module:two", "pkg/foo_bar.py")}
    assert len({edge.edge_id for edge in merged.edges}) == 2
    assert [graph.model_dump(mode="json") for graph in graphs] == originals


@pytest.mark.parametrize("reverse", [False, True])
def test_later_snapshot_replaces_conflicting_projection_without_stale_facts(reverse: bool) -> None:
    graphs = [
        build_context_graph_from_file_map(
            app_id="app_1",
            artifact_version_id=f"av_{name}",
            artifact_kind="app_bundle",
            file_map={
                "pkg/service.py": f"from .{name} import load\ndef {name}():\n    return load()\n",
                f"pkg/{name}.py": "def load():\n    return 1\n",
            },
        )
        for name in ("before", "after")
    ]
    if reverse:
        graphs.reverse()
    originals = [graph.model_dump(mode="json") for graph in graphs]
    latest = graphs[-1]
    merged = merge_context_graphs(app_id="app_1", graph_id="merged", graphs=graphs)
    assert {node.node_id for node in merged.nodes} == {node.node_id for node in latest.nodes}
    assert {
        (edge.edge_type, edge.source_node_id, edge.target_node_id) for edge in merged.edges
    } == {(edge.edge_type, edge.source_node_id, edge.target_node_id) for edge in latest.edges}
    assert {ref.source_ref_id for ref in merged.source_refs} == {
        ref.source_ref_id for ref in latest.source_refs
    }
    assert {
        (node.metadata["path"], node.checksum, node.artifact_version_id)
        for node in merged.nodes
        if node.node_type == GraphNodeType.FILE
    } == {
        (node.metadata["path"], node.checksum, node.artifact_version_id)
        for node in latest.nodes
        if node.node_type == GraphNodeType.FILE
    }
    assert [graph.model_dump(mode="json") for graph in graphs] == originals


@pytest.mark.parametrize("second_checksum", [None, "sha256:" + "a" * 64, "a" * 64])
def test_merge_accepts_matching_or_missing_checksum_with_all_projection_edges(
    second_checksum: str | None,
) -> None:
    checksum = "sha256:" + "a" * 64
    merged = merge_context_graphs(
        app_id="app_1",
        graph_id="merged",
        graphs=[
            _legacy_projection("one", "pkg\\service.py", checksum),
            _legacy_projection("two", "pkg/service.py", second_checksum),
        ],
    )
    files = [node for node in merged.nodes if node.node_type == GraphNodeType.FILE]
    assert len(files) == 1
    assert files[0].metadata["path"] == "pkg/service.py"
    assert files[0].checksum == checksum
    assert {(edge.source_node_id, edge.target_node_id) for edge in merged.edges} == {
        ("module:one", files[0].node_id),
        ("module:two", files[0].node_id),
    }
