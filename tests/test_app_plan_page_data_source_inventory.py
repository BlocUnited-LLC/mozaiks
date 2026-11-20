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
from types import SimpleNamespace

import pytest
import yaml

from factory_app.workflows._shared.pack_selection import pin_pack_choices
from factory_app.workflows._shared.subscription_contract_context import selected_feature_gates
from factory_app.workflows.AppGenerator.tools.app_plan_review import review_app_build_plan
from factory_app.workflows.AppGenerator.tools.hook_file_contract_context import (
    _build_file_contracts_body,
)
from factory_app.workflows.AppGenerator.tools.module_entitlement_gates import (
    apply_entitlement_gates,
)
from factory_app.workflows.AppGenerator.tools.plan_page_data_sources import (
    drop_unapproved_page_data_sources,
)
from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
    PackIntegrityError,
    verify_pack_integrity,
)
from factory_app.workflows.DesignDocs.tools.save_design_doc import _compose_reviewed_pack_data
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.generator_support.design_access_policy import access_policy_sha256
from mozaiksai.core.workflow.generator_support.module_role_grants import verified_pack_authorization
from mozaiksai.core.workflow.generator_support.page_plan_utils import (
    module_action_index_from_context,
)
from tests.reviewed_pack_fixture import registered_pack_fixture, reviewed_pack_fixture
from tests.test_continuous_deterministic_materialization import _load_models

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).parent / "fixtures" / "appplan_review_live_0b1b740d.json"
NOTE = "Planned data sources removed because no approved action exists: "


@pytest.fixture(autouse=True)
def _load_factory_contracts(monkeypatch, tmp_path):
    from tests.test_factory_pack_selection import _enable_managed

    _enable_managed(tmp_path, monkeypatch)
    _load_models()


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _context(variables: dict) -> ContextVariablesBridge:
    variables = deepcopy(variables)
    for pack in variables.get("capability_packs") or []:
        # Project the current source and its verified pins, retaining the raw fixture.
        source = ROOT / pack["pack_source_path"]
        metadata = verify_pack_integrity(source, pack["id"])
        pack.update(pack_source_path=str(source), version=metadata["version"], digest=metadata["digest"])
    return ContextVariablesBridge(reviewed_pack_fixture(variables, ["mozaikspay"]))


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
    expected_task = _page_task(expected)
    output_contract = expected_task["acceptance_criteria"][-1]
    assert output_contract.startswith(
        "Page output contract from final owned_paths: " + json.dumps(expected_task["owned_paths"]) + ". "
    )
    assert "Emit a non-null AppSchemaOutput.manifest to materialize app.json." in output_contract
    assert "inventory covers the full approved app_build_plan" in output_contract
    assert output_contract.endswith("leave unowned fields null. Do not widen owned_paths.")
    for task in (page_task, expected_task):
        assert task["acceptance_criteria"][-1] == output_contract
        assert task["initial_message"].endswith("\n\n" + output_contract)
    # Remove only the diagnosed hint's note, retaining the authoritative output
    # contract and every other instruction for the exact whole-plan comparison.
    removed_source_note = note + (
        " Bind each such section only to an action its module declares, through that "
        "action's declared output keys; do not invent actions."
    )
    assert page_task["initial_message"].count("\n\n" + removed_source_note) == 1
    page_task["initial_message"] = page_task["initial_message"].replace("\n\n" + removed_source_note, "", 1)
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


def _stock_design(pack_id):
    # DesignDocs adds the stock module and its collection entities to this UI-only design.
    surface_map = {"surfaces": [{"surface_id": "editor", "surface_kind": "ui_only", "owner": "app"}]}
    selection = pin_pack_choices({}, [pack_id])
    authorization = verified_pack_authorization({}, selection=selection)
    data = _compose_reviewed_pack_data({"version": "1", "surfaces": [], "shared_collections": []}, surface_map, authorization)
    return {"design_surface_map": surface_map, "data_contract": data}


