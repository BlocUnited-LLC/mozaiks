"""Selected task closures share the downstream validator's requirements."""

from copy import deepcopy

import pytest

from factory_app.workflows.AppGenerator.tools.app_build_plan import (
    _CANONICAL_INITIAL_AGENTS,
    _construct_task_requirements,
    _ensure_context_selected_capability_packs,
    _normalize_facade_task_dependencies,
    _validate_build_tasks,
)
from factory_app.workflows.AppGenerator.tools.app_plan_review import review_app_build_plan
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.frozen import detach
from tests.test_app_plan_review import _context, _plan
from tests.test_continuous_deterministic_materialization import _load_models


@pytest.fixture(autouse=True)
def _factory_models():
    _load_models()


@pytest.mark.parametrize("kind,worker", _CANONICAL_INITIAL_AGENTS.items())
def test_selected_task_worker_is_constructed_from_validator_contract(kind, worker):
    context = ContextVariablesBridge({})
    task = {
        "task_id": "selected", "task_type": kind, "initial_agent": "WrongAgent",
        "execution_target": "AppGenerator", "capability_pack_id": "reports",
        "surface_kind": "module", "owned_paths": [],
    }
    if kind == "page_bundle":
        task["capability_pack_id"] = None
        task["surface_kind"] = "ui_only"
    plan = {"build_tasks": [task]}
    assert _construct_task_requirements(plan, context)
    _validate_build_tasks(plan["build_tasks"])
    assert task["initial_agent"] == worker
    assert task["execution_target"] == "AppGenerator"
    before = deepcopy(plan)
    assert _construct_task_requirements(plan, context) == []
    assert plan == before


@pytest.mark.parametrize(
    "kind,surface,paths,required",
    [
        ("refinement_harness", "refinement", [], {
            "config/refinement_policy.yaml", "refinement_harness/config/harness.yaml",
        }),
        ("api_surface", "external_integration", ["services/admin_config.py"], {
            "services/admin_config.py", "services/routes/admin.py",
        }),
        ("api_surface", "external_integration", ["services/routes/admin.py"], {
            "services/admin_config.py", "services/routes/admin.py",
        }),
    ],
)
def test_review_constructs_required_outputs_for_selected_optional_task(kind, surface, paths, required):
    plan, context = _plan(), _context()
    design = detach(context.get("design_surface_map"))
    design["surfaces"].append({"surface_id": "selected", "surface_kind": surface, "owner": "app"})
    context.set("design_surface_map", design)
    plan["build_tasks"].append({
        "task_id": "selected", "task_type": kind, "surface_id": "selected",
        "surface_kind": surface, "capability_pack_id": None,
        "initial_agent": "WrongAgent", "execution_target": "AppGenerator",
        "owned_paths": paths, "depends_on": [],
        "description": "Apply the approved optional contract.",
        "initial_message": "Implement the approved optional contract.",
    })
    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)
    assert result["outcome"] == "ready", result
    cached = detach(context.get("app_build_plan"))
    task = next(task for task in cached["build_tasks"] if task["task_id"] == "selected")
    assert set(task["owned_paths"]) == required
    assert task["initial_agent"] == _CANONICAL_INITIAL_AGENTS[kind]


def test_selected_task_construction_does_not_choose_optional_work():
    context = ContextVariablesBridge({})
    plan = {"build_tasks": []}
    assert _construct_task_requirements(plan, context) == []
    assert plan == {"build_tasks": []}


def test_selected_task_construction_preserves_invalid_output_for_rejection():
    context = ContextVariablesBridge({})
    plan = {"build_tasks": [{
        "task_id": "refinement", "task_type": "refinement_harness",
        "initial_agent": "RefinementHarnessAgent", "surface_kind": "refinement",
        "capability_pack_id": None, "owned_paths": ["modules/rogue/backend/handler.py"],
    }]}
    _construct_task_requirements(plan, context)
    assert "modules/rogue/backend/handler.py" in plan["build_tasks"][0]["owned_paths"]
    with pytest.raises(ValueError, match="invalid refinement harness paths"):
        _validate_build_tasks(plan["build_tasks"])


