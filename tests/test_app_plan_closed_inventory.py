"""Reject invented surfaces before plan review can repair or schedule them."""

from __future__ import annotations

from copy import deepcopy

import pytest

from factory_app.workflows.AppGenerator.tools.app_plan_review import (
    review_app_build_plan,
    validate_plan_origins,
)
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.frozen import detach
from tests.test_app_plan_managed_facade_repair import _capability, _task
from tests.test_app_plan_task_identity_repair import _live_plan
from tests.test_continuous_deterministic_materialization import _load_models


@pytest.fixture(autouse=True)
def _load_factory_contracts():
    _load_models()


def _approved_plan(*, monetized=True):
    plan, context = _live_plan(monetized=monetized, repeated_ids=False)
    assert isinstance(context, ContextVariablesBridge)
    design = detach(context.get("design_surface_map"))
    if not any(surface["surface_id"] == "app_pages" for surface in design["surfaces"]):
        design["surfaces"].append({
            "surface_id": "app_pages", "surface_kind": "ui_only", "owner": "app",
        })
    context.set("design_surface_map", design)
    page = next(task for task in plan["build_tasks"] if task["task_type"] == "page_bundle")
    page.update(surface_id="app_pages", surface_kind="ui_only")
    if monetized:
        provider = next(task for task in plan["build_tasks"] if task["task_type"] == "api_surface")
        provider["surface_id"] = "mozaikspay_managed"
    return plan, context


def _add_auth(plan, *, surface_kind="module", capability=True, tasks=True):
    if capability:
        plan["capability_packs"].append({
            **_capability("user_auth"), "surface_kind": surface_kind,
        })
    if tasks:
        for kind, agent, files in (
            ("module_contract", "ConfigMiddlewareAgent", ["module.yaml"]),
            ("data_models", "ModelAgent", ["backend/schemas.py"]),
            ("business_services", "ServiceAgent", ["backend/handler.py", "backend/service.py"]),
        ):
            task = _task(
                f"user_auth.{kind}", kind, agent,
                "user_auth" if capability else "billing_pack",
                [f"modules/user_auth/{path}" for path in files],
            )
            task.update(surface_id="user_auth", surface_kind=surface_kind)
            plan["build_tasks"].append(task)
        plan["generation_order"] = [task["task_id"] for task in plan["build_tasks"]]


def _assert_auth_feedback(error):
    message = error.lower()
    assert "user_auth" in message
    assert "unapproved" in message
    assert "remove" in message and "capabilit" in message and "tasks" in message
    assert "relabel" in message
    assert "platform-provided" in message
    assert "no generated module" in message
    assert "matches 0 declared capabilities" not in message
    assert "must name exactly one of" not in message


@pytest.mark.parametrize("shape", ["module_replay", "live_app_policy"])
def test_unapproved_auth_review_returns_actionable_feedback_before_capability_ids(shape):
    plan, context = _approved_plan()
    _add_auth(
        plan, surface_kind="module" if shape == "module_replay" else "app_policy",
        capability=shape == "module_replay",
    )
    original = deepcopy(plan)
    context.set("app_plan_ready", True)
    context.set("app_build_plan", {"stale": True})
    context.set("app_task_batch_items", [{"task_id": "stale"}])

    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    assert result["outcome"] == "needs_revision", result
    assert result["ok"] is False
    _assert_auth_feedback(result["error"])
    assert context.get("app_plan_feedback") == result["error"]
    assert context.get("app_plan_ready") is False
    assert context.get("app_build_plan") is None
    assert not context.get("app_task_batch_items")
    assert plan == original


@pytest.mark.parametrize("capability,tasks", [(True, True), (True, False), (False, True)])
def test_every_capability_and_task_requires_an_approved_surface(capability, tasks):
    plan, context = _approved_plan()
    _add_auth(plan, capability=capability, tasks=tasks)
    original = deepcopy(plan)

    with pytest.raises(ValueError) as error:
        validate_plan_origins(plan, context)

    _assert_auth_feedback(str(error.value))
    assert plan == original


def test_auth_replay_and_live_shape_have_the_same_surface_feedback():
    errors = []
    for surface_kind, capability in (("module", True), ("app_policy", False)):
        plan, context = _approved_plan()
        _add_auth(plan, surface_kind=surface_kind, capability=capability)
        result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)
        assert result["outcome"] == "needs_revision", result
        errors.append(result["error"])
    assert errors[0] == errors[1]


def test_unapproved_surface_remains_feedback_until_review_budget_is_exhausted():
    plan, context = _approved_plan()
    _add_auth(plan)
    original = deepcopy(plan)

    for attempt, outcome in enumerate(("needs_revision", "needs_revision", "blocked"), 1):
        result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)
        assert result["outcome"] == outcome, result
        _assert_auth_feedback(result["error"])
        assert context.get("app_plan_attempts") == attempt
        assert context.get("app_plan_feedback") == result["error"]
        assert context.get("app_build_plan") is None
        assert not context.get("app_task_batch_items")
        assert plan == original


