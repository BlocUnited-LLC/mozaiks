"""Tests for the contract surface planner.

Covers:
- ContractSurface contract types and canonical paths
- ContractSurfacePlan and ContractSurfaceUpdate models
- ContractSurfacePlanner eligibility and surface resolution
- HarnessDecision for_contract_surface_plan (targeted_regeneration and fallback)
- Dependency ordering
- Path resolution with and without workspace context
"""

from __future__ import annotations

from typing import Any

import pytest

from mozaiksai.control_plane.contracts import (
    CONTRACT_SURFACE_CANONICAL_PATHS,
    CONTRACT_SURFACE_DEPENDENCY_ORDER,
    ContractSurfacePlan,
    ContractSurfaceUpdate,
    HarnessDecision,
)
from mozaiksai.control_plane.implementations.contract_surface_planner import (
    ContractSurfacePlanner,
    _ContractSurfaceClassification,
    _SurfaceEntry,
)


# ---------------------------------------------------------------------------
# Contract type smoke tests
# ---------------------------------------------------------------------------


def test_contract_surface_canonical_paths_all_kinds_have_entries():
    expected_kinds = {
        "module_action",
        "module_contract",
        "page_binding",
        "data_schema",
        "workflow_tool",
        "workflow_agent",
        "ui_component",
        "app_config",
    }
    assert set(CONTRACT_SURFACE_CANONICAL_PATHS.keys()) == expected_kinds


def test_contract_surface_canonical_paths_target_id_substitution():
    paths = CONTRACT_SURFACE_CANONICAL_PATHS["module_action"]
    resolved = [p.replace("{target_id}", "projects") for p in paths]
    assert "modules/projects/module.yaml" in resolved
    assert "modules/projects/backend/handler.py" in resolved
    assert "modules/projects/backend/service.py" in resolved
    assert "modules/projects/backend/schemas.py" in resolved


def test_contract_surface_dependency_order_schema_before_action():
    assert CONTRACT_SURFACE_DEPENDENCY_ORDER["data_schema"] < CONTRACT_SURFACE_DEPENDENCY_ORDER["module_action"]


def test_contract_surface_dependency_order_action_before_page():
    assert CONTRACT_SURFACE_DEPENDENCY_ORDER["module_action"] < CONTRACT_SURFACE_DEPENDENCY_ORDER["page_binding"]


def test_contract_surface_dependency_order_tool_before_agent():
    assert CONTRACT_SURFACE_DEPENDENCY_ORDER["workflow_tool"] <= CONTRACT_SURFACE_DEPENDENCY_ORDER["workflow_agent"]


# ---------------------------------------------------------------------------
# ContractSurfacePlan model
# ---------------------------------------------------------------------------


def test_contract_surface_plan_empty_surfaces():
    plan = ContractSurfacePlan(
        surfaces=[],
        summary="nothing to update",
        fallback_to_workflow=True,
        fallback_reason="too broad",
        confidence=0.0,
    )
    assert plan.fallback_to_workflow is True
    assert plan.surfaces == []


def test_contract_surface_update_model():
    update = ContractSurfaceUpdate(
        kind="module_action",
        target_id="projects",
        target_kind="module",
        affected_paths=["modules/projects/module.yaml", "modules/projects/backend/handler.py"],
        dependency_order=2,
        rationale="add export action",
        confidence=0.9,
        generation_hint="Add export_projects action to module.yaml and handler.py",
    )
    assert update.kind == "module_action"
    assert update.target_id == "projects"
    assert "modules/projects/module.yaml" in update.affected_paths


# ---------------------------------------------------------------------------
# ContractSurfacePlanner eligibility
# ---------------------------------------------------------------------------


def test_planner_eligible_feature_app_bundle():
    planner = ContractSurfacePlanner.__new__(ContractSurfacePlanner)
    assert planner.eligible(change_class="feature", artifact_kind="app_bundle") is True


def test_planner_eligible_design_app_bundle():
    planner = ContractSurfacePlanner.__new__(ContractSurfacePlanner)
    assert planner.eligible(change_class="design", artifact_kind="app_bundle") is True


def test_planner_not_eligible_patch():
    planner = ContractSurfacePlanner.__new__(ContractSurfacePlanner)
    assert planner.eligible(change_class="patch", artifact_kind="app_bundle") is False


def test_planner_not_eligible_core():
    planner = ContractSurfacePlanner.__new__(ContractSurfacePlanner)
    assert planner.eligible(change_class="core", artifact_kind="app_bundle") is False


