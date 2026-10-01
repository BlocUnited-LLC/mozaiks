"""config/subscriptions.yaml is written by assembly, never by a build task.

Assembly always wrote the file from the saved subscription contract
(materialize_app_config_contracts). The plan still carried a model-run
subscription_config task whose output was accepted and then overwritten: model
work that was always discarded, and a place a run could fail for nothing. The
2026-09-25 acceptance run at 27ebdedc failed on it three times -- the planner
omitted the task, and a provider without the task was rejected.

The task type is gone. The provider is validated against the approved contract
instead, and a task that still lists the file loses it, as pack-owned outputs
do (#765).
"""

from __future__ import annotations

import inspect
from copy import deepcopy
from typing import Any

from factory_app.workflows._shared.subscription_contract_context import _render_contract
from factory_app.workflows.AppGenerator.tools import app_build_plan as cache_module
from factory_app.workflows.AppGenerator.tools import app_plan_review
from factory_app.workflows.AppGenerator.tools.app_build_plan import (
    _resolve_monetization_provider,
    _subscription_contract_required,
    _validate_monetization_provider_selection,
    release_subscriptions_config,
)
from factory_app.workflows.AppGenerator.tools.app_plan_review import review_app_build_plan
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.frozen import detach
from tests.test_app_plan_managed_facade_repair import _task
from tests.test_app_plan_task_identity_repair import _live_plan
from tests.test_continuous_deterministic_materialization import _load_models

PATH = "config/subscriptions.yaml"
CONTRACT = {
    "contract_required": True,
    "subscription_config_file": {"plans": [{"plan_id": "free"}, {"plan_id": "pro"}]},
}


def _plan_with(*tasks: dict[str, Any]) -> dict[str, Any]:
    return {"build_tasks": list(tasks), "generation_order": [task["task_id"] for task in tasks]}


def test_a_task_that_only_built_the_file_is_dropped_with_its_edges() -> None:
    plan = _plan_with(
        {"task_id": "subs", "owned_paths": [PATH], "depends_on": []},
        {"task_id": "pages", "owned_paths": ["ui/pages/home.yaml"], "depends_on": ["subs"]},
    )
    repairs = release_subscriptions_config(plan)
    assert [task["task_id"] for task in plan["build_tasks"]] == ["pages"]
    assert plan["build_tasks"][0]["depends_on"] == []
    assert plan["generation_order"] == ["pages"]
    assert repairs == ["dropped task 'subs': every owned path ['config/subscriptions.yaml'] is assembly-owned"]


def test_a_task_with_other_work_keeps_it_and_loses_the_file() -> None:
    plan = _plan_with({"task_id": "services", "owned_paths": ["services/config.py", PATH], "depends_on": []})
    repairs = release_subscriptions_config(plan)
    assert plan["build_tasks"][0]["owned_paths"] == ["services/config.py"]
    assert repairs == [
        "services: released assembly-owned ['config/subscriptions.yaml']; "
        "assembly writes it from the approved subscription contract"
    ]


def test_a_plan_that_never_listed_the_file_is_untouched() -> None:
    plan = _plan_with({"task_id": "pages", "owned_paths": ["ui/pages/home.yaml"], "depends_on": []})
    original = deepcopy(plan)
    assert release_subscriptions_config(plan) == []
    assert plan == original


def test_the_27ebdedc_shape_passes_without_a_task() -> None:
    """State null, artifact carries the contract, provider named, no task: the live shape."""
    context = ContextVariablesBridge({
        "subscription_contract": None,
        "subscription_contract_artifact": {"metadata": {"summary_payload": CONTRACT}},
    })
    assert _subscription_contract_required(context) is True
    packs, provider = _resolve_monetization_provider(
        [], contract_required=True, monetization_provider="mozaiks_pay", context_variables=context,
    )
    assert "mozaikspay" in [pack.get("capability_pack_id") for pack in packs]
    _validate_monetization_provider_selection(packs, contract_required=True, monetization_provider=provider)


def test_no_contract_means_no_subscription_build() -> None:
    for state in ({}, {"subscription_contract": {"contract_required": False}}):
        assert _subscription_contract_required(ContextVariablesBridge(state)) is False
    assert _subscription_contract_required(None) is False


def test_review_releases_the_file_from_a_planner_task() -> None:
    _load_models()
    plan, context = _live_plan(monetized=True, repeated_ids=False)
    plan["build_tasks"].append({
        **_task("app_services", "service_foundation", "ConfigMiddlewareAgent", None, ["services/config.py", PATH]),
        "surface_id": "billing_portal", "surface_kind": "external_integration",
    })
    plan["generation_order"].append("app_services")

    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    assert result["outcome"] == "ready", result
    cached = detach(context.get("app_build_plan"))
    assert all(PATH not in task["owned_paths"] for task in cached["build_tasks"])
    queued = detach(context.get("app_task_batch_items"))
    assert all(PATH not in task["owned_paths"] for task in queued)


def test_the_planner_schema_cannot_express_the_task() -> None:
    _load_models()
    plan, context = _live_plan(monetized=True, repeated_ids=False)
    plan["build_tasks"].append({
        **_task("subscription_config", "subscription_config", "ConfigMiddlewareAgent", None, [PATH]),
        "surface_id": "subscription_contract",
    })

    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    assert result["outcome"] == "needs_revision", result
    assert "task_type" in result["error"] and "subscription_config" in result["error"]


def test_the_release_runs_before_validation_in_review_and_in_the_cache() -> None:
    review = inspect.getsource(app_plan_review.review_app_build_plan)
    assert review.index("release_subscriptions_config") < review.index("validate_plan_origins")
    cache = inspect.getsource(cache_module.app_build_plan)
    assert cache.index("release_subscriptions_config") < cache.index("_validate_build_tasks(")


def test_the_injected_contract_no_longer_asks_for_a_task() -> None:
    rendered = _render_contract(CONTRACT)
    assert "task_type='subscription_config'" not in rendered
    assert "AppGenerator assembly writes config/subscriptions.yaml" in rendered
    assert "No build task owns config/subscriptions.yaml" in rendered
