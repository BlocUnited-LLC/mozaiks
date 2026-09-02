"""ADR 0007 Slice 3 offline projection and archetype-corpus proof."""

from __future__ import annotations

import copy
import json
import re
import subprocess
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
from mozaiksai.core.semantics.payloads import (
    ApplicationPayload,
    AuthPayload,
    IntegrationPayload,
    OptionalFamilyKind,
    OptionalFamilySelectionStatus,
    WorkflowPayload,
)
from mozaiksai.core.semantics.refs import ExecutionAccessScopeRef
from mozaiksai.core.session.build_context_schema import validate_pack_context
from mozaiksai.core.taxonomy import (
    NamespaceKind,
    SemanticCategory,
    TaxonomyEntry,
    TaxonomyNamespace,
    build_taxonomy_registry,
)

ROOT = Path(__file__).resolve().parents[1]
SCOPE = ExecutionAccessScopeRef(tenant_id="tenant-1", workspace_id="workspace-1")


def _pinned_registry():
    """Build the corpus taxonomy from Slice 1 primitives only.

    ``default_taxonomy_registry()`` lazily imports the runtime layout registry
    and the transport event contract, which drags in the workflow manager and
    a workflow-catalog read. Projection must not own that, so callers pin a
    registry; ``build_taxonomy_registry`` itself performs no imports, which is
    what makes the cold-cache purity proof below meaningful.
    """
    return build_taxonomy_registry(
        [
            TaxonomyNamespace(
                namespace_id="slice3.corpus",
                version=1,
                kind=NamespaceKind.EXTENSION,
                grants=("domain", "reports"),
                entries=(
                    TaxonomyEntry(
                        identifier="domain.reports.generated", category=SemanticCategory.EVENT
                    ),
                    # Declared by the committed DesignDocs bundle fixture.
                    TaxonomyEntry(
                        identifier="domain.users.user_created", category=SemanticCategory.EVENT
                    ),
                    TaxonomyEntry(
                        identifier="reports.export", category=SemanticCategory.CAPABILITY
                    ),
                    TaxonomyEntry(
                        identifier="reports.view", category=SemanticCategory.CAPABILITY
                    ),
                ),
            )
        ]
    )


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
            "manifest": {
                "app_name": "Reports App",
                "description": "Create and distribute reports.",
                "tagline": None,
                "value_proposition": "Reliable reporting for teams.",
                "version": "1.0.0",
                "auth_strategy": "role-based",
                "roles": ["admin", "member"],
                "default_route": "/reports",
                "pages": ["Reports"],
                "custom_routes": [],
            },
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
            "custom_route_bundle": None,
            "theme_config_patch": {"theme": {"mode": "dark"}},
            "shell_config": None,
            "asset_manifest": None,
            "data_contract": None,
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
        "integrations": [
            {
                "app_id": "slice-3-corpus",
                "service": "resend",
                "kind": "api_key",
                "purpose": "Send report-ready notifications",
                "required_at": "runtime",
                "optional": False,
                "required_fields": [
                    {
                        "name": "RESEND_API_KEY",
                        "type": "secret",
                        "required": True,
                        "frontend_safe": False,
                    }
                ],
                "connector_status": "not_configured",
            }
        ],
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
                    },
                    {
                        "filename": "agents.yaml",
                        "content": "agents:\n  - name: ReportAgent\n",
                    },
                    {
                        "filename": "transition_graph.yaml",
                        "content": (
                            "transition_rules:\n"
                            "  - source_agent: ReportAgent\n"
                            "    target_agent: terminate\n"
                            "    transition_type: after_turn\n"
                        ),
                    },
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


def _project(source: dict | None = None, *, scope: ExecutionAccessScopeRef = SCOPE, registry=None):
    return project_semantic_graph(
        source or _corpus_source(),
        graph_id="slice-3-corpus",
        version=1,
        scope=scope,
        taxonomy_registry=registry or _pinned_registry(),
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
        "application": ("application", "slice-3-corpus", ()),
        "auth": ("auth", "slice-3-corpus", ()),
        "integration": ("integration", "resend", ()),
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
            ("declares", "application", "page_reports"),
            ("declares", "application", "auth"),
            ("declares", "application", "integration"),
            ("declares", "application", "workflow"),
        }
    }
    identity_facts = {(nid, kind, refs) for nid, kind, refs, _digest in result.represented_facts.nodes}
    assert identity_facts == expected_nodes
    payload_digest_by_node = {payload.node_id: payload.payload_digest for payload in result.payloads}
    for nid, _kind, _refs, digest in result.represented_facts.nodes:
        assert digest == payload_digest_by_node[nid]
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
        json.loads(result.model_dump_json())["schema_version"] == "mozaiks.semantic_projection.v2"
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
        project_semantic_graph(
            recorded, graph_id="recorded-projects", version=1, scope=SCOPE,
            taxonomy_registry=_pinned_registry(),
        )
    assert exc_info.value.gaps[0].kind is ProjectionGapKind.MISSING
    assert "domain.projects" in exc_info.value.gaps[0].reason


