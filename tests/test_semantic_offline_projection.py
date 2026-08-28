"""ADR 0007 Slice 3 offline projection and archetype-corpus proof."""

from __future__ import annotations

import builtins
import copy
import json
import os
import re
import sys
from pathlib import Path

import pytest
import yaml

from mozaiksai.core.app_context.models import AppContextVersion, OwnershipBoundary
from mozaiksai.core.runtime.app.module_loader import (
    ModuleDefinition,
    ModuleEventsManifest,
    ModuleReactionsManifest,
)
from mozaiksai.core.runtime.app.page_schema import AppPageSchema
from mozaiksai.core.runtime.app.subscriptions_loader import SubscriptionsConfig
from mozaiksai.core.semantics.canonical import canonical_digest, canonical_json
from mozaiksai.core.semantics.graph import SemanticEdgeKind, SemanticNodeKind
from mozaiksai.core.semantics.offline_projection import (
    ProjectionDisposition,
    ProjectionError,
    ProjectionGapKind,
    extract_semantic_facts,
    project_semantic_graph,
)
from mozaiksai.core.semantics.refs import ExecutionAccessScopeRef
from mozaiksai.core.session.build_context_schema import validate_pack_context

ROOT = Path(__file__).resolve().parents[1]
SCOPE = ExecutionAccessScopeRef(tenant_id="tenant-1", workspace_id="workspace-1")


