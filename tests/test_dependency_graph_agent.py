import pytest
import yaml

from mozaiksai.control_plane.implementations.dependency_graph_agent import _build_evidence
from mozaiksai.core.app_context.context_graph import build_context_graph_from_file_map
from mozaiksai.core.app_context.models import GraphEdgeType
from mozaiksai.core.runtime.app.page_schema import iter_page_module_endpoints, validate_page_schema


@pytest.mark.parametrize(("root", "relative_page"), [("", "ui/pages/orders.yaml"), ("app/", "ui/pages/orders/page.yml")])
def test_page_scope_projects_only_its_own_declared_module_calls(root, relative_page):
    page_path = root + relative_page
    module_path = root + "modules/orders/module.yaml"
    page = {
        "schema_version": "mozaiks.app_page.v1", "name": "orders", "route": "/orders", "title": "Orders",
        "meta": {"ask_context": [{"module": "orders", "action": "summary", "params": {"api_endpoint": "/api/modules/alerts/list"}}]},
        "page_type": "record_list", "layout": "full-width", "sections": [
            {"id": "heading", "primitive": "PageHeader", "config": {
                "title": "Orders", "subtitle": "The guide covers /api/modules/alerts/list",
                "actions": [{"action_type": "submit", "label": "Confirm", "href": "/api/modules/orders/confirm",
                             "payload": {"href": "/api/modules/alerts/list", "message": "https://outside.invalid/api/modules/alerts/list"}}],
            }},
            {"id": "totals", "primitive": "SummaryStrip", "config": {
                "api_endpoint": "/api/modules/orders/summary", "items": [{"label": "Total", "value_key": "total"}],
            }},
        ],
    }
    manifest = {"module": {"id": "orders"}, "actions": [{"id": "summary"}, {"id": "confirm"}]}
    other_root = "" if root else "app/"
    files = {page_path: yaml.safe_dump(page), module_path: yaml.safe_dump(manifest),
             root + "modules/alerts/module.yaml": "module: {id: alerts}\nactions: [{id: list}]",
             other_root + "modules/orders/module.yaml": yaml.safe_dump(manifest)}
    runtime = validate_page_schema(page, action_index={"orders": frozenset({"confirm", "summary"})})
    assert {(module, action) for _, module, action in iter_page_module_endpoints(runtime)} == {("orders", "confirm"), ("orders", "summary")}

    def scoped_calls():
        graph = build_context_graph_from_file_map(app_id="orders", artifact_version_id="original", artifact_kind="app_bundle", file_map=files)
        evidence = _build_evidence(graph=graph, provenance={
            "app_id": "orders", "build_record_id": "original", "build_family": "app_bundle", "build_key": "app_bundle",
            "bundle_digest": "sha256:" + "a" * 64,
        }, file_map=files, health={}, raw_request="Change what the order page displays.", selected_paths=[page_path])
        assert evidence is not None
        calls = {(edge.source_path, edge.target_path, edge.kind) for edge in evidence.dependencies}
        resolved = [e.metadata["action_ids"] for e in graph.edges if e.metadata.get("action_ids")]
        return calls, resolved

    expected = {(page_path, module_path, GraphEdgeType.CALLS)}
    assert scoped_calls() == (expected, [["confirm", "summary"]])
    # The duplicate action declaration is ambiguous. The other action still has one owner.
    files[module_path] = yaml.safe_dump({**manifest, "actions": [*manifest["actions"], {"id": "confirm"}]})
    assert scoped_calls() == (expected, [["summary"]])
    files[module_path] = "module: {id: orders}\nactions: [{id: undeclared_here}]"
    assert scoped_calls() == (set(), [])
    files[module_path] = "module: {id: orders}\nactions: []"
    assert scoped_calls() == (set(), [])
    files[module_path] = yaml.safe_dump(manifest)
    files[root + "modules/duplicate/module.yaml"] = files[module_path]
    assert scoped_calls() == (set(), [])
    del files[root + "modules/duplicate/module.yaml"]
    page["sections"][0]["config"]["actions"][0]["href"] = "https://outside.invalid/api/modules/orders/confirm"
    files[page_path] = yaml.safe_dump(page)
    assert scoped_calls() == (set(), [])  # An invalid external action cannot witness a runtime page call.


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
