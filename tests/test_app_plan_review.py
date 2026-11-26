from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.app_plan_review import (
    _label_persistence_tasks,
    _repair_plan,
    review_app_build_plan,
    validate_plan_coverage,
    validate_plan_origins,
)
from mozaiksai.core.session.build_context_schema import VALID_CAPABILITY_SOURCES
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.frozen import detach
from tests.appgenerator_subscription_fixture import subscription_contract
from tests.reviewed_pack_fixture import registered_pack_fixture, reviewed_pack_fixture
from tests.test_continuous_deterministic_materialization import _load_models, _plan_payload

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _load_factory_contracts(monkeypatch, tmp_path):
    from tests.test_factory_pack_selection import _enable_managed

    _enable_managed(tmp_path, monkeypatch)
    _load_models()


def _plan():
    plan = _plan_payload()["AppBuildPlan"]
    plan["readiness_profile"] = "none"
    plan["capability_packs"] = [{
        "capability_pack_id": "reports", "surface_id": "reports",
        "surface_kind": "module", "capability_source": "generated_module",
        "pack_type": "custom_domain", "label": "Reports", "summary": "Stored reports",
        "implementation_mode": "hybrid", "primary_entities": ["Report"],
    }]
    service = next(task for task in plan["build_tasks"] if task["task_type"] == "business_services")
    service["owned_paths"] += ["modules/reports/backend/repo.py", "modules/reports/backend/policy.py"]
    page = next(task for task in plan["build_tasks"] if task["task_type"] == "page_bundle")
    page.update(surface_id="reports", surface_kind="module")
    return plan


def _context(ui_surface="declarative_page"):
    return ContextVariablesBridge({
        "build_mode": "initial", "app_plan_attempts": 0, "app_plan_outcome": "blocked",
        "design_surface_map": {"surfaces": [{"surface_id": "reports", "surface_kind": "module", "owner": "app", "primary_entities": ["Report"]}]},
        "experience_spec": {"pages": [{"name": "Reports", "ui_surface": ui_surface, "route": "/reports"}]},
        "data_contract": {"version": "1", "surfaces": [{
            "surface_id": "reports", "surface_kind": "module", "collections": [{
                "name": "reports", "entity": "Report", "scope": "app", "tenancy": "per_user",
                "owner_field": "owner_id", "ownership": {"surface_id": "reports", "surface_kind": "module"},
                "fields": [{"name": "owner_id", "type": "string", "required": True}],
            }],
        }]},
    })


def _reviewed(context, pack_ids):
    return ContextVariablesBridge(reviewed_pack_fixture(context.snapshot(), pack_ids))


def _revision_with_form(source, reachable=False):
    plan = _plan()
    plan["build_tasks"] = [task for task in plan["build_tasks"] if task["task_type"] != "page_bundle"]
    path = "ui/pages/reports.yaml"
    values = _context().snapshot()
    collection = values["data_contract"]["surfaces"][0]["collections"][0]
    collection["lifecycle"] = {"write_mode": "module_action"}
    section = {
        "id": "edit-selected", "primitive": "Form", "intent": "Edit the selected report's title",
        "config_hint": json.dumps({"data_source": {"module_id": "reports", "action_id": source}}) if source else None,
    }
    values["experience_spec"]["pages"][0]["sections"] = [section]
    values.update(build_mode="revision", workflow_sequence="design_revision", revision_source_artifact_version_id="v4")
    values = reviewed_pack_fixture(values, [])
    values["design_docs_build_artifact"] = values.pop("design_docs_artifact")
    ask = [{"module": "reports", "action": "summary", "label": "Current reports", "params": {"view": "current"}}]
    baseline = {"route": "/reports", "sections": [], "meta": {"ask_context": ask}}
    plan["pages"][0]["retained_ask_context"] = [{"module": "other", "action": "unreviewed"}]
    if reachable:
        baseline["sections"].append({"id": "edit", "primitive": "Form", "config": {
            "submit_action": {"action_type": "submit", "href": "/api/modules/reports/update_report"},
        }})
    values["generated_files"] = {path: yaml.safe_dump(baseline)}
    return plan, ContextVariablesBridge(values), path, ask


@pytest.mark.parametrize(("source", "reachable", "needs_task"), [
    ("update_report", False, True), ("update_report", True, False),
    ("list_reports", False, False), ("save_private_result", False, False), (None, False, False),
])
def test_revision_schedules_only_missing_reviewed_form_write(source, reachable, needs_task):
    from factory_app.workflows.AppGenerator.tools.repair_policy import owned_diagnostics

    plan, context, path, ask = _revision_with_form(source, reachable)
    if needs_task:
        with pytest.raises(ValueError, match="Missing page_bundle task ownership"):
            validate_plan_coverage(plan, context)
    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)
    assert result["outcome"] == "ready", result
    cached = detach(context.get("app_build_plan"))
    tasks = [task for task in cached["build_tasks"] if task["task_type"] == "page_bundle"]
    assert len(tasks) == int(needs_task)
    assert cached["pages"][0]["required_write_actions"] == (
        [{"module_id": "reports", "action_id": "update_report"}] if source == "update_report" else []
    )
    assert cached["pages"][0].get("retained_ask_context") == (ask if needs_task else None)
    if needs_task:
        [task] = tasks
        cached["pages"][0]["retained_ask_context"] = []
        assert review_app_build_plan(AppBuildPlan=cached, context_variables=context)["outcome"] == "ready"
        assert detach(context.get("app_build_plan"))["pages"][0]["retained_ask_context"] == ask
        assert task["owned_paths"] == [path] and task["initial_agent"] == "AppSchemaAgent"
        assert "meta.ask_context" in task["initial_message"]
        assert {t["task_id"] for t in cached["build_tasks"] if t["task_type"] == "module_contract"} <= set(task["depends_on"])
        diagnostic = owned_diagnostics(context, [{"path": path, "error": "The edit control is missing"}])[0]
        assert diagnostic["owner_agent"] == "AppSchemaAgent" and diagnostic["task_id"] == task["task_id"]
    validate_plan_coverage(cached, context)