def test_existing_recorded_saas_fixture_projects_with_explicit_gaps() -> None:
    recorded = json.loads(
        (ROOT / "tests/fixtures/appplan_saas_entitlement_dispatch_output.json").read_text(
            encoding="utf-8"
        )
    )
    result = project_semantic_graph(
        recorded, graph_id="recorded-saas", version=1, scope=SCOPE,
        taxonomy_registry=_pinned_registry(),
    )
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
            {"filename": "agents.yaml", "content": "agents:\n  - name: ReportAgent\n"},
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
        taxonomy_registry=_pinned_registry(),
    )
    assert result.source_facts == extract_semantic_facts(result.graph)
    workflow = next(
        payload
        for payload in result.payloads
        if payload.payload_kind is SemanticNodeKind.WORKFLOW
    )
    assert workflow.topology is not None
    assert not any(gap.source_path == "agent_workflows[0].files" for gap in result.gaps)
    assert any(gap.source_path.endswith("ownership_boundaries[0].ownership") for gap in result.gaps)
    mapped = dict(source)
    mapped["modules"] = {"reports": module_bundle}
    mapped["agent_workflows"] = {"report_builder": workflow_bundle, "_meta": {"count": 1}}
    mapped_result = project_semantic_graph(
        mapped,
        graph_id="current-contracts",
        version=1,
        scope=SCOPE,
        taxonomy_registry=_pinned_registry(),
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


def test_appschema_custom_route_identity_uses_current_producer_path() -> None:
    result = project_semantic_graph(
        {
            "app_schema": {
                "custom_route_bundle": {
                    "route_manifest": [
                        {
                            "id": "checkout_success",
                            "path": "/checkout/success",
                            "component": "CheckoutSuccessPage",
                        }
                    ],
                    "page_files": [],
                }
            }
        },
        graph_id="custom-route",
        version=1,
        scope=SCOPE,
        taxonomy_registry=_pinned_registry(),
    )
    path = "app_schema.custom_route_bundle.route_manifest[0].id"
    row = next(row for row in result.coverage if row.source_path == path)
    assert row.disposition is ProjectionDisposition.PROJECTED
    assert row.target_node_kind is SemanticNodeKind.PAGE
    assert row.fully_representable is True
    assert not any(gap.source_path == path for gap in result.gaps)


def test_committed_design_docs_subscription_build_context_and_route_sources() -> None:
    from tests.test_design_docs_bundle_persistence import _bundle
    from tests.test_subscription_contract_designer import _sample_contract

    design_result = project_semantic_graph(
        {"DesignDocsBundle": _bundle()},
        graph_id="recorded-design-docs",
        version=1,
        scope=SCOPE,
        taxonomy_registry=_pinned_registry(),
    )
    assert design_result.graph.nodes
    assert design_result.source_facts == design_result.represented_facts

    with pytest.raises(ProjectionError) as exc_info:
        project_semantic_graph(
            {"SubscriptionContractOutput": _sample_contract()},
            graph_id="recorded-subscription",
            version=1,
            scope=SCOPE,
            taxonomy_registry=_pinned_registry(),
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
        taxonomy_registry=_pinned_registry(),
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
    assert "not in this projection's classified set" in gap.reason
    assert "typo" not in gap.reason


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


@pytest.mark.parametrize(
    "surface_kind",
    ["app_policy", "refinement", "external_integration", "ui_only"],
)
def test_non_graph_v1_surface_kinds_are_precise_typed_gaps(surface_kind: str) -> None:
    result = project_semantic_graph(
        {
            "app_build_plan": {
                "surface_map": {
                    "surfaces": [
                        {
                            "surface_id": "special_surface",
                            "surface_kind": surface_kind,
                            "owner": "app",
                        }
                    ]
                }
            }
        },
        graph_id=f"surface-{surface_kind}",
        version=1,
        scope=SCOPE,
        taxonomy_registry=_pinned_registry(),
    )
    path = "app_build_plan.surface_map.surfaces[0].surface_kind"
    gap = next(gap for gap in result.gaps if gap.source_path == path)
    row = next(row for row in result.coverage if row.source_path == path)
    assert gap.kind is ProjectionGapKind.UNSUPPORTED
    assert surface_kind in gap.reason
    assert "no typed payload field retains" in gap.reason
    assert row.disposition is ProjectionDisposition.DEFERRED
    assert row.fully_representable is False
    assert not any(
        node.kind in {SemanticNodeKind.MODULE, SemanticNodeKind.WORKFLOW}
        for node in result.graph.nodes
    )


@pytest.mark.parametrize(
    ("surface", "expected_kind"),
    [
        ({"surface_id": "missing"}, ProjectionGapKind.MISSING),
        (
            {"surface_id": "invented", "surface_kind": "invented_kind"},
            ProjectionGapKind.UNSUPPORTED,
        ),
    ],
)
def test_missing_or_unknown_surface_kind_fails_closed(
    surface: dict[str, str], expected_kind: ProjectionGapKind
) -> None:
    with pytest.raises(ProjectionError) as exc_info:
        project_semantic_graph(
            {"app_build_plan": {"surface_map": {"surfaces": [surface]}}},
            graph_id="invalid-surface-kind",
            version=1,
            scope=SCOPE,
            taxonomy_registry=_pinned_registry(),
        )
    gap = exc_info.value.gaps[0]
    assert gap.kind is expected_kind
    assert gap.source_path.endswith("surface_kind")


_COLD_PURITY_PROBE = r'''
import builtins, copy, json, os, socket, sys
from pathlib import Path

# Import ONLY the projection seam and Slice 1 primitives. Importing the runtime
# model helpers this file uses elsewhere would warm the very modules this probe
# exists to detect, which is what made the previous in-process assertion vacuous.
from mozaiksai.core.semantics.offline_projection import project_semantic_graph
from mozaiksai.core.semantics.refs import ExecutionAccessScopeRef
from mozaiksai.core.taxonomy import (
    NamespaceKind, SemanticCategory, TaxonomyEntry, TaxonomyNamespace, build_taxonomy_registry,
)

scope = ExecutionAccessScopeRef(tenant_id="tenant-1", workspace_id="workspace-1")
registry = build_taxonomy_registry([
    TaxonomyNamespace(
        namespace_id="slice3.cold", version=1, kind=NamespaceKind.EXTENSION,
        grants=("reports",),
        entries=(
            TaxonomyEntry(identifier="reports.export", category=SemanticCategory.CAPABILITY),
        ),
    )
])
source = {
    "modules": [
        {
            "manifest": {
                "module": {"id": "reports"},
                "actions": [{"id": "export_report", "entitlement_gate": "reports.export"}],
                "capabilities": [{"capability_id": "reports.export"}],
            }
        }
    ]
}
guarded = copy.deepcopy(source)

failures = []
def _boom(kind):
    def _fail(*_a, **_k):
        failures.append(kind)
        raise AssertionError(kind)
    return _fail

# Guard every prohibited effect for the duration of the call itself.
builtins.open = _boom("filesystem read/write")
Path.open = _boom("filesystem read/write")
Path.read_text = _boom("filesystem read")
Path.read_bytes = _boom("filesystem read")
Path.write_text = _boom("filesystem write")
Path.write_bytes = _boom("filesystem write")
Path.mkdir = _boom("filesystem write")
os.chdir = _boom("cwd mutation")
socket.socket.connect = _boom("network")
socket.getaddrinfo = _boom("network")

cwd, environment, modules = os.getcwd(), dict(os.environ), set(sys.modules)
result = project_semantic_graph(
    source, graph_id="cold", version=1, scope=scope, taxonomy_registry=registry
)
added = sorted(set(sys.modules) - modules)

print(json.dumps({
    "failures": failures,
    "modules_added": added,
    "cwd_changed": os.getcwd() != cwd,
    "env_changed": dict(os.environ) != environment,
    "input_mutated": source != guarded,
    "node_count": len(result.graph.nodes),
}))
'''


def test_projection_has_no_process_or_external_side_effects() -> None:
    """Prove the projection seam is pure in a cold interpreter.

    This runs in a fresh subprocess that imports only the projection seam and
    Slice 1 primitives. The previous in-process form asserted
    ``set(sys.modules) == modules`` after 22 earlier tests in this file had
    already warmed the cache, so it asserted nothing where it passed and failed
    outright when run alone. Nothing is imported here to make the measurement
    succeed — a cold cache is the point.
    """
    completed = subprocess.run(
        [sys.executable, "-c", _COLD_PURITY_PROBE],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert completed.returncode == 0, f"{completed.stdout}\n{completed.stderr}"
    report = json.loads(completed.stdout.strip().splitlines()[-1])

    assert report["failures"] == [], report
    assert report["cwd_changed"] is False
    assert report["env_changed"] is False
    assert report["input_mutated"] is False
    assert report["node_count"] > 0
    # No runtime, workflow, persistence, control-plane, host, or model modules
    # may be pulled in by projecting.
    forbidden = [
        name
        for name in report["modules_added"]
        if any(
            token in name
            for token in (
                "runtime",
                "workflow",
                "persistence",
                "control_plane",
                "hosts",
                "ag2",
                "openai",
                "httpx",
            )
        )
    ]
    assert forbidden == [], f"projection imported prohibited modules: {forbidden}"


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


def test_provenance_roots_are_classified_not_falsely_reported_as_empty() -> None:
    """build_context and workflows are accepted, classified, and honestly diagnosed.

    Both are real parts of current recorded sources but carry no SemanticGraph
    v1 identity. Previously they were listed as supported roots with no
    projector, so a source containing only one of them failed with "source
    contains no representable semantic identity" — which names the wrong cause.
    """
    build_context = yaml.safe_load(
        (ROOT / "factory_app/build_context/AppGenerator/context.yaml").read_text(encoding="utf-8")
    )
    for root, payload in (
        ("build_context", build_context),
        ("workflows", [{"workflow_name": "report_builder", "status": "completed"}]),
    ):
        with pytest.raises(ProjectionError) as exc_info:
            project_semantic_graph(
                {root: payload},
                graph_id=f"provenance-{root}",
                version=1,
                scope=SCOPE,
                taxonomy_registry=_pinned_registry(),
            )
        gaps = exc_info.value.gaps
        assert all(gap.kind is ProjectionGapKind.UNSUPPORTED for gap in gaps), gaps
        assert all(gap.source_path == root for gap in gaps), gaps
        assert "no semantic-graph identity" in gaps[0].reason
        assert "no representable semantic identity" not in gaps[0].reason

    # Alongside real semantic roots they contribute typed gaps, never nodes.
    source = _corpus_source()
    source["workflows"] = [{"workflow_name": "report_builder", "status": "completed"}]
    result = _project(source)
    workflow_gaps = [gap for gap in result.gaps if gap.source_path.startswith("workflows")]
    assert workflow_gaps
    assert all(gap.kind is ProjectionGapKind.UNSUPPORTED for gap in workflow_gaps)


def test_committed_notifications_page_projects_with_explicit_non_module_binding_gap() -> None:
    """The committed first-party page must project, not hard-fail.

    AppPageSchema permits any /api/... path; only /api/modules/{module}/{action}
    names a declared action. /api/notifications is valid and real, so it becomes
    a typed gap rather than an error or an invented action target.
    """
    page = yaml.safe_load(
        (ROOT / "factory_app/app/ui/pages/notifications.yaml").read_text(encoding="utf-8")
    )
    result = project_semantic_graph(
        {"pages": [page]},
        graph_id="committed-notifications",
        version=1,
        scope=SCOPE,
        taxonomy_registry=_pinned_registry(),
    )
    assert any(node.kind is SemanticNodeKind.PAGE for node in result.graph.nodes)
    binding_gaps = [
        gap
        for gap in result.gaps
        if gap.source_path.endswith("api_endpoint")
        and "non-module page API binding" in gap.reason
    ]
    assert binding_gaps, [gap.reason for gap in result.gaps]
    assert all(gap.kind is ProjectionGapKind.UNSUPPORTED for gap in binding_gaps)
    assert not any(node.kind is SemanticNodeKind.ACTION for node in result.graph.nodes)


def test_invalid_api_path_still_fails_closed() -> None:
    page = {
        "name": "Broken",
        "route": "/broken",
        "sections": [
            {"id": "s", "primitive": "Card", "config": {"api_endpoint": "/api/has spaces"}}
        ],
    }
    with pytest.raises(ProjectionError) as exc_info:
        project_semantic_graph(
            {"pages": [page]},
            graph_id="invalid-api",
            version=1,
            scope=SCOPE,
            taxonomy_registry=_pinned_registry(),
        )
    assert exc_info.value.gaps[0].kind is ProjectionGapKind.AMBIGUOUS
    assert "valid AppPageSchema api path" in exc_info.value.gaps[0].reason


def test_duplicate_canonical_root_aliases_fail_closed() -> None:
    """Supplying both an alias and its canonical name must not silently drop one."""
    source = _corpus_source()
    diverging = copy.deepcopy(source["app_build_plan"])
    diverging["surface_map"]["surfaces"][0]["surface_id"] = "totally_different_surface"
    source["AppBuildPlan"] = diverging
    with pytest.raises(ProjectionError) as exc_info:
        _project(source)
    gap = exc_info.value.gaps[0]
    assert gap.kind is ProjectionGapKind.CONTRADICTORY
    assert "silently ignored" in gap.reason


def test_entitlement_gate_requires_declared_capability_input_closure() -> None:
    """Reproduce and pin the module-only entitlement coupling.

    The committed OSS pattern grants an action's entitlement_gate capability
    from subscriptions.yaml rather than the module's own capabilities[]. Alone,
    the module cannot close that reference — projection returns a precise typed
    missing-reference naming the entitlement_gate path, and never invents the
    capability. Supplying the subscription catalog closes it.
    """
    module = {
        "manifest": {
            "module": {"id": "reports"},
            "actions": [{"id": "export_report", "entitlement_gate": "reports.export"}],
        }
    }
    with pytest.raises(ProjectionError) as exc_info:
        project_semantic_graph(
            {"modules": [module]},
            graph_id="entitlement-open",
            version=1,
            scope=SCOPE,
            taxonomy_registry=_pinned_registry(),
        )
    gap = exc_info.value.gaps[0]
    assert gap.kind is ProjectionGapKind.MISSING
    assert gap.source_path.endswith("entitlement_gate")
    assert "does not resolve to a declared semantic node" in gap.reason

    closed = project_semantic_graph(
        {
            "modules": [module],
            "subscriptions": {
                "plans": [{"plan_id": "pro", "capabilities": ["reports.export"]}]
            },
        },
        graph_id="entitlement-closed",
        version=1,
        scope=SCOPE,
        taxonomy_registry=_pinned_registry(),
    )
    assert any(node.kind is SemanticNodeKind.CAPABILITY for node in closed.graph.nodes)
    assert any(edge.kind is SemanticEdgeKind.GATES for edge in closed.graph.edges)


# ---------------------------------------------------------------------------
# Slice 3E: typed payload content projection over graph v2
# ---------------------------------------------------------------------------


def _payload_for(result, kind: SemanticNodeKind, identity_fragment: str):
    matches = [
        payload
        for payload in result.payloads
        if payload.payload_kind is kind and identity_fragment in payload.node_id
    ]
    assert len(matches) == 1, (kind, identity_fragment, [p.node_id for p in result.payloads])
    return matches[0]


def test_projection_emits_v2_graph_with_bijective_payload_closure() -> None:
    from mozaiksai.core.semantics.payloads import validate_semantic_graph_v2_payload_closure
    from mozaiksai.core.semantics.resolver import SemanticReferenceResolver

    result = _project()
    assert result.schema_version == "mozaiks.semantic_projection.v2"
    assert result.graph.schema_version == "mozaiks.semantic_graph.v2"
    assert {payload.node_id for payload in result.payloads} == {
        node.node_id for node in result.graph.nodes
    }
    validate_semantic_graph_v2_payload_closure(result.graph, result.payloads)
    resolver = SemanticReferenceResolver()
    for payload in result.payloads:
        resolver.register_semantic_payload(payload)
    resolver.register_semantic_graph_v2(result.graph)
    for node in result.graph.nodes:
        resolved = resolver.resolve_semantic_payload(
            node.payload_ref, requesting_scope=result.graph.scope
        )
        assert resolved.payload_digest == node.payload_ref.content_digest


def test_section_ordering_is_projected_as_dense_positions() -> None:
    source = _corpus_source()
    page = source["app_schema"]["pages"][0]
    page["sections"] = [
        {"id": "hero", "primitive": "Hero"},
        {"id": "report_table", "primitive": "DataTable"},
        {"id": "footer", "primitive": "Footer"},
    ]
    result = _project(source)
    page_payload = _payload_for(result, SemanticNodeKind.PAGE, "reports")
    ordered = [entry.section_node_id for entry in page_payload.sections]
    assert [entry.position for entry in page_payload.sections] == [0, 1, 2]
    assert [fragment.split(".")[2].rsplit("_", 1)[0] for fragment in ordered] == [
        "reports_hero",
        "reports_report_table",
        "reports_footer",
    ]
    # The former "ordered page-section semantics" gap is gone: sections are
    # projected content now, not a deferred container.
    assert not any(
        "page-section" in gap.reason for gap in result.gaps
    )

    # Reversing the declared section order is a SEMANTIC change: payload and
    # graph digests must both move (ordering is content, not serialization).
    reversed_source = _corpus_source()
    reversed_source["app_schema"]["pages"][0]["sections"] = list(reversed(page["sections"]))
    reversed_result = _project(reversed_source)
    reversed_page = _payload_for(reversed_result, SemanticNodeKind.PAGE, "reports")
    assert reversed_page.payload_digest != page_payload.payload_digest
    assert reversed_result.graph.graph_digest != result.graph.graph_digest


def test_plan_limit_meter_and_product_content_is_projected() -> None:
    result = _project()
    plan = _payload_for(result, SemanticNodeKind.PLAN, "pro")
    assert plan.title == "Pro"
    limit = _payload_for(result, SemanticNodeKind.LIMIT, "pro_report_exports")
    assert limit.limit_value == 100
    assert limit.period is not None and limit.period.value == "monthly"
    meter = _payload_for(result, SemanticNodeKind.METER, "report_exports")
    assert meter.unit == "exports"
    product = _payload_for(result, SemanticNodeKind.PRODUCT, "extra_exports")
    assert product.title == "Extra exports"
    assert product.prices is None  # corpus declares no price: absence stays explicit


def test_prices_project_as_integer_minor_units_with_iso_currency() -> None:
    source = _corpus_source()
    source["subscriptions"]["top_up_products"][0]["price"] = {
        "amount_cents": 500,
        "currency": "usd",
        "display": "$5",
    }
    result = _project(source)
    product = _payload_for(result, SemanticNodeKind.PRODUCT, "extra_exports")
    assert len(product.prices) == 1
    spec = product.prices[0]
    assert spec.amount_minor_units == 500
    assert spec.currency == "USD"
    assert spec.period.value == "one_time"


def test_descriptions_are_projected_but_never_invented() -> None:
    result = _project()
    permission = _payload_for(result, SemanticNodeKind.PERMISSION, "reports_reports_read")
    assert permission.description == "Read reports"
    module = _payload_for(result, SemanticNodeKind.MODULE, "reports")
    assert module.description is None  # corpus module declares none


def test_residual_gaps_are_typed_and_never_claim_v1_limits() -> None:
    result = _project()
    reasons = [gap.reason for gap in result.gaps]
    assert any("navigation ordering has no typed semantic payload field" in r for r in reasons)
    assert any(
        gap.source_path.endswith("pattern_id") and gap.kind is ProjectionGapKind.UNSUPPORTED
        for gap in result.gaps
    )
    for text in reasons + [row.reason for row in result.coverage] + [
        row.stable_identity_derivation for row in result.coverage
    ]:
        assert "SemanticGraph v1" not in text and "graph v1" not in text, text


def test_payload_byte_change_reroots_the_projected_graph() -> None:
    baseline = _project()
    changed_source = _corpus_source()
    changed_source["modules"][0]["manifest"]["permissions"][0]["description"] = "Read all reports"
    changed = _project(changed_source)
    base_perm = _payload_for(baseline, SemanticNodeKind.PERMISSION, "reports_reports_read")
    changed_perm = _payload_for(changed, SemanticNodeKind.PERMISSION, "reports_reports_read")
    assert base_perm.payload_digest != changed_perm.payload_digest
    assert baseline.graph.graph_digest != changed.graph.graph_digest
    # Untouched nodes keep identical payload digests: the re-root is exactly
    # the Merkle chain, not a wholesale rebuild difference.
    base_plan = _payload_for(baseline, SemanticNodeKind.PLAN, "pro")
    changed_plan = _payload_for(changed, SemanticNodeKind.PLAN, "pro")
    assert base_plan.payload_digest == changed_plan.payload_digest


def test_endpoint_trigger_binding_is_payload_content() -> None:
    source = _corpus_source()
    content = source["agent_workflows"][0]["files"][0]["content"]
    source["agent_workflows"][0]["files"][0]["content"] = content + (
        "  - type: endpoint\n"
        "    endpoint: /api/hooks/report\n"
    )
    result = _project(source)
    trigger = _payload_for(result, SemanticNodeKind.TRIGGER, "api_hooks_report")
    assert trigger.trigger_kind is not None and trigger.trigger_kind.value == "endpoint"
    assert trigger.endpoint_path == "/api/hooks/report"
    # Event triggers keep their binding in CONSUMES edges only — the payload
    # never duplicates an edge-owned fact.
    event_trigger = _payload_for(result, SemanticNodeKind.TRIGGER, "domain_reports_generated")
    assert event_trigger.trigger_kind is None and event_trigger.event_id is None


def test_conflicting_payload_content_fails_closed() -> None:
    source = _corpus_source()
    source["subscription_contract"]["subscription_config_file"]["plans"][0]["label"] = "Premium"
    with pytest.raises(ProjectionError, match="conflicting payload content"):
        _project(source)


def test_application_auth_and_integration_facts_round_trip_without_open_state() -> None:
    result = _project()
    application = _payload_for(result, SemanticNodeKind.APPLICATION, "slice_3_corpus")
    auth = _payload_for(result, SemanticNodeKind.AUTH, "slice_3_corpus")
    integration = _payload_for(result, SemanticNodeKind.INTEGRATION, "resend")
    assert isinstance(application, ApplicationPayload)
    assert application.model_dump(mode="json", include={
        "application_id",
        "display_name",
        "description",
        "tagline",
        "value_proposition",
        "version",
        "default_route",
    }) == {
        "application_id": "slice-3-corpus",
        "display_name": "Reports App",
        "description": "Create and distribute reports.",
        "tagline": None,
        "value_proposition": "Reliable reporting for teams.",
        "version": "1.0.0",
        "default_route": "/reports",
    }
    assert isinstance(auth, AuthPayload)
    assert auth.auth_required is True
    assert auth.strategy.value == "role_based"
    assert auth.roles == ("admin", "member")
    assert isinstance(integration, IntegrationPayload)
    assert integration.integration_id == "resend"
    assert integration.config_requirements[0].name == "RESEND_API_KEY"
    serialized = integration.model_dump(mode="json")
    assert "connector_status" not in serialized
    assert "workspace_status" not in serialized
    assert "declared_at" not in serialized


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("app_schema", "manifest", "app_name"), "Reports Pro"),
        (("app_schema", "manifest", "auth_strategy"), "public"),
        (("integrations", 0, "purpose"), "Deliver transactional email"),
    ],
)
def test_application_fact_mutations_reroot_only_through_canonical_payloads(
    path: tuple[str | int, ...], value: object
) -> None:
    baseline = _project()
    source = _corpus_source()
    cursor: object = source
    for part in path[:-1]:
        cursor = cursor[part]  # type: ignore[index]
    cursor[path[-1]] = value  # type: ignore[index]
    if path[-1] == "auth_strategy":
        cursor["roles"] = []  # type: ignore[index]
    changed = _project(source)
    assert changed.graph.graph_digest != baseline.graph.graph_digest


