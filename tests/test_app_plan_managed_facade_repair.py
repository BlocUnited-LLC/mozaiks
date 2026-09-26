"""Exercise managed facade planning through the runtime context and review gate."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.app_plan_review import (
    review_app_build_plan,
    validate_plan_origins,
)
from mozaiksai.core.session.build_context import discover_pack_descriptors
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.frozen import detach
from tests.test_continuous_deterministic_materialization import _load_models, _plan_payload

ROOT = Path(__file__).resolve().parents[1]
PACK_ROOT = ROOT / "factory_app/build_context/mozaikspay"


@pytest.fixture(autouse=True)
def _load_factory_contracts():
    _load_models()


def _provider_descriptor():
    config = yaml.safe_load((PACK_ROOT / "context.yaml").read_text(encoding="utf-8"))
    return discover_pack_descriptors(PACK_ROOT, config)[0]


def _facade_contract():
    contract = yaml.safe_load((PACK_ROOT / "contract.yaml").read_text(encoding="utf-8"))
    return contract["facades"][0]


def _capability(module_id, *, entities=None):
    return {
        "capability_pack_id": module_id, "surface_id": module_id,
        "surface_kind": "module", "capability_source": "generated_module",
        "pack_type": "custom_domain", "label": module_id.replace("_", " ").title(),
        "summary": f"App-owned {module_id} module.", "implementation_mode": "hybrid",
        "primary_entities": list(entities or []), "primary_pages": [], "operations": [],
    }


def _task(task_id, kind, agent, pack_id, paths, *, dependencies=()):
    return {
        "task_id": task_id, "task_type": kind, "capability_pack_id": pack_id,
        "surface_id": pack_id, "surface_kind": "module", "execution_target": "AppGenerator",
        "initial_agent": agent, "description": f"Materialize {kind} for {pack_id}",
        "initial_message": f"Materialize the approved {pack_id} contract.",
        "owned_paths": paths, "depends_on": list(dependencies),
    }


def _plan_and_context(*, monetized=True, collapsed=True):
    plan = _plan_payload()["AppBuildPlan"]
    plan.update(
        agent_message="Build the approved task management app.", readiness_profile="none",
        revenue_model="subscription" if monetized else "free",
        monetization_provider="mozaiks_pay" if monetized else None,
        entities=[{"name": "Task", "operations": ["create", "read"], "notes": None}],
        service_scope=["task_management"], frontend_scope=["dashboard", "tasks"],
    )
    if not monetized:
        plan.pop("monetization_provider")
    pages = [{"name": name, "route": f"/{name.lower()}"} for name in ("Dashboard", "Tasks")]
    if monetized:
        pages += deepcopy(_facade_contract()["pages"])
    plan["pages"] = [
        {
            **page, "purpose": f"Use {page['name'].lower()}",
            "primary_entities": page.get("primary_entities", []),
            "primary_actions": page.get("primary_actions", []),
            "ui_layout": "full-width", "ui_surface": "declarative_page",
            "page_type_hint": page.get("page_type_hint", "analytics_dashboard"),
            "sections_hint": [],
        }
        for page in pages
    ]
    task_pack = _capability("task_management", entities=["Task"])
    task_pack.update(primary_pages=["Dashboard", "Tasks"], operations=["create_task", "list_tasks"])
    plan["capability_packs"] = [task_pack]
    plan["build_tasks"] = [
        _task("6", "module_contract", "ConfigMiddlewareAgent", "task_management", ["modules/task_management/module.yaml"]),
        _task("7", "data_models", "ModelAgent", "task_management", ["modules/task_management/backend/schemas.py"], dependencies=["6"]),
        _task("8", "business_services", "ServiceAgent", "task_management", [
            f"modules/task_management/backend/{name}.py" for name in ("handler", "service", "repo", "policy")
        ], dependencies=["6", "7"]),
        {
            **_task("1", "page_bundle", "AppSchemaAgent", None, [
                "app.json", "brand/theme_config.json", *[f"ui/pages/{page['route'][1:]}.yaml" for page in pages],
            ]),
            "surface_id": "task_management", "surface_kind": "module",
        },
    ]
    surfaces = [{
        "surface_id": "task_management", "surface_kind": "module", "owner": "app",
        "primary_entities": ["Task"], "owned_pages": ["Dashboard", "Tasks"],
    }]
    if monetized:
        surfaces.append({
            "surface_id": "billing_portal", "surface_kind": "module", "owner": "app",
            "label": "Billing Portal", "primary_entities": [],
            "source_capability_packs": ["mozaikspay"], "owned_pages": ["Pricing", "Billing", "Usage"],
        })
        plan["capability_packs"].append({
            "capability_pack_id": "mozaikspay",
            "surface_id": "billing_portal" if collapsed else "mozaikspay_managed",
            "surface_kind": "module" if collapsed else "external_integration",
            "capability_source": "managed_capability", "pack_type": "mozaikspay",
            "label": "MozaiksPay", "summary": "Managed subscription provider.",
            "implementation_mode": "external_integration", "primary_entities": [],
        })
        if not collapsed:
            facade = _capability("billing_portal")
            facade.update(
                primary_pages=["Pricing", "Billing", "Usage"],
                operations=list(dict.fromkeys(action for page in _facade_contract()["pages"] for action in page["primary_actions"])),
            )
            plan["capability_packs"].append(facade)
        plan["build_tasks"] += [
            {
                **_task("2", "api_surface", "ControllerAgent", "mozaikspay", ["services/integrations/mozaikspay_client.py"]),
                "surface_id": "billing_portal" if collapsed else "mozaikspay_managed",
                "surface_kind": "module" if collapsed else "external_integration",
            },
            _task("3", "module_contract", "ConfigMiddlewareAgent", "billing_portal", ["modules/billing_portal/module.yaml"]),
            _task("4", "data_models", "ModelAgent", "billing_portal", ["modules/billing_portal/backend/schemas.py"], dependencies=["3"]),
            _task("5", "business_services", "ServiceAgent", "billing_portal", [
                "modules/billing_portal/backend/handler.py", "modules/billing_portal/backend/service.py",
            ], dependencies=["3", "4", "2"]),
        ]
    plan["generation_order"] = [task["task_id"] for task in plan["build_tasks"]]
    context = ContextVariablesBridge({
        "build_mode": "initial", "app_plan_attempts": 0, "app_plan_outcome": "blocked",
        "monetization_enabled": monetized, "design_surface_map": {"surfaces": surfaces},
        "experience_spec": {"pages": pages},
        "capability_packs": [_provider_descriptor()] if monetized else [],
        "subscription_contract": {
            "contract_required": True,
            "subscription_config_file": {"plans": [{"plan_id": "free"}, {"plan_id": "pro"}]},
        } if monetized else None,
    })
    return plan, context


def _assert_reviewed_facade(plan, context):
    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)
    assert result["outcome"] == "ready", result
    cached = detach(context.get("app_build_plan"))
    validate_plan_origins(cached, context)
    packs = {pack["capability_pack_id"]: pack for pack in cached["capability_packs"]}
    assert packs["billing_portal"]["capability_source"] == "generated_module"
    assert packs["billing_portal"]["surface_id"] == "billing_portal"
    assert packs["billing_portal"]["primary_entities"] == []
    required_actions = {action for page in _facade_contract()["pages"] for action in page["primary_actions"]}
    assert required_actions <= set(packs["billing_portal"]["operations"])
    assert {page.lower() for page in packs["billing_portal"]["primary_pages"]} == {"pricing", "billing", "usage"}
    assert packs["mozaikspay"]["capability_source"] == "managed_capability"
    assert packs["mozaikspay"]["surface_kind"] == "external_integration"
    assert packs["mozaikspay"]["surface_id"] != "billing_portal"
    facade_paths = {
        path for task in cached["build_tasks"] if task["capability_pack_id"] == "billing_portal"
        for path in task["owned_paths"]
    }
    assert "modules/billing_portal/backend/repo.py" not in facade_paths
    assert "modules/billing_portal/backend/policy.py" not in facade_paths
    provider_task = next(task for task in cached["build_tasks"] if task["task_id"] == "2")
    assert provider_task["capability_pack_id"] == "mozaikspay"
    assert provider_task["surface_id"] == packs["mozaikspay"]["surface_id"]
    assert provider_task["surface_kind"] == "external_integration"
    assert provider_task["owned_paths"] == ["services/integrations/mozaikspay_client.py"]
    assert {(page["name"], page["route"]) for page in cached["pages"]} == {
        ("Dashboard", "/dashboard"), ("Tasks", "/tasks"), ("Pricing", "/pricing"),
        ("Billing", "/billing"), ("Usage", "/usage"),
    }
    return cached


def test_unapproved_duplicate_facade_is_rejected_before_review_can_drop_it():
    plan, context = _plan_and_context()
    duplicate = _capability("billing_module")
    duplicate.update(
        primary_pages=["pricing", "billing", "usage"],
        operations=["list_plans", "get_subscription_status", "get_usage_status"],
    )
    plan["capability_packs"].append(duplicate)
    before = deepcopy(plan)

    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    assert result["outcome"] == "needs_revision", result
    assert "billing_module" in result["error"]
    assert "unapproved" in result["error"].lower()
    assert "relabel" in result["error"].lower()
    assert not context.get("app_task_batch_items")
    assert plan == before


def test_correct_provider_and_facade_survive_public_review():
    plan, context = _plan_and_context(collapsed=False)
    _assert_reviewed_facade(plan, context)


def test_unmonetized_plan_survives_public_review_without_billing():
    plan, context = _plan_and_context(monetized=False)
    before = deepcopy(plan)
    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)
    assert result["outcome"] == "ready", result
    assert plan == before
    cached = detach(context.get("app_build_plan"))
    assert [pack["capability_pack_id"] for pack in cached["capability_packs"]] == ["task_management"]
    assert not any("billing_portal" in path for task in cached["build_tasks"] for path in task["owned_paths"])


@pytest.mark.parametrize("monetized", [False, True], ids=["unmonetized", "correct_facade"])
def test_repair_leaves_an_already_correct_plan_untouched(monetized):
    from factory_app.workflows.AppGenerator.tools.app_plan_review import (
        _repair_managed_facade_capabilities,
    )

    plan, context = _plan_and_context(monetized=monetized, collapsed=False)
    before = deepcopy(plan)
    assert _repair_managed_facade_capabilities(plan, context) == []
    assert plan == before


def test_same_surface_alias_preserves_tasks_and_retargets_owned_module_paths():
    plan, context = _plan_and_context()
    duplicate = _capability("billing_module")
    duplicate["surface_id"] = "billing_portal"
    plan["capability_packs"].append(duplicate)
    for task in plan["build_tasks"]:
        if task["capability_pack_id"] == "billing_portal":
            task["capability_pack_id"] = "billing_module"
            task["owned_paths"] = [path.replace("modules/billing_portal/", "modules/billing_module/") for path in task["owned_paths"]]
    cached = _assert_reviewed_facade(plan, context)
    assert not any(pack["capability_pack_id"] == "billing_module" for pack in cached["capability_packs"])
    tasks = {task["task_id"]: task for task in cached["build_tasks"]}
    assert tasks["3"]["owned_paths"] == ["modules/billing_portal/module.yaml"]
    assert tasks["4"]["capability_pack_id"] == "billing_portal"
    assert "3" in tasks["4"]["depends_on"]
    assert {"2", "3", "4"} <= set(tasks["5"]["depends_on"])


def test_separately_approved_billing_module_is_preserved():
    plan, context = _plan_and_context()
    other = _capability("billing_module")
    other["operations"] = ["list_invoices"]
    plan["capability_packs"].append(other)
    design = detach(context.get("design_surface_map"))
    design["surfaces"].append({
        "surface_id": "billing_module", "surface_kind": "module", "owner": "app",
        "primary_entities": [], "owned_pages": [],
    })
    context.set("design_surface_map", design)
    cached = _assert_reviewed_facade(plan, context)
    other_after = next(pack for pack in cached["capability_packs"] if pack["capability_pack_id"] == "billing_module")
    assert other_after["operations"] == ["list_invoices"]
    assert other_after["surface_id"] == "billing_module"


def test_facade_descriptor_display_entities_do_not_invent_app_persistence():
    plan, context = _plan_and_context(collapsed=False)
    descriptor = _provider_descriptor()
    descriptor["facades"] = [_facade_contract()]
    context.set("capability_packs", [descriptor])
    _assert_reviewed_facade(plan, context)


def test_conflicting_task_ownership_remains_rejected():
    plan, context = _plan_and_context()
    alias = _capability("billing_module")
    alias["surface_id"] = "billing_portal"
    plan["capability_packs"].append(alias)
    plan["build_tasks"].append({
        **_task("conflicting.contract", "module_contract", "ConfigMiddlewareAgent", "billing_module", ["modules/billing_module/module.yaml"]),
        "surface_id": "billing_portal",
    })
    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)
    assert result["outcome"] == "needs_revision", result
    assert "own" in result["error"].lower()
    assert not context.get("app_task_batch_items")


def test_ambiguous_app_capabilities_claiming_one_facade_remain_rejected():
    plan, context = _plan_and_context()
    for module_id, entity in (("billing_one", "Invoice"), ("billing_two", "Refund")):
        alias = _capability(module_id, entities=[entity])
        alias["surface_id"] = "billing_portal"
        plan["capability_packs"].append(alias)
    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)
    assert result["outcome"] == "needs_revision", result
    assert "billing_portal" in result["error"]
    assert not context.get("app_task_batch_items")


def test_competing_provider_declarations_remain_rejected():
    plan, context = _plan_and_context()
    provider = next(pack for pack in plan["capability_packs"] if pack["capability_pack_id"] == "mozaikspay")
    plan["capability_packs"].append(deepcopy(provider))
    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)
    assert result["outcome"] == "needs_revision", result
    assert "billing_portal" in result["error"]
    assert not context.get("app_task_batch_items")


def test_missing_facade_ownership_is_not_guessed_from_provider_name():
    from factory_app.workflows.AppGenerator.tools.app_plan_review import (
        _repair_managed_facade_capabilities,
    )

    plan, context = _plan_and_context()
    design = detach(context.get("design_surface_map"))
    design["surfaces"][1]["source_capability_packs"] = []
    context.set("design_surface_map", design)
    before = deepcopy(plan)
    assert _repair_managed_facade_capabilities(plan, context) == []
    assert plan == before


@pytest.mark.parametrize("extra_scope", [
    {"operations": ["download_invoice"]},
    {"primary_pages": ["Invoices"]},
], ids=["extra_action", "extra_page"])
def test_same_surface_alias_with_unproven_scope_is_preserved_and_rejected(extra_scope):
    from factory_app.workflows.AppGenerator.tools.app_plan_review import (
        _repair_managed_facade_capabilities,
    )

    plan, context = _plan_and_context()
    alias = {**_capability("billing_module"), "surface_id": "billing_portal", **extra_scope}
    plan["capability_packs"].append(alias)
    before = deepcopy(alias)

    _repair_managed_facade_capabilities(plan, context)

    assert next(pack for pack in plan["capability_packs"] if pack["capability_pack_id"] == "billing_module") == before
    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)
    assert result["outcome"] == "needs_revision", result
    assert "billing_portal" in result["error"]
    assert not context.get("app_task_batch_items")


def test_same_surface_alias_with_independently_approved_identity_is_not_consolidated():
    from factory_app.workflows.AppGenerator.tools.app_plan_review import (
        _repair_managed_facade_capabilities,
    )

    plan, context = _plan_and_context()
    alias = {**_capability("billing_module"), "surface_id": "billing_portal"}
    plan["capability_packs"].append(alias)
    design = detach(context.get("design_surface_map"))
    design["surfaces"].append({
        "surface_id": "billing_module", "surface_kind": "module", "owner": "app",
        "primary_entities": [], "owned_pages": [],
    })
    context.set("design_surface_map", design)
    before = deepcopy(alias)

    _repair_managed_facade_capabilities(plan, context)

    assert next(pack for pack in plan["capability_packs"] if pack["capability_pack_id"] == "billing_module") == before
    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)
    assert result["outcome"] == "needs_revision", result
    assert "billing_portal" in result["error"]
    assert not context.get("app_task_batch_items")


def test_repairing_the_reported_plan_a_second_time_is_a_noop():
    from factory_app.workflows.AppGenerator.tools.app_plan_review import (
        _repair_managed_facade_capabilities,
    )

    plan, context = _plan_and_context()
    duplicate = _capability("billing_module")
    duplicate.update(
        primary_pages=["pricing", "billing", "usage"],
        operations=["list_plans", "get_subscription_status", "get_usage_status"],
    )
    plan["capability_packs"].append(duplicate)
    assert _repair_managed_facade_capabilities(plan, context)
    repaired = deepcopy(plan)

    assert _repair_managed_facade_capabilities(plan, context) == []
    assert plan == repaired