def _corpus_source() -> dict:
    return {
        "source_scopes": {
            "app_generator": SCOPE.model_dump(mode="json"),
            "agent_generator": SCOPE.model_dump(mode="json"),
        },
        "app_build_plan": {
            "agent_message": "Deterministic corpus plan",
            "pages": [
                {"name": "Reports", "route": "/reports"},
                {"name": "Billing", "route": "/billing"},
            ],
            "surface_map": {
                "surfaces": [
                    {
                        "surface_id": "reports",
                        "surface_kind": "module",
                        "owner": "app",
                        "owned_mutations": ["export_report", "view_report"],
                        "events_emitted": ["domain.reports.generated"],
                        "dependencies": [],
                    },
                    {
                        "surface_id": "billing",
                        "surface_kind": "module",
                        "owner": "app",
                        "owned_mutations": [],
                        "events_emitted": [],
                        "dependencies": ["reports"],
                    },
                ]
            },
            "capability_packs": [
                {
                    "capability_pack_id": "reporting_pack",
                    "surface_id": "reports",
                    "label": "Reporting",
                }
            ],
            "event_flows": [
                {
                    "event_type": "domain.reports.generated",
                    "producer_pack_id": "reports",
                    "subscriber_intents": ["notify"],
                }
            ],
            "workflow_touchpoints": [
                {
                    "page_name": "Reports",
                    "workflow_id": "report_builder",
                    "action_id": "launch",
                    "placement": "page",
                }
            ],
            "data_contract": {
                "version": "1",
                "surfaces": [
                    {
                        "surface_id": "reports",
                        "surface_kind": "module",
                        "collections": [
                            {
                                "name": "reports",
                                "scope": "app",
                                "fields": [{"name": "status", "type": "string"}],
                            }
                        ],
                    }
                ],
                "aliases": [
                    {
                        "alias": "reports.current",
                        "collection": "reports",
                        "owner_module": "reports",
                    }
                ],
            },
            "deployment_targets": [
                {
                    "target_id": "local_container",
                    "target_kind": "container",
                    "deployment_profile": "local",
                }
            ],
            "generation_order": ["modules", "pages"],
        },
        "app_schema": {
            "pages": [
                {
                    "name": "Reports",
                    "route": "/reports",
                    "sections": [
                        {
                            "id": "report_table",
                            "primitive": "DataTable",
                            "config": {
                                "actions": [
                                    {
                                        "id": "export",
                                        "api_endpoint": "/api/modules/reports/export_report",
                                    }
                                ]
                            },
                        }
                    ],
                }
            ],
            "theme_config_patch": {"theme": {"mode": "dark"}},
        },
        "design_docs": {
            "agent_message": "Design corpus",
            "experience_spec": {
                "navigation_model": "sidebar",
                "pages": [{"name": "Reports", "route": "/reports", "sections": []}],
            },
            "surface_map": {
                "surfaces": [
                    {
                        "surface_id": "reports",
                        "surface_kind": "module",
                        "owner": "app",
                        "owned_mutations": [],
                        "events_emitted": [],
                        "dependencies": [],
                    }
                ]
            },
        },
        "modules": [
            {
                "manifest": {
                    "schema_version": "mozaiks.module.v1",
                    "module": {"id": "reports", "display_name": "Reports"},
                    "actions": [
                        {
                            "id": "export_report",
                            "emits": ["domain.reports.generated"],
                            "entitlement_gate": "reports.export",
                        },
                        {"id": "view_report", "entitlement_gate": "reports.view"},
                    ],
                    "permissions": [{"id": "reports.read", "description": "Read reports"}],
                    "capabilities": [
                        {"capability_id": "reports.export"},
                        {"capability_id": "reports.view"},
                    ],
                },
                "events": {"events": [{"type": "domain.reports.generated"}]},
                "reactions": {
                    "reactions": [
                        {
                            "id": "notify_report_ready",
                            "event_type": "domain.reports.generated",
                        }
                    ]
                },
                "notifications": {
                    "notifications": [
                        {
                            "id": "report_ready",
                            "event_type": "domain.reports.generated",
                            "title": "Report ready",
                            "body": "Your report is ready.",
                        }
                    ]
                },
            }
        ],
        "subscriptions": {
            "schema_version": "mozaiks.subscriptions.v1",
            "plans": [
                {
                    "plan_id": "pro",
                    "label": "Pro",
                    "capabilities": ["reports.export", "reports.view"],
                    "usage_limits": [
                        {
                            "meter_id": "report_exports",
                            "monthly_limit": 100,
                            "unit": "exports",
                        }
                    ],
                }
            ],
            "top_up_products": [
                {"product_id": "extra_exports", "label": "Extra exports", "token_amount": 100}
            ],
        },
        "subscription_contract": {
            "contract_required": True,
            "rationale": "Reports are metered",
            "subscription_config_file": {
                "schema_version": "mozaiks.subscriptions.v1",
                "plans": [
                    {
                        "plan_id": "pro",
                        "label": "Pro",
                        "capabilities": ["reports.view"],
                        "usage_limits": [],
                    }
                ],
            },
        },
        "agent_workflows": [
            {
                "workflow_name": "report_builder",
                "agent_message": "Generated.",
                "pattern_id": 1,
                "pattern_name": "Pipeline",
                "files": [
                    {
                        "filename": "orchestrator.yaml",
                        "content": """workflow_name: report_builder
max_turns: 4
human_in_the_loop: false
workflow_startup_mode: BackendOnly
orchestration_pattern: ag2_network
initial_agent: ReportAgent
initial_message: Build reports.
triggers:
  - type: event
    event: domain.reports.generated
    description: Resume after generation
""",
                    }
                ],
            }
        ],
        "ownership_evidence": {
            "mode": "hybrid",
            "ownership_boundaries": [
                {
                    "path_or_artifact": "app/modules/reports",
                    "ownership": "generated_overlay",
                    "source_ref": "source-1",
                },
                {
                    "path_or_artifact": "existing/admin.py",
                    "ownership": "external_system",
                    "source_ref": "source-1",
                },
            ],
        },
        "build_context": {
            "context_id": "AppGenerator",
            "assets": [{"path": "file_contracts.yaml", "kind": "contract"}],
        },
    }


def _project(source: dict | None = None, *, scope: ExecutionAccessScopeRef = SCOPE):
    return project_semantic_graph(
        source or _corpus_source(), graph_id="slice-3-corpus", version=1, scope=scope
    )


