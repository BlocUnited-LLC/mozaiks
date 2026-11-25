import json

import pytest
from pydantic import ValidationError

from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import scan_generated_bundle
from mozaiksai.core.app_context import (
    AppContextGraph,
    AppContextGraphEdge,
    AppContextGraphNode,
    AppContextStaleStatus,
    AppJourney,
    AppJourneyCatalog,
    ContextGraphHealthReport,
    load_app_journeys,
    select_affected_journeys,
)


def journey(name, *nodes):
    return AppJourney(
        journey_id=name,
        task=f"Complete {name}",
        starting_state="A signed-in project owner",
        success_criterion="The saved change is visible after a reload",
        graph_node_ids=nodes,
    )


def catalog(*journeys):
    return AppJourneyCatalog(app_id="projects", journeys=journeys)


def test_generated_bundle_accepts_the_app_owned_journey_catalog():
    case = catalog(journey("publish-project")).model_dump_json()
    manifest = {"app.json": json.dumps({"appId": "projects"})}
    assert scan_generated_bundle({**manifest, "config/journeys.json": case}) == []
    assert scan_generated_bundle(manifest) == []
    assert scan_generated_bundle({**manifest, "config/journeys-other.json": case})


def catalog_document(**overrides):
    return json.dumps({**catalog(journey("publish")).model_dump(), **overrides})


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        pytest.param("{", "Invalid JSON", id="malformed-json"),
        pytest.param(catalog_document(app_id="other"), "app_id must match", id="other-app"),
        pytest.param(catalog_document(schema_version="unknown"), "schema_version", id="unknown-schema"),
        pytest.param(
            catalog_document(journeys=[{**journey("publish").model_dump(), "start_route": "//outside"}]),
            "start_route", id="external-route",
        ),
        pytest.param(" " * (4 * 1024 * 1024 + 1), "4 MiB", id="file-limit"),
    ],
)
def test_generated_bundle_rejects_invalid_journey_catalogs(payload, message):
    errors = scan_generated_bundle({"app.json": '{"appId":"projects"}', "config/journeys.json": payload})
    assert any(error.startswith("config/journeys.json:") and message in error for error in errors)


def graph():
    return AppContextGraph(
        app_id="projects",
        graph_id="version-one",
        stale_status="current",
        nodes=[
            AppContextGraphNode(
                node_id=node_id, node_type=node_id.split(":")[0], stale_status="current"
            )
            for node_id in ("route:projects", "page:projects", "module:projects", "module:billing")
        ],
        edges=[
            AppContextGraphEdge(
                source_node_id="route:projects",
                target_node_id="page:projects",
                edge_type="renders",
                stale_status="current",
            ),
            AppContextGraphEdge(
                source_node_id="page:projects",
                target_node_id="module:projects",
                edge_type="calls",
                stale_status="current",
            ),
        ],
    )


def test_changed_module_selects_dependent_user_journey_without_billing():
    cases = catalog(
        journey("view-invoice", "module:billing"), journey("publish-project", "route:projects")
    )
    selection = select_affected_journeys(cases, graph=graph(), changed_node_ids=["module:projects"])
    assert selection.journey_ids == ("publish-project",)
    assert selection.reason == "impact"
    assert selection.graph_digest
    assert (
        select_affected_journeys(cases, graph=graph(), changed_node_ids=["module:projects"])
        == selection
    )


def test_complete_graph_with_uncovered_change_selects_no_journeys():
    cases = catalog(journey("view-invoice", "module:billing"))
    result = select_affected_journeys(cases, graph=graph(), changed_node_ids=["module:projects"])
    assert result.journey_ids == ()
    assert result.reason == "impact"


def test_first_run_missing_graph_and_stale_or_missing_bindings_keep_baseline():
    cases = catalog(journey("publish-project", "route:projects"))
    stale = graph().model_copy(update={"stale_status": AppContextStaleStatus.STALE})
    assert select_affected_journeys(cases, graph=graph()).reason == "baseline"
    assert (
        select_affected_journeys(cases, changed_node_ids=["module:projects"]).reason
        == "graph_unavailable"
    )
    for snapshot, seeds, declaration in [
        (stale, ["module:projects"], cases),
        (graph(), ["module:removed"], cases),
        (graph(), ["module:billing"], catalog(journey("publish-project", "route:removed"))),
    ]:
        result = select_affected_journeys(declaration, graph=snapshot, changed_node_ids=seeds)
        assert result.reason == "graph_incomplete"
        assert result.journey_ids == ("publish-project",)


def test_graph_scope_mismatch_is_an_error_even_for_baseline():
    with pytest.raises(ValueError, match="different apps"):
        select_affected_journeys(
            catalog(journey("view-invoice", "module:billing")),
            graph=graph().model_copy(update={"app_id": "another-app"}),
        )


