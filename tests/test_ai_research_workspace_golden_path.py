from __future__ import annotations

import json
import textwrap
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from factory_app.workflows.AgentGenerator.tools.workflow_converter import (
    promote_generated_workflow,
)
from factory_app.workflows.AppGenerator.tools import generate_and_download as download_module
from factory_app.workflows.AppGenerator.tools.app_build_plan import app_build_plan
from factory_app.workflows.AppGenerator.tools.app_validation import (
    validate_app_bundle_from_request,
)
from factory_app.workflows.AppGenerator.tools.assemble_app_tasks import (
    assemble_app_tasks,
)
from factory_app.workflows.AppGenerator.tools.export_app_code import resolve_export_gate
from factory_app.workflows.SubscriptionContractDesigner.tools import (
    save_subscription_contract as subscription_module,
)
from mozaiksai.core.runtime.app.loader import AppLoader
from mozaiksai.core.session.build_context import (
    build_provider_values,
    load_build_context,
)
from mozaiksai.core.workflow.workflow_manager import UnifiedWorkflowManager

REPO_ROOT = Path(__file__).resolve().parents[1]
MOZAIKSPAY_CONTEXT_ROOT = REPO_ROOT / "factory_app" / "build_context" / "mozaikspay"


class _Context:
    def __init__(self, values: dict[str, Any] | None = None) -> None:
        self.values = dict(values or {})

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.values[key] = value

    def __getitem__(self, key: str) -> Any:
        return self.values[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.values[key] = value


def _subscription_contract() -> dict[str, Any]:
    usage_limit = {
        "meter_id": "research_executions",
        "label": "Research executions",
        "unit": "requests",
        "capability_id": "research.execute",
    }
    return {
        "agent_message": "AI Research Workspace subscription contract ready.",
        "contract_required": True,
        "rationale": (
            "The workspace offers plan-gated AI research with declared monthly "
            "execution limits. The limits are contract metadata, not proof of runtime counting."
        ),
        "app_id": "research",
        "app_name": "AI Research Workspace",
        "subscription_config_file": {
            "schema_version": "mozaiks.subscriptions.v1",
            "label": "AI Research Workspace Plans",
            "default_plan_id": "free",
            "assignment_store": {
                "data_alias": "billing.subscriptions",
                "app_id_field": "app_id",
                "tenant_id_field": "tenant_id",
                "user_id_field": "user_id",
                "plan_id_field": "plan_id",
                "status_field": "status",
                "capabilities_field": "granted_capabilities",
                "plan_snapshot_field": "plan_snapshot",
                "active_statuses": ["active", "trialing"],
            },
            "plans": [
                {
                    "plan_id": "free",
                    "label": "Free",
                    "description": "Explore the research workspace.",
                    "capabilities": ["research.view", "research.execute"],
                    "usage_limits": [{**usage_limit, "monthly_limit": 20}],
                },
                {
                    "plan_id": "pro",
                    "label": "Pro",
                    "description": "Higher-volume AI research.",
                    "capabilities": ["research.view", "research.execute"],
                    "usage_limits": [{**usage_limit, "monthly_limit": 500}],
                },
            ],
        },
        "plan_design_rationale": [
            {
                "source_context": "product_request",
                "signal": "The requested Free and Pro tiers declare 20 and 500 monthly research executions.",
                "decision": "Preserve both limits on the research_executions meter.",
                "affected_plan_ids": ["free", "pro"],
                "affected_pricing_group_ids": [],
            }
        ],
        "metering_declarations": [
            {
                "surface_type": "workflow",
                "surface_id": "ResearchWorkflow",
                "action_id": "research.execute",
                "scope": "user",
                "enforcement": "declaration_only",
                "idempotency_key_source": "workflow_run_id",
            }
        ],
        "module_contract_updates": [
            {
                "module_id": "research",
                "action_id": "execute_research",
                "entitlement_gate": "research.execute",
                "metering": None,
            }
        ],
        "workflow_contract_updates": [
            {
                "workflow_id": "ResearchWorkflow",
                "capability_id": "research.execute",
                "metering": {"meter_id": "research_executions", "unit": "requests"},
            }
        ],
        "page_surface_requirements": [
            {
                "page_id": "usage",
                "route": "/usage",
                "purpose": "Show declared plan limits and available runtime usage.",
                "required_runtime_endpoints": ["/api/me/usage"],
            }
        ],
        "app_generator_instructions": [],
        "validation_notes": [
            "Execution-count enforcement is not currently implemented by the OSS runtime."
        ],
        "forbidden_outputs": [],
        "code_files": [],
    }


def _subscription_task() -> dict[str, Any]:
    return {
        "task_id": "research.subscription_config",
        "task_type": "subscription_config",
        "capability_pack_id": None,
        "surface_id": "subscription_contract",
        "surface_kind": "app_policy",
        "execution_target": "AppGenerator",
        "initial_agent": "ConfigMiddlewareAgent",
        "description": "Materialize the confirmed subscription contract.",
        "initial_message": "Write config/subscriptions.yaml from the confirmed contract.",
        "owned_paths": ["config/subscriptions.yaml"],
        "depends_on": [],
        "acceptance_criteria": ["Free and Pro research execution limits are preserved."],
    }


def _research_module_task() -> dict[str, Any]:
    return {
        "task_id": "research.module",
        "task_type": "module_contract",
        "capability_pack_id": "research",
        "surface_id": "research",
        "surface_kind": "module",
        "execution_target": "AppGenerator",
        "initial_agent": "ConfigMiddlewareAgent",
        "description": "Declare saved research and workflow launch actions.",
        "initial_message": "Generate the canonical research module contract.",
        "owned_paths": ["modules/research/module.yaml"],
        "depends_on": ["research.subscription_config"],
        "acceptance_criteria": ["execute_research is gated by research.execute."],
    }


def _research_page_task() -> dict[str, Any]:
    return {
        "task_id": "research.pages",
        "task_type": "page_bundle",
        "capability_pack_id": None,
        "surface_id": "research_workspace",
        "surface_kind": "ui_only",
        "execution_target": "AppGenerator",
        "initial_agent": "AppSchemaAgent",
        "description": "Generate the research workspace page.",
        "initial_message": "Generate a schema page for saved research and research execution.",
        "owned_paths": ["app.json", "ui/pages/research.yaml"],
        "depends_on": ["research.module"],
        "acceptance_criteria": ["Research actions bind through the research module."],
    }


def _build_plan(mozaikspay_pack: dict[str, Any]) -> dict[str, Any]:
    tasks = [_subscription_task(), _research_module_task(), _research_page_task()]
    return {
        "agent_message": "Deterministic AI Research Workspace plan ready.",
        "app_kind": "saas",
        "pages": [
            {
                "name": "Research",
                "route": "/research",
                "purpose": "Run AI research and inspect saved results.",
            }
        ],
        "entities": [
            {
                "name": "ResearchResult",
                "operations": ["read", "create"],
                "notes": "Saved result produced by the research workflow.",
            }
        ],
        "roles": ["user"],
        "auth_strategy": "basic",
        "service_scope": ["research"],
        "frontend_scope": ["research", "billing", "pricing", "usage"],
        "monetization_provider": "mozaiks_pay",
        "capability_packs": [
            mozaikspay_pack,
            {
                "capability_pack_id": "research",
                "surface_id": "research",
                "surface_kind": "module",
                "label": "AI research",
                "operations": ["list_results", "execute_research"],
            },
        ],
        "external_integrations": [],
        "agent_backend_required": False,
        "build_tasks": tasks,
        "generation_order": [task["task_id"] for task in tasks],
    }


def _research_files() -> dict[str, str]:
    module_yaml = textwrap.dedent(
        """
        schema_version: mozaiks.module.v1
        module:
          id: research
          display_name: Research
          version: 1.0.0
          description: Saved AI research results and workflow launches.
          owner: app
          visibility: private
          handler: backend.handler:ResearchHandler
        permissions:
          - id: research.read
            description: Read saved research results.
          - id: research.execute
            description: Start AI research.
        actions:
          - id: list_results
            description: List saved research results.
            handler_method: list_results
            input_schema:
              type: object
              properties: {}
            output_schema:
              type: object
              required: [results]
              properties:
                results: {type: array}
            permissions: [research.read]
          - id: execute_research
            description: Save a research request and start the AI research workflow.
            handler_method: execute_research
            emits: [domain.research.requested]
            input_schema:
              type: object
              required: [query]
              properties:
                query: {type: string}
            output_schema:
              type: object
              required: [research_id, status]
              properties:
                research_id: {type: string}
                status: {type: string}
            permissions: [research.execute]
        capabilities:
          - capability_id: research.view
            kind: action
            target: list_results
            title: View research
          - capability_id: research.execute
            kind: workflow
            target: ResearchWorkflow
            title: Execute research
        """
    ).strip() + "\n"
    return {
        "app.json": json.dumps(
            {
                "appId": "research",
                "appName": "AI Research Workspace",
                "version": "1.0.0",
                "startup": {"landing_spot": "/research"},
            }
        ),
        "config/ai.json": json.dumps(
            {"chat": {"chat_startup_mode": "ask"}, "workflows": {"entry_point": "ResearchWorkflow"}}
        ),
        "config/shell.json": json.dumps(
            {"navigation": {"autoFromPages": True}, "header": {"show": True}}
        ),
        "data/contract.json": json.dumps(
            {
                "version": "1",
                "app_id": "research",
                "surfaces": [
                    {
                        "surface_id": "research",
                        "surface_kind": "module",
                        "collections": [
                            {
                                "name": "research_results",
                                "ownership": {"surface_id": "research", "surface_kind": "module"},
                            }
                        ],
                    }
                ],
                "shared_collections": [
                    {"name": "subscription_assignments", "data_alias": "billing.subscriptions"}
                ],
            }
        ),
        "ui/route_manifest.json": json.dumps(
            {
                "pages": [
                    {
                        "path": "/research",
                        "component": "SchemaPage",
                        "label": "Research",
                        "order": 10,
                        "schema": "research",
                        "navigation": {"group": "main"},
                        "meta": {"title": "Research"},
                    }
                ]
            }
        ),
        "ui/pages/research.yaml": textwrap.dedent(
            """
            schema_version: mozaiks.app_page.v1
            name: Research
            route: /research
            title: AI Research Workspace
            page_type: record_list
            layout: full-width
            sections:
              - id: saved-research
                primitive: DataTable
                title: Saved research
                config:
                  api_endpoint: /api/modules/research/list_results
                  columns:
                    - key: research_id
                      label: Research
                    - key: status
                      label: Status
              - id: run-research
                primitive: Form
                title: Run research
                config:
                  fields:
                    - name: query
                      label: Research question
                      type: text
                  submit_action:
                    label: Research
                    action_type: submit
                    href: /api/modules/research/execute_research
            """
        ).strip() + "\n",
        "modules/research/module.yaml": module_yaml,
        "modules/research/contracts/events.yaml": textwrap.dedent(
            """
            schema_version: mozaiks.events.v1
            events:
              - type: domain.research.requested
                version: 1
                description: A user requested AI research.
                producer: research
                payload_schema:
                  type: object
                  properties:
                    research_id: {type: string}
                    query: {type: string}
            """
        ).strip() + "\n",
        "modules/research/contracts/reactions.yaml": textwrap.dedent(
            """
            schema_version: mozaiks.reactions.v1
            reactions:
              - id: run_research_workflow
                event_type: domain.research.requested
                target:
                  kind: capability
                  capability_id: research.execute
            """
        ).strip() + "\n",
        "modules/research/backend/__init__.py": "",
        "modules/research/backend/handler.py": textwrap.dedent(
            """
            from .service import ResearchService


            class ResearchHandler:
                def __init__(self):
                    self.service = ResearchService()

                async def list_results(self, ctx, **params):
                    return await self.service.list_results(ctx, **params)

                async def execute_research(self, ctx, **params):
                    return await self.service.execute_research(ctx, **params)
            """
        ).strip() + "\n",
        "modules/research/backend/service.py": textwrap.dedent(
            """
            from .repo import ResearchRepo
            from .schemas import research_request


            class ResearchService:
                async def list_results(self, ctx, **_params):
                    return {"results": await ResearchRepo(ctx).list_results()}

                async def execute_research(self, ctx, *, query, **_params):
                    record = research_request(query=query)
                    await ResearchRepo(ctx).save(record)
                    return {"research_id": record["research_id"], "status": record["status"]}
            """
        ).strip() + "\n",
        "modules/research/backend/repo.py": textwrap.dedent(
            """
            class ResearchRepo:
                def __init__(self, ctx):
                    self.collection = ctx.persistence.collection("research", "research_results")

                async def list_results(self):
                    cursor = self.collection.find({})
                    return await cursor.to_list(length=100)

                async def save(self, record):
                    await self.collection.insert_one(record)
                    return record
            """
        ).strip() + "\n",
        "modules/research/backend/policy.py": textwrap.dedent(
            """
            class ResearchPolicy:
                @staticmethod
                def scope_query(query, *, user_id=None):
                    scoped = dict(query or {})
                    if user_id:
                        scoped["user_id"] = user_id
                    return scoped
            """
        ).strip() + "\n",
        "modules/research/backend/schemas.py": textwrap.dedent(
            """
            from uuid import uuid4


            def research_request(*, query):
                return {
                    "research_id": uuid4().hex,
                    "query": str(query).strip(),
                    "status": "requested",
                }
            """
        ).strip() + "\n",
    }


def _research_workflow_files() -> dict[str, str]:
    return {
        "orchestrator.yaml": textwrap.dedent(
            """
            workflow_name: ResearchWorkflow
            workflow_startup_mode: UserDriven
            orchestration_pattern: ag2_network
            initial_agent: ResearchAgent
            initial_message: Research the requested topic and synthesize a saved result.
            max_turns: 4
            human_in_the_loop: false
            triggers:
              - type: event
                event: domain.research.requested
                description: Start research for a saved request.
            """
        ).strip() + "\n",
        "agents.yaml": textwrap.dedent(
            """
            agents:
              - name: ResearchAgent
                structured_outputs_required: false
                system_message: Research the supplied question using configured tools and return cited findings.
              - name: SynthesisAgent
                structured_outputs_required: false
                system_message: Synthesize findings into a concise saved research result.
            """
        ).strip() + "\n",
        "transition_graph.yaml": textwrap.dedent(
            """
            transition_rules:
              - source_agent: ResearchAgent
                target_agent: SynthesisAgent
                transition_type: after_turn
              - source_agent: SynthesisAgent
                target_agent: terminate
                transition_type: after_turn
                transition_target: TerminateTarget
            """
        ).strip() + "\n",
        "context_variables.yaml": textwrap.dedent(
            """
            definitions:
              research_id:
                type: string
                source: {type: state, default: ""}
              query:
                type: string
                source: {type: state, default: ""}
            agents:
              ResearchAgent:
                variables: [research_id, query]
              SynthesisAgent:
                variables: [research_id, query]
            """
        ).strip() + "\n",
    }


def _workflow_metadata() -> dict[str, Any]:
    return {
        "contract_version": "1.0",
        "bundle_name": "AI Research Workflow",
        "primary_workflow": "ResearchWorkflow",
        "workflows": [
            {
                "workflow_name": "ResearchWorkflow",
                "capability_id": "research.execute",
                "startup_mode": "UserDriven",
                "trigger_events": [{"event_type": "domain.research.requested"}],
            }
        ],
    }


def _write_files(root: Path, files: dict[str, str]) -> None:
    for relative_path, content in files.items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


@pytest.mark.asyncio
async def test_ai_research_workspace_offline_golden_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay agent outputs through production assembly, validation, load, and export.

    The subscription, AppBuildPlan, app-file, and workflow-file dictionaries are
    deterministic fixtures for structured outputs normally produced by model
    agents. This test does not prove raw-prompt generation, live AG2 execution,
    live MozaiksPay fulfillment, or monthly execution-count enforcement.
    """
    product_request = (
        "Build an AI Research Workspace with saved results, Free and Pro plans, "
        "MozaiksPay billing, gated AI research, usage visibility, and deployment output."
    )
    pack_config = load_build_context(MOZAIKSPAY_CONTEXT_ROOT / "context.yaml")
    provider_values = build_provider_values(root=MOZAIKSPAY_CONTEXT_ROOT, config=pack_config)
    mozaikspay_pack = provider_values["capability_packs"][0]
    assert mozaikspay_pack["id"] == "mozaikspay"
    assert mozaikspay_pack["capability_source"] == "managed_capability"

    context = _Context(
        {
            "workflow_name": "SubscriptionContractDesigner",
            "app_id": "research",
            "chat_id": "golden",
            "user_id": "offline-test-user",
            "product_request": product_request,
            "structured_output": _subscription_contract(),
            "capability_packs": [mozaikspay_pack],
            "available_managed_capabilities": [mozaikspay_pack],
            "workflow_integration_metadata": _workflow_metadata(),
            "generated_workflow_name": "ResearchWorkflow",
            "generated_workflow_capability_id": "research.execute",
        }
    )

    async def approve_review(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"action": "confirm", "approved": True, "status": "approved"}

    async def persist_contract(**_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(id="av_subscription_contract_offline")

    monkeypatch.setattr(subscription_module, "use_ui_tool", approve_review)
    monkeypatch.setattr(subscription_module, "persist_summary_artifact", persist_contract)
    subscription_result = await subscription_module.save_subscription_contract(context)
    assert subscription_result["success"] is True

    plan_result = app_build_plan(
        AppBuildPlan=_build_plan(mozaikspay_pack),
        context_variables=context,
    )
    assert "AI Research Workspace plan ready" in plan_result
    assert context["app_plan_ready"] is True
    normalized_plan = context["app_build_plan"]
    assert normalized_plan["monetization_provider"] == "mozaiks_pay"
    assert {
        pack.get("capability_pack_id") or pack.get("pack_id") or pack.get("id")
        for pack in normalized_plan["capability_packs"]
    } >= {
        "mozaikspay",
        "research",
    }

    generated_workflow = tmp_path / "generated" / "ResearchWorkflow"
    _write_files(generated_workflow, _research_workflow_files())
    active_workflows_root = tmp_path / "active" / "workflows"
    promotion = promote_generated_workflow(generated_workflow, active_workflows_root)
    assert Path(promotion["target_dir"]).is_dir()
    monkeypatch.setattr(UnifiedWorkflowManager, "_instance", None)
    workflow_manager = UnifiedWorkflowManager(workflows_base_path=str(active_workflows_root))
    workflow_info = workflow_manager.get_workflow_info("ResearchWorkflow")
    assert workflow_info is not None
    assert workflow_info["status"] == "loaded", workflow_info

    context.set(
        "code_files",
        [{"filename": path, "content": content} for path, content in _research_files().items()],
    )
    context.set("workflow_name", "AppGenerator")
    assembled = await assemble_app_tasks(context_variables=context)
    files = {entry["filename"]: entry["content"] for entry in assembled["code_files"]}

    subscriptions = yaml.safe_load(files["config/subscriptions.yaml"])
    limits = {
        plan["plan_id"]: plan["usage_limits"][0]["monthly_limit"]
        for plan in subscriptions["plans"]
    }
    assert limits == {"free": 20, "pro": 500}
    research_module = yaml.safe_load(files["modules/research/module.yaml"])
    execute_action = next(action for action in research_module["actions"] if action["id"] == "execute_research")
    assert execute_action["entitlement_gate"] == "research.execute"
    assert "services/integrations/mozaikspay_client.py" in files
    assert {"ui/pages/billing.yaml", "ui/pages/pricing.yaml", "ui/pages/usage.yaml"} <= set(files)

    context.set("generated_files", files)
    context.set("code_files", assembled["code_files"])
    validation = await validate_app_bundle_from_request(
        {"validation_strategy": "skip", "start_dev_server": False},
        context_variables=context,
    )
    acceptance = validation["app_bundle_acceptance_result"]
    assert acceptance["passed"] is True, acceptance["failed_tests"]
    assert set(acceptance["validation_evidence"]["completed"]) == {
        "agent_backend",
        "app_runtime_load",
        "bundle_scan",
        "functional_completeness",
        "module_implementation",
        "module_runtime_quality",
        "module_wiring",
        "workflow_integration",
    }
    assert acceptance["agent_backend"]["checks"][0]["id"] == "agent_backend_integration_not_required"
    assert acceptance["workflow_integration"]["checks"][0]["id"] == "workflow_integration_contract"
    assert context["app_validation_status"] == "skipped"
    assert context["integration_tests_passed"] is True
    export_gate = resolve_export_gate(context)
    assert export_gate["allow_export"] is True, export_gate["reasons"]

    class _Persistence:
        async def gather_latest_agent_jsons(self, **_kwargs: Any) -> dict[str, Any]:
            return {}

    async def no_op(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def download_ui(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"action": "download", "status": "confirmed"}

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(download_module, "AG2PersistenceManager", lambda: _Persistence())
    monkeypatch.setattr(download_module, "get_latest_workflow_export", no_op)
    monkeypatch.setattr(download_module, "_register_app_bundle_artifact_version", no_op)
    monkeypatch.setattr(download_module, "update_build_status", no_op)
    monkeypatch.setattr(download_module, "use_ui_tool", download_ui)

    exported = await download_module.generate_and_download(
        {
            "storage_backend": "none",
            "include_dockerfiles": True,
            "include_workflow": True,
            "include_compose": True,
            "deployment_profile": "generic_container",
        },
        "AI Research Workspace bundle ready.",
        context_variables=context,
    )
    assert exported["status"] == "success", exported
    exported_paths = set(exported["files_written"])
    assert {
        "Dockerfile",
        "docker-compose.yml",
        ".env.example",
        "deployment.manifest.json",
        ".github/workflows/deploy.yml",
    } <= exported_paths
    assert Path(exported["bundle_zip"]).is_file()

    loaded = await AppLoader.load(exported["bundle_dir"])
    assert loaded.failed_module_names == []
    assert {module.name for module in loaded.modules} >= {"research", "billing_portal"}
    # App and workflow bundles have distinct canonical roots. Their connection
    # is proven above by workflow loading and by the app acceptance gate's
    # workflow_integration check, not by copying workflows into the app bundle.
    assert loaded.definition.workflows == []
    assert loaded.subscriptions_config is not None
    loaded_limits = {
        plan.plan_id: plan.usage_limits[0].monthly_limit
        for plan in loaded.subscriptions_config.plans
    }
    assert loaded_limits == {"free": 20, "pro": 500}

    # This proof deliberately stops at declared contract preservation. The OSS
    # runtime does not yet count or enforce generic monthly research executions.
    assert context["subscription_contract"]["validation_notes"] == [
        "Execution-count enforcement is not currently implemented by the OSS runtime."
    ]