def test_archetype_corpus_projects_complete_relationship_spine() -> None:
    result = _project()
    kinds = {node.kind for node in result.graph.nodes}
    assert {
        SemanticNodeKind.SURFACE,
        SemanticNodeKind.MODULE,
        SemanticNodeKind.PAGE,
        SemanticNodeKind.SECTION,
        SemanticNodeKind.ACTION,
        SemanticNodeKind.CAPABILITY,
        SemanticNodeKind.PERMISSION,
        SemanticNodeKind.EVENT,
        SemanticNodeKind.REACTION,
        SemanticNodeKind.NOTIFICATION,
        SemanticNodeKind.DATA_COLLECTION,
        SemanticNodeKind.DATA_ALIAS,
        SemanticNodeKind.WORKFLOW,
        SemanticNodeKind.TRIGGER,
        SemanticNodeKind.PLAN,
        SemanticNodeKind.PRODUCT,
        SemanticNodeKind.METER,
        SemanticNodeKind.LIMIT,
        SemanticNodeKind.DEPLOYMENT_TARGET,
    } <= kinds
    edge_kinds = {edge.kind for edge in result.graph.edges}
    assert {
        SemanticEdgeKind.DECLARES,
        SemanticEdgeKind.EMITS,
        SemanticEdgeKind.CONSUMES,
        SemanticEdgeKind.RENDERS,
        SemanticEdgeKind.BINDS,
        SemanticEdgeKind.DEPENDS_ON,
        SemanticEdgeKind.GATES,
        SemanticEdgeKind.OWNS,
    } <= edge_kinds
    assert result.source_facts == result.represented_facts
    assert extract_semantic_facts(result.graph) == result.represented_facts


def test_independent_source_expectations_equal_every_graph_fact() -> None:
    result = _project()

    def node_id(kind: str, identity: str) -> str:
        slug = re.sub(r"[^a-z0-9_]+", "_", identity.strip().lower()).strip("_")
        return f"mozaiks.{kind}.{slug}_{canonical_digest(identity)[:12]}"

    specs = {
        "surface_reports": ("surface", "reports", ()),
        "surface_billing": ("surface", "billing", ()),
        "module_reports": ("module", "reports", ()),
        "module_billing": ("module", "billing", ()),
        "export": ("action", "reports_export_report", ()),
        "view": ("action", "reports_view_report", ()),
        "page_reports": ("page", "Reports", ()),
        "page_billing": ("page", "Billing", ()),
        "section": ("section", "Reports_report_table", ()),
        "event": ("event", "domain.reports.generated", (("event", "domain.reports.generated"),)),
        "cap_export": ("capability", "reports.export", (("capability", "reports.export"),)),
        "cap_view": ("capability", "reports.view", (("capability", "reports.view"),)),
        "permission": ("permission", "reports_reports.read", ()),
        "reaction": ("reaction", "reports_notify_report_ready", ()),
        "notification": ("notification", "reports_report_ready", ()),
        "collection": ("data_collection", "reports_reports", ()),
        "alias": ("data_alias", "reports.current", ()),
        "workflow": ("workflow", "report_builder", ()),
        "trigger": ("trigger", "domain.reports.generated", ()),
        "plan": ("plan", "pro", ()),
        "product": ("product", "extra_exports", ()),
        "meter": ("meter", "report_exports", ()),
        "limit": ("limit", "pro_report_exports", ()),
        "deployment": ("deployment_target", "local_container", ()),
    }
    ids = {key: node_id(kind, identity) for key, (kind, identity, _refs) in specs.items()}
    expected_nodes = {(ids[key], kind, refs) for key, (kind, _identity, refs) in specs.items()}
    expected_edges = {
        (kind, ids[source], ids[target], None)
        for kind, source, target in {
            ("owns", "surface_reports", "module_reports"),
            ("owns", "surface_billing", "module_billing"),
            ("depends_on", "surface_billing", "surface_reports"),
            ("declares", "module_reports", "export"),
            ("declares", "module_reports", "view"),
            ("declares", "module_reports", "cap_export"),
            ("declares", "module_reports", "cap_view"),
            ("declares", "module_reports", "permission"),
            ("declares", "module_reports", "reaction"),
            ("declares", "module_reports", "notification"),
            ("emits", "module_reports", "event"),
            ("emits", "export", "event"),
            ("consumes", "reaction", "event"),
            ("consumes", "notification", "event"),
            ("consumes", "trigger", "event"),
            ("gates", "cap_export", "export"),
            ("gates", "cap_view", "view"),
            ("gates", "plan", "cap_export"),
            ("gates", "plan", "cap_view"),
            ("gates", "plan", "limit"),
            ("binds", "page_reports", "export"),
            ("binds", "page_reports", "workflow"),
            ("binds", "trigger", "workflow"),
            ("binds", "alias", "collection"),
            ("binds", "limit", "meter"),
            ("owns", "module_reports", "collection"),
            ("renders", "page_reports", "section"),
        }
    }
    assert set(result.represented_facts.nodes) == expected_nodes
    assert set(result.represented_facts.edges) == expected_edges


