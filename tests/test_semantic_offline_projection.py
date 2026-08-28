"""ADR 0007 Slice 3 offline projection and archetype-corpus proof."""

from __future__ import annotations

import ast
import copy
import json
import os
import sys
from pathlib import Path

import pytest

from mozaiksai.core.semantics.canonical import canonical_json
from mozaiksai.core.semantics.graph import SemanticEdgeKind, SemanticNodeKind
from mozaiksai.core.semantics.offline_projection import (
    ProjectionDisposition,
    ProjectionError,
    ProjectionGapKind,
    extract_semantic_facts,
    project_semantic_graph,
)
from mozaiksai.core.semantics.refs import ExecutionAccessScopeRef

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
                "description": "Build reports",
                "triggers": [
                    {
                        "type": "event",
                        "event": "domain.reports.generated",
                        "description": "Resume after generation",
                    }
                ],
            }
        ],
        "ownership_evidence": {
            "mode": "hybrid",
            "owned_surfaces": ["reports", "billing"],
            "unowned_paths": ["existing/admin.py"],
        },
        "build_context": {
            "context_id": "AppGenerator",
            "assets": [{"path": "file_contracts.yaml", "kind": "contract"}],
        },
        "recorded_artifacts": {
            "modules": [
                {
                    "manifest": {
                        "module": {"id": "audit"},
                        "actions": [{"id": "list_entries"}],
                        "capabilities": [],
                    }
                }
            ],
            "pages": [
                {
                    "name": "Audit",
                    "route": "/audit",
                    "sections": [
                        {
                            "id": "entries",
                            "config": {"api_endpoint": "/api/modules/audit/list_entries"},
                        }
                    ],
                }
            ],
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
        SemanticNodeKind.MODULE,
        SemanticNodeKind.PAGE,
        SemanticNodeKind.SECTION,
        SemanticNodeKind.ACTION,
        SemanticNodeKind.CAPABILITY,
        SemanticNodeKind.EVENT,
        SemanticNodeKind.REACTION,
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
    assert extract_semantic_facts(result.graph) == result.represented_facts


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


def test_machine_readable_coverage_classifies_every_source_leaf() -> None:
    source = _corpus_source()
    result = _project(source)
    leaf_paths = {path for path, _ in _walk_leaves(source)}
    coverage_paths = {row.source_path for row in result.coverage}
    assert coverage_paths == leaf_paths
    assert {row.disposition for row in result.coverage} == {
        ProjectionDisposition.PROJECTED,
        ProjectionDisposition.DELIBERATELY_NON_SEMANTIC,
        ProjectionDisposition.DEFERRED,
    }
    deferred = {row.source_path for row in result.coverage if row.disposition is ProjectionDisposition.DEFERRED}
    assert deferred == {gap.source_path for gap in result.gaps}
    assert json.loads(result.model_dump_json())["schema_version"] == "mozaiks.semantic_projection.v1"


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


@pytest.mark.parametrize("field", ["event", "capability"])
def test_unknown_taxonomy_references_fail_closed(field: str) -> None:
    source = _corpus_source()
    if field == "event":
        source["modules"][0]["events"]["events"][0]["type"] = "domain.unknown.created"
    else:
        source["modules"][0]["manifest"]["capabilities"][0]["capability_id"] = "unknown.capability"
    with pytest.raises(ProjectionError, match="unknown .* taxonomy identifier"):
        _project(source)


def test_recorded_appbuildplan_unknown_semantics_are_not_silently_omitted() -> None:
    recorded = json.loads(
        (ROOT / "tests/fixtures/appplan_persistent_projects_output.json").read_text(
            encoding="utf-8"
        )
    )
    with pytest.raises(ProjectionError) as exc_info:
        project_semantic_graph(recorded, graph_id="recorded-projects", version=1, scope=SCOPE)
    assert exc_info.value.gaps[0].kind is ProjectionGapKind.UNSUPPORTED
    assert "domain.projects" in exc_info.value.gaps[0].reason


def test_existing_recorded_saas_fixture_projects_with_explicit_gaps() -> None:
    recorded = json.loads(
        (ROOT / "tests/fixtures/appplan_saas_entitlement_dispatch_output.json").read_text(
            encoding="utf-8"
        )
    )
    result = project_semantic_graph(
        recorded, graph_id="recorded-saas", version=1, scope=SCOPE
    )
    assert result.graph.nodes
    assert result.gaps
    assert all(gap.kind is ProjectionGapKind.UNSUPPORTED for gap in result.gaps)
    assert all(row.source_file != "unknown" for row in result.coverage)


def test_incomplete_required_and_contradictory_duplicate_inputs_fail_closed() -> None:
    missing = {"modules": [{"manifest": {"actions": []}}]}
    with pytest.raises(ProjectionError, match="module id is required"):
        _project(missing)

    contradictory = _corpus_source()
    contradictory["app_build_plan"]["capability_packs"][0]["capability_pack_id"] = (
        "reports.export"
    )
    with pytest.raises(ProjectionError, match="conflicting facts reuse node identity"):
        _project(contradictory)


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
    source["ownership_evidence"]["owned_surfaces"] = []
    with pytest.raises(ProjectionError, match="requires explicit owned surfaces"):
        _project(source)


def test_projection_has_no_process_or_external_side_effects(monkeypatch) -> None:
    cwd = Path.cwd()
    environment = dict(os.environ)
    modules = set(sys.modules)
    monkeypatch.setattr("socket.socket.connect", lambda *_args, **_kwargs: pytest.fail("network"))
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
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(relative))
            except SyntaxError:
                # Build-context Python templates contain unresolved {{placeholders}}.
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == (
                    "mozaiksai.core.semantics.offline_projection"
                ):
                    offenders.append(relative.as_posix())
                if isinstance(node, ast.Import) and any(
                    alias.name == "mozaiksai.core.semantics.offline_projection"
                    for alias in node.names
                ):
                    offenders.append(relative.as_posix())
    assert offenders == []