def test_degraded_and_depth_limited_queries_keep_all_journeys():
    cases = catalog(journey("view-invoice", "module:billing"))
    snapshot = graph()
    snapshot.nodes[2].metadata["parser_authority"] = "degraded"
    assert (
        select_affected_journeys(cases, graph=snapshot, changed_node_ids=["module:projects"]).reason
        == "graph_incomplete"
    )

    snapshot = graph()
    target_id = "module:projects"
    for index in range(10):
        node_id = f"module:dependency-{index}"
        snapshot.nodes.append(
            AppContextGraphNode(node_id=node_id, node_type="module", stale_status="current")
        )
        snapshot.edges.append(
            AppContextGraphEdge(
                source_node_id=node_id,
                target_node_id=target_id,
                edge_type="depends_on",
                stale_status="current",
            )
        )
        target_id = node_id
    assert select_affected_journeys(
        cases, graph=snapshot, changed_node_ids=["module:projects"]
    ).journey_ids == ("view-invoice",)


def test_app_root_loader_preserves_order_and_rejects_invalid_declarations(tmp_path):
    assert load_app_journeys(tmp_path) is None
    (tmp_path / "config").mkdir()
    path = tmp_path / "config" / "journeys.json"
    one = journey("publish-project", "route:projects")
    two = journey("view-invoice", "module:billing")
    expected = catalog(one, two)
    path.write_text(expected.model_dump_json(), encoding="utf-8")
    assert load_app_journeys(tmp_path) == expected
    for payload in (
        {**expected.model_dump(), "journeys": [one.model_dump(), one.model_dump()]},
        {**expected.model_dump(), "schema_version": "invalid"},
        {**expected.model_dump(), "journeys": [{**one.model_dump(), "task": " "}]},
        {
            **expected.model_dump(),
            "journeys": [{**one.model_dump(), "graph_node_ids": ["page:p", "page:p"]}],
        },
        {**expected.model_dump(), "executable": "unregistered"},
    ):
        path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(ValidationError):
            load_app_journeys(tmp_path)


@pytest.mark.parametrize("route", ["/", "/projects/", "/projects/:projectId/tasks", "/my-app/new"])
def test_app_routes_are_usable_as_study_starting_points(route):
    case = journey("publish", "route:projects")
    assert (
        AppJourney.model_validate({**case.model_dump(), "start_route": route}).start_route == route
    )


@pytest.mark.parametrize(
    "route",
    [
        "https://example.com",
        "//example.com",
        "/a/../b",
        "/a?x",
        "/a#b",
        "/a\\b",
        "/%2fexample.com",
        "/a\nb",
        " /a",
        "/a\r",
    ],
)
def test_start_routes_cannot_change_the_app_origin_or_add_query_or_fragment(route):
    with pytest.raises(ValidationError):
        AppJourney.model_validate({**journey("publish").model_dump(), "start_route": route})


def test_unbound_journey_is_always_selected():
    cases = catalog(journey("smoke"), journey("view-invoice", "module:billing"))
    result = select_affected_journeys(cases, graph=graph(), changed_node_ids=["module:projects"])
    assert result.journey_ids == ("smoke",)


def test_functional_bundle_acceptance_checks_optional_journey_route():
    from mozaiksai.core.validation.functional_generated_app import scan_functional_generated_app

    case = journey("publish-project", "route:projects")
    doc = catalog(case).model_dump(mode="json")
    doc["journeys"][0]["start_route"] = "/projects"
    files = {
        "config/journeys.json": json.dumps(doc),
        "ui/route_manifest.json": json.dumps(
            {"pages": [{"path": "/projects", "component": "AdminPortal"}]}
        ),
    }
    assert not scan_functional_generated_app(files)
    files["ui/route_manifest.json"] = '{"pages": []}'
    assert [issue.code for issue in scan_functional_generated_app(files)] == [
        "MISSING_JOURNEY_ROUTE"
    ]
    files["config/journeys.json"] = "{}"
    assert [issue.code for issue in scan_functional_generated_app(files)] == [
        "INVALID_JOURNEY_CATALOG"
    ]


def test_incomplete_scan_or_stale_relationship_selects_the_entire_catalog():
    cases = catalog(journey("view-invoice", "module:billing"))
    snapshot = graph()
    health = ContextGraphHealthReport(
        status="warning", warnings=["context_graph_file_or_char_limit_reached"]
    )
    assert (
        select_affected_journeys(
            cases, graph=snapshot, graph_health=health, changed_node_ids=["module:projects"]
        ).reason
        == "graph_incomplete"
    )
    snapshot.edges[0].stale_status = AppContextStaleStatus.PARTIALLY_STALE
    assert select_affected_journeys(
        cases, graph=snapshot, changed_node_ids=["module:projects"]
    ).journey_ids == ("view-invoice",)