def test_projection_is_byte_identical_order_independent_and_input_immutable() -> None:
    source = _corpus_source()
    before = copy.deepcopy(source)
    first = _project(source)
    reordered = copy.deepcopy(source)
    reordered["app_build_plan"]["pages"].reverse()
    reordered["app_build_plan"]["surface_map"]["surfaces"].reverse()
    reordered["modules"][0]["manifest"]["actions"].reverse()
    reordered = dict(reversed(list(reordered.items())))
    second = _project(reordered)
    third = _project(source)
    assert source == before
    assert first.graph.graph_digest == second.graph.graph_digest == third.graph.graph_digest
    assert canonical_json(first.graph.canonical_payload()).encode("ascii") == canonical_json(
        second.graph.canonical_payload()
    ).encode("ascii")
    assert first.represented_facts == second.represented_facts
    assert first.coverage == second.coverage
    assert first.gaps == second.gaps
    assert any(gap.source_path == "app_build_plan.pages" for gap in first.gaps)


def test_machine_readable_coverage_classifies_every_source_leaf() -> None:
    source = _corpus_source()
    result = _project(source)
    canonical_source = copy.deepcopy(source)
    canonical_source["app_build_plan"]["surface_map"]["surfaces"].sort(
        key=lambda item: item["surface_id"]
    )
    canonical_source["modules"].sort(key=lambda item: item["manifest"]["module"]["id"])
    for module in canonical_source["modules"]:
        module["manifest"]["actions"].sort(key=lambda item: item["id"])
        module["manifest"]["capabilities"].sort(key=lambda item: item["capability_id"])
        module["manifest"]["permissions"].sort(key=lambda item: item["id"])
    leaf_paths = {path for path, _ in _walk_leaves(canonical_source)}
    coverage_paths = {row.source_path for row in result.coverage}
    assert leaf_paths <= coverage_paths
    assert len(result.coverage) == len(coverage_paths)
    assert {row.disposition for row in result.coverage} == {
        ProjectionDisposition.PROJECTED,
        ProjectionDisposition.DELIBERATELY_NON_SEMANTIC,
        ProjectionDisposition.DEFERRED,
    }
    deferred = {
        row.source_path
        for row in result.coverage
        if row.disposition is ProjectionDisposition.DEFERRED
    }
    assert deferred == {gap.source_path for gap in result.gaps}
    for path in coverage_paths:
        _lookup_path(canonical_source, path)
    assert (
        json.loads(result.model_dump_json())["schema_version"] == "mozaiks.semantic_projection.v1"
    )


def _walk_leaves(value, path=""):
    if isinstance(value, dict):
        if not value:
            yield path, value
        for key in sorted(value):
            yield from _walk_leaves(value[key], f"{path}.{key}" if path else key)
    elif isinstance(value, list):
        if not value:
            yield path, value
        for index, item in enumerate(value):
            yield from _walk_leaves(item, f"{path}[{index}]")
    else:
        yield path, value


def _lookup_path(value, path):
    import re

    for name, index in re.findall(r"([^.\[\]]+)|\[(\d+)\]", path):
        value = value[int(index)] if index else value[name]
    return value