def test_capability_source_uses_canonical_finite_vocabulary():
    schema = yaml.safe_load((ROOT / "factory_app/workflows/AppGenerator/structured_outputs.yaml").read_text(encoding="utf-8"))
    field = schema["models"]["AppCapabilityPack"]["fields"]["capability_source"]
    assert field["type"] == "literal"
    assert set(field["values"]) == VALID_CAPABILITY_SOURCES | {"host_universal"}


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["indirect", "known_path", "known_path_and_wildcard", "direct_page", "unrelated_workflow", "removed_create",
                                   "different_page_owner", "mixed_ask", "restricted_ask"])
async def test_explicit_page_task_retains_only_reviewed_workflow_controls(change, monkeypatch):
    from unittest.mock import AsyncMock

    from factory_app.workflows.AppGenerator.tools.app_build_plan import _apply_reviewed_page_writes
    from mozaiksai.core.workflow.generator_support import module_revision
    from mozaiksai.core.workflow.generator_support.page_action_bindings import (
        planned_page_writes,
        validate_required_page_writes,
    )

    plan, ctx, path, ask = _revision_with_form("update_report")
    ctx = ctx.snapshot()
    surface = ctx["design_surface_map"]["surfaces"][0]
    surface.update(owned_mutations=["create_report", "update_report"], owned_pages=["Reports"], custom_reads=["summary"])
    if change == "removed_create":
        surface["owned_mutations"].remove("create_report")
    if change == "different_page_owner":
        surface["owned_pages"] = ["Other"]
    ctx["design_docs_build_artifact"]["commit_metadata"]["metadata"]["summary_payload"]["surface_map"] = deepcopy(ctx["design_surface_map"])
    paths = {"known_path": [path], "known_path_and_wildcard": [path, "ui/pages/*.yaml"]}.get(change, ["ui/pages/*.yaml"])
    ctx.update(revision_scope="feature", change_intent={"requires_workflow_revision": change != "unrelated_workflow"},
               impact_set={"affected_bundle_paths": paths})
    if change == "direct_page":
        ctx["workflow_sequence"] = "app_revision"
    monkeypatch.setattr(module_revision, "pinned_result_workflows", lambda context: [{"result_write_plans": [
        {"module_id": "reports", "result_write": {"source_action": "create_report"}}]}])
    document = yaml.safe_load(ctx["generated_files"][path])
    document["sections"].append({"id": "create", "primitive": "Form", "config": {
        "submit_action": {"action_type": "submit", "href": "/api/modules/reports/create_report"}}})
    if change == "mixed_ask":
        document["meta"]["ask_context"].append({"module": "another", "action": "summary"})
    files = {path: yaml.safe_dump(document), "modules/reports/module.yaml": yaml.safe_dump({
        "module": {"id": "reports"}, "actions": [{"id": "create_report"},
        {"id": "summary", "ask_context_safe": True, "permissions": ["restricted"] if change == "restricted_ask" else []}]})}
    bindings = module_revision.retained_workflow_page_bindings(ctx, files)
    if change in {"direct_page", "unrelated_workflow", "removed_create", "different_page_owner"}:
        assert bindings == {}
        return
    assert bindings[path]["writes"] == [{"module_id": "reports", "action_id": "create_report"}]
    if change == "restricted_ask":
        assert "ask_context" not in bindings[path]
        return
    assert bindings[path]["ask_context"] == ask
    assert bool(bindings[path].get("partial_ask_context")) == (change == "mixed_ask")
    ctx["retained_workflow_page_bindings"] = bindings
    page = {**plan["pages"][0], "ui_surface": "declarative_page"}
    _apply_reviewed_page_writes([page], ctx)
    assert page["retained_ask_context"] == ask
    if change == "mixed_ask":
        from mozaiksai.core.workflow.generator_support.page_plan_utils import validate_planned_page

        minimal = {"schema_version": "mozaiks.app_page.v1", "name": "reports", "title": "Reports",
                   "route": "/reports", "page_type": "record_list", "layout": "full-width",
                   "sections": [{"id": "header", "primitive": "PageHeader", "config": {"title": "Reports"}}],
                   "meta": {"ask_context": document["meta"]["ask_context"]}}
        validate_planned_page(yaml.safe_dump(minimal), page, path)
        minimal["meta"]["ask_context"] = deepcopy(ask)
        validate_planned_page(yaml.safe_dump(minimal), page, path)
        minimal["meta"]["ask_context"][0]["params"] = {}
        with pytest.raises(ValueError, match="meta.ask_context"):
            validate_planned_page(yaml.safe_dump(minimal), page, path)
    required = planned_page_writes([page])
    assert required == {"/reports": {"reports/create_report", "reports/update_report"}}
    with pytest.raises(ValueError, match="update_report"):
        validate_required_page_writes(files[path], "/reports", required, path, identity_validated=True)
    read = AsyncMock(return_value={**files, "ui/pages/unrelated.yaml": "private-other-page"})
    monkeypatch.setattr("factory_app.workflows._shared.revision_app_source.read_bound_revision_files", read)
    readonly_ctx = ContextVariablesBridge(ctx)
    prompt = await module_revision.retained_workflow_page_prompt(readonly_ctx, [path, path, "app.json"])
    rows = json.loads(prompt.split("app data.\n")[1])
    assert list(rows) == [path] and rows[path]["source"] == files[path]
    assert rows[path]["retained"]["ask_context"] == ask and rows[path]["truncated"] is False
    read.assert_awaited_once_with(readonly_ctx)
    assert not await module_revision.retained_workflow_page_prompt(ctx, ["ui/pages/unrelated.yaml"])


@pytest.mark.asyncio
async def test_rejected_cached_page_projection_discards_dispatch_rows():
    from factory_app.workflows.AppGenerator.tools.hydrate_app_build_plan_context import (
        hydrate_app_build_plan_context,
    )

    plan, context, path, _ = _revision_with_form("update_report")
    context.set("app_build_plan", plan)
    context.set("app_task_batch_items", [{"task_id": "past-executable-page"}])
    context.set("retained_workflow_page_bindings", {path: {"writes": [{"module_id": "reports", "action_id": "unknown"}]}})
    with pytest.raises(ValueError, match="unknown or non-page-callable action"):
        await hydrate_app_build_plan_context(context)
    assert not context.get("app_task_batch_items") and not context.get("app_plan_ready")


def test_complete_plan_is_cached_without_losing_typed_fields():
    plan = _plan()
    plan["shell_preset_hint"] = "operations"
    context = _context()
    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)
    assert result["outcome"] == "ready", result
    assert context.get("app_plan_ready") is True
    assert context.get("app_build_plan")["shell_preset_hint"] == "operations"
    assert context.get("app_build_plan")["revenue_model"] == "free"


