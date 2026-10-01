"""Plan review labels a page task by what it builds, not by the page it is named after.

The live release run at 95ad6325 (AppGenerator chat 64dbe4b9) planned one
page_bundle task per approved page and gave each the page's name as its
surface_id. Review rejected the four labels as unapproved surfaces, told the
planner to remove the tasks (which would have dropped the approved Dashboard),
and the model resubmitted the same plan until its three attempts ran out.

``fixtures/appplan_review_live_95ad6325.json`` holds that plan exactly as the
model submitted it (read from the AG2 WAL) and the chat's starting context,
reduced to the keys review reads, on a real ContextVariablesBridge.
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
FIXTURE = Path(__file__).parent / "fixtures" / "appplan_review_live_95ad6325.json"
REVIEW_LOGGER = app_plan_review.logger.name
PAGE_LABELS = {"dashboard", "pricing", "billing", "usage"}
PACK_PAGES = {"ui/pages/pricing.yaml", "ui/pages/billing.yaml", "ui/pages/usage.yaml"}


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


def test_without_the_label_repair_review_reproduces_the_live_rejection(monkeypatch):
    monkeypatch.setattr(app_plan_review, "_label_page_tasks", lambda plan, context: [])

    result, _context = _review(_plan())

    assert result["outcome"] == "needs_revision"
    assert result["error"].startswith(_fixture()["live_rejection"])
    for label in PAGE_LABELS:
        assert f"unapproved surface {label!r}" in result["error"]


def test_the_live_plan_is_ready_and_dispatch_accepts_it(caplog):
    caplog.set_level(logging.INFO, logger=REVIEW_LOGGER)

    result, context = _review(_plan())

    assert result == {"outcome": "ready", "task_count": 5}
    items = detach(context.get("app_task_batch_items"))
    cached = detach(context.get("app_build_plan"))
    for tasks in (items, cached["build_tasks"]):
        assert not {task["surface_id"] for task in tasks} & PAGE_LABELS
        assert not {path for task in tasks for path in task["owned_paths"]} & PACK_PAGES
    (page,) = [task for task in items if task["task_type"] == "page_bundle"]
    assert (page["task_id"], page["surface_id"], page["surface_kind"]) == (
        "page_bundle_tasks_dashboard", "page_bundle", "ui_only",
    )
    assert "ui/pages/dashboard.yaml" in page["owned_paths"]
    _dispatch_preflight(items)

    repairs = [record.getMessage() for record in caplog.records]
    for label in PAGE_LABELS:
        task_id = f"page_bundle_tasks_{label}"
        assert (
            f"[AppGenerator] plan repaired: {task_id}: surface {label!r} -> 'page_bundle'; a page_bundle task "
            f"owning only approved pages ['ui/pages/{label}.yaml'] is not a surface of its own"
        ) in repairs


def test_a_page_task_owning_a_page_outside_the_approved_inventory_is_still_rejected():
    plan = _plan()
    page = next(task for task in plan["build_tasks"] if task["task_id"] == "page_bundle_tasks_dashboard")
    page["surface_id"], page["owned_paths"] = "reports", ["ui/pages/reports.yaml"]

    result, _context = _review(plan)

    assert result["outcome"] == "needs_revision"
    assert "unapproved surface 'reports'" in result["error"]


def test_a_page_task_under_an_approved_surface_keeps_its_label(caplog):
    caplog.set_level(logging.INFO, logger=REVIEW_LOGGER)
    plan = _plan()
    page = next(task for task in plan["build_tasks"] if task["task_id"] == "page_bundle_tasks_dashboard")
    page["surface_id"] = "tasks"

    app_plan_review._label_page_tasks(plan, _live_context())

    assert page["surface_id"] == "tasks"


def test_a_non_page_task_with_an_invented_surface_is_still_rejected():
    plan = _plan()
    services = next(task for task in plan["build_tasks"] if task["task_type"] == "business_services")
    services["surface_id"] = "analytics"

    result, _context = _review(plan)

    assert result["outcome"] == "needs_revision"
    assert "unapproved surface 'analytics'" in result["error"]
