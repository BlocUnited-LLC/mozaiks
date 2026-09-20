from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.app_plan_review import (
    _repair_plan,
    review_app_build_plan,
    validate_plan_coverage,
    validate_plan_origins,
)
from mozaiksai.core.session.build_context_schema import VALID_CAPABILITY_SOURCES
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.frozen import detach
from tests.test_continuous_deterministic_materialization import _load_models, _plan_payload

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _load_factory_contracts():
    _load_models()


def _plan():
    plan = _plan_payload()["AppBuildPlan"]
    plan["readiness_profile"] = "none"
    plan["capability_packs"] = [{
        "capability_pack_id": "reports", "surface_id": "reports",
        "surface_kind": "module", "capability_source": "generated_module",
        "pack_type": "custom_domain", "label": "Reports", "summary": "Stored reports",
        "implementation_mode": "hybrid", "primary_entities": ["Report"],
    }]
    service = next(task for task in plan["build_tasks"] if task["task_type"] == "business_services")
    service["owned_paths"] += ["modules/reports/backend/repo.py", "modules/reports/backend/policy.py"]
    return plan


def _context():
    return ContextVariablesBridge({
        "build_mode": "initial", "app_plan_attempts": 0, "app_plan_outcome": "blocked",
        "design_surface_map": {"surfaces": [{"surface_id": "reports", "surface_kind": "module", "owner": "app", "primary_entities": ["Report"]}]},
        "experience_spec": {"pages": [{"name": "Reports", "route": "/reports"}]},
    })


def test_capability_source_uses_canonical_finite_vocabulary():
    schema = yaml.safe_load((ROOT / "factory_app/workflows/AppGenerator/structured_outputs.yaml").read_text(encoding="utf-8"))
    field = schema["models"]["AppCapabilityPack"]["fields"]["capability_source"]
    assert field["type"] == "literal"
    assert set(field["values"]) == VALID_CAPABILITY_SOURCES | {"host_universal"}


def test_complete_plan_is_cached_without_losing_typed_fields():
    plan = _plan()
    plan["shell_preset_hint"] = "operations"
    context = _context()
    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)
    assert result["outcome"] == "ready", result
    assert context.get("app_plan_ready") is True
    assert context.get("app_build_plan")["shell_preset_hint"] == "operations"
    assert context.get("app_build_plan")["revenue_model"] == "free"


def test_review_queues_synthesized_module_workers_with_actual_prerequisites():
    plan = _plan()
    page = next(task for task in plan["build_tasks"] if task["task_type"] == "page_bundle")
    page["depends_on"] = []
    plan["build_tasks"] = [page]
    context = _context()

    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    assert result["outcome"] == "ready", result
    tasks = {task["task_type"]: task for task in context.get("app_task_batch_items")}
    contract_id = tasks["module_contract"]["task_id"]
    models_id = tasks["data_models"]["task_id"]
    assert list(tasks["data_models"]["depends_on"]) == [contract_id]
    assert list(tasks["business_services"]["depends_on"]) == [contract_id, models_id]
    for kind in ("data_models", "business_services"):
        assert tasks[kind]["current_build_task"]["depends_on"] == tasks[kind]["depends_on"]
    assert contract_id in tasks["page_bundle"]["depends_on"]


def test_repeated_plan_normalization_preserves_owned_reaction_contract():
    plan = _plan()
    contract = next(task for task in plan["build_tasks"] if task["task_type"] == "module_contract")
    reaction_path = "modules/reports/contracts/reactions.yaml"
    contract["owned_paths"].append(reaction_path)
    plan["event_flows"] = [{
        "event_type": "domain.documents.analysis_requested",
        "producer_pack_id": "documents",
        "producing_action": "request_analysis",
        "subscriber_intents": ["reports-review"],
        "workflow_capability_ids": ["reports-review"],
    }]
    context = _context()

    _repair_plan(plan, context)
    normalized = detach(plan)
    _repair_plan(normalized, context)
    contract_after = next(
        task for task in normalized["build_tasks"]
        if task["task_type"] == "module_contract" and task["capability_pack_id"] == "reports"
    )
    assert reaction_path in contract_after["owned_paths"]


def test_review_routes_a_dependency_cycle_to_feedback_before_queuing_workers():
    plan = _plan()
    contract = next(task for task in plan["build_tasks"] if task["task_type"] == "module_contract")
    contract["depends_on"] = ["reports.services"]
    context = _context()

    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    assert result["outcome"] == "needs_revision"
    assert "dependency cycle detected" in result["error"]
    assert not context.get("app_task_batch_items")