@pytest.mark.parametrize("field", ["event", "capability"])
def test_unknown_taxonomy_references_fail_closed(field: str) -> None:
    source = _corpus_source()
    if field == "event":
        source["modules"][0]["events"]["events"][0]["type"] = "domain.unknown.created"
    else:
        source["modules"][0]["manifest"]["capabilities"][0]["capability_id"] = "unknown.capability"
    with pytest.raises(
        ProjectionError, match="absent from the pinned taxonomy registry"
    ) as exc_info:
        _project(source)
    assert exc_info.value.gaps[0].kind is ProjectionGapKind.MISSING


def test_invalid_taxonomy_grammar_is_distinct_from_missing_registry_entry() -> None:
    source = _corpus_source()
    source["modules"][0]["manifest"]["capabilities"][0]["capability_id"] = "INVALID CAPABILITY"
    with pytest.raises(ProjectionError, match="invalid capability taxonomy identifier") as exc_info:
        _project(source)
    assert exc_info.value.gaps[0].kind is ProjectionGapKind.UNSUPPORTED


def test_recorded_appbuildplan_unknown_semantics_are_not_silently_omitted() -> None:
    recorded = json.loads(
        (ROOT / "tests/fixtures/appplan_persistent_projects_output.json").read_text(
            encoding="utf-8"
        )
    )
    with pytest.raises(ProjectionError) as exc_info:
        project_semantic_graph(recorded, graph_id="recorded-projects", version=1, scope=SCOPE)
    assert exc_info.value.gaps[0].kind is ProjectionGapKind.MISSING
    assert "domain.projects" in exc_info.value.gaps[0].reason


def test_existing_recorded_saas_fixture_projects_with_explicit_gaps() -> None:
    recorded = json.loads(
        (ROOT / "tests/fixtures/appplan_saas_entitlement_dispatch_output.json").read_text(
            encoding="utf-8"
        )
    )
    result = project_semantic_graph(recorded, graph_id="recorded-saas", version=1, scope=SCOPE)
    assert result.graph.nodes
    assert result.gaps
    assert all(gap.kind is ProjectionGapKind.UNSUPPORTED for gap in result.gaps)
    assert all(row.source_file != "unknown" for row in result.coverage)