def test_registered_managed_adapter_kind_uses_registered_provider():
    context = ContextVariablesBridge({"capability_packs": [{
        "id": "provider", "capability_source": "managed_capability",
    }]})
    plan = {
        "capability_packs": [{"capability_pack_id": "provider", "capability_source": "managed_capability"}],
        "build_tasks": [{
            "task_id": "provider_client", "task_type": "api_surface",
            "capability_pack_id": "provider", "surface_kind": "module",
            "initial_agent": "ControllerAgent", "owned_paths": ["services/integrations/provider_client.py"],
        }],
    }
    _construct_task_requirements(plan, context)
    _validate_build_tasks(plan["build_tasks"], managed_capability_ids=frozenset({"provider"}))
    assert plan["build_tasks"][0]["surface_kind"] == "external_integration"


@pytest.mark.parametrize("source", ["managed_capability", "framework_pack", "operator_pack"])
def test_omitted_selected_packs_are_constructed_without_selecting_catalog_entries(source):
    selected = {"id": "selected", "capability_source": source}
    context = ContextVariablesBridge({
        "capability_packs": [selected],
        "available_managed_capabilities": [{"id": "catalog_only", "capability_source": "managed_capability"}],
    })
    packs = _ensure_context_selected_capability_packs([], context_variables=context)
    assert len(packs) == 1
    assert packs[0]["capability_pack_id"] == "selected"
    assert packs[0]["capability_source"] == source
    model = _load_models()["AppCapabilityPack"]
    # Registry descriptors also carry template-source metadata outside the
    # model contract; the constructed planning fields must remain strict.
    typed = {key: value for key, value in packs[0].items() if key in model.model_fields}
    assert model.model_validate(typed).model_dump(mode="json")["pack_type"] == "custom_domain"
    assert _ensure_context_selected_capability_packs(packs, context_variables=context) == packs


def _facade_dependency_tasks():
    return [
        {"task_id": "client", "task_type": "api_surface", "capability_pack_id": "provider"},
        {
            "task_id": "module", "task_type": "module_contract", "capability_pack_id": "facade",
            "depends_on": [], "initial_message": "Call provider_client for the provider service.",
        },
    ]


@pytest.mark.parametrize("registered", [False, True])
def test_facade_dependencies_require_registered_binding_not_prose_or_single_adapter(registered):
    context = ContextVariablesBridge({"capability_packs": [{
        "id": "provider", "capability_source": "managed_capability",
        "facades": [{"module_id": "facade", "provider_module": "provider"}] if registered else [],
    }]})
    result = _normalize_facade_task_dependencies(
        _facade_dependency_tasks(),
        capability_packs=[{"capability_pack_id": "provider", "capability_source": "managed_capability"}],
        managed_capability_ids=frozenset({"provider"}), context_variables=context,
    )
    assert result[1]["depends_on"] == (["client"] if registered else [])


def test_ambiguous_registered_adapter_dependency_remains_bounded_feedback():
    context = ContextVariablesBridge({"capability_packs": [{
        "id": "provider", "capability_source": "managed_capability",
        "facades": [{"module_id": "facade", "provider_module": "provider"}],
    }]})
    tasks = _facade_dependency_tasks()
    tasks.append({**tasks[0], "task_id": "second_client"})
    kwargs = {
        "capability_packs": [{"capability_pack_id": "provider", "capability_source": "managed_capability"}],
        "managed_capability_ids": frozenset({"provider"}), "context_variables": context,
    }
    with pytest.raises(ValueError, match="declare the required adapter task dependency explicitly"):
        _normalize_facade_task_dependencies(tasks, **kwargs)
    tasks[1]["depends_on"] = ["second_client"]
    assert _normalize_facade_task_dependencies(tasks, **kwargs)[1]["depends_on"] == ["second_client"]


def test_missing_subscription_provider_choice_remains_bounded_review_feedback():
    plan, context = _plan(), _context()
    context.set("subscription_contract", {
        "contract_required": True,
        "subscription_config_file": {"plans": [{"plan_id": "free"}, {"plan_id": "pro"}]},
    })
    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)
    assert result["outcome"] == "needs_revision"
    assert "monetization_provider is required" in result["error"]
    assert context.get("app_build_plan") is None
