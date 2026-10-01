"""Plan review accepts only plans task dispatch can run (live AppGenerator chat e67150d7 at OSS 49c69860).

Review accepted a seven-task plan and dispatch refused it inside AppPlanAgent's
turn, which ended the run: "glob characters not allowed in owned path:
'modules/billing_portal/contracts/*.yaml'". Review checked owned paths with
is_safe_app_path, which accepts a pattern; dispatch normalizes them with
normalize_owned_path, which refuses one. The plan also gave models work inside
the MozaiksPay facade module, a directory the pack owns entirely.

The fixture holds that plan, read from the AG2 WAL and projected back to the
planner schema, with the context keys review reads. These tests run the real
review, and dispatch's own preflight, on a real ContextVariablesBridge.
"""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from pathlib import Path

import pytest

from factory_app.workflows.AppGenerator.tools import app_plan_review
from factory_app.workflows.AppGenerator.tools.app_build_plan import release_pack_facade_paths
from factory_app.workflows.AppGenerator.tools.app_plan_review import (
    _apply_dispatch_path_rules,
    _authored_module_paths,
    _required_module_paths,
    review_app_build_plan,
    validate_plan_dispatch,
)
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.generator_support.module_action_inventory import (
    PackFacadeDirectory,
    pack_facade_directories,
    pack_owned_output_paths,
)
from mozaiksai.core.workflow.path_ownership import normalize_owned_path
from mozaiksai.core.workflow.task_batches import (
    _normalize_task_items,
    _validate_batch_owned_paths,
    load_task_batches_config,
    optional_task_output_paths,
)
from tests.test_app_plan_managed_facade_repair import _plan_and_context, _task
from tests.test_continuous_deterministic_materialization import _load_models

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).parent / "fixtures" / "appplan_review_live_49c69860.json"
REVIEW_LOGGER = "factory_app.workflows.AppGenerator.tools.app_plan_review"
FACADE = "modules/billing_portal/"


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


def _dispatch_preflight(items: list[dict]) -> None:
    """Exactly what execute_task_batches_for_trigger runs before AppPlanAgent's batch starts."""
    config = load_task_batches_config("AppGenerator", ROOT / "factory_app" / "workflows")
    assert config is not None
    (batch,) = [batch for batch in config.batches if batch.trigger_agent == "AppPlanAgent"]
    _validate_batch_owned_paths(batch, _normalize_task_items(items))


def _dispatch_message(path: str) -> str:
    with pytest.raises(ValueError) as refused:
        normalize_owned_path(path)
    return str(refused.value)


# ------------------------------------------------------------------ the live plan


def test_fixture_records_the_live_failure():
    fixture = _fixture()
    live = {task["task_id"]: task["owned_paths"] for task in fixture["live_cached_build_tasks"]}
    assert "modules/billing_portal/contracts/*.yaml" in live["billing_portal_module_contract"]
    assert "modules/task_registry/contracts/*.yaml" in live["task_registry_module_contract"]
    assert live["business_services_for_billing_portal"] == ["modules/billing_portal/backend/repo.py"]
    assert fixture["live_dispatch_failure"].endswith(_dispatch_message("modules/billing_portal/contracts/*.yaml"))
    # The live plan failed dispatch's own preflight, the check review now runs.
    with pytest.raises(ValueError, match="glob characters not allowed"):
        _dispatch_preflight(fixture["live_cached_build_tasks"])