def test_current_runtime_models_and_agentgenerator_bundle_shape_project() -> None:
    module = ModuleDefinition.model_validate(
        {
            "schema_version": "mozaiks.module.v1",
            "module": {"id": "reports", "handler": "backend.handler:Handler"},
            "actions": [
                {
                    "id": "export_report",
                    "description": "Export a report.",
                    "handler_method": "export_report",
                    "emits": ["domain.reports.generated"],
                    "entitlement_gate": "reports.export",
                }
            ],
            "capabilities": [
                {
                    "capability_id": "reports.export",
                    "kind": "action",
                    "target": "export_report",
                    "title": "Export reports",
                }
            ],
        }
    )
    events = ModuleEventsManifest.model_validate(
        {
            "events": [
                {
                    "type": "domain.reports.generated",
                    "version": 1,
                    "producer": "reports",
                }
            ]
        }
    )
    reactions = ModuleReactionsManifest.model_validate(
        {
            "reactions": [
                {
                    "id": "index_report",
                    "event_type": "domain.reports.generated",
                    "target": {"kind": "handler", "handler_method": "index_report"},
                }
            ]
        }
    )
    page = AppPageSchema.model_validate(
        {
            "schema_version": "mozaiks.app_page.v1",
            "name": "Reports",
            "route": "/reports",
            "title": "Reports",
            "page_type": "record_list",
            "layout": "full-width",
            "sections": [
                {
                    "id": "header",
                    "primitive": "PageHeader",
                    "config": {
                        "title": "Reports",
                        "actions": [
                            {
                                "id": "export",
                                "label": "Export",
                                "action_type": "submit",
                                "href": "/api/modules/reports/export_report",
                            }
                        ],
                    },
                }
            ],
        }
    )
    subscriptions = SubscriptionsConfig.model_validate(
        {
            "schema_version": "mozaiks.subscriptions.v1",
            "label": "Reports plans",
            "default_plan_id": "pro",
            "plans": [
                {
                    "plan_id": "pro",
                    "label": "Pro",
                    "capabilities": ["reports.export"],
                    "usage_limits": [
                        {"meter_id": "report_exports", "unit": "requests", "monthly_limit": 100}
                    ],
                }
            ],
        }
    )
    app_context = AppContextVersion(
        context_version_id="context-1",
        app_id="reports-app",
        mode="hybrid",
        ownership_boundaries=[
            OwnershipBoundary(
                path_or_artifact="app/modules/reports",
                ownership="generated_overlay",
                allowed_operations=["generate_overlay"],
            )
        ],
    )
    workflow_bundle = {
        "workflow_name": "report_builder",
        "agent_message": "Generated.",
        "pattern_id": 1,
        "pattern_name": "Pipeline",
        "files": [
            {
                "filename": "orchestrator.yaml",
                "content": """workflow_name: report_builder
max_turns: 4
human_in_the_loop: false
workflow_startup_mode: BackendOnly
orchestration_pattern: ag2_network
initial_agent: ReportAgent
initial_message: Build reports.
triggers:
  - type: event
    event: domain.reports.generated
    capability_id: reports.export
""",
            },
            {"filename": "agents.yaml", "content": "agents: []\n"},
        ],
    }
    module_bundle = {"manifest": module, "events": events, "reactions": reactions}
    source = {
        "modules": [module_bundle],
        "pages": [page],
        "subscriptions": subscriptions,
        "agent_workflows": [workflow_bundle],
        "app_context": app_context,
    }
    result = project_semantic_graph(
        source,
        graph_id="current-contracts",
        version=1,
        scope=SCOPE,
    )
    assert result.source_facts == extract_semantic_facts(result.graph)
    assert any(gap.source_path == "agent_workflows[0].files" for gap in result.gaps)
    assert any(gap.source_path.endswith("ownership_boundaries[0].ownership") for gap in result.gaps)
    mapped = dict(source)
    mapped["modules"] = {"reports": module_bundle}
    mapped["agent_workflows"] = {"report_builder": workflow_bundle, "_meta": {"count": 1}}
    mapped_result = project_semantic_graph(
        mapped,
        graph_id="current-contracts",
        version=1,
        scope=SCOPE,
    )
    assert mapped_result.graph.graph_digest == result.graph.graph_digest
    assert all(row.source_file != "unknown" for row in mapped_result.coverage)


def test_projection_field_access_is_pinned_to_current_structured_outputs() -> None:
    app = yaml.safe_load(
        (ROOT / "factory_app/workflows/AppGenerator/structured_outputs.yaml").read_text(
            encoding="utf-8"
        )
    )["models"]
    assert {
        "surface_map",
        "pages",
        "entities",
        "event_flows",
        "workflow_touchpoints",
        "data_contract",
        "deployment_targets",
    } <= set(app["AppBuildPlan"]["fields"])
    assert {"pages", "data_contract", "custom_route_bundle"} <= set(
        app["AppSchemaOutput"]["fields"]
    )

    design = yaml.safe_load(
        (ROOT / "factory_app/workflows/DesignDocs/structured_outputs.yaml").read_text(
            encoding="utf-8"
        )
    )["models"]["DesignDocsBundle"]["fields"]
    assert {"experience_spec", "surface_map", "data_contract"} <= set(design)

    subscription = yaml.safe_load(
        (
            ROOT / "factory_app/workflows/SubscriptionContractDesigner/structured_outputs.yaml"
        ).read_text(encoding="utf-8")
    )["models"]["SubscriptionContractOutput"]["fields"]
    assert "subscription_config_file" in subscription

    agent = yaml.safe_load(
        (ROOT / "factory_app/workflows/AgentGenerator/structured_outputs.yaml").read_text(
            encoding="utf-8"
        )
    )["models"]
    assert set(agent["WorkflowBundleBuilderOutput"]["fields"]) >= {
        "workflow_name",
        "files",
    }
    assert "triggers" not in agent["WorkflowBundleBuilderOutput"]["fields"]
    assert "triggers" in agent["OrchestrationConfigOutput"]["fields"]