@pytest.mark.parametrize("owns_manifest", [False, True])
def test_page_output_contract_reconciles_manifest_ownership_with_page_only_brief(owns_manifest):
    plan = _plan()
    task = next(task for task in plan["build_tasks"] if task["task_type"] == "page_bundle")
    task["owned_paths"] = (["app.json"] if owns_manifest else []) + ["ui/pages/reports.yaml"]
    task["initial_message"] = "Emit ui/pages/reports.yaml only. Keep owner-only editing in a modal."
    task["acceptance_criteria"] = ["No manifest or unrelated UI files are added.", "Keep owner-only editing."]
    original = deepcopy(task)
    context = _context()

    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    assert result["outcome"] == "ready", result
    accepted = next(item for item in detach(context.get("app_task_batch_items")) if item["task_id"] == task["task_id"])
    assert accepted["owned_paths"] == ["app.json", "ui/pages/reports.yaml"]
    contract = accepted["acceptance_criteria"][-1]
    assert "Emit a non-null AppSchemaOutput.manifest" in contract
    assert "supersedes earlier artifact inclusion/exclusion" in contract
    assert accepted["initial_message"].endswith(contract)
    assert accepted["initial_message"].startswith(original["initial_message"])
    assert accepted["acceptance_criteria"][:-1] == original["acceptance_criteria"]
    assert accepted["current_build_task"]["initial_message"] == accepted["initial_message"]
    assert task == original, "Review must not mutate the submitted plan."


@pytest.mark.parametrize("manifest_owner", [0, 1])
def test_split_page_output_contract_has_one_manifest_owner(manifest_owner):
    plan = _plan()
    first = next(task for task in plan["build_tasks"] if task["task_type"] == "page_bundle")
    second = {**deepcopy(first), "task_id": "archive-page", "owned_paths": ["ui/pages/archive.yaml"]}
    first["owned_paths"] = ["ui/pages/reports.yaml"]
    page_tasks = [first, second]
    page_tasks[manifest_owner]["owned_paths"].insert(0, "app.json")
    for task in page_tasks:
        task["initial_message"] = "Emit only this page. Keep its approved behavior."
        task["acceptance_criteria"] = ["No manifest is added."]
    plan["build_tasks"].append(second)
    plan["pages"].append({**deepcopy(plan["pages"][0]), "name": "Archive", "route": "/archive"})
    context = _context()
    experience = detach(context.get("experience_spec"))
    experience["pages"].append({"name": "Archive", "route": "/archive", "ui_surface": "declarative_page"})
    context.set("experience_spec", experience)

    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    assert result["outcome"] == "ready", result
    accepted = {task["task_id"]: task for task in detach(context.get("app_task_batch_items"))}
    for index, task in enumerate(page_tasks):
        item = accepted[task["task_id"]]
        assert set(item["owned_paths"]) == set(task["owned_paths"])
        contract = item["acceptance_criteria"][-1]
        expected = ("Emit a non-null AppSchemaOutput.manifest" if index == manifest_owner
                    else "Set AppSchemaOutput.manifest to null")
        assert expected in contract
        assert item["initial_message"].endswith(contract)


def test_revision_page_output_contract_preserves_page_only_scope_and_is_idempotent():
    plan = _plan()
    task = next(task for task in plan["build_tasks"] if task["task_type"] == "page_bundle")
    task["owned_paths"] = ["ui/pages/reports.yaml"]
    task["initial_message"] = "Revise only this page's editor; preserve its route and roles."
    task["acceptance_criteria"] = ["Keep the approved editor fields."]
    context = _context()
    context.set("build_mode", "revision")
    context.set("generated_files", {"app.json": '{"appName":"Existing App","authRequired":true}'})
    baseline = detach(context.get("generated_files"))

    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    assert result["outcome"] == "ready", result
    accepted = next(item for item in detach(context.get("app_task_batch_items")) if item["task_id"] == task["task_id"])
    assert accepted["owned_paths"] == ["ui/pages/reports.yaml"]
    assert "Set AppSchemaOutput.manifest to null" in accepted["acceptance_criteria"][-1]
    assert detach(context.get("generated_files")) == baseline
    first = deepcopy(accepted)
    result = review_app_build_plan(AppBuildPlan=detach(context.get("app_build_plan")), context_variables=context)
    assert result["outcome"] == "ready", result
    accepted = next(item for item in detach(context.get("app_task_batch_items")) if item["task_id"] == task["task_id"])
    assert accepted == first


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["forged_refs", "removed_mark", "unreviewed"])
async def test_recover_revision_page_duty_from_immutable_source_or_require_plan_review(state, monkeypatch):
    from unittest.mock import AsyncMock

    from factory_app.workflows.AppGenerator.tools import hydrate_app_build_plan_context as hydration

    plan, context, path, ask = _revision_with_form("update_report")
    assert review_app_build_plan(AppBuildPlan=plan, context_variables=context)["outcome"] == "ready"
    approved = detach(context.get("app_build_plan"))
    source = detach(context.get("generated_files"))
    read = AsyncMock(return_value=source)
    monkeypatch.setattr(hydration, "read_bound_revision_files", read)
    context.set("generated_files", {path: yaml.safe_dump({"route": "/reports", "sections": []})})
    if state == "removed_mark":
        approved["pages"][0].pop("retained_ask_context")
    else:
        approved["pages"][0]["retained_ask_context"] = [{"module": "other", "action": "wrong"}]
    context.set("app_build_plan", approved)
    context.set("app_plan_ready", state != "unreviewed")
    context.set("app_task_batch_items", [])
    result = await hydration.hydrate_app_build_plan_context(context)
    read.assert_awaited_once_with(context)
    if state == "forged_refs":
        assert result["status"] == "hydrated"
        assert detach(context.get("app_build_plan"))["pages"][0]["retained_ask_context"] == ask
        assert context.get("app_task_batch_items")
    else:
        assert result == {"status": "skipped", "reason": "revision_page_plan_requires_review"}
        assert not context.get("app_plan_ready") and not context.get("app_task_batch_items")


