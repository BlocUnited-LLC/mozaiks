"""Plan review merges one unit of work split across tasks that claim the same files.

The live release run at c8b9ea2e (AppGenerator chat 0d442d1f) planned the tasks
module's business_services as four tasks, one per action (create_task,
update_task, delete_task, list_tasks), each owning
modules/tasks/backend/service.py. Review rejected every attempt as overlapping
ownership while the model kept resubmitting the split.

``fixtures/appplan_review_live_c8b9ea2e.json`` holds the first recorded plan as
the model submitted it (AG2 WAL) and the chat's starting context reduced to the
keys review reads, on a real ContextVariablesBridge.
"""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from pathlib import Path

import pytest

from factory_app.workflows.AppGenerator.tools import app_plan_review
from factory_app.workflows.AppGenerator.tools.app_plan_review import review_app_build_plan
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.frozen import detach
from tests.test_continuous_deterministic_materialization import _load_models
from tests.test_plan_review_dispatch_preflight import _dispatch_preflight

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).parent / "fixtures" / "appplan_review_live_c8b9ea2e.json"
REVIEW_LOGGER = app_plan_review.logger.name
SPLIT = ["create_task", "update_task", "delete_task", "list_tasks"]
SERVICE = "modules/tasks/backend/service.py"


@pytest.fixture(autouse=True)
def _load_factory_contracts():
    _load_models()


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _live_context() -> ContextVariablesBridge:
    variables = deepcopy(_fixture()["context_variables"])
    for pack in variables.get("capability_packs") or []:
        pack["pack_source_path"] = str(ROOT / pack["pack_source_path"])
    return ContextVariablesBridge(variables)


def _plan() -> dict:
    return deepcopy(_fixture()["AppBuildPlan"])


def _review(plan: dict) -> tuple[dict, ContextVariablesBridge]:
    context = _live_context()
    return review_app_build_plan(AppBuildPlan=plan, context_variables=context), context


def _task(task_id: str, task_type: str, paths: list[str], **extra) -> dict:
    return {
        "task_id": task_id, "task_type": task_type, "capability_pack_id": "tasks", "surface_id": "tasks",
        "surface_kind": "module", "owned_paths": paths, "depends_on": [], "initial_message": f"Build {task_id}.",
        **extra,
    }


def test_without_the_merge_review_reproduces_the_live_rejection(monkeypatch):
    monkeypatch.setattr(app_plan_review, "_merge_split_tasks", lambda plan, context: [])

    result, _context = _review(_plan())

    assert result["outcome"] == "needs_revision"
    assert result["error"].startswith(_fixture()["live_rejection"])


def test_the_live_plan_is_ready_and_dispatch_accepts_it(caplog):
    caplog.set_level(logging.INFO, logger=REVIEW_LOGGER)

    result, context = _review(_plan())

    assert result == {"outcome": "ready", "task_count": 5}
    items = detach(context.get("app_task_batch_items"))
    owners = [task["task_id"] for task in items if SERVICE in task["owned_paths"]]
    assert owners == ["create_task"]
    (services,) = [task for task in items if task["task_type"] == "business_services"]
    assert services["task_id"] == "create_task"
    for task_id in SPLIT[1:]:
        assert task_id not in {task["task_id"] for task in items}
    _dispatch_preflight(items)

    repairs = [record.getMessage() for record in caplog.records]
    assert (
        "[AppGenerator] plan repaired: merged ['create_task', 'update_task', 'delete_task', 'list_tasks'] into "
        "'create_task': one business_services task for 'tasks' split across tasks that claim the same files"
    ) in repairs


def test_every_reference_to_an_absorbed_task_names_the_survivor():
    plan = {
        "build_tasks": [
            _task("create_task", "business_services", [SERVICE], acceptance_criteria=["creates"]),
            _task("update_task", "business_services", [SERVICE, "modules/tasks/backend/repo.py"],
                  acceptance_criteria=["updates"], depends_on=["module_contract"]),
            _task("module_contract", "module_contract", ["modules/tasks/module.yaml"]),
            _task("pages", "page_bundle", ["ui/pages/tasks.yaml"], depends_on=["update_task", "create_task"]),
        ],
        "generation_order": ["module_contract", "update_task", "create_task", "pages"],
        "carry_forward_decisions": [{"module_id": "tasks", "affected_build_tasks": ["update_task"]}],
    }

    repairs = app_plan_review._merge_split_tasks(plan, None)

    assert len(repairs) == 1
    by_id = {task["task_id"]: task for task in plan["build_tasks"]}
    assert set(by_id) == {"create_task", "module_contract", "pages"}
    survivor = by_id["create_task"]
    assert survivor["owned_paths"] == [SERVICE, "modules/tasks/backend/repo.py"]
    assert survivor["acceptance_criteria"] == ["creates", "updates"]
    assert survivor["depends_on"] == ["module_contract"]
    assert survivor["initial_message"] == "Build create_task.\n\nBuild update_task."
    assert by_id["pages"]["depends_on"] == ["create_task"]
    assert plan["generation_order"] == ["module_contract", "create_task", "pages"]
    assert plan["carry_forward_decisions"][0]["affected_build_tasks"] == ["create_task"]