def test_the_live_plan_is_ready_and_dispatch_accepts_it(caplog):
    context = _live_context()
    caplog.set_level(logging.INFO, logger=REVIEW_LOGGER)

    result = review_app_build_plan(AppBuildPlan=deepcopy(_fixture()["AppBuildPlan"]), context_variables=context)

    assert result == {"outcome": "ready", "task_count": 5}
    cached = detach(context.get("app_build_plan"))
    items = detach(context.get("app_task_batch_items"))
    for tasks in (cached["build_tasks"], items):
        owned = [path for task in tasks for path in task["owned_paths"]]
        assert not [path for path in owned if any(char in path for char in "*?[")]
        assert not [path for path in owned if path.startswith(FACADE)]
    contract = next(task for task in items if task["task_id"] == "task_registry_module_contract")
    assert sorted(contract["owned_paths"]) == [
        "modules/task_registry/contracts/events.yaml", "modules/task_registry/module.yaml",
    ]
    # Every companion manifest the pattern reached for is still the task's to emit.
    assert {
        "modules/task_registry/contracts/reactions.yaml", "modules/task_registry/contracts/settings.yaml",
        "modules/task_registry/contracts/admin.yaml", "modules/task_registry/contracts/notifications.yaml",
    } <= optional_task_output_paths(contract)
    page = next(task for task in items if task["task_type"] == "page_bundle")
    assert "billing_portal_module_contract" not in page["depends_on"]
    _dispatch_preflight(items)

    repairs = [record.getMessage() for record in caplog.records]
    assert (
        "[AppGenerator] plan repaired: task_registry_module_contract: released a pattern, not a file "
        "['modules/task_registry/contracts/*.yaml']; task dispatch refuses patterns, and a module's "
        "contracts/ companions are optional outputs of its module_contract task"
    ) in repairs
    assert (
        "[AppGenerator] plan repaired: dropped task 'business_services_for_billing_portal': "
        "every owned path ['modules/billing_portal/backend/repo.py'] is pack-owned"
    ) in repairs
    assert (
        "[AppGenerator] plan repaired: dropped task 'billing_portal_module_contract': "
        "every owned path ['modules/billing_portal/contracts/events.yaml'] is pack-owned"
    ) in repairs


# ------------------------------------------------------------------ 1. dispatch path rules


def _plan(*tasks: dict) -> dict:
    return {"build_tasks": list(tasks), "generation_order": [task["task_id"] for task in tasks]}


def test_a_pattern_is_released_and_the_task_keeps_its_files():
    plan = _plan({
        "task_id": "contract", "task_type": "module_contract", "capability_pack_id": "tasks",
        "owned_paths": ["modules/tasks/module.yaml", "modules/tasks/contracts/*.yaml"], "depends_on": [],
    })
    repairs = _apply_dispatch_path_rules(plan)
    assert plan["build_tasks"][0]["owned_paths"] == ["modules/tasks/module.yaml"]
    assert repairs == [
        "contract: released a pattern, not a file ['modules/tasks/contracts/*.yaml']; task dispatch "
        "refuses patterns, and a module's contracts/ companions are optional outputs of its module_contract task"
    ]


def test_a_task_that_owned_only_patterns_is_dropped_with_its_edges():
    plan = _plan(
        {"task_id": "prompts", "owned_paths": ["refinement_harness/prompts/*.yaml"], "depends_on": []},
        {"task_id": "pages", "owned_paths": ["ui/pages/home.yaml"], "depends_on": ["prompts"]},
    )
    repairs = _apply_dispatch_path_rules(plan)
    assert [task["task_id"] for task in plan["build_tasks"]] == ["pages"]
    assert plan["build_tasks"][0]["depends_on"] == []
    assert plan["generation_order"] == ["pages"]
    assert repairs == [
        "dropped task 'prompts': every owned path ['refinement_harness/prompts/*.yaml'] is a pattern, not a file"
    ]


def test_an_edge_to_a_surviving_task_of_a_repeated_id_is_kept():
    """Before identity repair qualifies a repeated id, the edge may name the survivor."""
    plan = _plan(
        {"task_id": "contract", "owned_paths": ["modules/a/contracts/*.yaml"], "depends_on": []},
        {"task_id": "contract", "owned_paths": ["modules/b/module.yaml"], "depends_on": []},
        {"task_id": "services", "owned_paths": ["modules/b/backend/service.py"], "depends_on": ["contract"]},
    )
    _apply_dispatch_path_rules(plan)
    assert [task["owned_paths"] for task in plan["build_tasks"]] == [
        ["modules/b/module.yaml"], ["modules/b/backend/service.py"],
    ]
    assert plan["build_tasks"][1]["depends_on"] == ["contract"]