def test_malformed_or_open_application_and_auth_facts_fail_closed() -> None:
    for field, value in (("version", "v1"), ("runtime_session_id", "session-1")):
        source = _corpus_source()
        source["app_schema"]["manifest"][field] = value
        with pytest.raises(ProjectionError):
            _project(source)

    contradictory = _corpus_source()
    contradictory["app_schema"]["manifest"]["auth_strategy"] = "public"
    with pytest.raises(ProjectionError, match="public auth"):
        _project(contradictory)

    unknown_strategy = _corpus_source()
    unknown_strategy["app_schema"]["manifest"]["auth_strategy"] = "passport-session"
    with pytest.raises(ProjectionError, match="closed provider-neutral vocabulary"):
        _project(unknown_strategy)


def test_integration_secret_or_provider_state_smuggling_fails_before_payload_authority() -> None:
    source = _corpus_source()
    source["integrations"][0]["access_token"] = "live-token"
    with pytest.raises(ProjectionError, match="unknown integration declaration field"):
        _project(source)

    source = _corpus_source()
    source["integrations"][0]["required_fields"][0]["value"] = "secret-value"
    with pytest.raises(ProjectionError, match="not structurally closed"):
        _project(source)


def test_optional_family_selection_distinguishes_selected_absent_and_not_applicable() -> None:
    source = _corpus_source()
    source["app_schema"]["manifest"]["auth_strategy"] = "public"
    source["app_schema"]["manifest"]["roles"] = []
    source["integrations"] = []
    result = _project(source)
    application = _payload_for(result, SemanticNodeKind.APPLICATION, "slice_3_corpus")
    statuses = {item.family: item.status for item in application.optional_families}
    assert statuses[OptionalFamilyKind.AUTH] is OptionalFamilySelectionStatus.NOT_APPLICABLE
    assert statuses[OptionalFamilyKind.THEME] is OptionalFamilySelectionStatus.SELECTED
    assert (
        statuses[OptionalFamilyKind.INTEGRATIONS]
        is OptionalFamilySelectionStatus.ABSENT_BY_DECLARATION
    )
    assert (
        statuses[OptionalFamilyKind.CUSTOM_ROUTES]
        is OptionalFamilySelectionStatus.ABSENT_BY_DECLARATION
    )


