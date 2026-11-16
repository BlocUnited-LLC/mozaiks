"""Real AppPlan JSON acceptance preserves deterministic pack/task ownership.

This exercises declared structured outputs and planning, without a model call,
assembly, browser, persistence, or generated-app acceptance claim.
"""

from __future__ import annotations

import json
import shutil
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.app_build_plan import app_build_plan
from mozaiksai.core.session.build_context import load_trusted_build_context
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge, _workflow_tool_invocation
from mozaiksai.core.workflow.context.authority import build_context_authority_policy
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.context.schema import load_context_variables_config
from mozaiksai.core.workflow.context.structured_output_overlay import StructuredOutputOverlay
from mozaiksai.core.workflow.generator_support.module_action_inventory import (
    pack_owned_output_paths,
)
from mozaiksai.core.workflow.outputs import structured
from mozaiksai.core.workflow.outputs.runtime_validation import validate_agent_structured_output
from tests.factory_context import factory_context

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / "factory_app/workflows/AppGenerator"
PACK = ROOT / "factory_app/build_context/content"
FIXTURES = "modules/content/contracts/fixtures.yaml"
PAGE = "ui/pages/entries.yaml"


@pytest.fixture(scope="module")
def registry():
    """Use the runtime loader with real YAML and isolated caches, restoring globals."""
    config = yaml.safe_load((WORKFLOW / "structured_outputs.yaml").read_text(encoding="utf-8"))
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(structured, "workflow_manager", SimpleNamespace(
            get_config=lambda workflow: {"structured_outputs": config} if workflow == "AppGenerator" else None,
        ))
        for name in ("_workflow_models", "_workflow_registries", "_workflow_structured_agents"):
            patch.setattr(structured, name, {})
        models, registry = structured.load_workflow_structured_outputs("AppGenerator")
        assert registry["AppPlanAgent"] is models["AppBuildPlanOutput"]
        assert all(model.model_config.get("extra") == "forbid" for model in models.values())
        yield registry