@pytest.mark.parametrize("path", [
    "/modules/task_management/backend/extra.py",
    "C:/modules/task_management/backend/extra.py",
    "modules/task_management/../escape.py",
    "services/integrations/vault_client.py",
], ids=["absolute", "drive", "traversal", "secret_term"])
def test_any_other_path_dispatch_refuses_is_rejected_with_its_message(path):
    plan, context = _plan_and_context(monetized=False)
    services = next(task for task in plan["build_tasks"] if task["task_id"] == "8")
    services["owned_paths"].append(path)

    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    assert result["ok"] is False and result["outcome"] == "needs_revision", result
    assert f"- 8: {_dispatch_message(path)}" in result["error"].splitlines()
    assert context.get("app_plan_ready") is False
    assert not detach(context.get("app_task_batch_items"))


def test_every_refused_path_is_named_in_one_rejection():
    plan, context = _plan_and_context(monetized=False)
    tasks = {task["task_id"]: task for task in plan["build_tasks"]}
    tasks["7"]["owned_paths"].append("/abs/schemas.py")
    tasks["8"]["owned_paths"].append("services/integrations/secret_client.py")

    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    assert result["outcome"] == "needs_revision", result
    assert result["error"].splitlines() == [
        "Task dispatch refuses these owned paths; every owned path is one exact app-bundle-relative file:",
        f"- 7: {_dispatch_message('/abs/schemas.py')}",
        f"- 8: {_dispatch_message('services/integrations/secret_client.py')}",
    ]


# ------------------------------------------------------------------ 3. pack facade directories


def test_the_facade_module_is_the_packs_entirely_at_genesis():
    _, context = _plan_and_context(collapsed=False)
    (facade,) = pack_facade_directories(context)
    assert facade == PackFacadeDirectory("mozaikspay", "modules/billing_portal", frozenset())
    assert facade.owns("modules/billing_portal")
    assert facade.owns("modules/billing_portal/backend/repo.py")
    assert not facade.owns("modules/billing_portal_extra/module.yaml")
    assert not facade.owns("modules/task_management/module.yaml")


def test_a_workspace_file_stays_the_workspaces_after_genesis():
    _, context = _plan_and_context(collapsed=False)
    context.set("build_mode", "revision")
    handler = "modules/billing_portal/backend/handler.py"
    assert handler not in pack_owned_output_paths(context)
    (facade,) = pack_facade_directories(context)
    assert facade.exempt == {handler}
    plan = _plan({"task_id": "hooks", "owned_paths": [handler, "modules/billing_portal/backend/repo.py"],
                  "depends_on": []})
    repairs = release_pack_facade_paths(plan, context)
    assert plan["build_tasks"][0]["owned_paths"] == [handler]
    assert repairs == [
        "hooks: released pack-owned ['modules/billing_portal/backend/repo.py']; "
        "the mozaikspay pack owns its facade module modules/billing_portal/"
    ]


def test_review_releases_undeclared_files_in_the_facade_module(caplog):
    plan, context = _plan_and_context(collapsed=False)
    tasks = {task["task_id"]: task for task in plan["build_tasks"]}
    tasks["3"]["owned_paths"].append("modules/billing_portal/contracts/events.yaml")
    tasks["5"]["owned_paths"].append("modules/billing_portal/backend/repo.py")
    tasks["8"]["depends_on"].append("5")
    caplog.set_level(logging.INFO, logger=REVIEW_LOGGER)

    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    assert result["outcome"] == "ready", result
    items = detach(context.get("app_task_batch_items"))
    assert not [path for task in items for path in task["owned_paths"] if path.startswith(FACADE)]
    assert not [task["task_id"] for task in items if {"3", "4", "5"} & {task["task_id"], *task["depends_on"]}]
    repairs = [record.getMessage() for record in caplog.records]
    assert any(
        "every owned path ['modules/billing_portal/backend/repo.py'] is pack-owned" in line for line in repairs
    ), repairs