def test_review_rechecks_dependencies_after_merging_persistence_tasks():
    plan = _plan()
    contract = next(task for task in plan["build_tasks"] if task["task_type"] == "module_contract")
    contract["depends_on"] = ["persistence_before"]
    for task_id, dependencies in (
        ("persistence_before", []), ("persistence_after", [contract["task_id"]]),
    ):
        plan["build_tasks"].append({
            "task_id": task_id, "task_type": "persistence_contract",
            "capability_pack_id": "reports", "surface_id": "reports", "surface_kind": "module",
            "initial_agent": "DatabaseAgent", "execution_target": "AppGenerator",
            "description": "Declare persistence", "initial_message": "Emit the app data contract.",
            "owned_paths": ["data/contract.json"], "depends_on": dependencies,
        })
    context = _context()

    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    assert result["outcome"] == "needs_revision"
    assert "dependency cycle detected" in result["error"]
    assert not context.get("app_task_batch_items")


@pytest.mark.parametrize("filename", ["app.json", "ui/pages/reports.yaml", "modules/reports/module.yaml", "modules/reports/backend/schemas.py", "modules/reports/backend/handler.py", "modules/reports/backend/service.py", "modules/reports/backend/repo.py", "modules/reports/backend/policy.py"])
def test_incomplete_plan_reports_every_missing_file(filename):
    plan = _plan()
    for task in plan["build_tasks"]:
        task["owned_paths"] = [path for path in task["owned_paths"] if path != filename]
    with pytest.raises(ValueError, match=filename.replace(".", r"\.")):
        validate_plan_coverage(plan, _context())


def test_approved_page_cannot_disappear():
    context = _context()
    context.set("experience_spec", {"pages": [{"name": "Reports", "route": "/reports"}, {"name": "Dashboard", "route": "/dashboard"}]})
    with pytest.raises(ValueError, match="approved name/route inventory"):
        validate_plan_coverage(_plan(), context)


def test_page_case_mismatch_reports_received_and_expected_paths_without_rewriting():
    plan = _plan()
    task = next(task for task in plan["build_tasks"] if task["task_type"] == "page_bundle")
    task["owned_paths"] = [path.replace("ui/pages/reports.yaml", "ui/pages/Reports.yaml") for path in task["owned_paths"]]
    with pytest.raises(ValueError) as error:
        validate_plan_coverage(plan, _context())
    assert "case-sensitive" in str(error.value)
    assert "ui/pages/Reports.yaml" in str(error.value)
    assert "ui/pages/reports.yaml" in str(error.value)
    assert "ui/pages/Reports.yaml" in task["owned_paths"]


def test_user_scoped_module_gets_its_account_data_handler_planned():
    """The handler is required, and the review now supplies it rather than re-asking.

    A user-scoped module must own backend/account_data_handler.py. Which file
    that is, is derivable from the pack, so rejecting the plan over it cost
    three model rounds and then the run - that is what killed every live build.
    The review now adds the path and accepts the plan.

    The guarantee is unchanged and still enforced where it matters: on the
    generated bundle. test_user_scope_cannot_be_lost_during_module_materialization
    below asserts _scan_planned_user_data_scope rejects output whose module.yaml
    drops the scope or whose account_data_handler.py is missing. Planning the
    file is bookkeeping; producing it is the safety property.
    """
    plan = _plan()
    plan["capability_packs"][0]["user_data_scope"] = True
    context = _context()

    accepted = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    assert accepted["outcome"] == "ready", accepted
    cached = context.get("app_build_plan")
    assert cached["capability_packs"][0]["user_data_scope"] is True
    owned = {
        path
        for task in cached["build_tasks"]
        if task.get("task_type") == "business_services"
        for path in task.get("owned_paths") or []
    }
    assert "modules/reports/backend/account_data_handler.py" in owned


def test_user_scope_cannot_be_lost_during_module_materialization():
    from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import (
        _scan_planned_user_data_scope,
    )

    packs = [{"capability_pack_id": "reports", "capability_source": "generated_module", "user_data_scope": True}]
    files = {"modules/reports/module.yaml": "module:\n  id: reports\n  user_data_scope: false\n"}
    errors = _scan_planned_user_data_scope(files, packs)
    assert len(errors) == 2
    files["modules/reports/module.yaml"] = "module:\n  id: reports\n  user_data_scope: true\n"
    files["modules/reports/backend/account_data_handler.py"] = "# Implementation checked by runtime loader\n"
    assert _scan_planned_user_data_scope(files, packs) == []