@pytest.mark.asyncio
async def test_authored_plan_cannot_choose_prior_read_timestamp_guarantees():
    from factory_app.workflows.AppGenerator.tools.hydrate_app_build_plan_context import (
        hydrate_app_build_plan_context,
    )
    plan, context, _, _ = _revision_with_form("update_report")
    plan["legacy_optional_updated_at_reads"] = {"other": ["erase"]}
    context.set("legacy_optional_updated_at_reads", {"reports": ["list_reports"]})
    assert review_app_build_plan(AppBuildPlan=plan, context_variables=context)["outcome"] == "ready"
    approved = detach(context.get("app_build_plan"))
    assert approved["legacy_optional_updated_at_reads"] == {"reports": ["list_reports"]}
    approved["legacy_optional_updated_at_reads"] = {"other": ["bad"]}
    context.set("app_build_plan", approved)
    await hydrate_app_build_plan_context(context)
    assert detach(context.get("app_build_plan"))["legacy_optional_updated_at_reads"] == {"reports": ["list_reports"]}
    context.set("build_mode", "genesis")
    await hydrate_app_build_plan_context(context)
    assert "legacy_optional_updated_at_reads" not in context.get("app_build_plan")


@pytest.mark.parametrize("replacement", [[], [{"module_id": "reports", "action_id": "erase_all"}]])
def test_rereview_binds_writes_from_design_and_rejects_other_unknown_page_fields(replacement):
    context = _context()
    experience = detach(context.get("experience_spec"))
    source = {"module_id": "reports", "action_id": "create_report"}
    surfaces = detach(context.get("design_surface_map"))
    surfaces["surfaces"][0]["owned_mutations"] = [source["action_id"]]
    context.set("design_surface_map", surfaces)
    experience["pages"][0]["sections"] = [{
        "primitive": "Form",
        "config_hint": json.dumps({"submit_action": {"action_type": "submit", "data_source": source}}),
    }]
    context.set("experience_spec", experience)
    context = _reviewed(context, [])
    result = review_app_build_plan(AppBuildPlan=_plan(), context_variables=context)
    assert result["outcome"] == "ready", result
    cached = detach(context.get("app_build_plan"))
    assert cached["pages"][0]["required_write_actions"] == [source]
    cached["pages"][0]["required_write_actions"] = replacement

    result = review_app_build_plan(AppBuildPlan=cached, context_variables=context)

    assert result["outcome"] == "ready", result
    assert detach(context.get("app_build_plan"))["pages"][0]["required_write_actions"] == [source]
    assert cached["pages"][0]["required_write_actions"] == replacement
    cached["pages"][0]["unreviewed_field"] = True
    result = review_app_build_plan(AppBuildPlan=cached, context_variables=context)
    assert "pages.0.unreviewed_field" in result["error"]
    assert not context.get("app_plan_ready")


@pytest.mark.parametrize(("approved", "proposed"), [
    ("custom_react_page", "declarative_page"),
    ("declarative_page", "custom_react_page"),
])
def test_plan_cannot_change_an_approved_rendering_contract(approved, proposed):
    plan = _plan()
    plan["pages"][0]["ui_surface"] = proposed
    context = _context(approved)
    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)
    assert result["outcome"] == "needs_revision"
    assert "must preserve approved ui_surface" in result["error"]
    assert approved in result["error"]
    assert not context.get("app_plan_ready")
    assert not context.get("app_task_batch_items")


def test_saved_design_without_renderer_requires_design_reentry():
    plan = _plan()
    context = _context()
    experience = detach(context.get("experience_spec"))
    experience["pages"][0].pop("ui_surface")
    context.set("experience_spec", experience)
    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)
    assert result["outcome"] == "needs_revision"
    assert "revise DesignDocs before app planning" in result["error"]
    assert not context.get("app_plan_ready")


def test_custom_page_plan_materializes_the_approved_surface_without_a_phantom_yaml():
    plan = _plan()
    plan["pages"][0]["ui_surface"] = "custom_react_page"
    context = _context("custom_react_page")

    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    assert result["outcome"] == "ready", result
    tasks = [task for task in context.get("app_task_batch_items") if task["task_type"] == "page_bundle"]
    assert len(tasks) == 1
    paths = set(tasks[0]["owned_paths"])
    assert {"app.json", "ui/pages/custom/reports.jsx", "ui/route_manifest.json", "ui/index.js"} <= paths
    assert "ui/pages/reports.yaml" not in paths


def test_custom_bundle_cannot_split_registry_and_components_across_workers():
    plan = _plan()
    plan["pages"][0]["ui_surface"] = "custom_react_page"
    task = next(task for task in plan["build_tasks"] if task["task_type"] == "page_bundle")
    task["owned_paths"] = ["app.json", "ui/route_manifest.json", "ui/index.js"]
    second = {**task, "task_id": "custom-page", "owned_paths": ["ui/pages/custom/reports.jsx"]}
    plan["build_tasks"].append(second)
    with pytest.raises(ValueError, match="complete custom route"):
        validate_plan_coverage(plan, _context("custom_react_page"))

    context = _context("custom_react_page")
    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)
    assert result["outcome"] == "ready", result
    accepted = detach(context.get("app_build_plan"))
    validate_plan_coverage(accepted, context)
    paths = [path for task in accepted["build_tasks"] for path in task["owned_paths"]]
    assert len(paths) == len(set(paths))


@pytest.mark.parametrize("registry_path", ["ui/route_manifest.json", "ui/index.js"])
def test_custom_page_cannot_take_over_selected_pack_registry(monkeypatch, registry_path):
    from factory_app.workflows.AppGenerator.tools import app_plan_review

    plan = _plan()
    plan["pages"][0]["ui_surface"] = "custom_react_page"
    before = deepcopy(plan["build_tasks"])
    monkeypatch.setattr(app_plan_review, "pack_owned_output_paths", lambda context: frozenset({registry_path}))
    with pytest.raises(ValueError, match="selected-pack registry ownership"):
        app_plan_review._repair_coverage(plan, _context("custom_react_page"))
    assert plan["build_tasks"] == before
    with pytest.raises(ValueError, match="selected-pack registry ownership"):
        validate_plan_coverage(plan, _context("custom_react_page"))


