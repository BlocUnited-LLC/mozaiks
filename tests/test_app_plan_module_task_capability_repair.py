"""Keep approved module task ownership through live-context plan review."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy

import pytest

from factory_app.workflows.AppGenerator.tools.app_plan_review import (
    _repair_coverage,
    review_app_build_plan,
    validate_plan_origins,
)
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.frozen import detach
from tests.test_app_plan_managed_facade_repair import _task
from tests.test_app_plan_task_identity_repair import _live_plan
from tests.test_continuous_deterministic_materialization import _load_models


@pytest.fixture(autouse=True)
def _load_factory_contracts():
    _load_models()


def _reported_plan(label="task_registry"):
    plan, context = _live_plan(repeated_ids=False)
    assert isinstance(context, ContextVariablesBridge)
    plan["service_scope"] = ["tasks"]
    for pack in plan["capability_packs"]:
        if pack["capability_pack_id"] == "task_management":
            pack.update(capability_pack_id="tasks", surface_id="tasks")
    design = detach(context.get("design_surface_map"))
    design["surfaces"][0]["surface_id"] = "tasks"
    context.set("design_surface_map", design)
    ids = {"6": "module_contract", "7": "data_models", "8": "business_services"}
    for task in plan["build_tasks"]:
        if task["surface_id"] == "task_management":
            task["surface_id"] = "tasks"
        if task["capability_pack_id"] == "task_management":
            task["capability_pack_id"] = "tasks"
            task["owned_paths"] = [path.replace("modules/task_management/", "modules/tasks/") for path in task["owned_paths"]]
        task["task_id"] = ids.get(task["task_id"], task["task_id"])
        task["depends_on"] = [ids.get(dependency, dependency) for dependency in task["depends_on"]]
        if task["task_id"] in ids.values():
            task["capability_pack_id"] = label
    plan["generation_order"] = [task["task_id"] for task in plan["build_tasks"]]
    return plan, context


def _ownership(plan):
    return {task["task_id"]: set(task["owned_paths"]) for task in plan["build_tasks"]}


def _assert_exclusive_paths(plan):
    counts = Counter(path.replace("\\", "/") for task in plan["build_tasks"] for path in task["owned_paths"])
    assert not {path: count for path, count in counts.items() if count > 1}


@pytest.mark.parametrize("label", ["task_registry", "task_management"])
def test_reported_drift_keeps_model_tasks_and_one_trio_per_module(label):
    plan, context = _reported_plan(label)
    before = _ownership(plan)
    assert sorted(pack["capability_pack_id"] for pack in plan["capability_packs"]) == [
        "billing_portal", "mozaikspay", "tasks",
    ]

    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    assert result["outcome"] == "ready", result
    cached = detach(context.get("app_build_plan"))
    validate_plan_origins(cached, context)
    assert _ownership(cached) == before
    _assert_exclusive_paths(cached)
    for module in ("tasks", "billing_portal"):
        trio = [task for task in cached["build_tasks"] if task["capability_pack_id"] == module and task["task_type"] != "persistence_contract"]
        assert Counter(task["task_type"] for task in trio) == {
            "module_contract": 1, "data_models": 1, "business_services": 1,
        }
    by_id = {task["task_id"]: task for task in cached["build_tasks"]}
    for kind in ("module_contract", "data_models", "business_services"):
        assert by_id[kind]["capability_pack_id"] == "tasks"
        assert by_id[kind]["surface_id"] == "tasks"
    assert "module_contract" in by_id["data_models"]["depends_on"]
    assert {"module_contract", "data_models"} <= set(by_id["business_services"]["depends_on"])
    queued = detach(context.get("app_task_batch_items"))
    assert {task["task_id"] for task in queued} == set(before)


def test_drift_repair_composes_with_task_identity_and_managed_facade_repairs():
    plan, context = _reported_plan()
    plan["capability_packs"] = [pack for pack in plan["capability_packs"] if pack["capability_pack_id"] != "billing_portal"]
    provider = next(pack for pack in plan["capability_packs"] if pack["capability_pack_id"] == "mozaikspay")
    provider.update(surface_id="billing_portal", surface_kind="module")
    adapter = next(task for task in plan["build_tasks"] if task["task_type"] == "api_surface")
    adapter.update(surface_id="billing_portal", surface_kind="module")
    kinds = {"module_contract", "data_models", "business_services"}
    original = {
        (task["surface_id"], task["task_type"]): set(task["owned_paths"])
        for task in plan["build_tasks"] if task["task_type"] in kinds
    }
    ids = {task["task_id"]: task["task_type"] for task in plan["build_tasks"]}
    for task in plan["build_tasks"]:
        task["task_id"] = task["task_type"]
        task["depends_on"] = [ids.get(dependency, dependency) for dependency in task["depends_on"]]
    plan["generation_order"] = [task["task_id"] for task in plan["build_tasks"]]

    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    assert result["outcome"] == "ready", result
    cached = detach(context.get("app_build_plan"))
    validate_plan_origins(cached, context)
    assert len(cached["build_tasks"]) == len(plan["build_tasks"])
    _assert_exclusive_paths(cached)
    by_module = {
        module: [task for task in cached["build_tasks"] if task["capability_pack_id"] == module and task["task_type"] in kinds]
        for module in ("tasks", "billing_portal")
    }
    for module, trio in by_module.items():
        assert Counter(task["task_type"] for task in trio) == dict.fromkeys(kinds, 1)
        draft_scope = "task_registry" if module == "tasks" else module
        by_kind = {task["task_type"]: task for task in trio}
        for task in trio:
            assert task["task_id"] == f"{draft_scope}.{task['task_type']}"
            assert set(task["owned_paths"]) == original[(module, task["task_type"])]
        contract_id = by_kind["module_contract"]["task_id"]
        models = by_kind["data_models"]
        services = by_kind["business_services"]
        assert contract_id in models["depends_on"]
        assert {contract_id, models["task_id"]} <= set(services["depends_on"])
        sibling_scope = "billing_portal" if module == "tasks" else "task_registry"
        assert not any(dependency.startswith(f"{sibling_scope}.") for dependency in models["depends_on"] + services["depends_on"])
    packs = {pack["capability_pack_id"]: pack for pack in cached["capability_packs"]}
    assert packs["billing_portal"]["capability_source"] == "generated_module"
    assert packs["mozaikspay"]["surface_kind"] == "external_integration"
    queued = detach(context.get("app_task_batch_items"))
    assert {task["task_id"] for task in queued} == {task["task_id"] for task in cached["build_tasks"]}


def test_disagreeing_surface_and_paths_are_rejected_without_queuing_workers():
    plan, context = _reported_plan("tasks")
    task = next(task for task in plan["build_tasks"] if task["task_id"] == "module_contract")
    task["surface_id"] = "billing_portal"
    before = deepcopy(plan)

    with pytest.raises(ValueError, match="surface_id='billing_portal' disagrees"):
        validate_plan_origins(plan, context)
    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    assert result["outcome"] == "needs_revision", result
    assert "surface_id='billing_portal' disagrees" in result["error"]
    assert not context.get("app_task_batch_items")
    assert context.get("app_plan_ready") is False
    assert plan == before


def test_capability_repair_leaves_disagreeing_surface_and_paths_untouched():
    from factory_app.workflows.AppGenerator.tools.app_plan_review import (
        _repair_module_task_capabilities,
    )

    plan, context = _reported_plan("tasks")
    task = next(task for task in plan["build_tasks"] if task["task_id"] == "module_contract")
    task.update(surface_id="billing_portal", capability_pack_id="task_registry")
    before = deepcopy(plan)

    assert _repair_module_task_capabilities(plan, context) == []
    assert plan == before
    with pytest.raises(ValueError, match="capability_pack_id='task_registry' matches 0"):
        validate_plan_origins(plan, context)


def test_correct_plan_retains_its_task_identities_and_ownership():
    plan, context = _reported_plan("tasks")
    before = deepcopy(plan)

    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    assert result["outcome"] == "ready", result
    assert plan == before
    cached = detach(context.get("app_build_plan"))
    assert _ownership(cached) == _ownership(before)
    _assert_exclusive_paths(cached)


def test_capability_repair_leaves_correct_plan_untouched():
    from factory_app.workflows.AppGenerator.tools.app_plan_review import (
        _repair_module_task_capabilities,
    )

    plan, context = _reported_plan("tasks")
    before = deepcopy(plan)

    assert _repair_module_task_capabilities(plan, context) == []
    assert plan == before


@pytest.mark.parametrize("existing_typed_task", [False, True], ids=["synthesis", "augmentation"])
def test_coverage_does_not_assign_another_tasks_owned_path(existing_typed_task):
    plan, context = _reported_plan("tasks")
    service = next(task for task in plan["build_tasks"] if task["task_id"] == "business_services")
    claimed_path = "modules/tasks/backend/handler.py"
    if existing_typed_task:
        service["owned_paths"].remove(claimed_path)
    else:
        plan["build_tasks"].remove(service)
    plan["build_tasks"].append(_task(
        "misclassified_owner", "api_surface", "ControllerAgent", "tasks", [claimed_path],
    ))
    before = _ownership(plan)

    with pytest.raises(ValueError, match="modules/tasks/backend/handler.py"):
        _repair_coverage(plan, context)

    assert _ownership(plan) == before
    _assert_exclusive_paths(plan)
    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)
    assert result["outcome"] == "needs_revision", result
    assert not context.get("app_task_batch_items")


@pytest.mark.parametrize("kind,path", [
    ("persistence_contract", "data/contract.json"),
    ("data_migrations", "data/migrations/add_tasks.json"),
])
def test_data_plane_persistence_tasks_are_not_module_local(kind, path):
    from factory_app.workflows.AppGenerator.tools.app_plan_review import (
        _repair_module_task_capabilities,
    )

    plan, context = _reported_plan("tasks")
    plan["build_tasks"].append({
        **_task(f"data_plane_{kind}", kind, "DatabaseAgent", "task_registry", [path]),
        "surface_id": "tasks",
    })
    before = deepcopy(plan)

    assert _repair_module_task_capabilities(plan, context) == []
    assert plan == before


def test_normalized_module_paths_repair_only_the_capability_label():
    from factory_app.workflows.AppGenerator.tools.app_plan_review import (
        _repair_module_task_capabilities,
    )

    plan, context = _reported_plan()
    for task in plan["build_tasks"]:
        task["owned_paths"] = [path.replace("/", "\\") for path in task["owned_paths"]]
    expected = deepcopy(plan)
    for task in expected["build_tasks"]:
        if task["task_id"] in ("module_contract", "data_models", "business_services"):
            task["capability_pack_id"] = "tasks"

    assert len(_repair_module_task_capabilities(plan, context)) == 3
    assert plan == expected
    assert _repair_module_task_capabilities(plan, context) == []


@pytest.mark.parametrize("paths", [
    [],
    ["modules/tasks/module.yaml", "services/integrations/tasks.py"],
    ["modules/tasks/../billing_portal/module.yaml"],
    ["modules/tasks/module.yaml", ""],
], ids=["empty", "mixed", "unsafe", "blank_path"])
def test_module_scope_requires_every_owned_path_to_agree(paths):
    from factory_app.workflows.AppGenerator.tools.app_plan_review import (
        _repair_module_task_capabilities,
    )

    plan, context = _reported_plan("tasks")
    task = next(task for task in plan["build_tasks"] if task["task_id"] == "module_contract")
    task.update(capability_pack_id="task_registry", owned_paths=paths)
    before = deepcopy(plan)

    assert _repair_module_task_capabilities(plan, context) == []
    assert plan == before


@pytest.mark.parametrize("surface", [
    {"surface_id": "another_module", "surface_kind": "module", "owner": "app"},
    {"surface_id": "tasks", "surface_kind": "module", "owner": "platform"},
    {"surface_id": "tasks", "surface_kind": "app_policy", "owner": "app"},
], ids=["unapproved", "not_app_owned", "not_module"])
def test_capability_repair_requires_an_approved_app_module(surface):
    from factory_app.workflows.AppGenerator.tools.app_plan_review import (
        _repair_module_task_capabilities,
    )

    plan, context = _reported_plan()
    design = detach(context.get("design_surface_map"))
    design["surfaces"][0] = surface
    context.set("design_surface_map", design)
    before = deepcopy(plan)

    assert _repair_module_task_capabilities(plan, context) == []
    assert plan == before