def test_product_category_cannot_become_an_unregistered_managed_service():
    plan = _plan()
    plan["capability_packs"][0]["capability_source"] = "managed_capability"
    with pytest.raises(ValueError, match="registered provider pack"):
        validate_plan_origins(plan, _context())


def test_registered_pack_origin_is_read_from_frozen_context():
    context = _context()
    context.set("capability_packs", [{"id": "reports", "capability_source": "framework_pack"}])
    with pytest.raises(ValueError, match="must match the registered pack"):
        validate_plan_origins(_plan(), context)


def test_registered_operator_pack_survives_typed_plan_and_template_materialization(tmp_path):
    from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
        resolve_managed_capability_templates,
    )

    root = tmp_path / "reports"
    template = root / "templates/config/reports.yaml.j2"
    template.parent.mkdir(parents=True)
    template.write_text("label: {{ report_label }}\n", encoding="utf-8")
    (root / "context.yaml").write_text(yaml.safe_dump({
        "context_id": "reports", "applies_to_workflows": ["AppGenerator"],
        "assets": [{"path": "templates/", "kind": "templates"}],
        "pack": {"id": "reports", "version": "0.1.0", "status": "active", "capability_source": "operator_pack"},
    }), encoding="utf-8")
    context = _context()
    context.set("capability_packs", [{"id": "reports", "capability_source": "operator_pack", "pack_source_path": str(root)}])
    plan = _plan()
    plan["capability_packs"][0]["capability_source"] = "operator_pack"
    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)
    assert result["outcome"] == "ready", result
    from mozaiksai.core.workflow.context.frozen import detach

    packs = detach(context.get("app_build_plan"))["capability_packs"]
    assert packs[0]["capability_source"] == "operator_pack"
    assert packs[0]["pack_source_path"] == str(root)
    rendered = resolve_managed_capability_templates(packs, context_variables={"report_label": "Reports"})
    files = {item["filename"]: item["content"] for item in rendered}
    assert files["config/reports.yaml"] == "label: Reports"


def test_unregistered_operator_pack_is_not_a_generated_module():
    plan = _plan()
    plan["capability_packs"][0]["capability_source"] = "operator_pack"
    with pytest.raises(ValueError, match="operator_pack requires an installed pack"):
        validate_plan_origins(plan, _context())


def test_generated_module_identity_cannot_be_a_product_category():
    plan = _plan()
    plan["capability_packs"][0]["capability_pack_id"] = "crud_pack"
    with pytest.raises(ValueError, match="approved surface_id"):
        validate_plan_origins(plan, _context())


def test_product_category_is_not_an_installed_framework_pack():
    plan = _plan()
    plan["capability_packs"][0].update(capability_pack_id="crud_pack", capability_source="framework_pack")
    with pytest.raises(ValueError, match="framework_pack requires an installed pack"):
        validate_plan_origins(plan, _context())


def test_module_tasks_cannot_silently_normalize_a_different_identity():
    plan = _plan()
    plan["build_tasks"][0]["capability_pack_id"] = "invented"
    # The rejection now names the fault: an id matching no declared capability.
    with pytest.raises(ValueError, match="matches 0 declared capabilities"):
        validate_plan_origins(plan, _context())


def test_invalid_plan_clears_prior_work_and_repairs_within_declared_budget():
    review = review_app_build_plan
    context = _context()
    context.set("app_plan_ready", True)
    context.set("app_task_batch_items", [{"task_id": "stale"}])
    invalid = deepcopy(_plan())
    invalid["build_tasks"] = []
    result = review(AppBuildPlan=invalid, context_variables=context)
    assert result["outcome"] == "needs_revision"
    assert context.get("app_plan_ready") is False
    assert not context.get("app_task_batch_items")
    assert context.get("app_plan_feedback")
    assert review(AppBuildPlan=_plan(), context_variables=context)["outcome"] == "ready"
    assert context.get("app_plan_attempts") == 2


def test_repair_budget_is_finite():
    review = review_app_build_plan
    context = _context()
    for _ in range(2):
        assert review(AppBuildPlan={}, context_variables=context)["outcome"] == "needs_revision"
    assert review(AppBuildPlan={}, context_variables=context)["outcome"] == "blocked"
    assert review(AppBuildPlan=_plan(), context_variables=context)["outcome"] == "blocked"
    assert context.get("app_plan_attempts") == 3
    assert context.get("app_plan_ready") is False