def test_tasks_of_different_types_sharing_a_file_are_not_merged():
    plan = {"build_tasks": [
        _task("contract", "module_contract", ["modules/tasks/module.yaml"]),
        _task("services", "business_services", ["modules/tasks/module.yaml", SERVICE]),
    ]}

    assert app_plan_review._merge_split_tasks(plan, None) == []
    assert [task["task_id"] for task in plan["build_tasks"]] == ["contract", "services"]


def test_same_type_tasks_of_different_capabilities_are_not_merged():
    plan = {"build_tasks": [
        _task("tasks_services", "business_services", [SERVICE]),
        {**_task("other_services", "business_services", [SERVICE]), "capability_pack_id": "other"},
    ]}

    assert app_plan_review._merge_split_tasks(plan, None) == []
    assert len(plan["build_tasks"]) == 2


def test_tasks_sharing_only_app_json_are_not_merged():
    plan = {"build_tasks": [
        _task("dashboard_page", "page_bundle", ["app.json", "ui/pages/dashboard.yaml"]),
        _task("tasks_page", "page_bundle", ["app.json", "ui/pages/tasks.yaml"]),
    ]}

    assert app_plan_review._merge_split_tasks(plan, None) == []
    assert len(plan["build_tasks"]) == 2


def test_overlaps_chain_into_one_task():
    plan = {"build_tasks": [
        _task("a", "business_services", [SERVICE]),
        _task("b", "business_services", ["modules/tasks/backend/repo.py"]),
        _task("c", "business_services", [SERVICE, "modules/tasks/backend/repo.py"]),
    ]}

    app_plan_review._merge_split_tasks(plan, None)

    (task,) = plan["build_tasks"]
    assert task["task_id"] == "a"
    assert task["owned_paths"] == [SERVICE, "modules/tasks/backend/repo.py"]


def test_a_repeated_task_id_is_left_for_identity_repair():
    plan = {"build_tasks": [
        _task("services", "business_services", [SERVICE]),
        _task("services", "business_services", [SERVICE]),
    ]}

    assert app_plan_review._merge_split_tasks(plan, None) == []
    assert len(plan["build_tasks"]) == 2


def test_page_tasks_sharing_a_page_are_left_to_coverage():
    plan = {"build_tasks": [
        {**_task("pages_a", "page_bundle", ["ui/pages/tasks.yaml"]), "surface_id": "page_bundle", "surface_kind": "ui_only",
         "capability_pack_id": None},
        {**_task("pages_b", "page_bundle", ["ui/pages/tasks.yaml"]), "surface_id": "page_bundle", "surface_kind": "ui_only",
         "capability_pack_id": None},
    ]}

    assert app_plan_review._merge_split_tasks(plan, None) == []
    assert len(plan["build_tasks"]) == 2


def test_files_a_pack_or_assembly_owns_are_not_a_reason_to_merge():
    context = _live_context()
    for shared in ("services/integrations/mozaikspay_client.py", "config/subscriptions.yaml"):
        plan = {"build_tasks": [
            _task("svc_a", "business_services", [shared, SERVICE]),
            _task("svc_b", "business_services", [shared, "modules/tasks/backend/repo.py"]),
        ]}

        assert app_plan_review._merge_split_tasks(plan, context) == [], shared
        assert len(plan["build_tasks"]) == 2


def test_a_merge_that_would_close_a_dependency_cycle_is_left_to_the_checks():
    plan = {"build_tasks": [
        _task("svc_a", "business_services", [SERVICE]),
        _task("pages", "page_bundle", ["ui/pages/tasks.yaml"], depends_on=["svc_a"]),
        _task("svc_b", "business_services", [SERVICE], depends_on=["pages"]),
    ]}

    assert app_plan_review._merge_split_tasks(plan, None) == []
    assert len(plan["build_tasks"]) == 3


@pytest.mark.parametrize("keeper_id", ["", "contract"])
def test_a_blank_or_reused_id_is_left_for_identity_repair(keeper_id):
    plan = {"build_tasks": [
        _task(keeper_id, "business_services", [SERVICE]),
        _task("svc_b", "business_services", [SERVICE]),
        _task("contract", "module_contract", ["modules/tasks/module.yaml"]),
        _task("pages", "page_bundle", ["ui/pages/tasks.yaml"], depends_on=["svc_b"]),
    ]}

    assert app_plan_review._merge_split_tasks(plan, None) == []
    assert next(task for task in plan["build_tasks"] if task["task_id"] == "pages")["depends_on"] == ["svc_b"]


def test_integration_needs_and_task_context_follow_the_keeper():
    plan = {"build_tasks": [
        _task("svc_a", "business_services", [SERVICE],
              context_variables=[{"key": "action_name", "value": "create_task", "value_type": "string"}]),
        _task("svc_b", "business_services", [SERVICE],
              context_variables=[{"key": "action_name", "value": "update_task", "value_type": "string"},
                                 {"key": "extra", "value": "1", "value_type": "string"}]),
        _task("client", "api_surface", ["services/integrations/crm_client.py"],
              integration_needs=[{"service": "crm", "required_by": {"kind": "task", "id": "svc_b"}}]),
    ]}

    app_plan_review._merge_split_tasks(plan, None)

    by_id = {task["task_id"]: task for task in plan["build_tasks"]}
    assert by_id["svc_a"]["context_variables"] == [
        {"key": "action_name", "value": "create_task", "value_type": "string"},
        {"key": "extra", "value": "1", "value_type": "string"},
    ]
    assert by_id["client"]["integration_needs"][0]["required_by"] == {"kind": "task", "id": "svc_a"}
