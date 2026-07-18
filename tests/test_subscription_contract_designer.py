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
        if path.is_file() and path.suffix in {".yaml", ".py", ".md", ".jsx", ".js"}:
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
                    "depleted_balance": {
                        "recovery_action": "top_up",
                        "billing_route": "/billing",
                        "top_up_route": "/billing",
                        "upgrade_route": "/pricing",
                        "contact_route": None,
                        "message": "Add tokens or upgrade to keep generating reports.",
                    },
                }
            ],
            "top_up_products": [
                {
                    "product_id": "ai_tokens_10k",
                    "label": "10K AI tokens",
                    "wallet_id": "ai_tokens",
                    "token_amount": 10000,
                    "price": {
                        "amount_cents": 500,
                        "currency": "usd",
                        "display": "$5",
                    },
                    "description": "One-time report generation token pack.",
                    "active": True,
                }
            ],
            "usage_charge_policies": [
                {
                    "meter_id": "ai_tokens",
                    "label": "AI usage",
                    "source": "runtime_llm_usage",
                    "basis": "provider_cost_usd",
                    "markup_percent": 35,
                    "unit_price_usd_per_1k": None,
                    "minimum_charge_usd": 0,
                    "rounding": "cent",
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
    assert "UsageChargePolicy" in models
    assert "TokenWalletRecovery" in models
    assert "TokenTopUpProduct" in models
    assert "TokenTopUpPrice" in models

    subscription_fields = models["SubscriptionConfigFile"]["fields"]
    assert subscription_fields["pricing_catalog"]["variants"] == ["PricingCatalog", "null"]
    assert subscription_fields["usage_charge_policies"]["items"] == "UsageChargePolicy"
    assert subscription_fields["top_up_products"]["items"] == "TokenTopUpProduct"
    assert models["TokenWallet"]["fields"]["depleted_balance"]["variants"] == ["TokenWalletRecovery", "null"]
    assert models["TokenTopUpProduct"]["fields"]["price"]["type"] == "TokenTopUpPrice"

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
    assert "usage_charge_policies" in agents_text
    assert "markup_percent" in agents_text
    assert "provider-cost estimates" in agents_text
    assert "plan_design_rationale" in agents_text
    assert "Do not emit token_wallets" in agents_text
    assert "access only" in agents_text


def test_subscription_contract_designer_exposes_review_ui() -> None:
    orchestrator = _read_yaml(SUBSCRIPTION_WORKFLOW / "orchestrator.yaml")
    tools = _read_yaml(SUBSCRIPTION_WORKFLOW / "tools.yaml")
    ui_config = _read_yaml(SUBSCRIPTION_WORKFLOW / "ui_config.yaml")
    ui_index = (SUBSCRIPTION_WORKFLOW / "ui" / "index.js").read_text(encoding="utf-8")
    ui_source = (
        SUBSCRIPTION_WORKFLOW
        / "ui"
        / "SubscriptionContractDesigner"
        / "SubscriptionContractReview.jsx"
    ).read_text(encoding="utf-8")

    assert orchestrator["human_in_the_loop"] is True
    assert "ContractDesignerAgent" in ui_config["visual_agents"]
    tool = tools["tools"][0]
    assert tool["function"] == "save_subscription_contract"
    assert tool["tool_type"] == "UI_Tool"
    assert tool["ui"]["component"] == "SubscriptionContractReview"
    assert tool["ui"]["mode"] == "artifact"
    assert tool["ui_contract"]["actions_schema"][0]["id"] == "confirm"
    assert "SubscriptionContractReview" in ui_index
    assert "Confirm Subscription Plan Contract" in ui_source
    assert "matches what the user wants" in ui_source
    assert "does not charge anyone" in ui_source
    assert "Request Changes" in ui_source
    assert "canRequestChanges" in ui_source


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
    assert trimmed["subscription_config_file"]["usage_charge_policies"][0]["markup_percent"] == 35
    assert trimmed["subscription_config_file"]["top_up_products"][0]["product_id"] == "ai_tokens_10k"
    assert trimmed["subscription_config_file"]["top_up_products"][0]["price"]["amount_cents"] == 500
    assert trimmed["subscription_config_file"]["token_wallets"][0]["depleted_balance"]["recovery_action"] == "top_up"


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
    assert "usage_charge_policies" in agents_text
    assert "top_up_products" in agents_text
    assert "depleted_balance" in agents_text
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
    assert "/api/modules/billing_portal/*" in agents_text


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

    async def _fake_use_ui_tool(*args, **kwargs):
        return {"action": "confirm", "approved": True, "status": "approved"}

    monkeypatch.setattr(module, "use_ui_tool", _fake_use_ui_tool)
    context = {
        "app_id": "app_test",
        "chat_id": "chat_1",
        "user_id": "user_1",
        "structured_output": _sample_contract(),
    }

    result = await module.save_subscription_contract(context)

    assert result["success"] is True
    assert result["review_status"] == "confirmed"
    assert context["subscription_contract"]["contract_required"] is True
    assert context["subscription_contract"]["user_confirmed"] is True
    assert context["subscription_contract_review_status"] == "confirmed"
    assert context["subscription_contract_files"][0]["filename"] == "config/subscriptions.yaml"
    parsed = yaml.safe_load(context["subscription_contract_files"][0]["content"])
    config = SubscriptionsConfig.model_validate(parsed)
    assert config.token_wallets[0].wallet_id == "ai_tokens"
    assert config.token_wallets[0].depleted_balance is not None
    assert config.token_wallets[0].depleted_balance.recovery_action == "top_up"
    assert config.top_up_products[0].product_id == "ai_tokens_10k"
    assert config.top_up_products[0].price.amount_cents == 500
    assert config.top_up_products[0].price.currency == "usd"
    assert config.pricing_catalog is not None
    assert config.pricing_catalog.default_group_id == "platform"
    assert config.pricing_catalog.groups[1].group_id == "ai_usage"
    assert config.usage_charge_policies[0].markup_percent == 35
    assert config.plans[1].token_allowances[0].amount == 250000
    assert context["subscription_contract"]["plan_design_rationale"][0]["source_context"] == "concept_blueprint"

    assert persisted["artifact_kind"] == "subscription_contract"
    assert persisted["artifact_key"] == "subscription_contract"
    assert persisted["input_artifact_kinds"] == ("concept", "build_plan", "design_docs")


@pytest.mark.asyncio
async def test_save_subscription_contract_blocks_downstream_context_when_review_requests_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from factory_app.workflows.SubscriptionContractDesigner.tools import (
        save_subscription_contract as module,
    )

    async def _fake_persist_summary_artifact(**kwargs):
        raise AssertionError("unconfirmed subscription contracts must not be persisted")

    async def _fake_use_ui_tool(*args, **kwargs):
        return {
            "action": "request_changes",
            "approved": False,
            "status": "changes_requested",
            "requested_changes": "Make the free plan access-only.",
        }

    monkeypatch.setattr(module, "persist_summary_artifact", _fake_persist_summary_artifact)
    monkeypatch.setattr(module, "use_ui_tool", _fake_use_ui_tool)

    context = {
        "app_id": "app_test",
        "chat_id": "chat_1",
        "user_id": "user_1",
        "structured_output": _sample_contract(),
    }

    result = await module.save_subscription_contract(context)

    assert result["success"] is False
    assert result["review_status"] == "changes_requested"
    assert result["requested_changes"] == "Make the free plan access-only."
    assert context["subscription_contract"] is None
    assert context["subscription_contract_files"] == []
    assert context["subscription_contract_review_status"] == "changes_requested"


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


def test_subscription_contract_allows_access_only_saas_without_token_wallets() -> None:
    from factory_app.workflows.SubscriptionContractDesigner.tools.save_subscription_contract import (
        normalize_subscription_contract,
    )

    contract = _sample_contract()
    contract["rationale"] = "The app sells paid access tiers but no AI credits or quotas."
    contract["metering_declarations"] = []
    contract["workflow_contract_updates"] = []
    config = contract["subscription_config_file"]
    config.pop("token_wallets", None)
    config.pop("top_up_products", None)
    config.pop("usage_charge_policies", None)
    for plan in config["plans"]:
        plan["usage_limits"] = []
        plan["token_allowances"] = []

    normalized = normalize_subscription_contract(contract)

    parsed = normalized["subscription_config_file"]
    assert "token_wallets" not in parsed
    assert "top_up_products" not in parsed
    assert "usage_charge_policies" not in parsed
    assert all("token_allowances" not in plan for plan in parsed["plans"])
    assert all("usage_limits" not in plan for plan in parsed["plans"])
    assert parsed["plans"][1]["capabilities"] == ["reports.view", "reports.generate"]


def test_subscription_contract_rejects_token_wallets_without_usage_credit_or_quota_intent() -> None:
    from factory_app.workflows.SubscriptionContractDesigner.tools.save_subscription_contract import (
        normalize_subscription_contract,
    )

    contract = _sample_contract()
    contract["metering_declarations"] = []
    contract["module_contract_updates"][0]["metering"] = None
    contract["workflow_contract_updates"] = []
    config = contract["subscription_config_file"]
    config.pop("top_up_products", None)
    config.pop("usage_charge_policies", None)
    for plan in config["plans"]:
        plan["usage_limits"] = []

    with pytest.raises(ValueError, match="token_wallets are only valid"):
        normalize_subscription_contract(contract)
