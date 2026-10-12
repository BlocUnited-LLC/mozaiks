"""Selected-pack pages do not break the approved page inventory (live AppGenerator chat at OSS 20eb8742).

A Create App run for a free, offline customer tracker approved two pages,
Customers and Dashboard. The operator selects MozaiksPay for every app, and its
billing_portal facade declares Billing and Usage pages that the pack ships from
its own templates. DesignDocs leaves those pages out of a free app's
ExperienceSpec. Review rebuilt the plan's pages to the approved two and passed
coverage, then app_build_plan appended the facade pages to the cached plan,
and the coverage re-check on that cached plan rejected it: "pages must preserve
the approved name/route inventory". The same rejection hit all three attempts,
so the build stopped before any task ran.

The fixture holds AppPlanAgent's first AppBuildPlan from the AG2 WAL,
unmodified, and the starting context reduced to the keys review reads.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from factory_app.workflows.AppGenerator.tools.app_plan_review import (
    review_app_build_plan,
    validate_plan_coverage,
)
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.frozen import detach
from tests.test_continuous_deterministic_materialization import _load_models

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).parent / "fixtures" / "appplan_review_live_20eb8742.json"
APPROVED = {("Customers", "/customers"), ("Dashboard", "/dashboard")}
PACK_PAGES = {("Billing", "/billing"), ("Usage", "/usage")}


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


def _pages(plan: dict) -> set[tuple[str, str]]:
    return {(page["name"], page["route"]) for page in plan["pages"]}


def _reviewed() -> tuple[ContextVariablesBridge, dict]:
    context = _live_context()
    result = review_app_build_plan(AppBuildPlan=deepcopy(_fixture()["AppBuildPlan"]), context_variables=context)
    assert result == {"outcome": "ready", "task_count": 6}
    return context, detach(context.get("app_build_plan"))


def test_fixture_records_the_live_shape():
    fixture = _fixture()
    variables = fixture["context_variables"]
    assert {(page["name"], page["route"]) for page in variables["experience_spec"]["pages"]} == APPROVED
    assert variables["subscription_contract"]["contract_required"] is False
    assert variables["operator_capabilities"] == ["mozaikspay"]
    (facade,) = variables["capability_packs"][0]["facades"]
    assert {(page["name"], page["route"]) for page in facade["pages"]} == PACK_PAGES
    assert _pages(fixture["AppBuildPlan"]) == APPROVED
    assert fixture["live_failure"].endswith(
        "pages must preserve the approved name/route inventory: [('Customers', '/customers'), ('Dashboard', '/dashboard')]"
    )


def test_the_live_plan_is_ready_and_carries_the_pack_pages():
    context, cached = _reviewed()

    assert _pages(cached) == APPROVED | PACK_PAGES
    assert context.get("app_plan_feedback") == ""
    items = detach(context.get("app_task_batch_items"))
    page_owned = sorted(
        path for task in items if task["task_type"] == "page_bundle" for path in task["owned_paths"]
    )
    # The pack ships billing.yaml and usage.yaml; page_bundle authors only the approved pages.
    assert page_owned == ["app.json", "ui/pages/customers.yaml", "ui/pages/dashboard.yaml"]


def test_a_page_no_one_approved_or_ships_is_still_rejected():
    context, cached = _reviewed()
    invented = deepcopy(cached)
    invented["pages"].append({**deepcopy(invented["pages"][0]), "name": "Reports", "route": "/reports"})

    with pytest.raises(ValueError, match="pages must preserve the approved name/route inventory"):
        validate_plan_coverage(invented, context)


def test_dropping_an_approved_page_is_still_rejected():
    context, cached = _reviewed()
    dropped = deepcopy(cached)
    dropped["pages"] = [page for page in dropped["pages"] if page["route"] != "/customers"]

    with pytest.raises(ValueError, match="pages must preserve the approved name/route inventory"):
        validate_plan_coverage(dropped, context)