def test_committed_design_docs_subscription_build_context_and_route_sources() -> None:
    from tests.test_design_docs_bundle_persistence import _bundle
    from tests.test_subscription_contract_designer import _sample_contract

    design_result = project_semantic_graph(
        {"DesignDocsBundle": _bundle()},
        graph_id="recorded-design-docs",
        version=1,
        scope=SCOPE,
    )
    assert design_result.graph.nodes
    assert design_result.source_facts == design_result.represented_facts

    with pytest.raises(ProjectionError) as exc_info:
        project_semantic_graph(
            {"SubscriptionContractOutput": _sample_contract()},
            graph_id="recorded-subscription",
            version=1,
            scope=SCOPE,
        )
    assert exc_info.value.gaps[0].kind is ProjectionGapKind.MISSING
    assert "reports.generate" in exc_info.value.gaps[0].reason

    build_context = yaml.safe_load(
        (ROOT / "factory_app/build_context/AppGenerator/context.yaml").read_text(encoding="utf-8")
    )
    assert validate_pack_context(build_context).valid
    source = _corpus_source()
    source["build_context"] = build_context
    context_result = _project(source)
    assert any(gap.source_path.startswith("build_context.assets") for gap in context_result.gaps)

    route_manifest = json.loads(
        (
            ROOT / "tests/fixtures/community_packs/greetings/templates/ui/route_manifest.json"
        ).read_text(encoding="utf-8")
    )
    route_result = project_semantic_graph(
        {"route_manifest": route_manifest},
        graph_id="recorded-route-manifest",
        version=1,
        scope=SCOPE,
    )
    assert {node.kind for node in route_result.graph.nodes} == {SemanticNodeKind.PAGE}


def test_incomplete_required_and_contradictory_duplicate_inputs_fail_closed() -> None:
    missing = {"modules": [{"manifest": {"actions": []}}]}
    with pytest.raises(ProjectionError, match="module id is required"):
        _project(missing)

    duplicate = _corpus_source()
    duplicate["app_build_plan"]["pages"].append(
        copy.deepcopy(duplicate["app_build_plan"]["pages"][0])
    )
    with pytest.raises(ProjectionError, match="duplicate semantic identity"):
        _project(duplicate)

    conflicting = _corpus_source()
    conflicting["app_schema"]["pages"][0]["route"] = "/different-reports"
    with pytest.raises(ProjectionError) as exc_info:
        _project(conflicting)
    assert exc_info.value.gaps[0].kind is ProjectionGapKind.CONTRADICTORY
    assert "conflicting page route" in exc_info.value.gaps[0].reason


@pytest.mark.parametrize(
    "foreign_scope",
    [
        ExecutionAccessScopeRef(tenant_id="tenant-2", workspace_id="workspace-1"),
        ExecutionAccessScopeRef(tenant_id="tenant-1", workspace_id="workspace-2"),
    ],
)
def test_cross_tenant_or_workspace_composition_fails_closed(foreign_scope) -> None:
    source = _corpus_source()
    source["source_scopes"]["agent_generator"] = foreign_scope.model_dump(mode="json")
    with pytest.raises(ProjectionError, match="cross-tenant/workspace"):
        _project(source)


def test_brownfield_and_hybrid_require_ownership_evidence() -> None:
    source = _corpus_source()
    source["ownership_evidence"]["ownership_boundaries"] = []
    with pytest.raises(ProjectionError, match="requires AppContextVersion ownership boundaries"):
        _project(source)


def test_closure_does_not_invent_page_actions_events_or_capabilities() -> None:
    source = _corpus_source()
    source["modules"][0]["manifest"]["actions"] = []
    source["app_build_plan"]["surface_map"]["surfaces"][0]["owned_mutations"] = []
    with pytest.raises(ProjectionError, match="does not resolve to a declared semantic node"):
        _project(source)

    source = _corpus_source()
    source["modules"][0]["events"]["events"] = []
    source["modules"][0]["manifest"]["actions"][0]["emits"] = []
    source["app_build_plan"]["surface_map"]["surfaces"][0]["events_emitted"] = []
    source["app_build_plan"]["event_flows"] = []
    with pytest.raises(ProjectionError, match="does not resolve to a declared semantic node"):
        _project(source)

    source = _corpus_source()
    source["modules"][0]["manifest"]["capabilities"] = []
    source["subscriptions"]["plans"][0]["capabilities"] = []
    source["subscription_contract"]["subscription_config_file"]["plans"][0]["capabilities"] = []
    with pytest.raises(ProjectionError, match="does not resolve to a declared semantic node"):
        _project(source)


