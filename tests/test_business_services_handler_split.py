"""Business services tasks are steered to the two-file handler split (live AppGenerator chat 601d2f56 at OSS 157b92ec).

The canonical contract ships every generated module with ``backend/base_handler.py``
(the implementation base class) and ``backend/handler.py`` (the exact
``module.yaml`` ``module.handler`` class, a workspace subclass). The planner
gave ``tt_business_services_crm_core`` ownership of base_handler.py but an
initial_message that read "Implement backend/handler.py, backend/service.py,
backend/repo.py, backend/policy.py for CRM Core". ServiceAgent's only worked
example showed a flat handler.py, so the worker wrote a flat ``CrmCoreModule``
plus a policy copy. Attempt 1 was rejected only for the policy copy, because
the policy rejection stopped validation before the ownership check; attempt 2
dropped the policy and was rejected for the missing base_handler.py with no
round left, which ended the run.

The fixture holds the planner's crm_core tasks from the AG2 WAL, the crm_core
data_contract surface, and the recorded failure.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools import app_plan_review
from factory_app.workflows.AppGenerator.tools.app_plan_review import review_app_build_plan
from mozaiksai.core.adapters.ag2_task_batch_runner import AG2TaskBatchRunnerResult
from mozaiksai.core.ports.orchestration import RunStatus
from mozaiksai.core.workflow import task_batches
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge, _compose_prompt_sections
from mozaiksai.core.workflow.context.frozen import detach
from tests.test_continuous_deterministic_materialization import _load_models

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"
LIVE = FIXTURES / "business_services_split_live_601d2f56.json"
REVIEW_FIXTURE = FIXTURES / "appplan_review_live_20eb8742.json"
NOTE = "Handler split (authoritative, from the approved plan):"


@pytest.fixture(autouse=True)
def _load_factory_contracts():
    _load_models()


def _live() -> dict:
    return json.loads(LIVE.read_text(encoding="utf-8"))


def _task(plan: dict, task_type: str) -> dict:
    return next(task for task in plan["build_tasks"] if task["task_type"] == task_type)


def _authored_segment(message: str) -> str:
    return message.split("Author and emit:", 1)[1].split(". Code renders", 1)[0] if "Author and emit:" in message else ""


def test_fixture_records_the_live_evidence():
    live = _live()
    services = _task(live, "business_services")
    assert "modules/crm_core/backend/base_handler.py" in services["owned_paths"]
    assert "base_handler" not in services["initial_message"]
    assert "backend/policy.py" in services["initial_message"]
    failure = live["live_task_failure"]
    assert failure["recoverable"] is False and failure["attempts"] == 2
    assert "did not emit required owned_paths: ['modules/crm_core/backend/base_handler.py']" in failure["error"]
    assert "modules/crm_core/backend/base_handler.py" not in failure["rejected_output_paths"]
    assert live["live_module_handler"] == "backend.handler:CrmCoreModule"


def test_plan_review_restates_the_split_in_the_live_business_services_task():
    live = _live()
    plan = {"build_tasks": deepcopy(live["build_tasks"])}
    context = ContextVariablesBridge({"data_contract": live["data_contract"]})

    repairs = app_plan_review._restate_handler_split(plan, context)

    message = _task(plan, "business_services")["initial_message"]
    assert message.startswith(_task(live, "business_services")["initial_message"])
    assert NOTE in message
    # The class the module contract later declared live, and the split around it.
    assert live["live_module_handler"] in message
    assert "class CrmCoreBaseModule" in message
    assert "class CrmCoreModule(CrmCoreBaseModule)" in message
    assert "from .base_handler import CrmCoreBaseModule" in message
    authored = _authored_segment(message)
    for name in ("base_handler", "handler", "service", "repo"):
        assert f"modules/crm_core/backend/{name}.py" in authored
    assert "policy.py" not in authored
    assert "do not author them: modules/crm_core/backend/policy.py, modules/crm_core/backend/schemas.py" in message
    assert "canonical create/update/delete and list/get methods to CrmCoreModule" in message
    # Both tasks name one class.
    assert live["live_module_handler"] in _task(plan, "module_contract")["initial_message"]
    assert len(repairs) == 2
    # Idempotent: a second review pass adds nothing.
    assert app_plan_review._restate_handler_split(plan, context) == []


def test_plan_review_keeps_a_handler_class_the_plan_already_names():
    live = _live()
    plan = {"build_tasks": deepcopy(live["build_tasks"])}
    contract = _task(plan, "module_contract")
    contract["initial_message"] += " module.handler is backend.handler:CrmHandler."
    before = contract["initial_message"]

    app_plan_review._restate_handler_split(plan, ContextVariablesBridge({"data_contract": live["data_contract"]}))

    message = _task(plan, "business_services")["initial_message"]
    assert "`backend.handler:CrmHandler`" in message
    assert "class CrmHandler(CrmBaseHandler)" in message
    assert contract["initial_message"] == before


def _review(owns_base_handler: bool) -> dict:
    fixture = json.loads(REVIEW_FIXTURE.read_text(encoding="utf-8"))
    variables = deepcopy(fixture["context_variables"])
    for pack in variables.get("capability_packs") or []:
        pack["pack_source_path"] = str(ROOT / pack["pack_source_path"])
    context = ContextVariablesBridge(variables)
    plan = deepcopy(fixture["AppBuildPlan"])
    services = _task(plan, "business_services")
    if owns_base_handler:
        services["owned_paths"].insert(1, "modules/customer_registry/backend/base_handler.py")
    result = review_app_build_plan(AppBuildPlan=plan, context_variables=context)
    assert result["outcome"] == "ready", result
    return detach(context.get("app_build_plan"))


def test_real_plan_review_restates_the_split_for_a_task_owning_base_handler():
    cached = _review(owns_base_handler=True)
    message = _task(cached, "business_services")["initial_message"]
    assert NOTE in message
    assert "`backend.handler:CustomerRegistryModule`" in message
    assert "class CustomerRegistryModule(CustomerRegistryBaseModule)" in message
    assert "modules/customer_registry/backend/base_handler.py" in _authored_segment(message)
    # Code renders the account-data handler of a module owning per_user collections.
    assert "modules/customer_registry/backend/account_data_handler.py" not in _authored_segment(message)
    assert "modules/customer_registry/backend/account_data_handler.py" in message.split("do not author them:", 1)[1]
    assert "backend.handler:CustomerRegistryModule" in _task(cached, "module_contract")["initial_message"]


def test_real_plan_review_leaves_a_task_without_base_handler_alone():
    cached = _review(owns_base_handler=False)
    assert NOTE not in _task(cached, "business_services")["initial_message"]


# --- One rejection reports every problem of the attempt -------------------------------------

MODULE = "task_management"
BACKEND = f"modules/{MODULE}/backend"
MANIFEST = f"modules/{MODULE}/module.yaml"


def _contract() -> dict:
    return {"version": "1", "surfaces": [{
        "surface_id": MODULE, "surface_kind": "module", "collections": [{
            "name": "tasks", "scope": "app", "tenancy": "per_user", "owner_field": "owner_id", "entity": "Task",
            "ownership": {"surface_id": MODULE, "surface_kind": "module"},
            "fields": [{"name": name, "type": "string", "required": True} for name in ("id", "title", "owner_id")],
        }],
    }]}


def _contract_output() -> dict:
    return {"module_contract": {
        "module_id": MODULE,
        "module_yaml": {
            "schema_version": "mozaiks.module.v1",
            "module": {"id": MODULE, "handler": "backend.handler:TaskManagementModule"},
            "actions": [
                *({"id": name, "handler_method": name} for name in ("create_task", "update_task", "delete_task")),
                {"id": "task_summary", "handler_method": "task_summary"},
            ],
        },
    }}


async def _run_services_attempt(monkeypatch, authored: dict[str, str]) -> dict:
    tasks = [{
        "task_id": "contract", "task_type": "module_contract", "capability_pack_id": MODULE,
        "initial_agent": "ConfigMiddlewareAgent", "initial_message": "Declare task actions.",
        "owned_paths": [MANIFEST], "depends_on": [],
    }, {
        "task_id": "services", "task_type": "business_services", "capability_pack_id": MODULE,
        "initial_agent": "ServiceAgent", "initial_message": "Implement task summary dispatch.",
        "owned_paths": [f"{BACKEND}/{name}.py" for name in ("base_handler", "handler", "service", "repo", "policy")],
        "depends_on": ["contract"],
    }]
    context = {
        "app_build_plan": {
            "capability_packs": [{
                "capability_pack_id": MODULE, "capability_source": "generated_module", "primary_entities": ["Task"],
            }],
            "pages": [], "build_tasks": tasks,
        },
        "data_contract": _contract(), "app_task_batch_items": tasks,
        "design_surface_map": {"surfaces": [{
            "surface_id": MODULE, "surface_kind": "module", "owner": "app", "primary_entities": ["Task"],
            "owned_mutations": ["create_task", "update_task", "delete_task"], "custom_reads": ["task_summary"],
        }]},
    }

    async def run(_runner, request):
        if request.task_id == "contract":
            output = _contract_output()
        else:
            output = {"code_files": [{"filename": path, "content": source} for path, source in authored.items()]}
        return AG2TaskBatchRunnerResult(status=RunStatus.COMPLETED, output=deepcopy(output))

    monkeypatch.setattr(task_batches.AG2TaskBatchRunner, "run", run)
    config = task_batches.load_task_batches_config("AppGenerator", workflows_root=ROOT / "factory_app" / "workflows")

    async def checkpoint(_updates):
        return None

    await task_batches.execute_task_batches_for_trigger(
        workflow_name="AppGenerator", trigger_agent="AppPlanAgent", batches_config=config,
        agents={"ConfigMiddlewareAgent": object(), "ServiceAgent": object()}, context_variables=context,
        chat_id="handler-split", app_id="handler-split", user_id="owner",
        fresh_agents_per_task=False, parent_channel_id="handler-split-parent", checkpoint=checkpoint,
    )
    return context["app_task_batch_results"]


_FLAT_HANDLER = (
    "class TaskManagementModule:\n"
    "    async def task_summary(self, ctx, **params):\n"
    "        from . import service\n"
    "        return await service.task_summary(ctx)\n"
)
_SERVICE = (
    "async def task_summary(ctx):\n"
    "    from . import repo\n"
    "    result = await repo.list_tasks(ctx)\n"
    "    return {'total': result['total']}\n"
)
_POLICY_COPY = "def scoped_query(context, filters=None):\n    return dict(filters or {})\n"


@pytest.mark.asyncio
async def test_rejection_reports_the_policy_copy_and_the_missing_base_handler_together(monkeypatch):
    """Live attempt 1: flat handler.py plus a policy copy, no base_handler.py."""
    results = await _run_services_attempt(monkeypatch, {
        f"{BACKEND}/handler.py": _FLAT_HANDLER,
        f"{BACKEND}/service.py": _SERVICE,
        f"{BACKEND}/policy.py": _POLICY_COPY,
    })
    rejected = results["_failed"]["services"]
    assert rejected["failure_kind"] == "output_rejected"
    error = rejected["error"]
    assert f"{BACKEND}/policy.py: policy.py is rendered from data_contract; omit model-authored policy source" in error
    assert f"did not emit required owned_paths: ['{BACKEND}/base_handler.py']" in error
    # The retained candidate is the worker's own output, for the bounded correction.
    assert rejected["recoverable"] is True


@pytest.mark.asyncio
async def test_a_lone_policy_copy_is_still_reported_exactly(monkeypatch):
    base = (
        "class TaskManagementBaseModule:\n"
        "    async def task_summary(self, ctx, **params):\n"
        "        from . import service\n"
        "        return await service.task_summary(ctx)\n"
    )
    handler = (
        "from .base_handler import TaskManagementBaseModule\n\n\n"
        "class TaskManagementModule(TaskManagementBaseModule):\n"
        '    """Workspace extensions."""\n'
    )
    results = await _run_services_attempt(monkeypatch, {
        f"{BACKEND}/base_handler.py": base,
        f"{BACKEND}/handler.py": handler,
        f"{BACKEND}/service.py": _SERVICE,
        f"{BACKEND}/policy.py": _POLICY_COPY,
    })
    assert results["_failed"]["services"]["error"] == (
        f"{BACKEND}/policy.py: policy.py is rendered from data_contract; omit model-authored policy source"
    )


# --- ServiceAgent prompt shows the split, never the flat shape -----------------------------------

def _service_agent_prompt() -> str:
    agents = yaml.safe_load((ROOT / "factory_app/workflows/AppGenerator/agents.yaml").read_text(encoding="utf-8"))
    agent = next(item for item in agents["agents"] if item["name"] == "ServiceAgent")
    return _compose_prompt_sections(agent["prompt_sections"])


def test_service_agent_worked_example_is_the_handler_split():
    prompt = _service_agent_prompt()
    example = json.loads(prompt.split("```json", 1)[1].split("```", 1)[0])
    python_files = {entry["path"]: entry for entry in example["python_files"]}
    base = python_files["modules/task_manager/backend/base_handler.py"]
    handler = python_files["modules/task_manager/backend/handler.py"]
    assert base["kind"] == handler["kind"] == "handler"
    assert "class TaskManagerBaseModule:" in base["content"]
    assert "from .base_handler import TaskManagerBaseModule" in handler["content"]
    assert "class TaskManagerModule(TaskManagerBaseModule):" in handler["content"]
    assert "async def" not in handler["content"]
    mirror = {entry["filename"]: entry["content"] for entry in example["code_files"]}
    assert mirror == {path: entry["content"] for path, entry in python_files.items()}
    # No example or rule presents a flat module.handler class.
    assert "class TaskManagerModule:" not in prompt
    assert "`backend/handler.py` — action adapter only" not in prompt