@pytest.fixture
def context(tmp_path, monkeypatch):
    root = tmp_path / "build_context"
    shutil.copytree(PACK, root / "content", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    monkeypatch.setenv("MOZAIKS_BUILD_CONTEXT_PATH", str(root))
    config = load_context_variables_config(yaml.safe_load(
        (WORKFLOW / "context_variables.yaml").read_text(encoding="utf-8"),
    ))
    policy = build_context_authority_policy(workflow_name="AppGenerator", definitions=config.definitions)
    projected = load_trusted_build_context(policy, build_context_root=root)
    assert [pack["id"] for pack in projected["capability_packs"]] == ["content"]
    bridge = ContextVariablesBridge(factory_context(projected), authority_policy=policy)
    bridge._bind_run(("AppGenerator", "pack-planning-app", "882cbad6-4cf3-45fa-9aca-70a1c263f533"), policy)
    return bridge


def _task(task_id, task_type, initial_agent, owned_paths, *, depends_on=()):
    return {
        "task_id": task_id,
        "task_type": task_type,
        "capability_pack_id": None if task_type == "page_bundle" else "content",
        "surface_id": "entries_page" if task_type == "page_bundle" else "content",
        "surface_kind": "ui_only" if task_type == "page_bundle" else "module",
        "execution_target": "AppGenerator",
        "initial_agent": initial_agent,
        "description": f"Implement {task_id} within its declared files.",
        "initial_message": "Emit only the declared canonical files.",
        "owned_paths": owned_paths,
        "depends_on": list(depends_on),
        "acceptance_criteria": ["Declared references and ownership close."],
    }


def _candidate():
    return {"AppBuildPlan": {
        "agent_message": "Build the approved content app using its admitted reusable backend.",
        "app_kind": "editorial_portfolio",
        "pages": [{
            "name": "Entries", "route": "/entries", "purpose": "Browse published work.",
            "ui_surface": "declarative_page", "page_type_hint": "record_list",
            "ui_layout": "full-width", "primary_entities": ["ContentEntry"],
            "primary_actions": ["list_entries"], "sections_hint": [{
                "primitive": "DataTable",
                "data_source": {"module_id": "content", "action_id": "list_entries"},
                "config_hint": json.dumps({"columns": ["title", "summary", "status"], "data_key": "entries"}),
                "section_id_hint": "entries-table", "title_hint": "Published entries",
            }],
        }],
        "entities": [{"name": "ContentEntry", "operations": ["read"], "notes": None}],
        "roles": ["content_editor"], "auth_strategy": "role-based",
        "service_scope": ["content"], "frontend_scope": ["published entries"],
        "theme_preferences": None, "brand_intent": None,
        "capability_packs": [{
            "capability_pack_id": "content", "surface_id": "content", "surface_kind": "module",
            "pack_type": "custom_domain", "label": "Content", "summary": "Editorial entries.",
            "implementation_mode": "declarative_module", "capability_source": "generated_module",
        }],
        "readiness_profile": "none", "revenue_model": "free",
        "external_integrations": [], "agent_backend_required": False,
        "build_tasks": [
            _task("content.contract", "module_contract", "ConfigMiddlewareAgent", [
                "modules/content/module.yaml", "modules/content/contracts/events.yaml",
            ]),
            _task("content.models", "data_models", "ModelAgent", ["modules/content/backend/schemas.py"],
                  depends_on=["content.contract"]),
            _task("content.services", "business_services", "ServiceAgent", [
                "modules/content/backend/handler.py", "modules/content/backend/service.py",
                "modules/content/backend/repo.py", "modules/content/backend/policy.py",
            ], depends_on=["content.models"]),
            _task("content.fixtures", "module_contract", "ConfigMiddlewareAgent", [FIXTURES],
                  depends_on=["content.contract"]),
            _task("entries.page", "page_bundle", "AppSchemaAgent", [PAGE],
                  depends_on=["content.services", "content.fixtures"]),
        ],
        "pending_schema_migration": None,
        "generation_order": ["content.contract", "content.models", "content.services", "content.fixtures", "entries.page"],
    }}


def _validate(candidate, registry):
    return validate_agent_structured_output(
        agent_name="AppPlanAgent", reply=json.dumps(candidate), structured_registry=registry,
    )


def test_exact_app_plan_json_releases_pack_backend_and_preserves_app_work(registry, context):
    candidate = _candidate()
    original = deepcopy(candidate)
    validation = _validate(candidate, registry)
    assert validation is not None and validation.validation_passed, validation
    assert validation.structured_data is not None
    overlay = StructuredOutputOverlay(context, validation.structured_data)

    with _workflow_tool_invocation(context):
        app_build_plan(AppBuildPlan=overlay.get("structured_output")["AppBuildPlan"], context_variables=overlay)

    assert context.get("app_plan_ready") is True
    stored = detach(context.get("app_build_plan"))
    tasks = {task["task_id"]: task for task in stored["build_tasks"]}
    assert set(tasks) == {"content.fixtures", "entries.page"}
    assert tasks["content.fixtures"]["owned_paths"] == [FIXTURES]
    assert tasks["content.fixtures"]["depends_on"] == []
    assert tasks["entries.page"]["owned_paths"] == [PAGE]
    assert tasks["entries.page"]["depends_on"] == ["content.fixtures"]
    assert stored["generation_order"] == ["content.fixtures", "entries.page"]
    assert {task["task_id"] for task in detach(context.get("app_task_batch_items"))} == set(tasks)
    owned = pack_owned_output_paths(context)
    assert "modules/content/backend/service.py" in owned
    assert not owned.intersection(path for task in tasks.values() for path in task["owned_paths"])
    [selected] = stored["capability_packs"]
    [admitted] = detach(context.get("capability_packs"))
    for key in ("pack_source_path", "version"):
        assert selected[key] == admitted[key]
    assert candidate == original


def test_app_authored_module_keeps_optional_event_ownership_beside_a_template_pack(registry, context):
    candidate = _candidate()
    plan = candidate["AppBuildPlan"]
    plan["capability_packs"].append({
        **plan["capability_packs"][0], "capability_pack_id": "notes", "surface_id": "notes",
        "label": "Notes", "summary": "App-owned annotation behavior.",
    })
    plan["build_tasks"].append({
        **_task("notes.contract", "module_contract", "ConfigMiddlewareAgent", ["modules/notes/module.yaml"]),
        "capability_pack_id": "notes", "surface_id": "notes",
    })
    plan["generation_order"].append("notes.contract")
    validation = _validate(candidate, registry)
    assert validation is not None and validation.validation_passed, validation
    overlay = StructuredOutputOverlay(context, validation.structured_data)

    with _workflow_tool_invocation(context):
        app_build_plan(AppBuildPlan=overlay.get("structured_output")["AppBuildPlan"], context_variables=overlay)

    tasks = {task["task_id"]: task for task in detach(context.get("app_build_plan"))["build_tasks"]}
    assert tasks["notes.contract"]["owned_paths"] == [
        "modules/notes/module.yaml", "modules/notes/contracts/events.yaml",
    ]
    assert tasks["content.fixtures"]["owned_paths"] == [FIXTURES]
    assert not pack_owned_output_paths(context).intersection(
        path for task in tasks.values() for path in task["owned_paths"]
    )


@pytest.mark.parametrize(("path", "value", "error"), [
    (("AppBuildPlan", "capability_packs", 0, "capability_source"), "unregistered_origin", "enum"),
    (("AppBuildPlan", "capability_packs", 0, "pack_type"), "portfolio_pack", "enum"),
    (("AppBuildPlan", "capability_packs", 0, "implementation_mode"), "arbitrary_code", "enum"),
    (("AppBuildPlan", "pages", 0, "ui_surface"), "inline_page_code", "enum"),
    (("AppBuildPlan", "build_tasks", 0, "task_type"), "generate_everything", "enum"),
    (("AppBuildPlan", "unexpected_runtime_owner"), "model", "extra_forbidden"),
    (("AppBuildPlan", "build_tasks", 0, "owner"), "model", "extra_forbidden"),
])
def test_invalid_pack_planning_json_rejects_before_any_plan_is_saved(registry, context, path, value, error):
    candidate = _candidate()
    target = candidate
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    before = {key: detach(context.get(key)) for key in ("app_plan_ready", "app_build_plan", "app_task_batch_items")}

    validation = _validate(candidate, registry)

    assert validation is not None and validation.validation_passed is False
    assert validation.structured_data is None
    assert validation.raw_data == candidate
    assert error in validation.error
    assert ".".join(str(key) for key in path) in validation.error
    assert {key: detach(context.get(key)) for key in before} == before
