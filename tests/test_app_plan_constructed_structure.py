"""Reject the captured auth duplicate and retain omitted-structure coverage.

The fixture's model payload is the unchanged response captured three times in
the 2026-09-26 live traversal. Its context is a projection of the stored approved
inputs, with the labelled current-schema page rendering declarations added
after capture. Structural tests explicitly replace its invalid auth ownership with a
project-members domain module; no database, credentials, or model calls are needed.
"""

from __future__ import annotations

import json
from collections import Counter
from copy import deepcopy
from pathlib import Path

import pytest

from factory_app.workflows.AppGenerator.tools.app_plan_review import (
    review_app_build_plan,
    validate_plan_coverage,
    validate_plan_dependencies,
    validate_plan_origins,
)
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.generator_support.module_action_inventory import (
    pack_owned_output_paths,
)
from tests.test_app_plan_closed_inventory import _approved_plan
from tests.test_app_plan_managed_facade_repair import _capability, _task
from tests.test_continuous_deterministic_materialization import _load_models

FIXTURE = Path(__file__).parent / "fixtures/appplan_monetized_missing_structure.json"
MODULE_KINDS = {"module_contract", "data_models", "business_services"}
# The selected MozaiksPay pack ships billing_portal and its pages from templates.
PACK_PAGE_PATHS = {"ui/pages/pricing.yaml", "ui/pages/billing.yaml", "ui/pages/usage.yaml"}
PAGE_PATHS = {
    "app.json", "ui/pages/dashboard.yaml", "ui/pages/tasks.yaml", "ui/pages/members.yaml", *PACK_PAGE_PATHS,
}


@pytest.fixture(autouse=True)
def _load_factory_contracts():
    _load_models()


def _live_inputs():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    # Retire the old model-owned data contract field; the captured approved context is authoritative.
    fixture["plan"].pop("data_contract", None)
    values = fixture["context"]
    values["app_plan_attempts"] = 0
    for pack in values["capability_packs"]:
        pack["pack_source_path"] = str(
            Path(__file__).resolve().parents[1] / "factory_app/build_context" / pack["id"]
        )
    return fixture["plan"], ContextVariablesBridge(values)


def _domain_inputs():
    """Correct the test's approved inputs explicitly; review must never do this."""
    plan, context = _live_inputs()
    # Migrate authored read choices explicitly while retaining captured raw evidence.
    for planned_page in plan["pages"]:
        for hint in planned_page.get("sections_hint") or []:
            config = json.loads(hint["config_hint"]) if hint.get("config_hint") else {}
            if config.pop("api_endpoint", None) is not None:
                hint["data_source"] = {"module_id": "tasks", "action_id": "list_tasks"}
                hint["config_hint"] = json.dumps(config)
    section = {
        "primitive": "Form", "config_hint": '{"fields": [{"label": "Email", "name": "email"}]}',
        "section_id_hint": "invite-member", "title_hint": "Invite project member",
        "intent": "Invite a member to the project",
    }
    page = next(page for page in plan["pages"] if page["route"] == "/auth")
    page.update(
        name="Project Members", route="/members", purpose="Invite project members",
        design_intent="Invite project members by email", primary_entities=["Member"],
        primary_actions=["invite_member"], sections_hint=[section],
    )
    entity = next(entity for entity in plan["entities"] if entity["name"] == "User")
    entity.update(name="Member", operations=["invite_member"], notes="App-owned project membership.")
    approved = detach(context.get("design_surface_map"))
    for surface_map in (plan["surface_map"], approved):
        surface = next(surface for surface in surface_map["surfaces"] if surface["surface_id"] == "auth")
        surface.update(
            surface_id="project_members", label="Project Members", source_capability_packs=[],
            primary_entities=["Member"], owned_mutations=["invite_member"],
            events_emitted=["domain.project_members.member_invited"],
        )
        if surface["owned_pages"]:
            surface["owned_pages"] = ["Project Members"]
        if "summary" in surface:
            surface["summary"] = "Invite members to a project."
    context.set("design_surface_map", approved)
    experience = detach(context.get("experience_spec"))
    page = next(page for page in experience["pages"] if page["route"] == "/auth")
    page.update(
        name="Project Members", route="/members", intent="Invite project members by email",
        sections=[{
            "id": "invite-member", "primitive": section["primitive"],
            "intent": section["intent"], "config_hint": section["config_hint"],
        }],
    )
    context.set("experience_spec", experience)
    task = next(task for task in plan["build_tasks"] if task["surface_id"] == "auth")
    task.update(
        task_id="invite_project_member", surface_id="project_members", capability_pack_id="project_members",
        description="Business services for project member invitations.",
        initial_message="Handle invite_member requests.",
        owned_paths=[path.replace("modules/auth/", "modules/project_members/") for path in task["owned_paths"]],
    )
    data = detach(context.get("data_contract"))
    surface = next(surface for surface in data["surfaces"] if surface["surface_id"] == "auth")
    surface["surface_id"] = "project_members"
    collection = surface["collections"][0]
    collection.update(
        name="project_members", search_by="member_id", entity="Member",
        tenancy="app_wide", owner_field=None,
    )
    collection["ownership"]["surface_id"] = "project_members"
    collection["fields"] = [field for field in collection["fields"] if field["name"] != "password_hash"]
    next(field for field in collection["fields"] if field["name"] == "user_id")["name"] = "member_id"
    task_collection = next(surface for surface in data["surfaces"] if surface["surface_id"] == "tasks")["collections"][0]
    task_collection.update(entity="Task", tenancy="per_user", owner_field="user_id")
    context.set("data_contract", data)
    return plan, context


