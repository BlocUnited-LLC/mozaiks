from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.app_plan_review import (
    review_app_build_plan,
    validate_plan_coverage,
    validate_plan_origins,
)
from mozaiksai.core.session.build_context_schema import VALID_CAPABILITY_SOURCES
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
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


def test_user_scoped_module_requires_planned_account_data_handler():
    plan = _plan()
    plan["capability_packs"][0]["user_data_scope"] = True
    context = _context()
    rejected = review_app_build_plan(AppBuildPlan=plan, context_variables=context)
    assert rejected["outcome"] == "needs_revision"
    assert "backend/account_data_handler.py" in rejected["error"]
    service = next(task for task in plan["build_tasks"] if task["task_type"] == "business_services")
    service["owned_paths"].append("modules/reports/backend/account_data_handler.py")
    accepted = review_app_build_plan(AppBuildPlan=plan, context_variables=context)
    assert accepted["outcome"] == "ready", accepted
    assert context.get("app_build_plan")["capability_packs"][0]["user_data_scope"] is True


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
    with pytest.raises(ValueError, match="module task capability_pack_id"):
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
