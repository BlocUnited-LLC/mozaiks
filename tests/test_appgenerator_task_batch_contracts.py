from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools import assemble_app_tasks as assemble_module
from mozaiksai.core.workflow.task_batches import (
    execute_task_batches_for_trigger,
    load_task_batches_config,
)

WORKSPACE = Path(__file__).resolve().parents[1]
WORKFLOWS_ROOT = WORKSPACE / "factory_app" / "workflows"


def _load_module(relative_path: str, module_name: str):
    file_path = WORKSPACE / relative_path
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load module spec for {file_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read_yaml(relative_path: str) -> dict:
    return yaml.safe_load((WORKSPACE / relative_path).read_text(encoding="utf-8")) or {}


class _Context:
    def __init__(self) -> None:
        self.data: dict[str, object] = {}

    def set(self, key: str, value: object) -> None:
        self.data[key] = value

    def get(self, key: str, default=None):
        return self.data.get(key, default)


def _build_tasks() -> list[dict]:
    return [
        {
            "task_id": "task_service_foundation",
            "task_type": "service_foundation",
            "capability_pack_id": None,
            "surface_id": "app_shell",
            "surface_kind": "module",
            "execution_target": "AppGenerator",
            "initial_agent": "ConfigMiddlewareAgent",
            "description": "Generate backend glue.",
            "initial_message": "Generate backend glue only.",
            "owned_paths": ["services/config.py"],
            "depends_on": [],
            "acceptance_criteria": ["Environment-driven config only"],
        },
        {
            "task_id": "task_notifications_pages",
            "task_type": "page_bundle",
            "capability_pack_id": "notifications_center",
            "surface_id": "notifications_center",
            "surface_kind": "ui_only",
            "execution_target": "AppGenerator",
            "initial_agent": "AppSchemaAgent",
            "description": "Compile persistent notifications pages.",
            "initial_message": "Compile notifications page schemas only.",
            "owned_paths": ["ui/pages/notifications.yaml"],
            "depends_on": ["task_service_foundation"],
            "acceptance_criteria": ["Notifications route exists"],
        },
    ]


def _base_plan() -> dict:
    return {
        "agent_message": "Planned the app.",
        "app_kind": "operations-studio",
        "pages": [
            {
                "name": "Notifications",
                "route": "/notifications",
                "purpose": "Review build and runtime alerts",
            }
        ],
        "entities": [],
        "roles": ["operator"],
        "auth_strategy": "role-based",
        "service_scope": ["notifications feed"],
        "frontend_scope": ["notifications route"],
        "theme_preferences": "dark restrained",
        "brand_intent": None,
        "capability_packs": [
            {
                "capability_pack_id": "notifications_center",
                "surface_id": "notifications_center",
                "surface_kind": "ui_only",
                "pack_type": "notifications_pack",
                "label": "Notifications",
                "summary": "Review build and runtime notifications.",
                "implementation_mode": "hybrid",
            }
        ],
        "external_integrations": [],
        "agent_backend_required": False,
        "build_tasks": _build_tasks(),
        "generation_order": ["backend-foundation", "app-schema-bundle"],
    }


def test_app_build_plan_persists_task_batch_items_and_hydrates_current_task() -> None:
    module = _load_module(
        "factory_app/workflows/AppGenerator/tools/app_build_plan.py",
        "tests.app_build_plan_task_batch_contracts",
    )
    context = _Context()

    result = module.app_build_plan(
        AppBuildPlan=_base_plan(),
        context_variables=context,
    )

    task_batch_items = context.data["app_task_batch_items"]
    assert isinstance(task_batch_items, list)
    assert len(task_batch_items) == 2
    assert context.data["app_task_batch_status"] == "planned"
    assert "Task batch items: 2" in result

    page_task = next(
        item for item in task_batch_items if item["task_id"] == "task_notifications_pages"
    )
    assert page_task["initial_agent"] == "AppSchemaAgent"
    assert page_task["task_run_mode"] is True
    assert page_task["current_build_task"]["task_type"] == "page_bundle"
    assert page_task["current_build_task"]["owned_paths"] == [
        "ui/pages/notifications.yaml"
    ]


def test_app_build_plan_caches_hydrated_task_batch_items() -> None:
    module = _load_module(
        "factory_app/workflows/AppGenerator/tools/app_build_plan.py",
        "tests.app_build_plan_child_workflow_contracts",
    )
    context = _Context()

    result = module.app_build_plan(
        AppBuildPlan=_base_plan(),
        context_variables=context,
    )

    cached = context.data["app_task_batch_items"]
    page_item = next(item for item in cached if item["task_id"] == "task_notifications_pages")
    assert page_item["task_run_mode"] is True
    assert page_item["current_build_task_id"] == "task_notifications_pages"
    assert page_item["current_build_task_type"] == "page_bundle"
    assert page_item["current_build_task"]["owned_paths"] == [
        "ui/pages/notifications.yaml"
    ]
    assert "Task batch items: 2" in result


def test_app_build_plan_accepts_provider_output_envelope() -> None:
    module = _load_module(
        "factory_app/workflows/AppGenerator/tools/app_build_plan.py",
        "tests.app_build_plan_task_batch_provider_envelope",
    )
    context = _Context()

    module.app_build_plan(
        AppBuildPlan={"AppBuildPlan": _base_plan()},
        context_variables=context,
    )

    assert context.data["app_build_plan"]["app_kind"] == "operations-studio"
    assert context.data["app_task_batch_status"] == "planned"


def test_appgenerator_smoke_context_build_plan_matches_task_batch_contract() -> None:
    module = _load_module(
        "factory_app/workflows/AppGenerator/tools/app_build_plan.py",
        "tests.app_build_plan_smoke_context_task_batch_contract",
    )
    smoke_context = json.loads(
        (
            WORKSPACE
            / "factory_app"
            / "workflows"
            / "AppGenerator"
            / "smoke_context_ui.json"
        ).read_text(encoding="utf-8")
    )
    context = _Context()

    module.app_build_plan(
        AppBuildPlan=smoke_context["app_build_plan"],
        context_variables=context,
    )

    task_batch_items = context.get("app_task_batch_items")
    assert isinstance(task_batch_items, list)
    assert len(task_batch_items) == 5
    assert context.get("app_task_batch_status") == "planned"
    assert all(item["task_run_mode"] is True for item in task_batch_items)
    assert all(item.get("initial_agent") for item in task_batch_items)
    assert all(item.get("owned_paths") for item in task_batch_items)
    assert {item["task_type"] for item in task_batch_items} == {
        "business_services",
        "data_models",
        "module_contract",
        "page_bundle",
        "persistence_contract",
    }
    module_task = next(item for item in task_batch_items if item["task_type"] == "module_contract")
    assert module_task["capability_pack_id"] == "tickets"
    assert all("/backend/" not in path for path in module_task["owned_paths"])


def test_app_build_plan_rejects_page_bundle_task_with_wrong_initial_agent() -> None:
    module = _load_module(
        "factory_app/workflows/AppGenerator/tools/app_build_plan.py",
        "tests.app_build_plan_task_batch_page_bundle_owner",
    )
    plan = _base_plan()
    plan["build_tasks"][1]["initial_agent"] = "ServiceAgent"

    with pytest.raises(ValueError, match="must start at AppSchemaAgent"):
        module.app_build_plan(
            AppBuildPlan=plan,
            context_variables=_Context(),
        )


def test_app_build_plan_rejects_module_contract_task_with_backend_python() -> None:
    module = _load_module(
        "factory_app/workflows/AppGenerator/tools/app_build_plan.py",
        "tests.app_build_plan_task_batch_module_contract_owner",
    )
    plan = _base_plan()
    plan["build_tasks"].append(
        {
            "task_id": "task_profiles_module",
            "task_type": "module_contract",
            "capability_pack_id": "profiles",
            "surface_id": "profiles",
            "surface_kind": "module",
            "execution_target": "AppGenerator",
            "initial_agent": "ConfigMiddlewareAgent",
            "description": "Bad mixed module task.",
            "initial_message": "Generate mixed files.",
            "owned_paths": [
                "modules/profiles/module.yaml",
                "modules/profiles/backend/handler.py",
            ],
            "depends_on": [],
            "acceptance_criteria": ["No mixed ownership"],
        }
    )

    with pytest.raises(ValueError, match="mixes module contract YAML with backend Python"):
        module.app_build_plan(
            AppBuildPlan=plan,
            context_variables=_Context(),
        )


def test_app_build_plan_rejects_refinement_harness_without_runtime_yaml() -> None:
    module = _load_module(
        "factory_app/workflows/AppGenerator/tools/app_build_plan.py",
        "tests.app_build_plan_task_batch_control_plane_runtime",
    )
    plan = _base_plan()
    plan["build_tasks"].append(
        {
            "task_id": "task_refinement_harness",
            "task_type": "refinement_harness",
            "capability_pack_id": None,
            "surface_id": "app_refinement_harness",
            "surface_kind": "refinement",
            "execution_target": "AppGenerator",
            "initial_agent": "RefinementHarnessAgent",
            "description": "Generate app-local refinement harness files.",
            "initial_message": "Generate only the refinement harness.",
            "owned_paths": [
                "refinement_harness/config/harness.yaml",
            ],
            "depends_on": [],
            "acceptance_criteria": ["Harness routes stay declarative"],
        }
    )

    with pytest.raises(ValueError, match="missing required refinement harness paths"):
        module.app_build_plan(
            AppBuildPlan=plan,
            context_variables=_Context(),
        )


def test_appgenerator_context_exposes_app_task_batch_state() -> None:
    context_vars = _read_yaml("factory_app/workflows/AppGenerator/context_variables.yaml")
    definitions = context_vars["definitions"]
    agents = context_vars["agents"]

    assert "app_task_batch_items" in definitions
    assert "app_task_batch_status" in definitions
    assert "app_task_batch_results" in definitions
    assert "app_task_batch_results_summary" in definitions
    assert "code_files" in definitions
    assert "deleted_files" in definitions
    assert "integration_needs" in definitions
    assert "integration_readiness_status" in definitions
    assert definitions["app_task_batch_items"]["source"]["type"] == "computed"
    assert "app_task_batch_items" in agents["AssemblyAgent"]["variables"]
    assert "code_files" in agents["AssemblyAgent"]["variables"]
    assert "deleted_files" in agents["AssemblyAgent"]["variables"]
    assert "app_task_batch_results_summary" in agents["IntegrationReadinessAgent"]["variables"]
    assert "integration_needs" in agents["IntegrationReadinessAgent"]["variables"]


def test_appgenerator_task_batch_contract_uses_normalized_items() -> None:
    task_batches = _read_yaml(
        "factory_app/workflows/AppGenerator/extended_orchestration/task_batches.yaml"
    )

    batch = task_batches["batches"][0]
    assert batch["trigger_agent"] == "AppPlanAgent"
    assert batch["source"]["kind"] == "context_variable"
    assert batch["source"]["path"] == "app_task_batch_items"
    assert batch["result"]["context_key"] == "app_task_batch_results"
    assert batch["result"]["require_owned_paths"] is True


def test_appgenerator_handoffs_start_from_agents_not_pseudo_user() -> None:
    handoffs = _read_yaml("factory_app/workflows/AppGenerator/transition_graph.yaml")
    rules = handoffs["transition_rules"]

    assert all(rule["source_agent"] != "user" for rule in rules)
    interview_rules = [rule for rule in rules if rule["source_agent"] == "InterviewAgent"]
    assert [rule["target_agent"] for rule in interview_rules[:2]] == [
        "AppPlanAgent",
        "user",
    ]
    assert interview_rules[0]["condition_type"] == "context_equals"
    assert interview_rules[0]["condition_key"] == "interview_complete"
    assert interview_rules[0]["condition_value"] is True


@pytest.mark.asyncio
async def test_appgenerator_hydrates_seeded_build_plan_before_chat() -> None:
    module = _load_module(
        "factory_app/workflows/AppGenerator/tools/hydrate_app_build_plan_context.py",
        "tests.hydrate_app_build_plan_context",
    )
    context = _Context()
    context.set("app_build_plan", _base_plan())
    context.set("app_plan_ready", True)

    result = await module.hydrate_app_build_plan_context(context_variables=context)

    assert result["status"] == "hydrated"
    assert context.get("app_task_batch_status") == "planned"
    assert len(context.get("app_task_batch_items")) == 2


@pytest.mark.asyncio
async def test_appgenerator_task_batch_dogfood_path_executes_and_assembles() -> None:
    app_build_plan_module = _load_module(
        "factory_app/workflows/AppGenerator/tools/app_build_plan.py",
        "tests.app_build_plan_task_batch_dogfood",
    )
    context = _Context()
    context.set("app_id", "dogfood_app")
    context.set("app_ui_quality_status", "passed")

    app_build_plan_module.app_build_plan(
        AppBuildPlan=_base_plan(),
        context_variables=context,
    )

    config = load_task_batches_config("AppGenerator", workflows_root=WORKFLOWS_ROOT)
    assert config is not None
    assert context.get("app_task_batch_status") == "planned"
    assert len(context.get("app_task_batch_items")) == 2

    seen_tasks: list[dict] = []

    from mozaiksai.core.adapters.ag2_task_batch_runner import AG2TaskBatchRunnerResult
    from mozaiksai.core.ports.orchestration import RunStatus

    async def _fake_runner_run(request):
        task_ctx = request.context_variables
        task = task_ctx.get("current_build_task") or {}
        seen_tasks.append(
            {
                "task_id": task_ctx.get("current_build_task_id"),
                "task_run_mode": task_ctx.get("task_run_mode"),
                "task_type": task_ctx.get("current_build_task_type"),
                "message": request.prompt,
            }
        )
        return AG2TaskBatchRunnerResult(
            status=RunStatus.COMPLETED,
            output={
                "agent_message": f"completed {task.get('task_id', '')}",
                "code_files": [
                    {
                        "filename": path,
                        "content": f"# generated by {task.get('task_id', '')} for {path}",
                    }
                    for path in (task.get("owned_paths") or [])
                ],
            },
        )

    fake_agent = AsyncMock()

    with patch("mozaiksai.core.workflow.task_batches.AG2TaskBatchRunner") as mock_runner_cls:
        mock_runner_cls.return_value.run = _fake_runner_run

        await execute_task_batches_for_trigger(
            workflow_name="AppGenerator",
            trigger_agent="AppPlanAgent",
            batches_config=config,
            agents={
                "ConfigMiddlewareAgent": fake_agent,
                "AppSchemaAgent": fake_agent,
            },
            context_variables=context.data,
            chat_id="chat-dogfood",
            app_id="dogfood_app",
            user_id="user-dogfood",
            fresh_agents_per_task=False,
        )

    assert context.get("app_task_batch_status") == "completed"
    results = context.get("app_task_batch_results")
    assert isinstance(results, dict)
    assert results["_meta"]["batch_id"] == "app_build_tasks"
    assert results["_meta"]["completed_tasks"] == [
        "task_service_foundation",
        "task_notifications_pages",
    ]
    assert {item["task_id"] for item in seen_tasks} == {
        "task_service_foundation",
        "task_notifications_pages",
    }
    assert all(item["task_run_mode"] is True for item in seen_tasks)

    assembled = await assemble_module.assemble_app_tasks(context_variables=context)

    generated_files = context.get("generated_files")
    assert isinstance(generated_files, dict)
    assert set(generated_files) == {
        "config/integrations.yaml",
        "config/targets.json",
        "services/config.py",
        "ui/pages/notifications.yaml",
    }
    assert {item["filename"] for item in assembled["code_files"]} == set(generated_files)
    assert context.get("assembled_source") == "schema_and_task_batch_outputs"


@pytest.mark.asyncio
async def test_assemble_app_tasks_applies_accumulated_repair_overlay_and_deletions() -> None:
    context = _Context()
    context.set("app_id", "repair_app")
    context.set(
        "app_task_batch_results",
        {
            "task_service": {
                "code_files": [
                    {
                        "filename": "modules/billing/backend/service.py",
                        "content": "class BillingService:\n    pass\n",
                    },
                    {
                        "filename": "modules/billing/backend/token_wallet_ledger.py",
                        "content": "class TokenWalletLedger:\n    pass\n",
                    },
                ],
            },
        },
    )
    context.set(
        "code_files",
        [
            {
                "filename": "modules/billing/backend/service.py",
                "content": "class BillingService:\n    async def list_products(self, ctx, **params):\n        return []\n",
            },
        ],
    )
    context.set("deleted_files", ["modules/billing/backend/token_wallet_ledger.py"])

    assembled = await assemble_module.assemble_app_tasks(context_variables=context)

    file_map = {item["filename"]: item["content"] for item in assembled["code_files"]}
    assert file_map["modules/billing/backend/service.py"].startswith("class BillingService")
    assert "async def list_products" in file_map["modules/billing/backend/service.py"]
    assert "modules/billing/backend/token_wallet_ledger.py" not in file_map
    assert context.get("generated_files") == file_map


def test_assembly_aligns_module_handler_method_to_generated_action_method() -> None:
    code_files = assemble_module._apply_module_handler_method_alignment(
        [
            {
                "filename": "modules/tickets/module.yaml",
                "content": """
schema_version: mozaiks.module.v1
module:
  id: tickets
  handler: backend.handler:TicketsModule
actions:
- id: update_status
  handler_method: update_ticket_status
""",
            },
            {
                "filename": "modules/tickets/backend/handler.py",
                "content": """
class TicketsModule:
    async def update_status(self, ctx, **params):
        return {"ok": True}
""",
            },
        ]
    )

    module_yaml = next(
        item["content"]
        for item in code_files
        if item["filename"] == "modules/tickets/module.yaml"
    )
    parsed = yaml.safe_load(module_yaml)

    assert parsed["actions"][0]["handler_method"] == "update_status"


# ---------------------------------------------------------------------------
# module_interface.v1 retirement — agent_backend_integration is not a build task
# ---------------------------------------------------------------------------


_RESEARCH_TOUCHPOINT_INPUT = {
    "page_name": "Research",
    "section_id_hint": "research_actions",
    "action_id": "launch_document_analysis",
    "label": "Analyze documents",
    "workflow_id": "DocumentAnalysis",
    "placement": "primary_button",
    "context_variables": [
        {"key": "source", "value": "research_page", "value_type": "string"}
    ],
    "purpose": "Let researchers launch the analysis workflow from the page.",
}

_RESEARCH_TOUCHPOINT_NORMALIZED = {
    "page_name": "Research",
    "action_id": "launch_document_analysis",
    "label": "Analyze documents",
    "workflow_id": "DocumentAnalysis",
    "purpose": "Let researchers launch the analysis workflow from the page.",
    "placement": "primary_button",
    "section_id_hint": "research_actions",
    "context_variables": {"source": "research_page"},
}


def _workflow_module_plan() -> dict:
    """A workflow→module app plan: one module the AgentGenerator workflow calls,
    one persistent Research page with a user-launch workflow touchpoint, the
    normal generated workflow metadata carried in context — and no
    agent_backend_integration build task, because none is admissible."""
    plan = _base_plan()
    plan["pages"].append(
        {
            "name": "Research",
            "route": "/research",
            "purpose": "Launch and review document analysis",
        }
    )
    plan["workflow_touchpoints"] = [dict(_RESEARCH_TOUCHPOINT_INPUT)]
    plan["build_tasks"][1]["owned_paths"] = [
        "ui/pages/notifications.yaml",
        "ui/pages/research.yaml",
    ]
    plan["capability_packs"].append(
        {
            "capability_pack_id": "documents",
            "surface_id": "documents",
            "surface_kind": "module",
            "pack_type": "crud_pack",
            "label": "Documents",
            "summary": "Documents the analysis workflow reads and writes.",
            "implementation_mode": "hybrid",
        }
    )
    plan["build_tasks"].append(
        {
            "task_id": "task_documents_module",
            "task_type": "module_contract",
            "capability_pack_id": "documents",
            "surface_id": "documents",
            "surface_kind": "module",
            "execution_target": "AppGenerator",
            "initial_agent": "ConfigMiddlewareAgent",
            "description": "Documents module the analysis workflow integrates with.",
            "initial_message": (
                "Generate the documents module contract. The DocumentAnalysis "
                "workflow calls documents.get_content and commits through "
                "documents.store_analysis."
            ),
            "owned_paths": [
                "modules/documents/module.yaml",
                "modules/documents/contracts/events.yaml",
                "modules/documents/contracts/reactions.yaml",
            ],
            "depends_on": [],
            "acceptance_criteria": ["Documents module contract exists"],
        }
    )
    return plan


_WORKFLOW_METADATA_CONTEXT = {
    "generated_workflow_name": "DocumentAnalysis",
    "generated_workflow_capability_id": "document-analysis-workflow",
    "generated_workflow_trigger_events": ["domain.documents.created"],
    "workflow_integration_metadata": {
        "contract_version": "1.0",
        "bundle_name": "DocumentAnalysis",
        "primary_workflow": "DocumentAnalysis",
        "workflows": [
            {
                "workflow_name": "DocumentAnalysis",
                "capability_id": "document-analysis-workflow",
                "workflow_startup_mode": "BackendOnly",
                "trigger_events": ["domain.documents.created"],
            }
        ],
    },
}


@pytest.mark.asyncio
async def test_workflow_module_plan_executes_without_integration_build_task() -> None:
    """The user-launch workflow→module scenario after module_interface.v1
    retirement: the plan's workflow_touchpoint survives normalization into
    the persisted context and the page worker's context, the page bundle
    materializes the launch affordance from those explicit touchpoint facts
    (never from ambient generated_workflow_* metadata), the assembled page
    action validates under the real runtime page contract, no
    agent_backend_integration task exists, no module_interface.yaml exists,
    and the deferred workflow metadata context survives untouched."""
    app_build_plan_module = _load_module(
        "factory_app/workflows/AppGenerator/tools/app_build_plan.py",
        "tests.app_build_plan_workflow_module_retirement",
    )
    context = _Context()
    context.set("app_id", "workflow_module_app")
    context.set("app_ui_quality_status", "passed")
    for key, value in _WORKFLOW_METADATA_CONTEXT.items():
        context.set(key, json.loads(json.dumps(value)))

    app_build_plan_module.app_build_plan(
        AppBuildPlan=_workflow_module_plan(),
        context_variables=context,
    )

    normalized_plan = context.get("app_build_plan")
    assert isinstance(normalized_plan, dict)
    assert normalized_plan["workflow_touchpoints"] == [
        _RESEARCH_TOUCHPOINT_NORMALIZED
    ]

    task_batch_items = context.get("app_task_batch_items")
    assert isinstance(task_batch_items, list)
    assert len(task_batch_items) == 3
    assert all(
        item["current_build_task"]["task_type"] != "agent_backend_integration"
        for item in task_batch_items
    )
    for item in task_batch_items:
        owned_paths = item["current_build_task"]["owned_paths"]
        assert isinstance(owned_paths, list) and owned_paths
        assert all("module_interface" not in str(path) for path in owned_paths)

    config = load_task_batches_config("AppGenerator", workflows_root=WORKFLOWS_ROOT)
    assert config is not None

    from mozaiksai.core.adapters.ag2_task_batch_runner import AG2TaskBatchRunnerResult
    from mozaiksai.core.ports.orchestration import RunStatus

    worker_touchpoints_by_task: dict[str, object] = {}

    async def _fake_runner_run(request):
        task = request.context_variables.get("current_build_task") or {}
        task_id = str(task.get("task_id") or "")
        worker_plan = request.context_variables.get("app_build_plan") or {}
        worker_touchpoints_by_task[task_id] = json.loads(
            json.dumps(worker_plan.get("workflow_touchpoints"))
        )
        return AG2TaskBatchRunnerResult(
            status=RunStatus.COMPLETED,
            output={
                "agent_message": f"completed {task_id}",
                "code_files": [
                    {"filename": path, "content": f"# generated for {path}"}
                    for path in (task.get("owned_paths") or [])
                ],
            },
        )

    fake_agent = AsyncMock()
    with patch("mozaiksai.core.workflow.task_batches.AG2TaskBatchRunner") as mock_runner_cls:
        mock_runner_cls.return_value.run = _fake_runner_run
        await execute_task_batches_for_trigger(
            workflow_name="AppGenerator",
            trigger_agent="AppPlanAgent",
            batches_config=config,
            agents={
                "ConfigMiddlewareAgent": fake_agent,
                "AppSchemaAgent": fake_agent,
            },
            context_variables=context.data,
            chat_id="chat-workflow-module",
            app_id="workflow_module_app",
            user_id="user-workflow-module",
            fresh_agents_per_task=False,
        )

    assert context.get("app_task_batch_status") == "completed"
    results = context.get("app_task_batch_results")
    assert isinstance(results, dict)
    assert set(results["_meta"]["completed_tasks"]) == {
        "task_service_foundation",
        "task_notifications_pages",
        "task_documents_module",
    }
    # Every worker saw the exact canonical touchpoint facts.
    assert worker_touchpoints_by_task["task_notifications_pages"] == [
        _RESEARCH_TOUCHPOINT_NORMALIZED
    ]

    assembled = await assemble_module.assemble_app_tasks(context_variables=context)
    assembled_by_name = {
        item["filename"]: item["content"] for item in assembled["code_files"]
    }
    assert "modules/documents/module.yaml" in assembled_by_name
    assert not any("module_interface" in name for name in assembled_by_name)
    generated_files = context.get("generated_files")
    assert isinstance(generated_files, dict)
    assert not any("module_interface" in name for name in generated_files)

    # The assembled page (deterministically rendered by the planned-page
    # contract pass from the normalized plan's touchpoints — not from any
    # worker output or ambient generated_workflow_* metadata) retains the
    # workflow launch affordance, and the affordance validates under the real
    # runtime page-action contract.
    from mozaiksai.core.runtime.app.page_schema import AppPageAction

    research_page = yaml.safe_load(assembled_by_name["ui/pages/research.yaml"])
    launch_sections = [
        section
        for section in research_page["sections"]
        if section.get("primitive") == "ActionButton"
    ]
    assert len(launch_sections) == 1
    assert launch_sections[0]["id"] == "research_actions"
    launch_actions = launch_sections[0]["config"]["actions"]
    assert len(launch_actions) == 1
    launch_action = AppPageAction.model_validate(launch_actions[0])
    assert launch_action.action_type == "workflow"
    assert launch_action.workflow_id == "DocumentAnalysis"
    assert launch_action.id == "launch_document_analysis"
    assert launch_action.context_variables == {"source": "research_page"}

    # The notifications page has no touchpoint and gains no launch section.
    notifications_page = yaml.safe_load(
        assembled_by_name["ui/pages/notifications.yaml"]
    )
    assert not any(
        section.get("primitive") == "ActionButton"
        for section in notifications_page["sections"]
    )

    # The intentionally deferred workflow/module metadata surfaces are intact.
    for key, value in _WORKFLOW_METADATA_CONTEXT.items():
        assert context.get(key) == value


@pytest.mark.parametrize(
    "mutate, expected_error",
    [
        pytest.param(
            lambda tp: tp.pop("workflow_id"),
            "requires a non-empty\\s+workflow_id",
            id="missing-workflow-id",
        ),
        pytest.param(
            lambda tp: tp.update(workflow_id=""),
            "requires a non-empty\\s+workflow_id",
            id="empty-workflow-id",
        ),
        pytest.param(
            lambda tp: tp.pop("page_name"),
            "requires a non-empty\\s+page_name",
            id="missing-page-name",
        ),
        pytest.param(
            lambda tp: tp.update(page_name="Ghost"),
            "does not declare",
            id="undeclared-page",
        ),
        pytest.param(
            lambda tp: tp.update(surprise="uninvited"),
            "undeclared fields",
            id="unknown-extra-field",
        ),
        pytest.param(
            lambda tp: tp.update(placement="banner"),
            "placement",
            id="invalid-placement",
        ),
    ],
)
def test_workflow_touchpoint_hostile_entries_reject(mutate, expected_error) -> None:
    """Hostile touchpoint matrix over the declared AppWorkflowTouchpoint
    contract — malformed entries fail the whole plan, never a silent drop."""
    module = _load_module(
        "factory_app/workflows/AppGenerator/tools/app_build_plan.py",
        "tests.app_build_plan_touchpoint_hostile",
    )
    plan = _workflow_module_plan()
    mutate(plan["workflow_touchpoints"][0])

    with pytest.raises(ValueError, match=expected_error):
        module.app_build_plan(AppBuildPlan=plan, context_variables=_Context())


def test_workflow_touchpoint_structural_attacks_reject() -> None:
    module = _load_module(
        "factory_app/workflows/AppGenerator/tools/app_build_plan.py",
        "tests.app_build_plan_touchpoint_structural",
    )

    plan = _workflow_module_plan()
    plan["workflow_touchpoints"] = "not-a-list"
    with pytest.raises(ValueError, match="must be a list"):
        module.app_build_plan(AppBuildPlan=plan, context_variables=_Context())

    plan = _workflow_module_plan()
    plan["workflow_touchpoints"].append("not-a-mapping")
    with pytest.raises(ValueError, match="must be an AppWorkflowTouchpoint object"):
        module.app_build_plan(AppBuildPlan=plan, context_variables=_Context())

    plan = _workflow_module_plan()
    duplicate = dict(_RESEARCH_TOUCHPOINT_INPUT)
    duplicate["label"] = "A contradictory second definition"
    plan["workflow_touchpoints"].append(duplicate)
    with pytest.raises(ValueError, match="duplicates touchpoint identity"):
        module.app_build_plan(AppBuildPlan=plan, context_variables=_Context())


def test_invalid_task_type_remediation_offers_only_local_vocabulary() -> None:
    """P2 reproduction: the unsupported-task remediation lists only the
    AppGenerator-local admitted vocabulary — never the retired
    agent_backend_integration type the generic enum still carries."""
    module = _load_module(
        "factory_app/workflows/AppGenerator/tools/app_build_plan.py",
        "tests.app_build_plan_invalid_task_vocabulary",
    )
    plan = _base_plan()
    plan["build_tasks"].append(
        {
            "task_id": "task_bogus",
            "task_type": "totally_invalid_type",
            "capability_pack_id": None,
            "surface_id": "bogus",
            "surface_kind": "module",
            "execution_target": "AppGenerator",
            "initial_agent": "ConfigMiddlewareAgent",
            "description": "Bogus task.",
            "initial_message": "Do bogus work.",
            "owned_paths": ["config/bogus_targets.json"],
            "depends_on": [],
            "acceptance_criteria": ["n/a"],
        }
    )

    with pytest.raises(ValueError) as excinfo:
        module.app_build_plan(AppBuildPlan=plan, context_variables=_Context())

    message = str(excinfo.value)
    assert "unsupported task_type" in message
    assert "agent_backend_integration" not in message
    assert "page_bundle" in message and "module_contract" in message


def _integration_task_variant(**overrides) -> dict:
    task = {
        "task_id": "task_agent_backend_integration",
        "task_type": "agent_backend_integration",
        "capability_pack_id": None,
        "surface_id": "agent_backend",
        "surface_kind": "module",
        "execution_target": "AppGenerator",
        "initial_agent": "ConfigMiddlewareAgent",
        "description": "Retired non-materializing integration task.",
        "initial_message": "Wire the workflow to module actions.",
        "owned_paths": [],
        "depends_on": [],
        "acceptance_criteria": ["n/a"],
    }
    task.update(overrides)
    return task


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"owned_paths": []}, id="no-owned-paths"),
        pytest.param(
            {"owned_paths": ["workflows/DocumentAnalysis/module_interface.yaml"]},
            id="retired-v1-artifact-path",
        ),
        pytest.param({"owned_paths": ["some/fake/output.json"]}, id="fake-owned-path"),
        pytest.param(
            {
                "surface_kind": "external_integration",
                "owned_paths": ["services/integrations/agent_client.py"],
            },
            id="external-integration-surface",
        ),
        pytest.param(
            {
                "initial_agent": "AppSchemaAgent",
                "owned_paths": ["ui/pages/agent.yaml"],
            },
            id="arbitrary-valid-initial-agent",
        ),
    ],
)
def test_agent_backend_integration_build_task_rejects(overrides: dict) -> None:
    """Hostile matrix: no manually constructed build task can revive the
    retired v1 lane — rejection is typed and independent of owned_paths,
    surface_kind, and initial_agent."""
    module = _load_module(
        "factory_app/workflows/AppGenerator/tools/app_build_plan.py",
        "tests.app_build_plan_integration_task_rejection",
    )
    plan = _base_plan()
    plan["build_tasks"].append(_integration_task_variant(**overrides))

    with pytest.raises(ValueError, match="non-materializing"):
        module.app_build_plan(
            AppBuildPlan=plan,
            context_variables=_Context(),
        )