def test_unknown_root_and_synthetic_slice3_envelopes_fail_closed() -> None:
    with pytest.raises(ProjectionError) as exc_info:
        _project({"recorded_artifacts": {"pages": []}, "pages": [{"name": "Home"}]})
    assert exc_info.value.gaps[0].kind is ProjectionGapKind.UNSUPPORTED


def test_unclassified_field_is_ambiguous_instead_of_generic_deferred() -> None:
    source = {"pages": [{"name": "Home", "capabilty_id": "typo.value"}]}
    result = _project(source)
    gap = next(gap for gap in result.gaps if gap.source_path.endswith("capabilty_id"))
    assert gap.kind is ProjectionGapKind.AMBIGUOUS
    assert "typo" in gap.reason


def test_pre_app_scope_and_unowned_evidence_never_fabricate_identity_or_control() -> None:
    pre_app = ExecutionAccessScopeRef(tenant_id="tenant-1", pre_app_scope_id="creation-1")
    source = _corpus_source()
    source["source_scopes"] = {"app_generator": pre_app.model_dump(mode="json")}
    first = _project(source, scope=pre_app)
    source["ownership_evidence"]["ownership_boundaries"][1]["path_or_artifact"] = (
        "unrelated/system/root"
    )
    second = _project(source, scope=pre_app)
    assert first.graph.scope == pre_app
    assert "app_id" not in first.graph.scope.model_dump(mode="json")
    assert first.graph.graph_digest == second.graph.graph_digest


def test_semantic_descriptions_pricing_and_data_shapes_are_typed_gaps() -> None:
    result = _project()
    gap_paths = {gap.source_path for gap in result.gaps}
    assert "subscription_contract.rationale" in gap_paths
    assert "app_build_plan.data_contract.surfaces[0].collections[0].fields[0].type" in gap_paths
    assert "subscriptions.plans[0].usage_limits[0].monthly_limit" not in gap_paths
    deferred = {
        row.source_path
        for row in result.coverage
        if row.disposition is ProjectionDisposition.DEFERRED
    }
    assert gap_paths == deferred


def test_projection_has_no_process_or_external_side_effects(monkeypatch) -> None:
    cwd = Path.cwd()
    environment = dict(os.environ)
    modules = set(sys.modules)
    monkeypatch.setattr("socket.socket.connect", lambda *_args, **_kwargs: pytest.fail("network"))
    monkeypatch.setattr(builtins, "open", lambda *_args, **_kwargs: pytest.fail("filesystem open"))
    monkeypatch.setattr(
        Path, "write_text", lambda *_args, **_kwargs: pytest.fail("filesystem write")
    )
    monkeypatch.setattr(
        Path, "write_bytes", lambda *_args, **_kwargs: pytest.fail("filesystem write")
    )
    monkeypatch.setattr(os, "chdir", lambda *_args, **_kwargs: pytest.fail("cwd mutation"))
    _project()
    assert Path.cwd() == cwd
    assert dict(os.environ) == environment
    assert set(sys.modules) == modules


def test_production_sources_do_not_import_offline_projection() -> None:
    offenders: list[str] = []
    excluded = {
        Path("mozaiksai/core/semantics/offline_projection.py"),
        Path("tests/test_semantic_offline_projection.py"),
    }
    roots = [ROOT / "mozaiksai", ROOT / "factory_app"]
    for root in roots:
        for path in root.rglob("*.py"):
            relative = path.relative_to(ROOT)
            if relative in excluded:
                continue
            # Text scanning intentionally catches direct/aliased/relative imports,
            # package re-exports, importlib/__import__ strings, and loader strings.
            if "offline_projection" in path.read_text(encoding="utf-8"):
                offenders.append(relative.as_posix())
    assert offenders == []
