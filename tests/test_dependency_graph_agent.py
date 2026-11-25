from mozaiksai.control_plane.implementations.dependency_graph_agent import _build_evidence
from mozaiksai.core.app_context.context_graph import build_context_graph_from_file_map


def test_named_reaction_projects_only_its_saved_capability_and_exact_trigger():
    reaction = "modules/orders/contracts/reactions.yaml"
    manifest = "modules/review/module.yaml"
    trigger = "workflows/Review/orchestrator.yaml"
    files = {
        reaction: (
            "reactions:\n- event_type: domain.orders.ready\n"
            "  target: {kind: capability, capability_id: reviews.open}\n"
        ),
        manifest: (
            "module: {id: review}\ncapabilities:\n"
            "- {kind: workflow, capability_id: reviews.open, target: Review}\n"
        ),
        trigger: (
            "workflow_name: Review\ntriggers:\n"
            "- {type: event, event: domain.orders.ready, capability_id: reviews.open}\n"
        ),
        "workflows/Archive/orchestrator.yaml": (
            "workflow_name: Archive\ntriggers:\n"
            "- {type: event, event: domain.orders.archived, capability_id: reviews.open}\n"
        ),
    }

    def dependencies(selected_paths):
        graph = build_context_graph_from_file_map(
            app_id="orders", artifact_version_id="b1", artifact_kind="app_bundle", file_map=files,
        )
        evidence = _build_evidence(
            graph=graph, provenance={"app_id": "orders", "build_record_id": "b1",
                                     "build_family": "app_bundle", "build_key": "orders",
                                     "bundle_digest": f"sha256:{'a' * 64}"},
            file_map=files, health={}, raw_request="Change what happens when orders are ready.",
            selected_paths=selected_paths,
        )
        assert evidence is not None
        return {(edge.source_path, edge.target_path) for edge in evidence.dependencies}

    expected = {(reaction, manifest), (reaction, trigger)}
    assert dependencies([]) == expected
    assert dependencies([reaction]) == expected
    files["workflows/Second/orchestrator.yaml"] = files[trigger].replace("name: Review", "name: Second")
    assert dependencies([reaction]) == {(reaction, manifest)}
    del files[manifest]
    assert dependencies([reaction]) == set()


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