def test_planner_not_eligible_unknown_artifact():
    planner = ContractSurfacePlanner.__new__(ContractSurfacePlanner)
    assert planner.eligible(change_class="feature", artifact_kind="concept") is False


# ---------------------------------------------------------------------------
# Surface resolution
# ---------------------------------------------------------------------------


def _make_classification(**kwargs: Any) -> _ContractSurfaceClassification:
    defaults = {
        "surfaces": [],
        "summary": "test",
        "confidence": 0.9,
        "requires_schema_migration": False,
        "fallback_to_workflow": False,
        "fallback_reason": None,
    }
    defaults.update(kwargs)
    return _ContractSurfaceClassification.model_validate(defaults)


def test_resolve_surfaces_module_action_no_catalog():
    classification = _make_classification(
        surfaces=[
            {
                "kind": "module_action",
                "target_id": "projects",
                "target_kind": "module",
                "rationale": "add export action",
                "confidence": 0.9,
                "generation_hint": "add export_projects",
            }
        ]
    )
    surfaces = ContractSurfacePlanner._resolve_surfaces(
        classification=classification,
        context_graph_catalog=None,
    )
    assert len(surfaces) == 1
    s = surfaces[0]
    assert s.kind == "module_action"
    assert s.target_id == "projects"
    assert "modules/projects/module.yaml" in s.affected_paths
    assert "modules/projects/backend/handler.py" in s.affected_paths


def test_resolve_surfaces_filters_to_known_paths_when_catalog_present():
    classification = _make_classification(
        surfaces=[
            {
                "kind": "module_action",
                "target_id": "tasks",
                "target_kind": "module",
                "rationale": "add complete action",
                "confidence": 0.85,
                "generation_hint": "add complete_task action",
            }
        ]
    )
    catalog = {
        "file_tree": [
            "modules/tasks/module.yaml",
            "modules/tasks/backend/handler.py",
            "modules/tasks/backend/service.py",
            # schemas.py and repo.py are absent — not yet generated
        ],
        "candidate_files": [],
    }
    surfaces = ContractSurfacePlanner._resolve_surfaces(
        classification=classification,
        context_graph_catalog=catalog,
    )
    assert len(surfaces) == 1
    s = surfaces[0]
    # Only paths that exist in the workspace should be in affected_paths
    for path in s.affected_paths:
        if path in catalog["file_tree"]:
            assert True
        else:
            # Fallback: top canonical paths are included even if not in catalog
            assert path in ["modules/tasks/module.yaml", "modules/tasks/backend/handler.py"]


def test_resolve_surfaces_dependency_ordering():
    classification = _make_classification(
        surfaces=[
            {
                "kind": "page_binding",
                "target_id": "projects",
                "target_kind": "page",
                "rationale": "add export section to page",
                "confidence": 0.85,
                "generation_hint": "add export section",
            },
            {
                "kind": "module_action",
                "target_id": "projects",
                "target_kind": "module",
                "rationale": "add export action to module",
                "confidence": 0.9,
                "generation_hint": "add export_projects action",
            },
            {
                "kind": "data_schema",
                "target_id": "projects",
                "target_kind": "module",
                "rationale": "add ExportRequest schema",
                "confidence": 0.8,
                "generation_hint": "add ExportRequest, ExportResponse",
            },
        ]
    )
    surfaces = ContractSurfacePlanner._resolve_surfaces(
        classification=classification,
        context_graph_catalog=None,
    )
    # data_schema (order 0) → module_action (order 2) → page_binding (order 3)
    kinds = [s.kind for s in surfaces]
    assert kinds.index("data_schema") < kinds.index("module_action")
    assert kinds.index("module_action") < kinds.index("page_binding")


def test_resolve_surfaces_skips_unknown_kind():
    classification = _make_classification(
        surfaces=[
            {
                "kind": "not_a_real_surface",
                "target_id": "projects",
                "target_kind": "module",
                "rationale": "unknown",
                "confidence": 0.9,
                "generation_hint": "",
            },
            {
                "kind": "module_action",
                "target_id": "projects",
                "target_kind": "module",
                "rationale": "add action",
                "confidence": 0.9,
                "generation_hint": "add action",
            },
        ]
    )
    surfaces = ContractSurfacePlanner._resolve_surfaces(
        classification=classification,
        context_graph_catalog=None,
    )
    assert len(surfaces) == 1
    assert surfaces[0].kind == "module_action"


