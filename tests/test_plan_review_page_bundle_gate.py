"""Pack-shipped pages do not make the page_bundle label unapproved (live AppGenerator chats at OSS deeef42d).

Two Create App runs selected MozaiksPay, whose billing_portal facade ships the
Billing, Usage and Pricing pages from its own templates. Each planner labelled
its page work with the structural page_bundle label and let a page task own
those pack pages too. The surface inventory gate accepted page_bundle only for
a task owning approved experience_spec pages, so it rejected the plan as an
unapproved 'page_bundle' surface before release_pack_owned_paths could release
the pack pages. The message said to remove the surface's tasks; the planner
did, deleting the approved page work, and the run ended after three attempts.

Two more faults sat behind it in chat 5cd39d67: the page task owned
``app/app.json`` (the workspace spelling of the manifest), and its
persistence task waited on both data_models tasks while review makes those
tasks wait on it, a dependency cycle.

Each fixture holds AppPlanAgent's drafts from the AG2 WAL, unmodified, and the
starting context reduced to the keys review reads.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from factory_app.workflows.AppGenerator.tools import app_plan_review
from factory_app.workflows.AppGenerator.tools.app_plan_review import (
    _owns_only_approved_pages,
    _strip_workspace_app_root,
    review_app_build_plan,
)
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.frozen import detach
from tests.test_continuous_deterministic_materialization import _load_models

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"
PACK_PAGES = {"ui/pages/billing.yaml", "ui/pages/usage.yaml", "ui/pages/pricing.yaml"}
APPROVED_PAGE_ARTIFACTS = {
    "5cd39d67": ["app.json", "ui/pages/dashboard.yaml", "ui/pages/customers.yaml", "ui/pages/jobs.yaml"],
    "601d2f56": [
        "app.json", "ui/pages/dashboard.yaml", "ui/pages/customers.yaml", "ui/pages/jobs.yaml",
        "ui/pages/custom/calendar.jsx", "ui/route_manifest.json", "ui/index.js",
    ],
}
LIVE_DRAFTS = [("5cd39d67", "1"), ("5cd39d67", "2"), ("5cd39d67", "3"), ("601d2f56", "1")]


@pytest.fixture(autouse=True)
def _load_factory_contracts():
    _load_models()


def _fixture(chat: str) -> dict:
    return json.loads((FIXTURES / f"appplan_review_live_{chat}.json").read_text(encoding="utf-8"))


def _context(chat: str) -> ContextVariablesBridge:
    variables = deepcopy(_fixture(chat)["context_variables"])
    for pack in variables.get("capability_packs") or []:
        pack["pack_source_path"] = str(ROOT / pack["pack_source_path"])
    return ContextVariablesBridge(variables)


def _draft(chat: str, attempt: str) -> dict:
    return deepcopy(_fixture(chat)["attempts"][attempt])


def _review(chat: str, plan: dict) -> tuple[dict, ContextVariablesBridge]:
    context = _context(chat)
    return review_app_build_plan(AppBuildPlan=plan, context_variables=context), context


def _task(plan: dict, task_id: str) -> dict:
    return next(task for task in plan["build_tasks"] if task["task_id"] == task_id)


def test_fixtures_record_the_live_shape():
    for chat, attempt in LIVE_DRAFTS:
        fixture = _fixture(chat)
        assert fixture["live_failure"].startswith(
            "Plan surface inventory errors:\n- unapproved surface 'page_bundle': remove its capability and tasks"
        )
        assert [pack["pack_id"] for pack in fixture["context_variables"]["capability_packs"]] == ["mozaikspay"]
        page_tasks = [task for task in fixture["attempts"][attempt]["build_tasks"] if task["task_type"] == "page_bundle"]
        assert all(
            (task["surface_id"], task["surface_kind"], task["capability_pack_id"]) == ("page_bundle", "ui_only", None)
            for task in page_tasks
        )
        assert PACK_PAGES <= {path for task in page_tasks for path in task["owned_paths"]}
    first = _fixture("5cd39d67")["attempts"]["1"]
    assert "app/app.json" in _task(first, "pt_page_bundle_app")["owned_paths"]
    for attempt in ("1", "2", "3"):
        plan = _fixture("5cd39d67")["attempts"][attempt]
        assert _task(plan, "pt_persistence_customer_job_contract")["depends_on"] == [
            "pt_data_models_customer_registry", "pt_data_models_job_registry",
        ]


@pytest.mark.parametrize(("chat", "attempt"), LIVE_DRAFTS)
def test_every_live_draft_is_ready_with_one_approved_page_task(chat, attempt):
    result, context = _review(chat, _draft(chat, attempt))

    assert result["outcome"] == "ready", result
    assert context.get("app_plan_feedback") == ""
    items = detach(context.get("app_task_batch_items"))
    page_tasks = [task for task in items if task["task_type"] == "page_bundle"]
    assert len(page_tasks) == 1
    (page_task,) = page_tasks
    assert (page_task["surface_id"], page_task["surface_kind"], page_task["capability_pack_id"]) == (
        "page_bundle", "ui_only", None,
    )
    assert sorted(page_task["owned_paths"]) == sorted(APPROVED_PAGE_ARTIFACTS[chat])
    owned = {path for task in items for path in task["owned_paths"]}
    assert not owned & PACK_PAGES, "the pack's templates ship its pages; no task builds them"
    assert not [path for path in owned if path.startswith("app/")]


@pytest.mark.parametrize("attempt", ["1", "2", "3"])
def test_the_persistence_serializer_waits_on_no_module_task(attempt):
    _result, context = _review("5cd39d67", _draft("5cd39d67", attempt))
    cached = detach(context.get("app_build_plan"))
    persistence = "pt_persistence_customer_job_contract"

    assert _task(cached, persistence)["depends_on"] == []
    for module_id in ("customer_registry", "job_registry"):
        data_models = _task(cached, f"pt_data_models_{module_id}")["depends_on"]
        services = _task(cached, f"pt_business_services_{module_id}")["depends_on"]
        assert persistence in data_models and f"pt_module_{module_id}" in data_models
        assert persistence in services and f"pt_data_models_{module_id}" in services


def test_a_persistence_edge_to_a_task_that_does_not_consume_it_is_kept():
    _result, context = _review("601d2f56", _draft("601d2f56", "1"))
    cached = detach(context.get("app_build_plan"))

    assert _task(cached, "tt_persistence_crm_core")["depends_on"] == ["tt_module_contract_crm_core"]


def test_only_the_reverse_edges_are_dropped_beside_a_kept_edge():
    plan = _draft("5cd39d67", "2")
    persistence = _task(plan, "pt_persistence_customer_job_contract")
    persistence["depends_on"] = ["pt_module_customer_registry", *persistence["depends_on"]]

    result, context = _review("5cd39d67", plan)

    assert result["outcome"] == "ready", result
    cached = detach(context.get("app_build_plan"))
    assert _task(cached, "pt_persistence_customer_job_contract")["depends_on"] == ["pt_module_customer_registry"]


def test_an_edge_to_a_module_without_persistent_collections_is_kept():
    """Only tasks review binds to the serializer (modules that own collections) lose their reverse edge."""
    context = _context("5cd39d67")

    def module(module_id: str) -> list[dict]:
        return [
            {"task_id": f"{kind}_{module_id}", "task_type": kind, "capability_pack_id": module_id,
             "owned_paths": paths, "depends_on": []}
            for kind, paths in (
                ("module_contract", [f"modules/{module_id}/module.yaml"]),
                ("data_models", [f"modules/{module_id}/backend/schemas.py"]),
                ("business_services", [f"modules/{module_id}/backend/{name}.py" for name in ("handler", "service")]),
            )
        ]

    persistence = {
        "task_id": "contract", "task_type": "persistence_contract", "capability_pack_id": None,
        "owned_paths": ["data/contract.json"],
        "depends_on": ["data_models_customer_registry", "data_models_notes", "business_services_notes"],
    }
    plan = {
        "capability_packs": [
            {"capability_pack_id": module_id, "surface_id": module_id, "surface_kind": "module",
             "capability_source": "generated_module"}
            for module_id in ("customer_registry", "notes")
        ],
        "build_tasks": [*module("customer_registry"), *module("notes"), persistence],
    }

    app_plan_review._repair_module_task_dependencies(plan, context)

    assert persistence["depends_on"] == ["data_models_notes", "business_services_notes"]
    tasks = {task["task_id"]: task for task in plan["build_tasks"]}
    assert "contract" in tasks["data_models_customer_registry"]["depends_on"]
    assert "contract" not in tasks["data_models_notes"]["depends_on"]


def test_pack_pages_are_approved_page_artifacts_and_an_invented_page_is_not():
    context = _context("5cd39d67")
    task = {
        "task_type": "page_bundle", "capability_pack_id": None,
        "owned_paths": [*APPROVED_PAGE_ARTIFACTS["5cd39d67"], *sorted(PACK_PAGES)],
    }
    assert _owns_only_approved_pages(task, context)
    assert not _owns_only_approved_pages({**task, "owned_paths": [*task["owned_paths"], "ui/pages/reports.yaml"]}, context)
    assert not _owns_only_approved_pages(
        {**task, "owned_paths": [*task["owned_paths"], "modules/billing_portal/module.yaml"]}, context,
    ), "a pack's module files are not page artifacts"


def test_a_page_task_with_an_invented_page_is_told_to_keep_its_label():
    plan = _draft("601d2f56", "1")
    _task(plan, "tt_page_bundle_crm_billing_ui")["owned_paths"].append("ui/pages/reports.yaml")

    result, context = _review("601d2f56", plan)

    assert result["outcome"] == "needs_revision"
    message = result["error"]
    assert "unapproved surface 'page_bundle'" in message
    assert "task 'tt_page_bundle_crm_billing_ui'" in message
    assert "drop ['ui/pages/reports.yaml']: no approved page or selected pack declares those pages" in message, (
        "only the out-of-scope page is named, and it is dropped rather than moved"
    )
    assert "move [" not in message
    assert "Keep the page_bundle label" in message
    assert "need no task" in message and "ui/pages/billing.yaml" in message
    assert "remove its capability and tasks" not in message
    assert context.get("app_build_plan") is None


def test_a_page_bundle_capability_and_a_mislabelled_task_are_named():
    plan = _draft("5cd39d67", "2")
    plan["capability_packs"].append({**deepcopy(plan["capability_packs"][0]), "capability_pack_id": "page_bundle",
                                     "surface_id": "page_bundle", "surface_kind": "ui_only"})
    task = _task(plan, "pt_module_customer_registry")
    task["surface_id"], task["surface_kind"] = "page_bundle", "ui_only"

    result, _context_ = _review("5cd39d67", plan)

    message = result["error"]
    assert (
        "capability_packs entry 'page_bundle' uses surface_id='page_bundle', a task label rather than "
        "a capability surface; remove that entry"
    ) in message
    assert "task 'pt_module_customer_registry': task_type='module_contract' (page work needs 'page_bundle')" in message
    assert (
        "move ['modules/customer_registry/module.yaml', 'modules/customer_registry/contracts/events.yaml'] "
        "to the task of the approved surface that owns them"
    ) in message
    assert " has " not in message and "is not a capability" not in message


def test_with_no_approved_pages_no_page_bundle_task_is_suggested():
    context = _context("5cd39d67")
    spec = deepcopy(_fixture("5cd39d67")["context_variables"]["experience_spec"])
    spec["pages"] = []
    context.set("experience_spec", spec)

    result = review_app_build_plan(AppBuildPlan=_draft("5cd39d67", "1"), context_variables=context)

    message = result["error"]
    assert "unapproved surface 'page_bundle'" in message
    assert "build_tasks ['pt_page_bundle_app', 'pt_page_bundle_billing_portal'] use it" in message
    assert "declares no pages, so page_bundle approves no task here: remove those page_bundle tasks" in message
    assert "Keep the page_bundle label" not in message
    assert "['app.json']" not in message


def test_an_unapproved_module_surface_names_its_tasks_and_paths():
    plan = _draft("5cd39d67", "2")
    plan["build_tasks"].append({
        **deepcopy(_task(plan, "pt_module_customer_registry")),
        "task_id": "pt_module_reports", "surface_id": "reports", "capability_pack_id": "reports",
        "owned_paths": ["modules/reports/module.yaml"],
    })

    result, _context_ = _review("5cd39d67", plan)

    message = result["error"]
    assert "unapproved surface 'reports' (declared by task 'pt_module_reports' owning ['modules/reports/module.yaml'])" in message
    assert "remove its capability and tasks" in message


def test_workspace_app_root_is_stripped_from_the_leading_segment_only():
    untouched = ["app.json", "application/notes.yaml", "ui/pages/app/home.yaml", "modules/app/module.yaml", "app/"]
    plan = {"build_tasks": [
        {"task_id": "pages", "owned_paths": [
            "app/app.json", "app.json", "app\\ui/pages/jobs.yaml", "app/ui/pages/app/lobby.yaml", *untouched,
        ]},
        {"task_id": "module", "owned_paths": ["app/modules/jobs/module.yaml", "app/modules/jobs/module.yaml"]},
        {"task_id": "plain", "owned_paths": list(untouched)},
    ]}

    repairs = _strip_workspace_app_root(plan)

    pages, module, plain = plan["build_tasks"]
    assert pages["owned_paths"] == ["app.json", "ui/pages/jobs.yaml", "ui/pages/app/lobby.yaml", *untouched[1:]]
    assert module["owned_paths"] == ["modules/jobs/module.yaml"]
    assert plain["owned_paths"] == untouched
    assert [repair.split(":")[0] for repair in repairs] == ["pages", "module"]


def test_the_obsolete_admin_config_is_rejected_in_either_spelling():
    plan = _draft("5cd39d67", "2")
    plan["build_tasks"].append({
        **deepcopy(_task(plan, "pt_module_customer_registry")),
        "task_id": "pt_service_foundation", "task_type": "service_foundation", "initial_agent": "ConfigMiddlewareAgent",
        "surface_id": "customer_registry", "capability_pack_id": None, "depends_on": [],
        "owned_paths": ["services/config.py", "app/config/admin.json"],
    })

    result, _context_ = _review("5cd39d67", plan)

    assert result["outcome"] == "needs_revision"
    assert "owns obsolete path 'app/config/admin.json'" in result["error"]


def test_the_live_rejection_is_reproduced_without_pack_page_approval(monkeypatch):
    """Guard for the fix itself: approving only experience_spec pages brings the live rejection back."""
    monkeypatch.setattr(app_plan_review, "_pack_page_paths", lambda context: frozenset())

    result, _context_ = _review("601d2f56", _draft("601d2f56", "1"))

    assert result["outcome"] == "needs_revision"
    assert "unapproved surface 'page_bundle'" in result["error"]
    assert "ui/pages/billing.yaml" in result["error"]


def test_the_planner_prompt_does_not_ask_for_pack_shipped_billing_pages():
    agents = (ROOT / "factory_app" / "workflows" / "AppGenerator" / "agents.yaml").read_text(encoding="utf-8")

    assert "plan an app-owned `billing_portal`" not in agents
    assert "declares that facade or those pages in its `required_outputs`" in agents
    assert "plan no\n        task for those files or pages" in agents.replace("\r\n", "\n")