def test_coverage_requires_no_file_in_a_facade_module():
    plan, context = _plan_and_context(collapsed=False)
    facade = next(pack for pack in plan["capability_packs"] if pack["capability_pack_id"] == "billing_portal")
    facade["user_data_scope"] = True
    handler = "modules/billing_portal/backend/account_data_handler.py"
    assert handler in _required_module_paths(facade, context)["business_services"]
    authored = _authored_module_paths(
        facade, context, pack_owned_output_paths(context), pack_facade_directories(context),
    )
    assert authored == {"module_contract": set(), "data_models": set(), "business_services": set()}


# ------------------------------------------------------------------ 2. dispatch preflight


def test_the_preflight_names_every_refusal_in_one_message():
    context = ContextVariablesBridge({"app_task_batch_items": [
        {"task_id": "pattern", "owned_paths": ["modules/a/contracts/*.yaml"]},
        {"task_id": "empty", "owned_paths": []},
        {"task_id": "pages_a", "owned_paths": ["app.json", "ui/pages/a.yaml"]},
        {"task_id": "pages_b", "owned_paths": ["app.json", "ui/pages/b.yaml"]},
    ]})
    with pytest.raises(ValueError) as refused:
        validate_plan_dispatch(context)
    assert str(refused.value).splitlines() == [
        "Task dispatch would refuse this plan:",
        f"- pattern: {_dispatch_message('modules/a/contracts/*.yaml')}",
        "- empty: task batch 'app_build_tasks' requires owned_paths, but task 'empty' declares none",
        "- task batch 'app_build_tasks' has owned_paths declared by multiple tasks: {'app.json': ['pages_a', 'pages_b']}",
    ]


def test_the_preflight_accepts_what_dispatch_accepts():
    items = [
        {"task_id": "contract", "owned_paths": ["modules/a/module.yaml", "modules/a/contracts/events.yaml"]},
        {"task_id": "pages", "owned_paths": ["app.json", "ui/pages/a.yaml"]},
    ]
    _dispatch_preflight(items)
    validate_plan_dispatch(ContextVariablesBridge({"app_task_batch_items": items}))


def test_a_revision_sharing_app_json_is_rejected_not_dispatched():
    """app_build_plan exempts app.json from its overlap check; dispatch does not."""
    plan, context = _plan_and_context(monetized=False)
    context.set("build_mode", "revision")
    pages = next(task for task in plan["build_tasks"] if task["task_type"] == "page_bundle")
    pages["owned_paths"] = ["app.json", "ui/pages/dashboard.yaml"]
    plan["build_tasks"].append({
        **_task("1b", "page_bundle", "AppSchemaAgent", None, ["app.json", "ui/pages/tasks.yaml"]),
        "surface_id": "task_management", "surface_kind": "module",
    })

    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    assert result["outcome"] == "needs_revision", result
    assert result["error"] == (
        "Task dispatch would refuse this plan:\n"
        "- task batch 'app_build_tasks' has owned_paths declared by multiple tasks: {'app.json': ['1', '1b']}"
    )
    assert context.get("app_plan_ready") is False
    assert not detach(context.get("app_task_batch_items"))


def test_review_rejects_what_an_earlier_step_lets_through(monkeypatch):
    """The preflight is the last word: a path a looser step passes still never reaches dispatch."""
    monkeypatch.setattr(app_plan_review, "_apply_dispatch_path_rules", lambda plan: [])
    context = _live_context()

    result = review_app_build_plan(AppBuildPlan=deepcopy(_fixture()["AppBuildPlan"]), context_variables=context)

    assert result["outcome"] == "needs_revision", result
    assert result["error"].startswith("Task dispatch would refuse this plan:\n")
    assert f"- task_registry_module_contract: {_dispatch_message('modules/task_registry/contracts/*.yaml')}" in (
        result["error"].splitlines()
    )
    assert context.get("app_plan_ready") is False
    assert not detach(context.get("app_task_batch_items"))
