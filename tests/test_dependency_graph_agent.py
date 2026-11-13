from mozaiksai.control_plane.implementations.dependency_graph_agent import _build_evidence
from mozaiksai.core.app_context.context_graph import build_context_graph_from_file_map


def test_dependency_graph_discloses_dependencies_beyond_the_traversal_limit() -> None:
    files = {
        f"src/{name}.ts": f"import './{next_name}';\n"
        for name, next_name in zip("abcd", "bcde", strict=True)
    }
    files["src/e.ts"] = "export const leaf = 1;\n"
    graph = build_context_graph_from_file_map(
        app_id="app", artifact_version_id="build", artifact_kind="app_bundle", file_map=files,
    )
    evidence = _build_evidence(
        graph=graph,
        provenance={
            "app_id": "app", "build_record_id": "build",
            "build_family": "app_bundle", "build_key": "app_bundle",
            "bundle_digest": f"sha256:{'a' * 64}",
        },
        file_map=files, health={}, raw_request="Update the first module.",
        selected_paths=["src/a.ts"],
    )

    assert evidence is not None
    assert evidence.files == ("src/a.ts", "src/b.ts", "src/c.ts", "src/d.ts")
    assert evidence.coverage_incomplete
