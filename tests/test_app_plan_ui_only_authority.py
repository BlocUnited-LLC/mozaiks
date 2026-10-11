"""UI approval does not grant module or provider ownership to a build plan."""

from __future__ import annotations

from copy import deepcopy

import pytest

from factory_app.workflows.AppGenerator.tools.app_plan_review import (
    review_app_build_plan,
    validate_plan_origins,
)
from mozaiksai.core.session.build_context import normalize_pack_descriptor
from mozaiksai.core.workflow.context.frozen import detach
from tests.test_app_plan_review import _context, _plan
from tests.test_continuous_deterministic_materialization import _load_models


@pytest.fixture(autouse=True)
def _load_factory_contracts():
    _load_models()


def _ui_context():
    context = _context()
    context.set("design_surface_map", {"surfaces": [{
        "surface_id": "reports", "surface_kind": "ui_only", "owner": "app",
        "primary_entities": [], "source_capability_packs": ["ui_pack"],
    }]})
    context.set("data_contract", None)
    context.set("capability_packs", [])
    return context


def _page_plan():
    plan = _plan()
    plan.update(capability_packs=[], entities=[], service_scope=[])
    plan["pages"][0].update(primary_entities=[], primary_actions=[], sections_hint=[])
    page = next(task for task in plan["build_tasks"] if task["task_type"] == "page_bundle")
    page.update(
        surface_id="page_bundle", surface_kind="ui_only", capability_pack_id=None,
        depends_on=[], owned_paths=["app.json", "ui/pages/reports.yaml"],
    )
    plan["build_tasks"] = [page]
    plan["generation_order"] = [page["task_id"]]
    return plan


def _assert_not_queued(context, result):
    assert result["outcome"] == "needs_revision", result
    assert result["ok"] is False
    assert context.get("app_plan_feedback") == result["error"]
    assert context.get("app_plan_ready") is False
    assert context.get("app_build_plan") is None
    assert not context.get("app_task_batch_items")


@pytest.mark.parametrize("claim", ["capability_and_tasks", "capability", "tasks"])
def test_ui_approval_rejects_module_claims_before_repair_or_dispatch(claim):
    plan = _plan()
    if claim == "capability":
        plan["build_tasks"] = _page_plan()["build_tasks"]
    elif claim == "tasks":
        plan["capability_packs"] = []
    plan["generation_order"] = [task["task_id"] for task in plan["build_tasks"]]
    context = _ui_context()
    original = deepcopy(plan)
    design = detach(context.get("design_surface_map"))
    context.set("app_plan_ready", True)
    context.set("app_build_plan", {"stale": True})
    context.set("app_task_batch_items", [{"task_id": "stale"}])

    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    _assert_not_queued(context, result)
    assert "reports" in result["error"]
    assert "approved ui_only" in result["error"]
    assert "page_bundle" in result["error"]
    assert "matches 0 declared capabilities" not in result["error"]
    assert plan == original
    assert detach(context.get("design_surface_map")) == design


def test_module_paths_cannot_hide_under_ui_only_page_task_labels():
    plan = _page_plan()
    plan["build_tasks"][0].update(
        surface_id="reports", owned_paths=["modules/reports/backend/service.py"],
    )
    context = _ui_context()

    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    _assert_not_queued(context, result)
    assert "approved ui_only" in result["error"]
    assert "modules/" in result["error"]


@pytest.mark.parametrize("extra_path", [
    "modules/reports/backend/*.py",
    "modules/reports/backend/service.py",
    "services/adapters/evil.py",
    "data/migrations/001.json",
])
def test_ui_only_page_scope_rejects_backend_paths_before_dispatch_repairs(extra_path):
    plan = _page_plan()
    plan["build_tasks"][0]["surface_id"] = "reports"
    plan["build_tasks"][0]["owned_paths"].append(extra_path)
    original = deepcopy(plan)
    context = _ui_context()

    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    _assert_not_queued(context, result)
    assert "approved ui_only" in result["error"]
    assert extra_path in result["error"]
    assert plan == original


def test_ui_only_page_task_cannot_bind_an_unselected_pack_hint():
    plan = _page_plan()
    plan["build_tasks"][0].update(surface_id="reports", capability_pack_id="ui_pack")
    context = _ui_context()

    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    _assert_not_queued(context, result)
    assert "does not select task capability 'ui_pack'" in result["error"]