def _assert_complete_review(plan, context):
    assert isinstance(context, ContextVariablesBridge)
    original = deepcopy(plan)
    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)
    assert result["outcome"] == "ready", result
    assert plan == original
    cached = detach(context.get("app_build_plan"))
    validate_plan_coverage(cached, context)
    validate_plan_origins(cached, context)
    validate_plan_dependencies(cached, context)
    tasks = cached["build_tasks"]
    ids = [task["task_id"] for task in tasks]
    assert len(ids) == len(set(ids))
    paths = Counter(path for task in tasks for path in task["owned_paths"])
    assert all(count == 1 for count in paths.values())
    page_tasks = [task for task in tasks if task["task_type"] == "page_bundle"]
    assert {path for task in page_tasks for path in task["owned_paths"]} == PAGE_PATHS - PACK_PAGE_PATHS
    assert all(task["initial_agent"] == "AppSchemaAgent" for task in page_tasks)
    assert "ui/pages/invite_project_member.yaml" not in paths
    for module in ("tasks", "project_members"):
        trio = [task for task in tasks if task["capability_pack_id"] == module and task["task_type"] in MODULE_KINDS]
        assert Counter(task["task_type"] for task in trio) == dict.fromkeys(MODULE_KINDS, 1)
    # Pack-owned outputs are never model work: the facade module and pages come from templates.
    assert not [task for task in tasks if task["capability_pack_id"] == "billing_portal"]
    assert not set(paths) & pack_owned_output_paths(context)
    assert any(pack["capability_pack_id"] == "billing_portal" for pack in cached["capability_packs"])
    # Assembly writes config/subscriptions.yaml from the approved contract.
    assert "config/subscriptions.yaml" not in paths
    queued = detach(context.get("app_task_batch_items"))
    assert {task["task_id"] for task in queued} == set(ids)
    assert all(set(task["depends_on"]) <= set(ids) for task in tasks)
    return cached


def test_exact_live_auth_duplicate_is_rejected_before_structure_repair():
    plan, context = _live_inputs()
    assert len(plan["build_tasks"]) == 4
    assert all(task["task_type"] != "page_bundle" for task in plan["build_tasks"])
    original = deepcopy(plan)

    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    assert result["outcome"] == "needs_revision", result
    assert "platform" in result["error"].lower()
    assert "auth" in result["error"]
    assert context.get("app_build_plan") is None
    assert not context.get("app_task_batch_items")
    assert context.get("app_plan_ready") is False
    assert plan == original


def test_corrected_domain_missing_page_bundle_plan_passes_review():
    plan, context = _domain_inputs()
    assert len(plan["build_tasks"]) == 4
    assert all(task["task_type"] != "page_bundle" for task in plan["build_tasks"])
    _assert_complete_review(plan, context)


@pytest.mark.parametrize("omit_capabilities", [False, True], ids=["wrong_labels", "no_capabilities"])
def test_minimal_judgment_plan_constructs_all_omitted_structure(omit_capabilities):
    plan, context = _domain_inputs()
    plan["build_tasks"] = []
    plan["generation_order"] = []
    plan["capability_packs"] = []
    if not omit_capabilities:
        for surface in detach(context.get("design_surface_map"))["surfaces"]:
            capability = _capability(surface["surface_id"], entities=surface["primary_entities"])
            capability.update(
                capability_pack_id=f"wrong_{surface['surface_id']}_label",
                operations=surface["owned_mutations"],
            )
            plan["capability_packs"].append(capability)

    # Four authored pages retain judgment about layout and purpose. The other
    # two approved pages, every worker task, module trio,
    # managed-provider entry, and all dependency edges are absent.
    assert len(plan["pages"]) == 4
    cached = _assert_complete_review(plan, context)
    assert {"tasks", "project_members", "billing_portal", "mozaikspay"} <= {
        pack["capability_pack_id"] for pack in cached["capability_packs"]
    }