@pytest.mark.parametrize("pack_id,module_id,expected,refused", [
    ("content", "content", ["list_entries", "get_entry", "create_entry", "update_entry", "archive_entry"],
     ["get_entries", "create_content_entry", "delete_entry"]),
    ("social", "activity_feed", ["get_feed", "get_profile_activity"], ["record_activity", "on_friendship_created"]),
])
def test_app_page_uses_reviewed_pack_module_http_actions(pack_id, module_id, expected, refused):
    values = _stock_design(pack_id)
    context = ContextVariablesBridge(reviewed_pack_fixture(values, [pack_id]))
    sources = [{"module_id": module_id, "action_id": action} for action in expected + refused]
    hints = [{"data_source": dict(source)} for source in sources]
    plan = {"pages": [{"route": "/editor", "name": "Editor", "sections_hint": hints}], "build_tasks": []}

    drop_unapproved_page_data_sources(plan, context)
    assert [hint["data_source"] for hint in hints] == sources[:len(expected)] + [None] * len(refused)
    assert set(module_action_index_from_context(context)[module_id]) == set(expected)
    prompt = _build_file_contracts_body(SimpleNamespace(name="AppPlanAgent", context_variables=context), {})
    assert all(action in prompt for action in expected)
    assert all(action not in prompt for action in refused)

    # An available pack and a worker-supplied manifest cannot stand in for reviewed pack selection.
    offering = ROOT / f"factory_app/build_context/{pack_id}/templates/modules/{module_id}/module.yaml"
    values["generated_files"] = {f"modules/{module_id}/module.yaml": offering.read_text(encoding="utf-8")}
    unselected = ContextVariablesBridge(reviewed_pack_fixture(values, []))
    index = module_action_index_from_context(unselected)[module_id]
    assert set(expected).intersection(index) == ({"list_entries"} if module_id == "content" else set())


@pytest.mark.parametrize("pack_id,module_id", [("content", "content"), ("social", "activity_feed")])
def test_free_assembly_accepts_exact_stock_manifest_without_expanding_gate_authority(pack_id, module_id):
    context = ContextVariablesBridge(reviewed_pack_fixture(_stock_design(pack_id), [pack_id]))
    path = f"modules/{module_id}/module.yaml"
    text = (ROOT / f"factory_app/build_context/{pack_id}/templates" / path).read_text(encoding="utf-8")
    files = [{"filename": path, "content": text}]
    assert apply_entitlement_gates(files, context_variables=context) == files
    manifest = yaml.safe_load(text)
    original_action = manifest["actions"][-1]["id"]
    with pytest.raises(ValueError, match="outside the approved inventory"):
        selected_feature_gates({"pro": [f"module.{module_id}.{original_action}"]}, context)
    manifest["actions"][-1]["id"] = "invented_action"
    with pytest.raises(ValueError, match="unapproved module actions .*invented_action"):
        apply_entitlement_gates([{"filename": path, "content": yaml.safe_dump(manifest)}], context_variables=context)


def test_stale_stock_module_cannot_supply_page_actions():
    values = reviewed_pack_fixture(_stock_design("content"), ["content"])
    summary = values["design_docs_artifact"]["commit_metadata"]["metadata"]["summary_payload"]
    review = summary["access_policy_review"]
    review["pack_identities"][0]["digest"] = "sha256:" + "0" * 64
    review["policy_sha256"] = access_policy_sha256(
        summary["access_policy"], summary["experience_spec"], pack_identities=review["pack_identities"],
    )
    with pytest.raises(PackIntegrityError, match="Reviewed pack 'content' changed"):
        module_action_index_from_context(ContextVariablesBridge(values))