def test_review_queues_synthesized_module_workers_with_actual_prerequisites():
    plan = _plan()
    page = next(task for task in plan["build_tasks"] if task["task_type"] == "page_bundle")
    page["depends_on"] = []
    plan["build_tasks"] = [page]
    context = _context()

    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    assert result["outcome"] == "ready", result
    tasks = {task["task_type"]: task for task in context.get("app_task_batch_items")}
    contract_id = tasks["module_contract"]["task_id"]
    models_id = tasks["data_models"]["task_id"]
    persistence_id = tasks["persistence_contract"]["task_id"]
    assert list(tasks["data_models"]["depends_on"]) == [contract_id, persistence_id]
    assert list(tasks["business_services"]["depends_on"]) == [contract_id, models_id, persistence_id]
    for kind in ("data_models", "business_services"):
        assert tasks[kind]["current_build_task"]["depends_on"] == tasks[kind]["depends_on"]
    assert contract_id in tasks["page_bundle"]["depends_on"]


def test_repeated_plan_normalization_preserves_owned_reaction_contract():
    plan = _plan()
    contract = next(task for task in plan["build_tasks"] if task["task_type"] == "module_contract")
    reaction_path = "modules/reports/contracts/reactions.yaml"
    contract["owned_paths"].append(reaction_path)
    plan["event_flows"] = [{
        "event_type": "domain.documents.analysis_requested",
        "producer_pack_id": "documents",
        "producing_action": "request_analysis",
        "subscriber_intents": ["reports-review"],
        "workflow_capability_ids": ["reports-review"],
    }]
    context = _context()

    _repair_plan(plan, context)
    normalized = detach(plan)
    _repair_plan(normalized, context)
    contract_after = next(
        task for task in normalized["build_tasks"]
        if task["task_type"] == "module_contract" and task["capability_pack_id"] == "reports"
    )
    assert reaction_path in contract_after["owned_paths"]


def test_review_routes_a_dependency_cycle_to_feedback_before_queuing_workers():
    plan = _plan()
    contract = next(task for task in plan["build_tasks"] if task["task_type"] == "module_contract")
    contract["depends_on"] = ["reports.services"]
    context = _context()

    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    assert result["outcome"] == "needs_revision"
    assert "dependency cycle detected" in result["error"]
    assert not context.get("app_task_batch_items")


def test_review_rechecks_dependencies_after_merging_persistence_tasks():
    plan = _plan()
    contract = next(task for task in plan["build_tasks"] if task["task_type"] == "module_contract")
    contract["depends_on"] = ["persistence_before"]
    for task_id, dependencies in (
        ("persistence_before", []), ("persistence_after", [contract["task_id"]]),
    ):
        plan["build_tasks"].append({
            "task_id": task_id, "task_type": "persistence_contract",
            "capability_pack_id": "reports", "surface_id": "reports", "surface_kind": "module",
            "initial_agent": "DatabaseAgent", "execution_target": "AppGenerator",
            "description": "Declare persistence", "initial_message": "Emit the app data contract.",
            "owned_paths": ["data/contract.json"], "depends_on": dependencies,
        })
    context = _context()

    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    assert result["outcome"] == "needs_revision"
    assert "dependency cycle detected" in result["error"]
    assert not context.get("app_task_batch_items")


@pytest.mark.parametrize("filename", ["app.json", "ui/pages/reports.yaml", "modules/reports/module.yaml", "modules/reports/backend/schemas.py", "modules/reports/backend/handler.py", "modules/reports/backend/service.py", "modules/reports/backend/repo.py", "modules/reports/backend/policy.py"])
def test_incomplete_plan_reports_every_missing_file(filename):
    plan = _plan()
    for task in plan["build_tasks"]:
        task["owned_paths"] = [path for path in task["owned_paths"] if path != filename]
    with pytest.raises(ValueError, match=filename.replace(".", r"\.")):
        validate_plan_coverage(plan, _context())


def test_approved_page_cannot_disappear():
    context = _context()
    context.set("experience_spec", {"pages": [{"name": "Reports", "ui_surface": "declarative_page", "route": "/reports"}, {"name": "Dashboard", "ui_surface": "declarative_page", "route": "/dashboard"}]})
    with pytest.raises(ValueError, match="approved name/route inventory"):
        validate_plan_coverage(_plan(), context)


def test_page_case_mismatch_reports_received_and_expected_paths_without_rewriting():
    plan = _plan()
    task = next(task for task in plan["build_tasks"] if task["task_type"] == "page_bundle")
    task["owned_paths"] = [path.replace("ui/pages/reports.yaml", "ui/pages/Reports.yaml") for path in task["owned_paths"]]
    with pytest.raises(ValueError) as error:
        validate_plan_coverage(plan, _context())
    assert "case-sensitive" in str(error.value)
    assert "ui/pages/Reports.yaml" in str(error.value)
    assert "ui/pages/reports.yaml" in str(error.value)
    assert "ui/pages/Reports.yaml" in task["owned_paths"]


def test_user_scoped_module_gets_its_account_data_handler_planned():
    """The handler is required, and the review now supplies it rather than re-asking.

    A user-scoped module must own backend/account_data_handler.py. Which file
    that is, is derivable from the pack, so rejecting the plan over it cost
    three model rounds and then the run - that is what killed every live build.
    The review now adds the path and accepts the plan.

    The guarantee is unchanged and still enforced where it matters: on the
    generated bundle. test_user_scope_cannot_be_lost_during_module_materialization
    below asserts _scan_planned_user_data_scope rejects output whose module.yaml
    drops the scope or whose account_data_handler.py is missing. Planning the
    file is bookkeeping; producing it is the safety property.
    """
    plan = _plan()
    plan["capability_packs"][0]["user_data_scope"] = True
    context = _context()

    accepted = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    assert accepted["outcome"] == "ready", accepted
    cached = context.get("app_build_plan")
    assert cached["capability_packs"][0]["user_data_scope"] is True
    owned = {
        path
        for task in cached["build_tasks"]
        if task.get("task_type") == "business_services"
        for path in task.get("owned_paths") or []
    }
    assert "modules/reports/backend/account_data_handler.py" in owned


def test_user_scope_cannot_be_lost_during_module_materialization():
    from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import (
        _scan_planned_user_data_scope,
    )

    packs = [{"capability_pack_id": "reports", "capability_source": "generated_module", "user_data_scope": True}]
    files = {"modules/reports/module.yaml": "module:\n  id: reports\n  user_data_scope: false\n"}
    errors = _scan_planned_user_data_scope(files, packs)
    assert len(errors) == 2
    files["modules/reports/module.yaml"] = "module:\n  id: reports\n  user_data_scope: true\n"
    files["modules/reports/backend/account_data_handler.py"] = "# Implementation checked by runtime loader\n"
    assert _scan_planned_user_data_scope(files, packs) == []