def test_missing_optional_selection_evidence_fails_closed() -> None:
    source = _corpus_source()
    del source["integrations"]
    with pytest.raises(ProjectionError, match="selection evidence is required"):
        _project(source)


def test_workflow_topology_round_trip_allows_cycles_but_rejects_foreign_targets() -> None:
    source = _corpus_source()
    source["agent_workflows"][0]["files"][1]["content"] = (
        "agents:\n  - name: ReportAgent\n  - name: ReviewAgent\n"
    )
    source["agent_workflows"][0]["files"][2]["content"] = (
        "transition_rules:\n"
        "  - source_agent: ReportAgent\n"
        "    target_agent: ReviewAgent\n"
        "    transition_type: after_turn\n"
        "  - source_agent: ReviewAgent\n"
        "    target_agent: ReportAgent\n"
        "    transition_type: after_turn\n"
    )
    result = _project(source)
    workflow = _payload_for(result, SemanticNodeKind.WORKFLOW, "report_builder")
    assert isinstance(workflow, WorkflowPayload)
    assert workflow.topology is not None
    assert {item.participant_id for item in workflow.topology.participants} == {
        "reportagent",
        "reviewagent",
    }
    assert len(workflow.topology.transitions) == 2

    source["agent_workflows"][0]["files"][2]["content"] = (
        "transition_rules:\n"
        "  - source_agent: ReportAgent\n"
        "    target_agent: MissingAgent\n"
        "    transition_type: after_turn\n"
    )
    with pytest.raises(ProjectionError, match="referentially closed"):
        _project(source)


