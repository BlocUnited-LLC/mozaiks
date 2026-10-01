"""Replay the live f2504efe DesignDocs attempts through the real save boundary and plan review.

The fixture holds the model's two structured outputs from DesignDocs chat
f2504efe (OSS f922404a), read from the local AG2 network WAL, with that chat's
context variables. Live, both attempts were rejected with the message recorded
in the fixture (`behavior=['events_emitted']` on `user_authentication`), the
model resubmitted, and the run never left DesignDocs.
"""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

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

FIXTURE = Path(__file__).parent / "fixtures" / "designdocs_live_f2504efe.json"
APPROVED_PAGES = [
    ("Dashboard", "/dashboard"), ("Task Management", "/tasks"),
    ("Pricing", "/pricing"), ("Billing", "/billing"), ("Usage", "/usage"),
]
AUTH_EVENTS = ["domain.auth.user_logged_in", "domain.auth.user_logged_out"]
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
    first, second = fixture["attempts"]
    assert "behavior=['events_emitted']" in fixture["live_rejection"]
    for key in ("surface_map", "experience_spec", "frontend_markdown", "backend_markdown", "database_markdown"):
        assert first[key] == second[key]
    assert [group["surface_id"] for group in first["data_contract"]["surfaces"]] == ["task_management"]
    assert [group["surface_id"] for group in second["data_contract"]["surfaces"]] == [
        "task_management", "user_authentication",
    ]
    auth = next(s for s in first["surface_map"]["surfaces"] if s["surface_id"] == "user_authentication")
    assert auth["owner"] == "app"
    assert auth["events_emitted"] == AUTH_EVENTS
    assert auth["owned_pages"] == ["User Authentication"]
    assert ("User Authentication", "/auth") in {
        (page["name"], page["route"]) for page in first["experience_spec"]["pages"]
    }
    assert fixture["context_vars"]["capability_packs"] is None


@pytest.mark.parametrize("index", [0, 1], ids=["attempt1_no_users_collection", "attempt2_users_collection"])
def test_live_attempt_saves_without_platform_sign_in_page(persistence, index, caplog):
    store, _, summary = persistence

    with caplog.at_level(logging.INFO):
        context, result = _save_attempt(index)

    assert result["outcome"] == "saved", result
    assert result["page_count"] == 5
    experience = detach(context.get("experience_spec"))
    assert [(page["name"], page["route"]) for page in experience["pages"]] == APPROVED_PAGES
    surfaces = {surface["surface_id"]: surface for surface in detach(context.get("design_surface_map"))["surfaces"]}
    auth = surfaces["user_authentication"]
    assert auth["owner"] == "platform"
    assert auth["primary_entities"] == auth["owned_mutations"] == auth["events_emitted"] == auth["owned_pages"] == []
    assert surfaces["task_management"]["events_emitted"] == [
        "domain.tasks.task_created", "domain.tasks.task_updated", "domain.tasks.task_deleted",
    ]
    assert surfaces["billing_portal"]["owned_pages"] == ["Pricing", "Billing", "Usage"]
    data = detach(context.get("data_contract"))
    assert [(group["surface_id"], collection["name"]) for group in data["surfaces"] for collection in group["collections"]] == [
        ("task_management", "tasks"),
    ]
    removed = ["users"] if index == 1 else []
    record = {
        "surface_id": "user_authentication", "owner": "platform", "removed_collections": removed,
        "removed_events": AUTH_EVENTS, "removed_pages": [{"name": "User Authentication", "route": "/auth"}],
    }
    assert summary.await_args.kwargs["summary_payload"]["ownership_normalizations"] == [record]
    marker = (
        f"DESIGN_OWNERSHIP_NORMALIZED surface=user_authentication owner=platform removed=[{','.join(removed)}] "
        "removed_events=[domain.auth.user_logged_in,domain.auth.user_logged_out] "
        "removed_pages=[User Authentication@/auth]"
    )
    assert marker in caplog.text
    saved = {call.kwargs["kind"]: call.kwargs for call in store.upsert_design_doc.await_args_list}
    assert marker in saved["backend"]["content"]
    assert saved["ui_schema"]["extra_fields"]["experience_spec"] == experience
    # The YAML agents read as a string carries the same inventory and records the removal.
    ui_schema = yaml.safe_load(saved["ui_schema"]["content"])
    assert [(page["name"], page["route"]) for page in ui_schema["pages"]] == APPROVED_PAGES
    assert ui_schema["ownership_normalizations"] == [record]


def _plan_from_saved_design(context: ContextVariablesBridge) -> dict:
    """A plan that states only what the saved design approved."""
    experience = detach(context.get("experience_spec"))
    facade_pages = {page["route"]: page for page in _facade_contract()["pages"]}
    plan = _plan_payload()["AppBuildPlan"]
    plan.update(
        agent_message="Build TaskTracker Pro from the approved design.", app_kind="saas",
        readiness_profile="none", revenue_model="subscription", monetization_provider="mozaiks_pay",
        auth_strategy="basic-login", roles=[], service_scope=["auth"],
        frontend_scope=["billing_portal", "task_management"],
        entities=[{"name": "Task", "operations": ["create_task", "update_task", "delete_task"], "notes": None}],
    )
    plan["pages"] = [
        {
            "name": page["name"], "route": page["route"], "purpose": page["intent"],
            "primary_entities": list(facade_pages.get(page["route"], {}).get("primary_entities") or (
                ["Task"] if page["route"] == "/tasks" else []
            )),
            "primary_actions": list(facade_pages.get(page["route"], {}).get("primary_actions") or []),
            "ui_layout": "full-width", "ui_surface": "declarative_page",
            "page_type_hint": facade_pages.get(page["route"], {}).get("page_type_hint") or (
                "analytics_dashboard" if page["route"] == "/dashboard" else "record_list"
            ),
            "sections_hint": [],
        }
        for page in experience["pages"]
    ]
    task_pack = _capability("task_management", entities=["Task"])
    task_pack.update(
        primary_pages=["Dashboard", "Task Management"], operations=["create_task", "update_task", "delete_task"],
    )
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
    context, result = _save_attempt(1)
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
    assert sorted(page_bundle["owned_paths"]) == sorted(["app.json", "ui/pages/dashboard.yaml", "ui/pages/tasks.yaml"])
    assert not any(
        "auth" in str(path) for task in cached["build_tasks"] for path in task.get("owned_paths") or []
    )
    assert {pack["capability_pack_id"] for pack in cached["capability_packs"]} >= {"task_management", "mozaikspay"}
    validate_plan_coverage(cached, plan_context)