def test_product_category_cannot_become_an_unregistered_managed_service():
    plan = _plan()
    plan["capability_packs"][0]["capability_source"] = "managed_capability"
    with pytest.raises(ValueError, match="registered provider pack"):
        validate_plan_origins(plan, _context())


def test_billing_pack_is_only_a_category_for_generated_modules():
    plan = _plan()
    pack = plan["capability_packs"][0]
    pack["pack_type"] = "billing_pack"
    pack["capability_source"] = "generated_module"

    validate_plan_origins(plan, _context())
    assert pack["capability_pack_id"] == "reports"
    assert pack["pack_type"] == "billing_pack"


def test_planner_guidance_separates_billing_category_provider_and_subscription_task():
    guidance = (ROOT / "factory_app/workflows/AppGenerator/agents.yaml").read_text(encoding="utf-8")

    assert "Never emit a category such as `billing_pack` as a managed `capability_pack_id`" in guidance
    assert "canonical managed capability identity is `mozaikspay`" in guidance
    assert "exactly one `generated_module` capability" in guidance
    # Assembly writes config/subscriptions.yaml; the planner is told not to plan it.
    assert "plan no task for `config/subscriptions.yaml`" in guidance
    assert "task_type: subscription_config" not in guidance


def test_mozaikspay_is_the_canonical_managed_subscription_identity():
    plan = _plan()
    plan["capability_packs"] = [{
        "capability_pack_id": "mozaikspay",
        "surface_id": "mozaikspay_managed",
        "surface_kind": "external_integration",
        "pack_type": "mozaikspay",
        "label": "MozaiksPay",
        "summary": "Managed subscription assignment provider.",
        "implementation_mode": "external_integration",
        "capability_source": "managed_capability",
    }]
    plan["build_tasks"] = []
    context = _context()
    context.set("design_surface_map", {"surfaces": []})
    context.set("capability_packs", [{
        "id": "mozaikspay",
        "capability_source": "managed_capability",
    }])
    context = _reviewed(context, ["mozaikspay"])

    validate_plan_origins(plan, context)


def test_missing_approved_modules_are_repaired_to_surface_bound_generated_capabilities():
    plan = _plan()
    plan["capability_packs"] = []
    context = _context()
    context.set("design_surface_map", {
        "surfaces": [
            {"surface_id": "project_management", "surface_kind": "module", "owner": "app"},
            {"surface_id": "task_management", "surface_kind": "module", "owner": "app"},
        ]
    })

    _repair_plan(plan, context)

    capabilities = {
        pack["capability_pack_id"]: pack
        for pack in plan["capability_packs"]
    }
    assert set(capabilities) == {"project_management", "task_management"}
    assert all(pack["capability_source"] == "generated_module" for pack in capabilities.values())
    assert all(pack["surface_id"] == pack_id for pack_id, pack in capabilities.items())

    plan["build_tasks"] = [
        {
            "task_id": f"{module_id}.contract",
            "task_type": "module_contract",
            "capability_pack_id": module_id,
            "surface_id": module_id,
            "surface_kind": "module",
            "owned_paths": [f"modules/{module_id}/module.yaml"],
        }
        for module_id in sorted(capabilities)
    ]
    validate_plan_origins(plan, context)


@pytest.mark.parametrize(
    ("surface_id", "entities", "operations", "expected_owner"),
    [
        ("user_authentication", ["UserSession"], ["login"], "platform"),
        ("subscription_management", ["Subscription"], ["update_subscription"], "MozaiksPay"),
    ],
)
def test_review_rejects_approved_modules_owned_elsewhere_before_queuing_workers(
    surface_id, entities, operations, expected_owner,
):
    plan = _plan()
    plan["capability_packs"][0].update(
        capability_pack_id=surface_id,
        surface_id=surface_id,
        capability_source="managed_capability",
        primary_entities=entities,
        operations=operations,
    )
    plan["build_tasks"] = []
    context = _context()
    design = {"surfaces": [{
        "surface_id": surface_id, "surface_kind": "module", "owner": "app",
        "primary_entities": entities, "owned_mutations": operations,
    }]}
    context.set("design_surface_map", design)
    context.set("capability_packs", [{
        "id": "mozaikspay", "capability_source": "managed_capability",
        "pack_source_path": str(ROOT / "factory_app/build_context/mozaikspay"),
    }] if expected_owner == "MozaiksPay" else [])
    if expected_owner == "MozaiksPay":
        context.set("subscription_contract", subscription_contract("reports"))
        context = _reviewed(context, ["mozaikspay"])
    original_plan = deepcopy(plan)

    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    assert result["outcome"] == "needs_revision", result
    assert expected_owner in result["error"], result
    assert surface_id in result["error"], result
    assert context.get("app_plan_ready") is False
    assert context.get("app_build_plan") is None
    assert not context.get("app_task_batch_items")
    assert detach(context.get("design_surface_map")) == design
    assert plan == original_plan


def test_invalid_provider_blocks_after_budget_and_never_queues_a_batch():
    plan = _plan()
    plan["capability_packs"][0].update(
        capability_pack_id="billing_pack",
        capability_source="managed_capability",
        surface_id="billing_pack",
    )
    plan["build_tasks"] = [
        task for task in plan["build_tasks"]
        if task.get("capability_pack_id") == "billing_pack"
    ]
    context = _context()

    for _ in range(3):
        result = review_app_build_plan(AppBuildPlan=deepcopy(plan), context_variables=context)

    assert result["outcome"] == "blocked"
    assert not context.get("app_task_batch_items")


def test_unreviewed_frozen_inventory_cannot_relabel_an_app_owned_module():
    context = _context()
    context.set("capability_packs", [{"id": "reports", "capability_source": "framework_pack"}])
    plan = _plan()
    original = deepcopy(plan)
    validate_plan_origins(plan, context)
    assert plan == original
    assert plan["capability_packs"][0]["capability_source"] == "generated_module"