def test_ag2_runtime_identity_cannot_enter_workflow_semantic_topology() -> None:
    source = _corpus_source()
    content = source["agent_workflows"][0]["files"][0]["content"]
    source["agent_workflows"][0]["files"][0]["content"] = (
        content + "channel_id: live-channel\n"
    )
    with pytest.raises(ProjectionError, match="runtime-only field"):
        _project(source)


def test_new_payload_json_schemas_are_recursively_closed_and_evaluation_free() -> None:
    forbidden_names = {
        "campaign",
        "conversion",
        "evaluation",
        "portfolio",
        "revenue",
        "rating",
        "passport",
        "channel_id",
        "session_id",
    }

    def walk(value: object) -> None:
        if isinstance(value, dict):
            assert value != {}, "empty schema node is an unconstrained Any escape hatch"
            assert value.get("additionalProperties") is not True
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    for model in (ApplicationPayload, AuthPayload, IntegrationPayload, WorkflowPayload):
        assert forbidden_names.isdisjoint(model.model_fields)
        walk(model.model_json_schema())


def test_reordered_integration_declarations_do_not_change_graph_identity() -> None:
    source = _corpus_source()
    second = copy.deepcopy(source["integrations"][0])
    second.update({"service": "slack", "purpose": "Send team alerts"})
    source["integrations"].append(second)
    forward = _project(source)
    source["integrations"].reverse()
    reverse = _project(source)
    assert forward.graph.graph_digest == reverse.graph.graph_digest


