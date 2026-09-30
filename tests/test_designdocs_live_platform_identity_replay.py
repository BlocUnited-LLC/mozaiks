"""Replay the live c0d1e58d DesignDocs attempts through the real save boundary and plan review.

The fixture holds the model's four structured outputs from DesignDocs chat
c0d1e58d (OSS f4f53338), read from the local AG2 network WAL, with that chat's
context variables. The model designed the platform's user system as an app
module named ``user_management`` (entity User, a users collection with
password_hash, create_user/delete_user, a Users page at /users). Live, every
attempt was refused because ``created_at`` was typed ``date``, which the
ownership check did not count as bounded; the channel closed workflow_failed.
"""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from factory_app.workflows._shared.hook_utils import workflow_context_path
from factory_app.workflows.AppGenerator.tools.app_plan_review import (
    review_app_build_plan,
    validate_plan_coverage,
)
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.frozen import detach
from tests import test_designdocs_monetization_inventory as inventory
from tests.test_app_plan_managed_facade_repair import (
    _capability,
    _facade_contract,
    _provider_descriptor,
    _task,
)
from tests.test_continuous_deterministic_materialization import _load_models, _plan_payload

FIXTURE = Path(__file__).parent / "fixtures" / "designdocs_live_c0d1e58d.json"
APPROVED_PAGES = [("Dashboard", "/dashboard"), ("Pricing", "/pricing"), ("Billing", "/billing"), ("Usage", "/usage")]
RECORD = {
    "surface_id": "user_management", "owner": "platform", "removed_collections": ["users"],
    "removed_mutations": ["create_user", "delete_user"],
    "removed_events": ["domain.users.user_created"],
    "removed_pages": [{"name": "Users", "route": "/users", "builtin_panel": "users", "admin_page": "access"}],
}
MARKER = (
    "DESIGN_OWNERSHIP_NORMALIZED surface=user_management owner=platform removed=[users] "
    "removed_mutations=[create_user,delete_user] removed_events=[domain.users.user_created] "
    "removed_pages=[Users@/users->admin:users]"
)
persistence = inventory.persistence


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _save_attempt(index: int) -> tuple[ContextVariablesBridge, dict]:
    fixture = _fixture()
    context = ContextVariablesBridge(deepcopy(fixture["context_vars"]))
    bundle = deepcopy(fixture["attempts"][index])
    submitted = deepcopy(bundle)
    result = inventory._save(context, bundle)
    assert bundle == submitted, "the model's structured output is never edited in place"
    return context, result


def test_fixture_records_the_live_evidence():
    fixture = _fixture()
    attempts = fixture["attempts"]
    assert len(attempts) == 4 and fixture["live_close_reason"] == "workflow_failed"
    assert "unbounded fields=['created_at']" in fixture["live_rejection"]
    for attempt in attempts[1:]:
        for key in ("surface_map", "data_contract", "experience_spec", "frontend_markdown", "database_markdown"):
            assert attempt[key] == attempts[0][key]
    surfaces = {surface["surface_id"]: surface for surface in attempts[0]["surface_map"]["surfaces"]}
    users = surfaces["user_management"]
    assert (users["owner"], users["primary_entities"], users["owned_mutations"], users["owned_pages"]) == (
        "app", ["User"], ["create_user", "delete_user"], ["Users"],
    )
    # The platform auth rule does not name this surface: recognition cannot depend on the name.
    catalog = yaml.safe_load(
        workflow_context_path("AppGenerator", "capability_routing.yaml").read_text(encoding="utf-8"),
    )
    auth = catalog["layers"]["runtime_provided"]["surface_ownership"][0]
    assert auth["platform_capability"] == "authentication"
    assert "user_management" not in auth["surface_ids"]
    collection = attempts[0]["data_contract"]["surfaces"][1]["collections"][0]
    assert collection["name"] == "users"
    assert {field["name"]: field["type"] for field in collection["fields"]} == {
        "user_id": "string", "email": "string", "password_hash": "string", "created_at": "date",
    }
    assert ("Users", "/users") in {(page["name"], page["route"]) for page in attempts[0]["experience_spec"]["pages"]}


@pytest.mark.parametrize("index", [0, 1, 2, 3], ids=["attempt1", "attempt2", "attempt3", "attempt4"])
def test_live_attempt_saves_user_management_as_platform_identity(persistence, index, caplog):
    store, _, summary = persistence

    with caplog.at_level(logging.INFO):
        context, result = _save_attempt(index)

    assert result["outcome"] == "saved", result
    experience = detach(context.get("experience_spec"))
    assert [(page["name"], page["route"]) for page in experience["pages"]] == APPROVED_PAGES
    # Nothing points the generated app at the admin portal's users panel, which is not served yet.
    assert "/admin" not in json.dumps(experience)
    surfaces = {surface["surface_id"]: surface for surface in detach(context.get("design_surface_map"))["surfaces"]}
    users = surfaces["user_management"]
    assert users["owner"] == "platform"
    assert users["primary_entities"] == users["owned_mutations"] == users["custom_reads"] == []
    assert users["events_emitted"] == users["owned_pages"] == []
    tasks = surfaces["task_management"]
    assert (tasks["owner"], tasks["primary_entities"], tasks["owned_pages"]) == ("app", ["Task"], ["Dashboard"])
    assert tasks["owned_mutations"] == ["create_task", "delete_task", "update_task"]
    assert tasks["custom_reads"] == ["summarize_tasks"]
    data = detach(context.get("data_contract"))
    assert [
        (group["surface_id"], collection["name"]) for group in data["surfaces"] for collection in group["collections"]
    ] == [("task_management", "tasks")]
    assert summary.await_args.kwargs["summary_payload"]["ownership_normalizations"] == [RECORD]
    assert MARKER in caplog.text
    saved = {call.kwargs["kind"]: call.kwargs for call in store.upsert_design_doc.await_args_list}
    assert MARKER in saved["backend"]["content"]
    assert saved["ui_schema"]["extra_fields"]["experience_spec"] == experience
    ui_schema = yaml.safe_load(saved["ui_schema"]["content"])
    assert [(page["name"], page["route"]) for page in ui_schema["pages"]] == APPROVED_PAGES
    assert ui_schema["ownership_normalizations"] == [RECORD]