def test_revision_reads_a_workspace_owned_manifest_after_genesis(tmp_path, monkeypatch):
    path = "modules/notes/module.yaml"
    pack = registered_pack_fixture(tmp_path, monkeypatch, "notes_seed", "generated_module")
    root = Path(pack["pack_source_path"])
    config = yaml.safe_load((root / "context.yaml").read_text(encoding="utf-8"))
    config["assets"].append({"kind": "templates", "path": "templates"})
    (root / "context.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    contract = yaml.safe_load((root / "contract.yaml").read_text(encoding="utf-8"))
    contract["required_outputs"] = [{"path": path, "owner": "workspace"}]
    (root / "contract.yaml").write_text(yaml.safe_dump(contract), encoding="utf-8")

    def manifest(action):
        return yaml.safe_dump({"module": {"id": "notes"}, "actions": [{"id": action}]})

    template = root / "templates" / path
    template.parent.mkdir(parents=True)
    template.write_text(manifest("seed"), encoding="utf-8")
    values = reviewed_pack_fixture({
        "generated_files": {path: manifest("admitted")},
        "design_surface_map": {"surfaces": [{"surface_id": "notes", "surface_kind": "module", "owner": "app",
                                             "owned_mutations": ["seed", "admitted", "authored"]}]},
    }, ["notes_seed"])
    assert set(module_action_index_from_context(values)["notes"]) == {"seed"}
    values["build_mode"] = "revision"
    assert set(module_action_index_from_context(values)["notes"]) == {"admitted"}
    assert set(module_action_index_from_context(values, authored_files={path: manifest("authored")})["notes"]) == {"authored"}


@pytest.mark.parametrize("surface,path", [
    ("custom_react_page", "ui/pages/custom/dashboard.jsx"),
    ("declarative_page", "ui/pages/dashboard.yaml"),
])
def test_removed_hint_notifies_only_the_task_owning_the_canonical_page(surface, path):
    fixture = _fixture()
    page = deepcopy(fixture["AppBuildPlan"]["pages"][0])
    page["ui_surface"] = surface
    plan = {"pages": [page], "capability_packs": [], "build_tasks": [
        {"task_id": "dashboard", "task_type": "page_bundle", "owned_paths": [path], "initial_message": "Build the dashboard."},
        {"task_id": "other", "task_type": "page_bundle", "owned_paths": ["ui/pages/other.yaml"], "initial_message": "Build the other page."},
    ]}

    repairs = drop_unapproved_page_data_sources(plan, _context(fixture["context_variables"]))

    assert page["sections_hint"][0]["data_source"] is None
    assert "tasks/get_kpi_stats" in plan["build_tasks"][0]["initial_message"]
    assert NOTE in plan["build_tasks"][0]["initial_message"]
    assert "tasks declares" in plan["build_tasks"][0]["initial_message"]
    assert plan["build_tasks"][1]["initial_message"] == "Build the other page."
    assert any("dashboard: noted" in repair for repair in repairs)


@pytest.mark.parametrize("surface,path", [
    ("custom_react_page", "ui/pages/custom/focus.jsx"),
    ("declarative_page", "ui/pages/focus.yaml"),
])
def test_page_dependencies_follow_the_canonical_owned_page_and_declared_source(surface, path):
    from factory_app.workflows.AppGenerator.tools.app_build_plan import (
        _normalize_page_task_dependencies,
    )

    tasks = [
        {"task_id": "records_contract", "task_type": "module_contract", "capability_pack_id": "records"},
        {"task_id": "notes_contract", "task_type": "module_contract", "capability_pack_id": "notes"},
        {"task_id": "focus_page", "task_type": "page_bundle", "capability_pack_id": None,
         "owned_paths": [path], "depends_on": []},
    ]
    pages = [
        {"name": "Focus Timer", "route": "/focus", "ui_surface": surface,
         "sections_hint": [{"data_source": {"module_id": "records", "action_id": "list_records"}}]},
        {"name": "Other Page", "route": "/other", "ui_surface": "declarative_page",
         "sections_hint": [{"data_source": {"module_id": "notes", "action_id": "list_notes"}}]},
    ]

    normalized = _normalize_page_task_dependencies(tasks, pages=pages)

    assert normalized[-1]["depends_on"] == ["records_contract"]
    assert tasks[-1]["depends_on"] == []