def test_new_application_input_authority_never_consults_app_build_plan() -> None:
    baseline = _project()
    source = _corpus_source()
    del source["app_build_plan"]
    without_plan = _project(source)
    for kind in (
        SemanticNodeKind.APPLICATION,
        SemanticNodeKind.AUTH,
        SemanticNodeKind.INTEGRATION,
        SemanticNodeKind.WORKFLOW,
    ):
        baseline_payloads = {
            payload.node_id: payload.payload_digest
            for payload in baseline.payloads
            if payload.payload_kind is kind
        }
        projected_payloads = {
            payload.node_id: payload.payload_digest
            for payload in without_plan.payloads
            if payload.payload_kind is kind
        }
        assert projected_payloads == baseline_payloads


def test_target_input_gaps_are_closed_while_downstream_output_gaps_remain_explicit() -> None:
    result = _project()
    target_fragments = (
        "manifest.auth_strategy",
        "manifest.roles",
        "integrations[",
        "workflow topology requires",
    )
    assert not any(
        fragment in f"{gap.source_path} {gap.reason}"
        for gap in result.gaps
        for fragment in target_fragments
    )
    assert any(gap.source_path.endswith("pattern_id") for gap in result.gaps)
    assert any(gap.source_path.endswith("filename") for gap in result.gaps)
