from __future__ import annotations

import json
from copy import deepcopy

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.app_build_plan import (
    _construct_task_requirements,
    app_build_plan,
)
from factory_app.workflows.AppGenerator.tools.app_plan_review import (
    _validate_plan_surface_inventory,
)
from factory_app.workflows.AppGenerator.tools.code_file_utils import save_generated_code
from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import (
    _scan_planned_data_fields,
)
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.context.structured_output_overlay import StructuredOutputOverlay
from mozaiksai.core.workflow.generator_support.page_plan_utils import (
    module_action_index_from_context,
)


def _contract():
    return {"version": "1", "surfaces": [{
        "surface_id": "tasks", "surface_kind": "module", "collections": [{
            "name": "tasks", "entity": "Task", "scope": "app", "tenancy": "per_user",
            "owner_field": "owner_id",
            "ownership": {"surface_id": "tasks", "surface_kind": "module"},
            "fields": [{"name": "owner_id", "type": "string", "required": True}],
        }],
    }]}


def test_repair_serializes_approved_contract_without_mutating_context_authority():
    approved = _contract()
    competing = deepcopy(approved)
    competing["surfaces"][0]["collections"][0]["tenancy"] = "app_wide"
    context = ContextVariablesBridge({
        "data_contract": approved,
        "current_build_task": {"owned_paths": ["data/contract.json"]},
    })
    result = save_generated_code(StructuredOutputOverlay(context, {
        "database_files": [{"path": "data/contract.json", "content": json.dumps(competing)}],
    }))
    assert result["saved_files"] == ["data/contract.json"]
    saved = detach(context.get("code_files"))
    assert json.loads(saved[0]["content"]) == approved
    assert detach(context.get("data_contract")) == approved
    assert _scan_planned_data_fields({"data/contract.json": json.dumps(competing)}, approved)


def test_plan_cannot_supply_a_second_data_contract():
    context = ContextVariablesBridge({"data_contract": _contract()})
    with pytest.raises(ValueError, match="must not author data_contract"):
        app_build_plan(AppBuildPlan={"data_contract": _contract()}, context_variables=context)
    assert detach(context.get("data_contract")) == _contract()


@pytest.mark.parametrize("missing", ["entity", "tenancy", "owner_field"])
def test_factory_serialization_rejects_incomplete_ownership_even_though_runtime_accepts_it(missing):
    contract = _contract()
    contract["surfaces"][0]["collections"][0].pop(missing)
    context = ContextVariablesBridge({
        "data_contract": contract, "current_build_task": {"owned_paths": ["data/contract.json"]},
    })
    before = context.snapshot()
    with pytest.raises(ValueError, match=missing):
        save_generated_code(StructuredOutputOverlay(context, {"code_files": []}))
    assert context.snapshot() == before


@pytest.mark.parametrize("complete", [False, True])
def test_factory_shared_serialization_requires_complete_explicit_surface_ownership(complete):
    collection = dict(_contract()["surfaces"][0]["collections"][0]) if complete else {"name": "tasks", "entity": "Task"}
    collection.pop("ownership", None)
    contract = {"version": "1", "surfaces": [], "shared_collections": [collection]}
    context = ContextVariablesBridge({
        "data_contract": contract, "current_build_task": {"owned_paths": ["data/contract.json"]},
    })
    before = context.snapshot()
    with pytest.raises(ValueError):
        save_generated_code(StructuredOutputOverlay(context, {"code_files": []}))
    assert context.snapshot() == before


@pytest.mark.parametrize("shared", [False, True])
def test_policy_ownership_is_constructed_only_for_collection_owners(shared):
    plan = {"build_tasks": [{
        "task_id": module, "task_type": "business_services", "initial_agent": "ServiceAgent",
        "owned_paths": [f"modules/{module}/backend/service.py", f"modules/{module}/backend/policy.py"],
    } for module in ("tasks", "billing_portal")]}
    contract = _contract()
    if shared:
        contract["shared_collections"] = contract["surfaces"][0]["collections"]
        contract["surfaces"][0]["collections"] = []
    context = ContextVariablesBridge({"data_contract": contract})
    repairs = _construct_task_requirements(plan, context)
    assert plan["build_tasks"][0]["owned_paths"][-1] == "modules/tasks/backend/policy.py"
    assert plan["build_tasks"][1]["owned_paths"] == ["modules/billing_portal/backend/service.py"]
    assert len(repairs) == 1


def test_page_inventory_requires_both_approved_and_implemented_actions():
    manifest = {"module": {"id": "tasks"}, "actions": [
        {"id": action} for action in ("create_task", "task_summary", "list_tasks", "invented")
    ]}
    context = ContextVariablesBridge({
        "data_contract": _contract(),
        "design_surface_map": {"surfaces": [{
            "surface_id": "tasks", "surface_kind": "module", "owner": "app",
            "primary_entities": ["Task"], "owned_mutations": ["create_task"],
            "custom_reads": ["task_summary", "not_implemented"],
        }]},
        "generated_files": {"modules/tasks/module.yaml": yaml.safe_dump(manifest)},
    })
    assert module_action_index_from_context(context) == {
        "tasks": {action: {"id": action} for action in ("create_task", "task_summary", "list_tasks")},
    }


def test_data_contract_projection_cannot_authorize_unapproved_module_files():
    context = ContextVariablesBridge({
        "data_contract": _contract(),
        "design_surface_map": {"surfaces": [{
            "surface_id": "tasks", "surface_kind": "module", "owner": "app",
            "primary_entities": ["Task"], "owned_mutations": ["create_task"],
        }]},
    })
    task = {
        "task_id": "persistence", "task_type": "persistence_contract",
        "initial_agent": "DatabaseAgent",
        "capability_pack_id": None, "surface_id": "data_contract", "surface_kind": "module",
        "owned_paths": ["data/contract.json"],
    }
    _validate_plan_surface_inventory({"build_tasks": [task]}, context)
    task["owned_paths"].append("modules/unapproved/backend/handler.py")
    with pytest.raises(ValueError, match="unapproved surface"):
        _validate_plan_surface_inventory({"build_tasks": [task]}, context)
