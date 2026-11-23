"""AppGenerator page inventory and pack authorization regression tests.

The historical capture from OSS 20eb8742 records a free customer tracker with
two approved pages and an unapproved MozaiksPay claim. Replay the captured
AppBuildPlan and context as recorded. A separate synthetic build approves a
local page pack and exercises template ownership and page coverage.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.app_plan_review import (
    review_app_build_plan,
    validate_plan_coverage,
)
from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
    verified_pack_selection,
)
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.generator_support.design_access_policy import (
    resolve_reviewed_pack_selection,
)
from tests.reviewed_pack_fixture import registered_pack_fixture, reviewed_pack_fixture
from tests.test_continuous_deterministic_materialization import _load_models

FIXTURE = Path(__file__).parent / "fixtures" / "appplan_review_live_20eb8742.json"
APPROVED = {("Customers", "/customers"), ("Dashboard", "/dashboard")}
PACK_PAGES = {("Billing", "/billing"), ("Usage", "/usage")}
HELP_PAGE = ("Help", "/help")
HELP_PATH = "ui/pages/help.yaml"


@pytest.fixture(autouse=True)
def _load_factory_contracts():
    _load_models()


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _pages(plan: dict) -> set[tuple[str, str]]:
    return {(page["name"], page["route"]) for page in plan["pages"]}


@pytest.fixture
def reviewed_local_page_build(tmp_path, monkeypatch) -> tuple[ContextVariablesBridge, dict]:
    pack_id = "local_help"
    pack = registered_pack_fixture(tmp_path, monkeypatch, pack_id, "config_file")
    root = Path(pack["pack_source_path"])
    config_path = root / "context.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["assets"].append({"path": "templates", "kind": "templates"})
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    contract_path = root / "contract.yaml"
    contract = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    contract["required_outputs"] = [{"path": HELP_PATH, "owner": "templates"}]
    contract_path.write_text(yaml.safe_dump(contract), encoding="utf-8")

    name, route = HELP_PAGE
    template = root / "templates" / HELP_PATH
    template.parent.mkdir(parents=True)
    template.write_text(yaml.safe_dump({
        "schema_version": "mozaiks.app_page.v1",
        "name": name,
        "route": route,
        "title": name,
        "page_type": "landing",
        "layout": "full-width",
        "sections": [{"id": "help-heading", "primitive": "PageHeader", "config": {"title": name}}],
    }), encoding="utf-8")

    capture = _fixture()
    captured = capture["context_variables"]
    variables = {key: captured[key] for key in ("data_contract", "design_surface_map", "experience_spec")}
    page = {"name": name, "route": route, "ui_surface": "declarative_page"}
    variables["experience_spec"]["pages"].append({**page, "intent": "Read offline help.", "layout": "full-width"})
    context = ContextVariablesBridge(reviewed_pack_fixture(variables, [pack_id]))

    plan = capture["AppBuildPlan"]
    plan["agent_message"] = "Build the free customer tracker and its approved offline help page."
    plan["capability_packs"] = [
        pack for pack in plan["capability_packs"] if pack["capability_source"] == "generated_module"
    ]
    plan["pages"].append({**page, "purpose": "Read offline help.", "ui_layout": "full-width"})
    next(task for task in plan["build_tasks"] if task["task_type"] == "page_bundle")["owned_paths"].append(HELP_PATH)
    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)
    assert result == {"outcome": "ready", "task_count": 6}, result
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


def test_the_captured_free_plan_cannot_select_the_operator_pack():
    capture = _fixture()
    context = ContextVariablesBridge(capture["context_variables"])
    result = review_app_build_plan(AppBuildPlan=capture["AppBuildPlan"], context_variables=context)

    assert result["ok"] is False
    assert result["outcome"] == "needs_revision"
    assert "unapproved surface 'mozaikspay'" in result["error"]
    assert context.get("app_plan_ready") is False
    assert context.get("app_build_plan") is None


def test_the_reviewed_local_pack_page_stays_in_the_plan_without_model_work(reviewed_local_page_build):
    context, cached = reviewed_local_page_build

    assert _pages(cached) == APPROVED | {HELP_PAGE}
    assert cached["revenue_model"] == "free"
    assert cached["monetization_provider"] is None
    assert context.get("app_plan_feedback") == ""
    [approved_pack] = resolve_reviewed_pack_selection(context)
    assert approved_pack["pack_id"] == "local_help"
    [cached_pack] = [
        pack for pack in cached["capability_packs"] if pack["capability_pack_id"] == approved_pack["pack_id"]
    ]
    assert cached_pack["capability_source"] == "config_file"
    assert "pack_source_path" not in cached_pack
    assert (cached_pack["version"], cached_pack["digest"]) == (approved_pack["version"], approved_pack["digest"])
    resolved = verified_pack_selection(cached_pack, context_variables=context)
    assert resolved is not None and Path(resolved["source_path"]).is_dir()
    assert {key: resolved[key] for key in ("pack_id", "version", "digest")} == approved_pack

    items = detach(context.get("app_task_batch_items"))
    page_owned = sorted(
        path for task in items if task["task_type"] == "page_bundle" for path in task["owned_paths"]
    )
    # The pack ships help.yaml. The page tasks author the remaining approved pages.
    assert page_owned == ["app.json", "ui/pages/customers.yaml", "ui/pages/dashboard.yaml"]


def test_a_page_no_one_approved_or_ships_is_still_rejected(reviewed_local_page_build):
    context, cached = reviewed_local_page_build
    invented = deepcopy(cached)
    invented["pages"].append({**deepcopy(invented["pages"][0]), "name": "Reports", "route": "/reports"})

    with pytest.raises(ValueError, match="Page '/reports' has no reviewed page in ExperienceSpec"):
        validate_plan_coverage(invented, context)


def test_dropping_an_approved_page_is_still_rejected(reviewed_local_page_build):
    context, cached = reviewed_local_page_build
    dropped = deepcopy(cached)
    dropped["pages"] = [page for page in dropped["pages"] if page["route"] != "/customers"]

    with pytest.raises(ValueError, match="pages must preserve the approved name/route inventory"):
        validate_plan_coverage(dropped, context)