def test_ui_only_page_task_cannot_bind_selected_generated_module_pack():
    context = _ui_context()
    context.set("capability_packs", [{
        "id": "ui_pack", "surface_id": "reports", "surface_kind": "ui_only",
        "capability_source": "generated_module",
    }])
    plan = _page_plan()
    plan["build_tasks"][0].update(surface_id="reports", capability_pack_id="ui_pack")

    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    _assert_not_queued(context, result)
    assert "does not select task capability 'ui_pack'" in result["error"]


def test_ui_only_capability_cannot_claim_generated_module_source():
    plan = _page_plan()
    capability = _plan()["capability_packs"][0]
    capability["surface_kind"] = "ui_only"
    plan["capability_packs"] = [capability]

    with pytest.raises(ValueError, match="approved ui_only"):
        validate_plan_origins(plan, _ui_context())


def test_page_only_app_needs_no_capability_provider_or_backend_workers():
    plan = _page_plan()
    original = deepcopy(plan)
    context = _ui_context()

    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    assert result == {"outcome": "ready", "task_count": 1}
    cached = detach(context.get("app_build_plan"))
    assert cached["capability_packs"] == []
    assert [(page["name"], page["route"]) for page in cached["pages"]] == [("Reports", "/reports")]
    assert cached["pages"][0]["ui_surface"] == "declarative_page"
    tasks = detach(context.get("app_task_batch_items"))
    assert len(tasks) == 1
    assert tasks[0]["task_type"] == "page_bundle"
    assert tasks[0]["capability_pack_id"] is None
    assert set(tasks[0]["owned_paths"]) == {"app.json", "ui/pages/reports.yaml"}
    assert plan == original


@pytest.mark.parametrize("source", ["framework_pack", "operator_pack"])
def test_selected_registered_ui_provider_remains_valid(source):
    context = _ui_context()
    context.set("capability_packs", [{
        "id": "ui_pack", "surface_id": "reports", "surface_kind": "ui_only",
        "capability_source": source,
    }])
    plan = _page_plan()
    plan["capability_packs"] = [{
        "capability_pack_id": "ui_pack", "surface_id": "reports",
        "surface_kind": "ui_only", "capability_source": source,
    }]
    original = deepcopy(plan)

    validate_plan_origins(plan, context)

    assert plan == original


def test_ui_only_page_task_may_bind_a_selected_provider_on_the_same_surface():
    context = _ui_context()
    context.set("capability_packs", [{
        "id": "ui_pack", "surface_id": "reports", "surface_kind": "ui_only",
        "capability_source": "framework_pack",
    }])
    plan = _page_plan()
    plan["capability_packs"] = [{
        "capability_pack_id": "ui_pack", "surface_id": "reports",
        "surface_kind": "ui_only", "capability_source": "framework_pack",
    }]
    plan["build_tasks"][0].update(surface_id="reports", capability_pack_id="ui_pack")

    validate_plan_origins(plan, context)


@pytest.mark.parametrize("surface_id", ["reports", "ui_pack"])
@pytest.mark.parametrize("source", [
    "operator_pack", "managed_capability", "external_adapter",
    "config_file", "host_universal", "operator_extension",
])
@pytest.mark.parametrize("catalog_available", [False, True])
def test_source_capability_hint_does_not_register_a_provider(surface_id, source, catalog_available):
    plan = _page_plan()
    plan["capability_packs"] = [{
        **_plan()["capability_packs"][0], "capability_pack_id": "ui_pack",
        "surface_id": surface_id, "surface_kind": "ui_only",
        "capability_source": source,
    }]
    context = _ui_context()
    if catalog_available:
        context.set("available_managed_capabilities", [{"id": "ui_pack", "capability_source": source}])
    original = deepcopy(plan)

    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    _assert_not_queued(context, result)
    assert "ui_pack" in result["error"]
    assert "source_capability_packs" in result["error"]
    assert "capability_packs=[]" in result["error"]
    assert plan == original


def _real_descriptor(tmp_path, pack_id, source):
    """The descriptor shape a selected build context actually projects."""
    return normalize_pack_descriptor(
        {"pack": {"id": pack_id, "capability_source": source},
         "capabilities": [{"capability_id": f"{pack_id}.view"}]},
        context_root=tmp_path / pack_id,
    )


