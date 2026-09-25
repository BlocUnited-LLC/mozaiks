"""A task with no degrees of freedom is materialized, not requested.

The 2026-09-25 acceptance run at OSS 27ebdedc is the first where the
instruction is provably in front of the agent. #720's logging shows the
contract reaching the planner on every attempt:

    SUBSCRIPTION_CONTRACT_CONTEXT injected agent=AppPlanAgent
      contract_required=True plans=2 chars=5510      (x3)

That injected section says, verbatim, that AppBuildPlan must include a build
task with task_type='subscription_config', surface_kind='app_policy',
owned_paths=['config/subscriptions.yaml']. The planner's own prompt says it
twice more. The plan omitted it on all three attempts and was rejected each
time:

    AppBuildPlan.monetization_provider is only valid when build_tasks include
    task_type='subscription_config'.

Every field of that task is dictated by the contract -- task_type,
capability_pack_id, surface_id, surface_kind, initial_agent and owned_paths
are all fixed, and initial_message just says to serialize
subscription_config_file. There is no planning decision in it. Asking a model
to reproduce a fixed structure, and failing the build when it does not, is a
templating job dressed as planning.

So the repair lane materializes it, exactly as _repair_coverage and
_repair_contract_task_operations already do for tasks the planner should have
emitted and did not.

Five earlier runs failed at this same gate for five different reasons -- an
app_kind that reframed the plan (#715), an unread contract fallback (#716), a
no-op rejection message (#717), a dead injector (#718), and seventeen dead
context readers (#719). This is the one that remains once the agent can
demonstrably see what it was asked for.
"""

from __future__ import annotations

from typing import Any

from factory_app.workflows.AppGenerator.tools.app_build_plan import (
    _apply_default_monetization_provider,
    _validate_monetization_provider_selection,
)
from factory_app.workflows.AppGenerator.tools.app_plan_review import (
    _repair_subscription_config_task,
    _resolved_subscription_contract,
)
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge

CONTRACT = {
    "contract_required": True,
    "subscription_config_file": {"plans": [{"plan_id": "free"}, {"plan_id": "pro"}]},
}
# The live shape at 27ebdedc: state carries nothing, the artifact carries the contract.
LIVE = {
    "subscription_contract": None,
    "subscription_contract_artifact": {"metadata": {"summary_payload": CONTRACT}},
}


def _ctx(**overrides: Any) -> ContextVariablesBridge:
    return ContextVariablesBridge({**LIVE, **overrides})


def _plan(*tasks: dict) -> dict:
    return {
        "build_tasks": list(tasks) or [{"task_id": "m1", "task_type": "module_contract"}],
        "capability_packs": [],
        "monetization_provider": "mozaiks_pay",
    }


def _subscription_tasks(plan: dict) -> list[dict]:
    return [t for t in plan["build_tasks"] if t.get("task_type") == "subscription_config"]


def test_the_task_is_added_when_the_contract_requires_one() -> None:
    plan = _plan()
    repairs = _repair_subscription_config_task(plan, _ctx())
    assert repairs, "the repair must report what it did, like every other repair"
    (task,) = _subscription_tasks(plan)
    assert task["capability_pack_id"] is None
    assert task["surface_id"] == "subscription_contract"
    assert task["surface_kind"] == "app_policy"
    assert task["initial_agent"] == "ConfigMiddlewareAgent"
    assert task["owned_paths"] == ["config/subscriptions.yaml"]


def test_it_resolves_the_contract_from_the_artifact_fallback() -> None:
    """State was null on every run that reached this gate."""
    assert _resolved_subscription_contract(_ctx()) == CONTRACT


def test_it_resolves_the_contract_from_state_when_present() -> None:
    ctx = ContextVariablesBridge({"subscription_contract": CONTRACT, "subscription_contract_artifact": None})
    assert _resolved_subscription_contract(ctx) == CONTRACT


def test_a_plan_that_already_has_the_task_is_untouched() -> None:
    plan = _plan({"task_id": "existing", "task_type": "subscription_config", "owned_paths": ["config/subscriptions.yaml"]})
    assert _repair_subscription_config_task(plan, _ctx()) == []
    assert len(_subscription_tasks(plan)) == 1, "the repair must not duplicate the planner's own task"


def test_nothing_is_added_without_a_contract() -> None:
    plan = _plan()
    ctx = ContextVariablesBridge({"subscription_contract": None, "subscription_contract_artifact": None})
    assert _repair_subscription_config_task(plan, ctx) == []
    assert _subscription_tasks(plan) == []


def test_nothing_is_added_when_the_contract_says_no() -> None:
    """An unmonetized app must not acquire a subscription config."""
    plan = _plan()
    ctx = ContextVariablesBridge({"subscription_contract": {"contract_required": False}})
    assert _repair_subscription_config_task(plan, ctx) == []
    assert _subscription_tasks(plan) == []


def test_the_repaired_plan_clears_the_live_rejection() -> None:
    """End to end in the order app_build_plan uses: apply provider, then validate."""
    plan = _plan()

    packs, provider = _apply_default_monetization_provider(
        plan["capability_packs"], plan["build_tasks"],
        monetization_provider=plan["monetization_provider"], context_variables=_ctx(),
    )
    try:
        _validate_monetization_provider_selection(packs, plan["build_tasks"], monetization_provider=provider)
        raise AssertionError("the live run's rejection should reproduce before the repair")
    except ValueError as error:
        assert "subscription_config" in str(error)

    _repair_subscription_config_task(plan, _ctx())

    packs, provider = _apply_default_monetization_provider(
        plan["capability_packs"], plan["build_tasks"],
        monetization_provider=plan["monetization_provider"], context_variables=_ctx(),
    )
    assert "mozaikspay" in [p.get("capability_pack_id") for p in packs], (
        "the task is what makes the provider pack resolvable; that is why order matters"
    )
    _validate_monetization_provider_selection(packs, plan["build_tasks"], monetization_provider=provider)


def test_the_repair_runs_before_validation() -> None:
    """Guard the wiring: a repair registered after the validators would never help."""
    import inspect

    from factory_app.workflows.AppGenerator.tools import app_plan_review

    source = inspect.getsource(app_plan_review.review_app_build_plan)
    assert "_repair_subscription_config_task" in source, "the repair must be registered"
    assert source.index("_repair_subscription_config_task") < source.index("validate_plan_origins"), (
        "repairs run before validation; registering it later would leave the rejection in place"
    )
