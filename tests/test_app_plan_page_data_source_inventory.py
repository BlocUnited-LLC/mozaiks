"""Replay the live 0b1b740d plan review whose Dashboard KPI hint named no action.

The fixture holds AppPlanAgent's AppBuildPlan from AppGenerator chat 645653f2
(OSS 0b1b740d), read from the local AG2 network WAL, with the context keys
review_app_build_plan reads. Live, review accepted
``pages[Dashboard].sections_hint[0].data_source = tasks/get_kpi_stats``, an
action no approved surface declares, and the page_bundle task failed both
attempts with the recorded message. Pages bind only to the approved inventory,
so review now drops that hint and tells the page task what exists.
"""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from pathlib import Path

import pytest

from factory_app.workflows.AppGenerator.tools.app_plan_review import review_app_build_plan
from factory_app.workflows.AppGenerator.tools.plan_page_data_sources import (
    drop_unapproved_page_data_sources,
)
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.frozen import detach
from tests.test_continuous_deterministic_materialization import _load_models

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).parent / "fixtures" / "appplan_review_live_0b1b740d.json"
NOTE = "Planned data sources removed because no approved action exists: "


@pytest.fixture(autouse=True)
def _load_factory_contracts():
    _load_models()


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _context(variables: dict) -> ContextVariablesBridge:
    variables = deepcopy(variables)
    for pack in variables.get("capability_packs") or []:
        pack["pack_source_path"] = str(ROOT / pack["pack_source_path"])
    return ContextVariablesBridge(variables)


def _review(plan: dict) -> tuple[ContextVariablesBridge, dict]:
    context = _context(_fixture()["context_variables"])
    result = review_app_build_plan(AppBuildPlan=deepcopy(plan), context_variables=context)
    assert result["outcome"] == "ready", result
    return context, detach(context.get("app_build_plan"))


def _page_task(plan: dict) -> dict:
    return next(task for task in plan["build_tasks"] if "ui/pages/dashboard.yaml" in task["owned_paths"])


def _hints(plan: dict) -> dict[str, dict]:
    return {
        f"{page['name']}/{hint['section_id_hint']}": hint
        for page in plan["pages"] for hint in page.get("sections_hint") or []
    }


def test_fixture_records_the_live_evidence():
    fixture = _fixture()
    summary = fixture["AppBuildPlan"]["pages"][0]["sections_hint"][0]
    assert summary["data_source"] == {"module_id": "tasks", "action_id": "get_kpi_stats"}
    assert fixture["live_page_failure"] == {
        "task_id": "bc-5", "attempts": 2,
        "error": "ui/pages/dashboard.yaml: Dashboard.sections[0].config.data_source "
                 "references unknown module/action 'tasks/get_kpi_stats'",
    }
    tasks = next(
        surface for surface in fixture["context_variables"]["design_surface_map"]["surfaces"]
        if surface["surface_id"] == "tasks"
    )
    assert tasks["owned_mutations"] == ["create_task", "update_task", "delete_task"]
    assert tasks["custom_reads"] == []
    assert fixture["live_page_inventory"] == {
        "tasks": ["create_task", "delete_task", "get_tasks", "list_tasks", "update_task"],
    }


def test_live_plan_drops_the_unapproved_source_and_changes_nothing_else(caplog):
    fixture = _fixture()
    recorded = fixture["AppBuildPlan"]
    with caplog.at_level(logging.INFO):
        context, cached = _review(recorded)

    summary = _hints(cached)["Dashboard/summary"]
    planned = recorded["pages"][0]["sections_hint"][0]
    assert summary["data_source"] is None
    for field in ("primitive", "intent", "title_hint", "section_id_hint"):
        assert summary[field] == planned[field]
    # Facade routing re-serializes every config_hint; its content is unchanged.
    assert json.loads(summary["config_hint"]) == json.loads(planned["config_hint"])
    assert _hints(cached)["Dashboard/recent_activities"]["data_source"] == {"module_id": "tasks", "action_id": "list_tasks"}
    assert "plan repaired: Dashboard section 'summary': dropped data_source 'tasks/get_kpi_stats', not an approved action" in caplog.text

    # The page task is told what was removed and, from the same inventory the
    # page compiler enforces, which actions the module does declare.
    listed = ", ".join(fixture["live_page_inventory"]["tasks"])
    note = f"{NOTE}Dashboard section 'summary' named tasks/get_kpi_stats (tasks declares {listed})."
    assert note in _page_task(cached)["initial_message"]
    batch_item = next(item for item in context.get("app_task_batch_items") if item["task_id"] == _page_task(cached)["task_id"])
    assert note in batch_item["current_build_task"]["initial_message"]

    # Everything else is what review makes of the same plan with that hint unbound.
    unbound = deepcopy(recorded)
    unbound["pages"][0]["sections_hint"][0]["data_source"] = None
    _, expected = _review(unbound)
    page_task = _page_task(cached)
    page_task["initial_message"] = page_task["initial_message"].split(f"\n\n{NOTE}")[0]
    assert cached == expected


def test_an_unknown_module_is_dropped_and_the_note_names_the_approved_modules():
    plan = _fixture()["AppBuildPlan"]
    plan["pages"][0]["sections_hint"][0]["data_source"] = {"module_id": "analytics", "action_id": "get_kpi_stats"}

    _, cached = _review(plan)

    assert _hints(cached)["Dashboard/summary"]["data_source"] is None
    assert (
        "Dashboard section 'summary' named analytics/get_kpi_stats (approved modules are billing_portal, tasks)"
        in _page_task(cached)["initial_message"]
    )


def test_provider_pairs_are_judged_at_the_facade_they_route_to():
    plan = _fixture()["AppBuildPlan"]
    template = plan["pages"][0]["sections_hint"][1]
    plan["pages"][0]["sections_hint"] += [
        {**template, "section_id_hint": "status", "data_source": {"module_id": "mozaikspay", "action_id": "get_subscription_status"}},
        {**template, "section_id_hint": "plans", "data_source": {"module_id": "mozaikspay", "action_id": "list_plans"}},
    ]

    _, cached = _review(plan)

    # A registered provider action keeps its hint and is rebound to the facade.
    assert _hints(cached)["Dashboard/status"]["data_source"] == {
        "module_id": "billing_portal", "action_id": "get_subscription_status",
    }
    # No route registers list_plans on the provider, so that pair names nothing.
    assert _hints(cached)["Dashboard/plans"]["data_source"] is None


def test_a_page_the_selected_pack_writes_keeps_its_template_bindings():
    plan = _fixture()["AppBuildPlan"]
    pricing = deepcopy(plan["pages"][0])
    pricing.update(name="Pricing", route="/pricing", sections_hint=[{
        **pricing["sections_hint"][0], "section_id_hint": "plans",
        "data_source": {"module_id": "billing_portal", "action_id": "not_an_action"},
    }])
    plan["pages"].append(pricing)

    _, cached = _review(plan)

    assert _hints(cached)["Pricing/plans"]["data_source"] == {"module_id": "billing_portal", "action_id": "not_an_action"}
    assert _hints(cached)["Dashboard/summary"]["data_source"] is None


def test_without_an_approved_design_the_inventory_is_open():
    fixture = _fixture()
    variables = {key: value for key, value in fixture["context_variables"].items() if key != "design_surface_map"}
    plan = deepcopy(fixture["AppBuildPlan"])

    assert drop_unapproved_page_data_sources(plan, _context(variables)) == []
    assert plan == fixture["AppBuildPlan"]