@pytest.mark.parametrize("provider_surface", ["mozaikspay", "mozaikspay_managed"])
def test_selected_provider_facade_and_subscription_surfaces_pass_unchanged(provider_surface):
    plan, context = _approved_plan()
    for item in [*plan["capability_packs"], *plan["build_tasks"]]:
        if item.get("capability_pack_id") == "mozaikspay":
            item["surface_id"] = provider_surface
    # The selected pack's facade contract independently approves billing_portal.
    design = detach(context.get("design_surface_map"))
    design["surfaces"] = [surface for surface in design["surfaces"] if surface["surface_id"] != "billing_portal"]
    context.set("design_surface_map", design)
    original = deepcopy(plan)

    validate_plan_origins(plan, context)
    assert plan == original
    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    assert result["outcome"] == "ready", result
    assert plan == original
    cached = detach(context.get("app_build_plan"))
    assert {
        (pack["capability_pack_id"], pack["surface_id"], pack["surface_kind"])
        for pack in cached["capability_packs"]
    } == {
        (pack["capability_pack_id"], pack["surface_id"], pack["surface_kind"])
        for pack in original["capability_packs"]
    }
    assert {
        (task["task_id"], task["surface_id"], task["surface_kind"])
        for task in cached["build_tasks"]
    } == {
        (task["task_id"], task["surface_id"], task["surface_kind"])
        for task in original["build_tasks"]
    }
    assert result["task_count"] == len(original["build_tasks"])
    assert context.get("app_plan_feedback") == ""



def test_approved_plan_remains_unchanged_on_repeated_review():
    plan, context = _approved_plan(monetized=False)
    original = deepcopy(plan)
    first = review_app_build_plan(AppBuildPlan=plan, context_variables=context)
    assert first["outcome"] == "ready", first
    stable = detach(context.get("app_build_plan"))
    validate_plan_origins(stable, context)
    assert detach(context.get("app_build_plan")) == stable
    fresh_context = ContextVariablesBridge(context.snapshot())
    fresh_context.set("app_plan_attempts", 0)

    second = review_app_build_plan(AppBuildPlan=plan, context_variables=fresh_context)

    assert second["outcome"] == "ready", second
    assert detach(fresh_context.get("app_build_plan")) == stable
    assert plan == original


def test_cache_normalization_cannot_introduce_an_unselected_provider_surface():
    plan, context = _approved_plan(monetized=False)
    plan["revenue_model"] = "subscription"
    plan["build_tasks"].append({
        **_task("subscription_config", "subscription_config", "ConfigMiddlewareAgent", None, ["config/subscriptions.yaml"]),
        "surface_id": "subscription_contract", "surface_kind": "app_policy",
    })
    context.set("subscription_contract", {
        "contract_required": True,
        "subscription_config_file": {"plans": [{"plan_id": "free"}, {"plan_id": "pro"}]},
    })
    original = deepcopy(plan)
    validate_plan_origins(plan, context)

    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    assert result["outcome"] == "needs_revision", result
    assert "unapproved surface 'mozaikspay_managed'" in result["error"]
    assert context.get("app_plan_feedback") == result["error"]
    assert context.get("app_plan_ready") is False
    assert context.get("app_build_plan") is None
    assert not context.get("app_task_batch_items")
    assert plan == original


def test_subscription_surface_accepts_the_approved_artifact_fallback():
    plan, context = _approved_plan()
    contract = detach(context.get("subscription_contract"))
    context.set("subscription_contract", None)
    context.set("subscription_contract_artifact", {
        "commit_metadata": {"metadata": {"summary_payload": contract}},
    })

    validate_plan_origins(plan, context)
    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    assert result["outcome"] == "ready", result
    subscription = [
        task for task in detach(context.get("app_build_plan"))["build_tasks"]
        if task["surface_id"] == "subscription_contract"
    ]
    assert len(subscription) == 1
    assert subscription[0]["task_type"] == "subscription_config"


def test_subscription_surface_requires_approved_subscription_contract():
    plan, context = _approved_plan()
    context.set("subscription_contract", None)

    with pytest.raises(ValueError, match="subscription_contract"):
        validate_plan_origins(plan, context)


@pytest.mark.parametrize("unapproved_use", ["capability", "other_task"])
def test_subscription_contract_does_not_approve_arbitrary_capabilities_or_tasks(unapproved_use):
    plan, context = _approved_plan()
    if unapproved_use == "capability":
        plan["capability_packs"].append({
            **_capability("subscription_contract"), "surface_kind": "app_policy",
        })
    else:
        task = next(task for task in plan["build_tasks"] if task["task_type"] == "subscription_config")
        task["task_type"] = "service_foundation"

    with pytest.raises(ValueError, match="subscription_contract"):
        validate_plan_origins(plan, context)


def test_unselected_catalog_provider_is_not_an_approved_surface():
    plan, context = _approved_plan()
    context.set("available_managed_capabilities", [{
        "id": "unselected_provider", "capability_source": "managed_capability",
    }])
    plan["capability_packs"].append({
        **_capability("unselected_provider"),
        "surface_kind": "external_integration", "capability_source": "managed_capability",
    })

    with pytest.raises(ValueError, match="unapproved.*unselected_provider|unselected_provider.*unapproved"):
        validate_plan_origins(plan, context)