def test_resolve_surfaces_skips_empty_target_id():
    classification = _make_classification(
        surfaces=[
            {
                "kind": "module_action",
                "target_id": "",
                "target_kind": "module",
                "rationale": "missing id",
                "confidence": 0.9,
                "generation_hint": "",
            }
        ]
    )
    surfaces = ContractSurfacePlanner._resolve_surfaces(
        classification=classification,
        context_graph_catalog=None,
    )
    assert surfaces == []


# ---------------------------------------------------------------------------
# HarnessDecision for_contract_surface_plan
# ---------------------------------------------------------------------------


def _make_routing_decision(change_class: str = "feature", workflow_id: str = "AppGenerator") -> Any:
    from unittest.mock import MagicMock
    from mozaiksai.control_plane.implementations.refinement_router import ChangeClass

    intent = MagicMock()
    intent.change_class = ChangeClass(change_class)
    intent.rationale = "user wants to add export controls"
    intent.confidence = 0.9

    rd = MagicMock()
    rd.change_intent = intent
    rd.workflow_id = workflow_id
    rd.workflow_sequence = "app_revision"
    return rd


def test_for_contract_surface_plan_targeted_regeneration():
    from mozaiksai.control_plane.implementations.harness_decision import FirstPartyHarnessDecisionPolicy

    policy = FirstPartyHarnessDecisionPolicy.__new__(FirstPartyHarnessDecisionPolicy)
    plan = ContractSurfacePlan(
        surfaces=[
            ContractSurfaceUpdate(
                kind="module_action",
                target_id="projects",
                target_kind="module",
                affected_paths=["modules/projects/module.yaml"],
                dependency_order=2,
                rationale="add export action",
                confidence=0.9,
            ),
            ContractSurfaceUpdate(
                kind="page_binding",
                target_id="projects",
                target_kind="page",
                affected_paths=["ui/pages/projects.yaml"],
                dependency_order=3,
                rationale="add export section",
                confidence=0.85,
            ),
        ],
        summary="Add export_projects action and expose on projects page.",
        change_class="feature",
        artifact_kind="app_bundle",
        confidence=0.88,
        fallback_to_workflow=False,
    )
    routing_decision = _make_routing_decision()
    decision: HarnessDecision = policy.for_contract_surface_plan(
        routing_decision=routing_decision,
        plan=plan,
    )
    assert decision.decision_type == "targeted_regeneration"
    assert decision.metadata.get("contract_surface_plan") is not None
    assert len(decision.actions) == 2
    action_ids = [a.action_id for a in decision.actions]
    assert "review_surface_plan" in action_ids
    assert "apply_surface_plan" in action_ids


def test_for_contract_surface_plan_fallback_to_workflow_reentry():
    from mozaiksai.control_plane.implementations.harness_decision import FirstPartyHarnessDecisionPolicy

    policy = FirstPartyHarnessDecisionPolicy.__new__(FirstPartyHarnessDecisionPolicy)
    plan = ContractSurfacePlan(
        summary="Change too broad.",
        change_class="feature",
        artifact_kind="app_bundle",
        fallback_to_workflow=True,
        fallback_reason="confidence below threshold",
        confidence=0.45,
    )
    routing_decision = _make_routing_decision()
    decision: HarnessDecision = policy.for_contract_surface_plan(
        routing_decision=routing_decision,
        plan=plan,
    )
    assert decision.decision_type == "workflow_reentry"
    assert decision.metadata.get("contract_surface_fallback") is True


def test_for_contract_surface_plan_empty_surfaces_falls_back():
    from mozaiksai.control_plane.implementations.harness_decision import FirstPartyHarnessDecisionPolicy

    policy = FirstPartyHarnessDecisionPolicy.__new__(FirstPartyHarnessDecisionPolicy)
    plan = ContractSurfacePlan(
        surfaces=[],
        summary="no surfaces resolved",
        change_class="feature",
        artifact_kind="app_bundle",
        fallback_to_workflow=False,  # planner didn't set fallback but surfaces are empty
        confidence=0.8,
    )
    routing_decision = _make_routing_decision()
    decision: HarnessDecision = policy.for_contract_surface_plan(
        routing_decision=routing_decision,
        plan=plan,
    )
    # Empty surfaces should still fall back
    assert decision.decision_type == "workflow_reentry"