def test_reviewed_operator_pack_survives_typed_plan_and_template_materialization(tmp_path, monkeypatch):
    from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
        PackIntegrityError,
        resolve_managed_capability_templates,
        verify_pack_integrity,
    )

    root = tmp_path / "reports"
    template = root / "templates/config/reports.yaml.j2"
    template.parent.mkdir(parents=True)
    template.write_text("label: {{ report_label }}\nversion: {{ app_build_plan.capability_packs[0].version }}\n",
                        encoding="utf-8")
    (root / "context.yaml").write_text(yaml.safe_dump({
        "context_id": "reports", "applies_to_workflows": ["AppGenerator"],
        "assets": [{"path": "templates/", "kind": "templates"}],
        "pack": {"id": "reports", "version": "0.1.0", "status": "active", "capability_source": "operator_pack"},
        "projections": {"context_variables": {"capability_packs": {"from": "capability_packs"}}},
    }), encoding="utf-8")
    # A registered pack outside the factory catalog is an external source: it is selected with its pins.
    metadata = verify_pack_integrity(root, "reports")
    monkeypatch.setenv("MOZAIKS_BUILD_CONTEXT_PATH", str(tmp_path))
    monkeypatch.delenv("MOZAIKS_APP_WORKSPACE_PATH", raising=False)
    context = _context()
    context.set("capability_packs", [{
        "id": "reports", "capability_source": "operator_pack", "pack_source_path": str(root),
        "version": metadata["version"], "digest": metadata["digest"],
    }])
    context = _reviewed(context, ["reports"])
    plan = _plan()
    plan["capability_packs"][0]["capability_source"] = "operator_pack"
    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)
    assert result["outcome"] == "ready", result
    from mozaiksai.core.workflow.context.frozen import detach

    cached_plan = detach(context.get("app_build_plan"))
    packs = cached_plan["capability_packs"]
    assert packs[0]["capability_source"] == "operator_pack"
    assert "pack_source_path" not in packs[0]
    assert (packs[0]["version"], packs[0]["digest"]) == (metadata["version"], metadata["digest"])
    template_context = {
        "app_build_plan": cached_plan, "report_label": "Reports", "build_timestamp": "2026-01-01T00:00:00Z",
    }
    rendered = resolve_managed_capability_templates(packs, context_variables=template_context)
    files = {item["filename"]: item["content"] for item in rendered}
    assert files["config/reports.yaml"] == "label: Reports\nversion: 0.1.0"

    moved_registry = tmp_path / "moved"
    moved_registry.mkdir()
    root.rename(moved_registry / root.name)
    monkeypatch.setenv("MOZAIKS_BUILD_CONTEXT_PATH", str(moved_registry))
    after_move = resolve_managed_capability_templates(packs, context_variables=template_context)
    assert {item["filename"]: item["content"] for item in after_move} == files

    monkeypatch.setenv("MOZAIKS_BUILD_CONTEXT_PATH", str(tmp_path))
    with pytest.raises(PackIntegrityError, match="cannot be resolved to a verified source"):
        resolve_managed_capability_templates(packs, context_variables={
            **template_context, "build_context_root": str(moved_registry),
        })


def test_unregistered_operator_pack_is_not_a_generated_module():
    plan = _plan()
    plan["capability_packs"][0]["capability_source"] = "operator_pack"
    with pytest.raises(ValueError, match="operator_pack requires an installed pack"):
        validate_plan_origins(plan, _context())


def test_generated_module_identity_cannot_be_a_product_category():
    plan = _plan()
    plan["capability_packs"][0]["capability_pack_id"] = "crud_pack"
    with pytest.raises(ValueError, match="approved surface_id"):
        validate_plan_origins(plan, _context())


def test_product_category_is_not_an_installed_framework_pack():
    plan = _plan()
    plan["capability_packs"][0].update(capability_pack_id="crud_pack", capability_source="framework_pack")
    with pytest.raises(ValueError, match="framework_pack requires an installed pack"):
        validate_plan_origins(plan, _context())


def test_module_tasks_cannot_silently_normalize_a_different_identity():
    plan = _plan()
    plan["build_tasks"][0]["capability_pack_id"] = "invented"
    # The rejection now names the fault: an id matching no declared capability.
    with pytest.raises(ValueError, match="matches 0 declared capabilities"):
        validate_plan_origins(plan, _context())


def test_invalid_plan_clears_prior_work_and_repairs_within_declared_budget():
    review = review_app_build_plan
    context = _context()
    context.set("app_plan_ready", True)
    context.set("app_task_batch_items", [{"task_id": "stale"}])
    invalid = deepcopy(_plan())
    invalid["build_tasks"][0]["depends_on"] = ["unresolved_judgment_task"]
    result = review(AppBuildPlan=invalid, context_variables=context)
    assert result["outcome"] == "needs_revision"
    assert context.get("app_plan_ready") is False
    assert not context.get("app_task_batch_items")
    assert context.get("app_plan_feedback")
    assert review(AppBuildPlan=_plan(), context_variables=context)["outcome"] == "ready"
    assert context.get("app_plan_attempts") == 2


def test_repair_budget_is_finite():
    review = review_app_build_plan
    context = _context()
    for _ in range(2):
        assert review(AppBuildPlan={}, context_variables=context)["outcome"] == "needs_revision"
    assert review(AppBuildPlan={}, context_variables=context)["outcome"] == "blocked"
    assert review(AppBuildPlan=_plan(), context_variables=context)["outcome"] == "blocked"
    assert context.get("app_plan_attempts") == 3
    assert context.get("app_plan_ready") is False

def _mislabelled_persistence_plan():
    plan = _plan()
    page = next(task for task in plan["build_tasks"] if task["task_type"] == "page_bundle")
    task = {**deepcopy(page), "task_id": "persist-approved-contract", "task_type": "persistence_contract",
            "initial_agent": "DatabaseAgent", "surface_id": "main", "surface_kind": "module",
            "capability_pack_id": None, "owned_paths": ["data/contract.json"], "depends_on": []}
    plan["build_tasks"].append(task)
    return plan, task


def test_persistence_serializer_uses_existing_structural_identity_without_approving_a_module():
    plan, task = _mislabelled_persistence_plan()
    context = _context()
    approved = deepcopy(detach(context.get("design_surface_map")))
    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)
    assert result["outcome"] == "ready", result
    accepted = next(item for item in context.get("app_task_batch_items") if item["task_id"] == task["task_id"])
    assert accepted["surface_id"] == "data_contract"
    assert accepted["surface_kind"] == "module"
    assert list(accepted["owned_paths"]) == ["data/contract.json"]
    assert accepted["capability_pack_id"] is None
    assert detach(context.get("design_surface_map")) == approved
    assert all(pack["surface_id"] != "main" for pack in context.get("app_build_plan")["capability_packs"])