def _selected_ui_context(tmp_path, pack_id, source, surface_id=None):
    context = _ui_context()
    surface_id = surface_id or pack_id
    context.set("design_surface_map", {"surfaces": [{
        "surface_id": surface_id, "surface_kind": "ui_only", "owner": "app",
        "primary_entities": [], "source_capability_packs": [pack_id],
    }]})
    descriptor = _real_descriptor(tmp_path, pack_id, source)
    assert "surface_id" not in descriptor and "surface_kind" not in descriptor
    context.set("capability_packs", [descriptor])
    return context


def _cached_pack(context, pack_id):
    cached = detach(context.get("app_build_plan"))
    return next(
        pack for pack in cached["capability_packs"]
        if (pack.get("capability_pack_id") or pack.get("id")) == pack_id
    )


@pytest.mark.parametrize("source", ["operator_pack", "framework_pack"])
def test_selected_real_pack_on_a_ui_only_surface_is_admitted_when_the_plan_omits_it(tmp_path, source):
    context = _selected_ui_context(tmp_path, "files", source)

    result = review_app_build_plan(AppBuildPlan=_page_plan(), context_variables=context)

    assert result == {"outcome": "ready", "task_count": 1}, result
    assert _cached_pack(context, "files")["capability_source"] == source


@pytest.mark.parametrize("source", ["operator_pack", "framework_pack"])
def test_selected_real_pack_bound_to_its_ui_only_surface_is_admitted(tmp_path, source):
    context = _selected_ui_context(tmp_path, "files", source)
    plan = _page_plan()
    plan["capability_packs"] = [{
        **_plan()["capability_packs"][0], "capability_pack_id": "files",
        "surface_id": "files", "surface_kind": "ui_only", "capability_source": source,
        "primary_entities": [], "label": "Files", "summary": "Files",
        "implementation_mode": "declarative_module",
    }]
    plan["build_tasks"][0].update(surface_id="files", capability_pack_id="files")

    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    assert result == {"outcome": "ready", "task_count": 1}, result


@pytest.mark.parametrize("wrong_source", ["framework_pack", "managed_capability", "generated_module"])
def test_selected_ui_provider_with_a_wrong_source_label_is_repaired(tmp_path, wrong_source):
    context = _selected_ui_context(tmp_path, "files", "operator_pack")
    plan = _page_plan()
    plan["capability_packs"] = [{
        **_plan()["capability_packs"][0], "capability_pack_id": "files",
        "surface_id": "files", "surface_kind": "ui_only", "capability_source": wrong_source,
        "primary_entities": [], "label": "Files", "summary": "Files",
        "implementation_mode": "declarative_module",
    }]

    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    assert result == {"outcome": "ready", "task_count": 1}, result
    assert _cached_pack(context, "files")["capability_source"] == "operator_pack"


def test_ui_only_page_task_label_slip_is_repaired():
    plan = _page_plan()
    plan["build_tasks"][0].update(surface_id="reports", surface_kind="module")
    context = _ui_context()

    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    assert result == {"outcome": "ready", "task_count": 1}, result
    task = detach(context.get("app_task_batch_items"))[0]
    assert (task["surface_id"], task["surface_kind"]) == ("reports", "ui_only")
    assert task["capability_pack_id"] is None


def test_label_slip_with_a_backend_path_is_still_rejected():
    plan = _page_plan()
    plan["build_tasks"][0].update(surface_id="reports", surface_kind="module")
    plan["build_tasks"][0]["owned_paths"].append("modules/reports/backend/service.py")
    original = deepcopy(plan)
    context = _ui_context()

    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    _assert_not_queued(context, result)
    assert "modules/reports/backend/service.py" in result["error"]
    assert plan == original


def test_unselected_provider_rejection_names_its_actual_source():
    plan = _page_plan()
    plan["capability_packs"] = [{
        **_plan()["capability_packs"][0], "capability_pack_id": "ui_pack",
        "surface_id": "reports", "surface_kind": "module", "capability_source": "operator_pack",
    }]
    context = _ui_context()

    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)

    _assert_not_queued(context, result)
    message = next(line for line in result["error"].splitlines() if line.startswith("- ui_pack:"))
    assert "operator_pack" in message
    assert "generated_module" not in message