def test_complete_correct_plan_passes_unchanged():
    # Complete the independent prior-PR fixture explicitly, without using the
    # repair under test to manufacture its own supposedly correct input.
    plan, context = _approved_plan(monetized=False)
    assert isinstance(context, ContextVariablesBridge)
    tasks = {task["task_type"]: task for task in plan["build_tasks"]}
    # This fixture declares no collection ownership, so it must not plan a policy.
    tasks["business_services"]["owned_paths"].remove("modules/task_management/backend/policy.py")
    tasks["module_contract"]["initial_message"] = "Define create_task and list_tasks."
    tasks["module_contract"]["owned_paths"].append("modules/task_management/contracts/events.yaml")
    tasks["data_models"]["depends_on"] = ["6", "persistence_contract"]
    tasks["business_services"]["depends_on"] = ["6", "7", "persistence_contract"]
    tasks["page_bundle"]["depends_on"] = ["6"]
    tasks["page_bundle"]["owned_paths"] = [
        "brand/theme_config.json", "app.json", "ui/pages/dashboard.yaml", "ui/pages/tasks.yaml",
    ]
    plan["build_tasks"] = sorted(plan["build_tasks"], key=lambda task: task["task_id"])
    model = _load_models()["AppBuildPlan"]
    complete = model.model_validate(plan).model_dump(mode="json", exclude_none=True)
    expected = model.model_validate(complete).model_dump(mode="json")
    original = deepcopy(complete)
    validate_plan_coverage(complete, context)
    validate_plan_origins(complete, context)
    validate_plan_dependencies(complete, context)

    result = review_app_build_plan(AppBuildPlan=complete, context_variables=context)

    assert result["outcome"] == "ready", result
    assert complete == original
    cached = detach(context.get("app_build_plan"))
    for key in ("build_tasks", "capability_packs", "pages", "generation_order"):
        assert cached[key] == expected[key]


def test_unapproved_surface_is_rejected_before_missing_structure_is_constructed():
    plan, context = _domain_inputs()
    plan["build_tasks"] = []
    plan["capability_packs"].append(_capability("unapproved_surface"))
    original = deepcopy(plan)

    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    assert result["outcome"] == "needs_revision", result
    assert "unapproved surface 'unapproved_surface'" in result["error"]
    assert context.get("app_build_plan") is None
    assert not context.get("app_task_batch_items")
    assert context.get("app_plan_ready") is False
    assert plan == original


def test_explicit_custom_operations_are_preserved_without_inventing_read_actions():
    plan, context = _domain_inputs()
    plan["build_tasks"] = []
    plan["capability_packs"] = [{
        **_capability("project_members", entities=["Member"]),
        "operations": ["invite_member"],
    }]

    cached = _assert_complete_review(plan, context)

    members = next(pack for pack in cached["capability_packs"] if pack["capability_pack_id"] == "project_members")
    assert members["operations"] == ["invite_member"]


def test_selected_module_lane_constructs_missing_paths_and_labels():
    plan, context = _domain_inputs()
    task = next(task for task in plan["build_tasks"] if task["surface_id"] == "tasks")
    task.update(capability_pack_id="wrong_label", surface_kind="external_integration", owned_paths=[])

    cached = _assert_complete_review(plan, context)

    constructed = next(item for item in cached["build_tasks"] if item["task_id"] == task["task_id"])
    assert constructed["capability_pack_id"] == "tasks"
    assert constructed["surface_kind"] == "module"
    assert constructed["owned_paths"] == ["modules/tasks/module.yaml", "modules/tasks/contracts/events.yaml"]


@pytest.mark.parametrize("identity", ["blank", "repeated_task_type"])
def test_missing_or_repeated_task_ids_compose_with_omitted_structure(identity):
    plan, context = _domain_inputs()
    if identity == "blank":
        plan["build_tasks"][0]["task_id"] = ""
    else:
        for task in plan["build_tasks"]:
            task["task_id"] = task["task_type"]
    for task in plan["build_tasks"]:
        task["initial_agent"] = "WrongAgent"

    cached = _assert_complete_review(plan, context)

    assert all(task["task_id"] for task in cached["build_tasks"])
    assert all(task["initial_agent"] != "WrongAgent" for task in cached["build_tasks"])


@pytest.mark.parametrize("misuse", ["capability", "module_task", "service_file"])
def test_structural_page_scope_cannot_authorize_an_unapproved_module(misuse):
    plan, context = _domain_inputs()
    if misuse == "capability":
        plan["capability_packs"].append({
            **_capability("page_bundle"), "surface_kind": "ui_only",
        })
    elif misuse == "module_task":
        plan["build_tasks"].append({
            **_task("unapproved_module", "module_contract", "ConfigMiddlewareAgent", None, ["modules/page_bundle/module.yaml"]),
            "surface_id": "page_bundle", "surface_kind": "ui_only",
        })
    else:
        plan["build_tasks"].append({
            **_task("unapproved_service", "page_bundle", "AppSchemaAgent", None, [*sorted(PAGE_PATHS), "services/integrations/unapproved_client.py"]),
            "surface_id": "page_bundle", "surface_kind": "ui_only",
        })

    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    assert result["outcome"] == "needs_revision", result
    assert "unapproved surface 'page_bundle'" in result["error"]
    assert context.get("app_build_plan") is None
    assert not context.get("app_task_batch_items")
