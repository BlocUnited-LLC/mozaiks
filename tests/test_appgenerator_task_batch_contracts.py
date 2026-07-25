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


def test_app_build_plan_rejects_control_plane_pack_without_runtime_yaml() -> None:
    module = _load_module(
        "factory_app/workflows/AppGenerator/tools/app_build_plan.py",
        "tests.app_build_plan_task_batch_control_plane_runtime",
    )
    plan = _base_plan()
    plan["build_tasks"].append(
        {
            "task_id": "task_control_plane_pack",
            "task_type": "control_plane_pack",
            "capability_pack_id": None,
            "surface_id": "app_refinement_harness",
            "surface_kind": "control_plane",
            "execution_target": "AppGenerator",
            "initial_agent": "ControlPlaneAgent",
            "description": "Generate app-local refinement harness files.",
            "initial_message": "Generate only the control-plane pack.",
            "owned_paths": [
                "control_plane/config/control_plane.yaml",
            ],
            "depends_on": [],
            "acceptance_criteria": ["Harness routes stay declarative"],
        }
    )

    with pytest.raises(ValueError, match="missing required control-plane pack paths"):
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