@pytest.mark.parametrize("label", ["persistence_contract", "serialize-approved-data"])
def test_persistence_serializer_clears_an_unbound_capability_label(label):
    plan, task = _mislabelled_persistence_plan()
    task.update(task_id="persistence_contract", surface_id=label, capability_pack_id=label,
                execution_target="DatabaseAgent")
    dependent = next(item for item in plan["build_tasks"] if item["task_type"] == "business_services")
    dependent["depends_on"].append(task["task_id"])
    original = deepcopy(plan)
    context = _context()
    approved_contract = detach(context.get("data_contract"))
    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)
    assert result["outcome"] == "ready", result
    accepted = next(item for item in context.get("app_task_batch_items") if item["task_id"] == task["task_id"])
    assert accepted["surface_id"] == "data_contract"
    assert accepted["surface_kind"] == "module"
    assert accepted["capability_pack_id"] is None
    assert list(accepted["owned_paths"]) == ["data/contract.json"]
    cached = detach(context.get("app_build_plan"))
    assert task["task_id"] in next(item for item in cached["build_tasks"] if item["task_id"] == dependent["task_id"])["depends_on"]
    assert {pack["capability_pack_id"] for pack in cached["capability_packs"]} == {"reports"}
    assert detach(context.get("data_contract")) == approved_contract
    assert plan == original


@pytest.mark.parametrize("association", ["declared", "selected", "available"])
def test_persistence_serializer_distinguishes_declared_association_from_inventory(association):
    plan, task = _mislabelled_persistence_plan()
    task.update(surface_id="persistence_contract", capability_pack_id="persistence_contract")
    context = _context()
    capability = {**deepcopy(plan["capability_packs"][0]), "capability_pack_id": "persistence_contract"}
    if association == "declared":
        plan["capability_packs"].append(capability)
    elif association == "selected":
        context.set("capability_packs", [capability])
    else:
        context.set("available_managed_capabilities", [capability])
    original = deepcopy(plan)
    repairs = _label_persistence_tasks(plan, context)
    if association == "declared":
        assert repairs == []
        assert plan == original
    else:
        assert repairs
        assert task["capability_pack_id"] is None
        assert task["surface_id"] == "data_contract"


def test_persistence_serializer_preserves_a_reviewed_pack_association(tmp_path, monkeypatch):
    plan, task = _mislabelled_persistence_plan()
    task.update(surface_id="persistence_contract", capability_pack_id="persistence_contract")
    descriptor = registered_pack_fixture(tmp_path, monkeypatch, "persistence_contract", "operator_pack")
    context = _context()
    context.set("capability_packs", [descriptor])
    context = _reviewed(context, ["persistence_contract"])
    original = deepcopy(plan)
    assert _label_persistence_tasks(plan, context) == []
    assert plan == original


@pytest.mark.parametrize("defect", ["extra_path", "module_task", "capability", "wrong_agent", "no_approved_contract"])
def test_structural_persistence_label_cannot_authorize_other_work(defect):
    plan, task = _mislabelled_persistence_plan()
    context = _context()
    if defect == "extra_path":
        task["owned_paths"].append("modules/invented/module.yaml")
    elif defect == "module_task":
        task.update(task_type="module_contract", initial_agent="ConfigMiddlewareAgent",
                    owned_paths=["modules/main/module.yaml"])
    elif defect == "capability":
        task["capability_pack_id"] = "reports"
    elif defect == "wrong_agent":
        task["initial_agent"] = "ConfigMiddlewareAgent"
    else:
        context.set("data_contract", None)
    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)
    assert result["outcome"] == "needs_revision", result
    assert "unapproved surface 'main'" in result["error"]
    assert not context.get("app_task_batch_items")


@pytest.mark.parametrize("defect", [None, "unapproved_route", "extra_module"])
def test_custom_page_structural_label_uses_only_the_approved_route(defect):
    plan = _plan()
    plan["pages"][0]["ui_surface"] = "custom_react_page"
    task = next(task for task in plan["build_tasks"] if task["task_type"] == "page_bundle")
    task.update(surface_id="page_named_label", surface_kind="ui_only", capability_pack_id=None,
                owned_paths=["app.json", "ui/pages/custom/reports.jsx", "ui/route_manifest.json", "ui/index.js"])
    context = _context("custom_react_page")  # The approved ExperienceSpec has no rendering-surface field.
    if defect == "unapproved_route":
        plan["pages"][0]["route"] = "/invented"
        task["owned_paths"][1] = "ui/pages/custom/invented.jsx"
    elif defect == "extra_module":
        task["owned_paths"].append("modules/invented/module.yaml")
    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)
    if defect:
        assert result["outcome"] == "needs_revision", result
        assert "unapproved surface 'page_named_label'" in result["error"]
        assert not context.get("app_task_batch_items")
    else:
        assert result["outcome"] == "ready", result
        accepted = next(item for item in context.get("app_task_batch_items") if item["task_type"] == "page_bundle")
        assert accepted["surface_id"] == "page_bundle"
        assert "ui/pages/custom/reports.jsx" in accepted["owned_paths"]
        assert "ui/pages/reports.yaml" not in accepted["owned_paths"]


def test_blocked_plan_projects_feedback_to_existing_terminal_failure_message():
    plan, task = _mislabelled_persistence_plan()
    task["owned_paths"].append("modules/invented/module.yaml")
    context = _context()
    for attempt in range(1, 4):
        result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)
        assert result["outcome"] == ("needs_revision" if attempt < 3 else "blocked")
        if attempt < 3:
            assert context.get("app_build_failure_message") is None
    message = context.get("app_build_failure_message")
    assert message.startswith("The app build cannot continue: the implementation plan has unresolved contract errors.")
    assert context.get("app_plan_feedback") in message
    assert "unapproved surface 'main'" in message
    assert not context.get("app_task_batch_items")
    review_app_build_plan(AppBuildPlan=plan, context_variables=context)
    assert context.get("app_build_failure_message") == message


def test_ready_plan_clears_stale_terminal_failure_message():
    context = _context()
    context.set("app_build_failure_message", "Prior blocked plan")
    assert review_app_build_plan(AppBuildPlan=_plan(), context_variables=context)["outcome"] == "ready"
    assert context.get("app_build_failure_message") is None