def _plan_from_saved_design(context: ContextVariablesBridge) -> dict:
    """A plan that states only what the saved design approved."""
    experience = detach(context.get("experience_spec"))
    facade_pages = {page["route"]: page for page in _facade_contract()["pages"]}
    operations = ["create_task", "update_task", "delete_task", "summarize_tasks"]
    plan = _plan_payload()["AppBuildPlan"]
    plan.update(
        agent_message="Build TaskTracker Pro from the approved design.", app_kind="saas",
        readiness_profile="none", revenue_model="subscription", monetization_provider="mozaiks_pay",
        auth_strategy="basic-login", roles=[], service_scope=["auth"],
        frontend_scope=["billing_portal", "task_management"],
        entities=[{"name": "Task", "operations": operations, "notes": None}],
    )
    plan["pages"] = [
        {
            "name": page["name"], "route": page["route"], "purpose": page["intent"],
            "primary_entities": list(facade_pages.get(page["route"], {}).get("primary_entities") or (
                ["Task"] if page["route"] == "/dashboard" else []
            )),
            "primary_actions": list(facade_pages.get(page["route"], {}).get("primary_actions") or []),
            "ui_layout": "full-width", "ui_surface": "declarative_page",
            "page_type_hint": facade_pages.get(page["route"], {}).get("page_type_hint") or "analytics_dashboard",
            "sections_hint": [],
        }
        for page in experience["pages"]
    ]
    task_pack = _capability("task_management", entities=["Task"])
    task_pack.update(primary_pages=["Dashboard"], operations=operations)
    plan["capability_packs"] = [task_pack, {
        "capability_pack_id": "mozaikspay", "surface_id": "billing_portal", "surface_kind": "module",
        "capability_source": "managed_capability", "pack_type": "mozaikspay", "label": "MozaiksPay",
        "summary": "Managed subscription provider.", "implementation_mode": "external_integration",
        "primary_entities": [],
    }]
    # The selected MozaiksPay pack ships billing_portal, its client and its pages from templates;
    # pack-owned outputs are never model work, so a correct plan schedules no task for them.
    page_paths = ["app.json", *[
        f"ui/pages/{page['route'][1:]}.yaml" for page in experience["pages"]
        if page["route"] not in {"/pricing", "/billing", "/usage"}
    ]]
    plan["build_tasks"] = [
        _task("task_management.module_contract", "module_contract", "ConfigMiddlewareAgent", "task_management",
              ["modules/task_management/module.yaml"]),
        _task("task_management.data_models", "data_models", "ModelAgent", "task_management",
              ["modules/task_management/backend/schemas.py"], dependencies=["task_management.module_contract"]),
        _task("task_management.business_services", "business_services", "ServiceAgent", "task_management",
              [f"modules/task_management/backend/{name}.py" for name in ("handler", "service", "repo", "policy")],
              dependencies=["task_management.module_contract", "task_management.data_models"]),
        {
            **_task("page_bundle", "page_bundle", "AppSchemaAgent", None, page_paths,
                    dependencies=["task_management.business_services"]),
            "surface_id": "task_management", "surface_kind": "module",
        },
    ]
    plan["generation_order"] = [task["task_id"] for task in plan["build_tasks"]]
    return plan


def test_plan_from_the_saved_live_design_passes_review(persistence):
    models = _load_models()
    context, result = _save_attempt(0)
    assert result["outcome"] == "saved", result
    plan_context = ContextVariablesBridge({
        "build_mode": "initial", "app_plan_attempts": 0, "app_plan_outcome": "blocked",
        "monetization_enabled": True,
        "design_surface_map": detach(context.get("design_surface_map")),
        "experience_spec": detach(context.get("experience_spec")),
        "data_contract": detach(context.get("data_contract")),
        "capability_packs": [_provider_descriptor()],
        "subscription_contract": {
            "contract_required": True,
            "subscription_config_file": {"plans": [{"plan_id": "free"}, {"plan_id": "pro"}]},
        },
    })
    plan = _plan_from_saved_design(context)
    models["AppBuildPlan"].model_validate(plan)

    validate_plan_coverage(plan, plan_context)
    outcome = review_app_build_plan(AppBuildPlan=plan, context_variables=plan_context)

    assert outcome["outcome"] == "ready", outcome
    cached = detach(plan_context.get("app_build_plan"))
    assert [(page["name"], page["route"]) for page in cached["pages"]] == APPROVED_PAGES
    page_bundle = next(task for task in cached["build_tasks"] if task["task_type"] == "page_bundle")
    assert sorted(page_bundle["owned_paths"]) == ["app.json", "ui/pages/dashboard.yaml"]
    owned = [path for task in cached["build_tasks"] for path in task.get("owned_paths") or []]
    assert not any("user" in path for path in owned), owned
    assert {pack["capability_pack_id"] for pack in cached["capability_packs"]} >= {"task_management", "mozaikspay"}
    validate_plan_coverage(cached, plan_context)
