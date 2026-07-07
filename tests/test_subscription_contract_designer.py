from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from mozaiksai.core.runtime.app.subscriptions_loader import SubscriptionsConfig

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS_ROOT = REPO_ROOT / "factory_app" / "workflows"
SUBSCRIPTION_WORKFLOW = WORKFLOWS_ROOT / "SubscriptionContractDesigner"


def _read_yaml(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    assert isinstance(data, dict)
    return data


def _workflow_text() -> str:
    parts: list[str] = []
    for path in SUBSCRIPTION_WORKFLOW.rglob("*"):
        if path.is_file() and path.suffix in {".yaml", ".py", ".md"}:
            parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def _sample_contract() -> dict:
    return {
        "agent_message": "Contract ready.",
        "contract_required": True,
        "rationale": "The app sells AI usage plans.",
        "plan_design_rationale": [
            {
                "source_context": "concept_blueprint",
                "signal": "The concept sells AI report generation with monthly usage budgets.",
                "decision": "Create free and pro plans with ai_tokens allowances.",
                "affected_plan_ids": ["free", "pro"],
                "affected_pricing_group_ids": ["platform", "ai_usage"],
            },
            {
                "source_context": "design_surface_map",
                "signal": "ReportAnalysis is the metered workflow and reports.generate is the gated action.",
                "decision": "Gate report generation and meter the analysis workflow with ai_tokens.",
                "affected_plan_ids": ["pro"],
                "affected_pricing_group_ids": ["ai_usage"],
            },
        ],
        "app_id": "app_test",
        "app_name": "AI Reports",
        "subscription_config_file": {
            "schema_version": "mozaiks.subscriptions.v1",
            "label": "AI Reports Plans",
            "default_plan_id": "free",
            "assignment_store": {
                "data_alias": "billing.subscriptions",
                "app_id_field": "app_id",
                "tenant_id_field": "tenant_id",
                "workspace_id_field": None,
                "user_id_field": "user_id",
                "plan_id_field": "plan_id",
                "status_field": "status",
                "starts_at_field": "starts_at",
                "expires_at_field": "expires_at",
                "capabilities_field": "granted_capabilities",
                "plan_snapshot_field": "plan_snapshot",
                "active_statuses": ["active", "trialing"],
            },
            "token_wallets": [
                {
                    "wallet_id": "ai_tokens",
                    "label": "AI tokens",
                    "unit": "tokens",
                    "usage_meter_id": "ai_tokens",
                    "scope": "user",
                    "auto_debit_usage": True,
                    "allow_negative_balance": False,
                }
            ],
            "pricing_catalog": {
                "default_group_id": "platform",
                "groups": [
                    {
                        "group_id": "platform",
                        "label": "Platform",
                        "description": "Core report workspace access.",
                        "kind": "subscription",
                        "plan_ids": ["free", "pro"],
                        "capability_groups": ["reports"],
                        "add_on_ids": [],
                    },
                    {
                        "group_id": "ai_usage",
                        "label": "AI Usage",
                        "description": "Included monthly AI report analysis tokens.",
                        "kind": "service",
                        "plan_ids": ["free", "pro"],
                        "capability_groups": ["ai_tokens"],
                        "add_on_ids": [],
                    },
                ],
            },
            "plans": [
                {
                    "plan_id": "free",
                    "label": "Free",
                    "description": "Trial plan.",
                    "capabilities": ["reports.view"],
                    "usage_limits": [
                        {
                            "meter_id": "ai_tokens",
                            "label": "AI tokens",
                            "unit": "tokens",
                            "monthly_limit": 10000,
                            "capability_id": "reports.generate",
                        }
                    ],
                    "token_allowances": [
                        {
                            "wallet_id": "ai_tokens",
                            "amount": 10000,
                            "cadence": "monthly",
                            "label": "Monthly AI tokens",
                        }
                    ],
                },
                {
                    "plan_id": "pro",
                    "label": "Pro",
                    "description": "Paid plan.",
                    "capabilities": ["reports.view", "reports.generate"],
                    "usage_limits": [],
                    "token_allowances": [
                        {
                            "wallet_id": "ai_tokens",
                            "amount": 250000,
                            "cadence": "monthly",
                            "label": "Monthly AI tokens",
                        }
                    ],
                },
            ],
        },
        "metering_declarations": [
            {
                "surface_type": "workflow",
                "surface_id": "ReportAnalysis",
                "action_id": "report-analysis",
                "wallet_id": "ai_tokens",
                "scope": "user",
                "enforcement": "reserve_then_commit",
                "estimate": 20000,
                "idempotency_key_source": "workflow_run_id",
            }
        ],
        "module_contract_updates": [
            {
                "module_id": "reports",
                "action_id": "generate_report",
                "entitlement_gate": "reports.generate",
                "metering": None,
            }
        ],
        "workflow_contract_updates": [],
        "page_surface_requirements": [
            {
                "page_id": "usage",
                "route": "/usage",
                "purpose": "Show current token balance and usage.",
                "required_runtime_endpoints": ["/api/me/usage", "/api/me/tokens"],
            }
        ],
        "app_generator_instructions": [],
        "validation_notes": [],
        "forbidden_outputs": [],
        "code_files": [],
    }


def test_subscription_contract_designer_is_registered_before_generators() -> None:
    registry = json.loads(
        (WORKFLOWS_ROOT / "extended_orchestration" / "extension_registry.json").read_text(
            encoding="utf-8"
        )
    )
    workflow_ids = {item["id"] for item in registry["workflows"]}
    assert "SubscriptionContractDesigner" in workflow_ids

    graph = registry["artifact_dependency_graph"]
    assert graph["subscription_contract"] == ["concept", "build_plan", "design_docs"]
    assert "subscription_contract" in graph["workflow_bundle"]
    assert "subscription_contract" in graph["app_bundle"]

    build = next(item for item in registry["workflow_sequences"] if item["id"] == "build")
    ordered = [
        workflow
        for step in build["steps"]
        for workflow in step.get("workflows", [])
    ]
    assert ordered.index("DesignDocs") < ordered.index("SubscriptionContractDesigner")
    assert ordered.index("SubscriptionContractDesigner") < ordered.index("AgentGenerator")
    assert ordered.index("SubscriptionContractDesigner") < ordered.index("AppGenerator")
    assert "subscription_contract" in build["affected_declarative_families"]


def test_subscription_contract_designer_pack_is_host_agnostic() -> None:
    text = _workflow_text().lower()
    assert "mozaikspay" not in text
    assert "managed_billing" not in text
    assert "hosted billing" not in text


def test_subscription_contract_designer_schema_supports_semantic_pricing_catalog() -> None:
    structured_outputs = _read_yaml(SUBSCRIPTION_WORKFLOW / "structured_outputs.yaml")
    models = structured_outputs["models"]

    assert "PricingCatalog" in models
    assert "PricingCatalogGroup" in models
    assert "PlanDesignRationale" in models

    subscription_fields = models["SubscriptionConfigFile"]["fields"]
    assert subscription_fields["pricing_catalog"]["variants"] == ["PricingCatalog", "null"]

    output_fields = models["SubscriptionContractOutput"]["fields"]
    assert output_fields["plan_design_rationale"]["items"] == "PlanDesignRationale"


def test_subscription_contract_designer_prompt_maps_plans_to_upstream_context() -> None:
    agents_text = (SUBSCRIPTION_WORKFLOW / "agents.yaml").read_text(encoding="utf-8")

    assert "[SEMANTIC PLAN REASONING]" in agents_text
    assert "concept_blueprint" in agents_text
    assert "design_surface_map" in agents_text
    assert "experience_spec" in agents_text
    assert "builder_options" in agents_text
    assert "pricing_catalog.groups are display metadata" in agents_text
    assert "Do not create pricing.yaml" in agents_text
    assert "plan_design_rationale" in agents_text


def test_appgenerator_and_agentgenerator_receive_subscription_contract_context() -> None:
    app_context = _read_yaml(WORKFLOWS_ROOT / "AppGenerator" / "context_variables.yaml")
    agent_context = _read_yaml(WORKFLOWS_ROOT / "AgentGenerator" / "context_variables.yaml")
    app_middleware = _read_yaml(WORKFLOWS_ROOT / "AppGenerator" / "middleware.yaml")
    agent_middleware = _read_yaml(WORKFLOWS_ROOT / "AgentGenerator" / "middleware.yaml")

    assert "subscription_contract" in app_context["definitions"]
    assert "subscription_contract_artifact" in app_context["definitions"]
    assert "subscription_contract" in agent_context["definitions"]
    assert "subscription_contract_artifact" in agent_context["definitions"]

    assert "subscription_contract" in app_context["agents"]["AppPlanAgent"]["variables"]
    assert "subscription_contract" in app_context["agents"]["AppSchemaAgent"]["variables"]
    assert "subscription_contract" in app_context["agents"]["ConfigMiddlewareAgent"]["variables"]
    assert "subscription_contract" in agent_context["agents"]["PatternAgent"]["variables"]
    assert "subscription_contract" in agent_context["agents"]["WorkflowBundleBuilderAgent"]["variables"]

    rendered_app_middleware = yaml.safe_dump(app_middleware)
    rendered_agent_middleware = yaml.safe_dump(agent_middleware)
    assert "../_shared/subscription_contract_context.py" in rendered_app_middleware
    assert "../_shared/subscription_contract_context.py" in rendered_agent_middleware


def test_subscription_context_injection_preserves_plan_design_reasoning() -> None:
    from factory_app.workflows._shared.subscription_contract_context import _trim_contract

    trimmed = _trim_contract(_sample_contract())

    assert "plan_design_rationale" in trimmed
    assert trimmed["plan_design_rationale"][0]["source_context"] == "concept_blueprint"
    assert trimmed["subscription_config_file"]["pricing_catalog"]["groups"][0]["group_id"] == "platform"


def test_appgenerator_declares_subscription_config_task_contract() -> None:
    file_contracts = _read_yaml(REPO_ROOT / "factory_app" / "build_context" / "AppGenerator" / "file_contracts.yaml")
    structured_outputs = _read_yaml(WORKFLOWS_ROOT / "AppGenerator" / "structured_outputs.yaml")
    agents_text = (WORKFLOWS_ROOT / "AppGenerator" / "agents.yaml").read_text(encoding="utf-8")

    contract = file_contracts["task_contracts"]["subscription_config"]
    assert contract["required_outputs"] == ["config/subscriptions.yaml"]
    assert any("contracts/subscriptions.yaml" in rule for rule in contract["hard_constraints"])

    task_types = structured_outputs["models"]["AppBuildTask"]["fields"]["task_type"]["values"]
    assert "subscription_config" in task_types
    modes = structured_outputs["models"]["ConfigMiddlewareOutput"]["fields"]["mode"]["values"]
    assert "subscription_config" in modes

    assert "task_type: subscription_config" in agents_text
    assert 'owned_paths: ["config/subscriptions.yaml"]' in agents_text
    assert "current_build_task_type == \"subscription_config\"" in agents_text
    assert "module_contract_updates" in agents_text
    assert "set that action's `entitlement_gate` to the exact" in agents_text
    assert "Treat the action list in `current_build_task.initial_message` as a closed contract" in agents_text
    assert "Use `generate_report`, not `backend.handler:generate_report`" in agents_text
    assert "Do not invent events from action verbs" in agents_text
    assert "`module_contract` must be a `ModuleContractBundle` wrapper" in agents_text
    assert "`module_contract.module_yaml` must use the runtime loader shape exactly" in agents_text
    assert "`input_schema` and `output_schema` must be typed `JsonSchemaContract` objects" in agents_text
    assert "`properties` is a list of `{name, type, description, required, enum_values, items_type}`" in agents_text
    assert "Every `JsonSchemaProperty` must include all six fields" in agents_text
    assert "Never set an action's `input_schema` or `output_schema` to null" in agents_text
    assert "`permissions`, `actions`, and `capabilities` are top-level siblings" in agents_text
    assert "Capabilities do not use `id` or `grants`" in agents_text
    assert "YAML `code_files[].content` must be valid YAML serialized from the same typed object" in agents_text
    assert "must be indented inside that action's `actions:` list item" in agents_text
    assert "Always include `agent_message` as a required top-level string in every mode" in agents_text
    assert "/api/me/usage" in agents_text
    assert "/api/me/tokens" in agents_text


def test_app_build_plan_accepts_subscription_config_task() -> None:
    from factory_app.workflows.AppGenerator.tools.app_build_plan import app_build_plan

    class Context:
        def __init__(self) -> None:
            self.data = {}

        def set(self, key: str, value) -> None:
            self.data[key] = value

        def get(self, key: str, default=None):
            return self.data.get(key, default)

    context = Context()
    result = app_build_plan(
        AppBuildPlan={
            "agent_message": "Plan ready.",
            "app_kind": "saas",
            "pages": [
                {
                    "name": "Usage",
                    "route": "/usage",
                    "purpose": "Review usage and token balances.",
                }
            ],
            "entities": [],
            "roles": ["user"],
            "auth_strategy": "basic",
            "service_scope": [],
            "frontend_scope": [],
            "capability_packs": [],
            "external_integrations": [],
            "agent_backend_required": False,
            "build_tasks": [
                {
                    "task_id": "task_subscription_config",
                    "task_type": "subscription_config",
                    "capability_pack_id": None,
                    "surface_id": "subscription_contract",
                    "surface_kind": "control_plane",
                    "execution_target": "app_bundle",
                    "initial_agent": "ConfigMiddlewareAgent",
                    "description": "Emit provider-neutral subscription config.",
                    "initial_message": "Serialize subscription_contract.subscription_config_file only.",
                    "owned_paths": ["config/subscriptions.yaml"],
                    "depends_on": [],
                    "acceptance_criteria": [
                        "config/subscriptions.yaml exists and contains no provider internals."
                    ],
                }
            ],
            "generation_order": ["task_subscription_config"],
        },
        context_variables=context,
    )

    assert "Task batch items: 1" in result
    assert context.get("app_plan_ready") is True
    task = context.get("app_task_batch_items")[0]
    assert task["current_build_task_type"] == "subscription_config"
    assert task["current_build_task"]["owned_paths"] == ["config/subscriptions.yaml"]


def test_agentgenerator_preserves_workflow_metering_contract_without_runtime_logic() -> None:
    agents_text = (WORKFLOWS_ROOT / "AgentGenerator" / "agents.yaml").read_text(encoding="utf-8")

    assert "workflow_contract_updates and metering_declarations" in agents_text
    assert "Copy workflow ids, wallet ids, enforcement mode, estimates" in agents_text
    assert "must not implement reserve/commit logic" in agents_text
    assert "OSS runtime token wallet primitives" in agents_text


@pytest.mark.asyncio
async def test_save_subscription_contract_validates_and_persists_provider_neutral_config(monkeypatch: pytest.MonkeyPatch) -> None:
    from factory_app.workflows.SubscriptionContractDesigner.tools import (
        save_subscription_contract as module,
    )

    persisted: dict = {}

    async def _fake_persist_summary_artifact(**kwargs):
        persisted.update(kwargs)
        return SimpleNamespace(id="av_subscription_contract")

    monkeypatch.setattr(module, "persist_summary_artifact", _fake_persist_summary_artifact)
    context = {
        "app_id": "app_test",
        "chat_id": "chat_1",
        "user_id": "user_1",
        "structured_output": _sample_contract(),
    }

    result = await module.save_subscription_contract(context)

    assert result["success"] is True
    assert context["subscription_contract"]["contract_required"] is True
    assert context["subscription_contract_files"][0]["filename"] == "config/subscriptions.yaml"
    parsed = yaml.safe_load(context["subscription_contract_files"][0]["content"])
    config = SubscriptionsConfig.model_validate(parsed)
    assert config.token_wallets[0].wallet_id == "ai_tokens"
    assert config.pricing_catalog is not None
    assert config.pricing_catalog.default_group_id == "platform"
    assert config.pricing_catalog.groups[1].group_id == "ai_usage"
    assert config.plans[1].token_allowances[0].amount == 250000
    assert context["subscription_contract"]["plan_design_rationale"][0]["source_context"] == "concept_blueprint"

    assert persisted["artifact_kind"] == "subscription_contract"
    assert persisted["artifact_key"] == "subscription_contract"
    assert persisted["input_artifact_kinds"] == ("concept", "build_plan", "design_docs")


def test_subscription_contract_normalizer_rejects_hosted_product_terms() -> None:
    from factory_app.workflows.SubscriptionContractDesigner.tools.save_subscription_contract import (
        normalize_subscription_contract,
    )

    contract = _sample_contract()
    contract["app_generator_instructions"] = [
        {"target": "AppPlanAgent", "instruction": "Call managed_billing.create_plan_catalog."}
    ]

    with pytest.raises(ValueError, match="provider-neutral"):
        normalize_subscription_contract(contract)
