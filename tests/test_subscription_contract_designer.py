from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from mozaiksai.core.runtime.app.subscriptions_loader import SubscriptionsConfig
from tests.factory_context import factory_context

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
            "add_on_products": [
                {
                    "add_on_id": "priority_review",
                    "label": "Priority Review",
                    "description": "One-time expert review for a generated report.",
                    "kind": "service",
                    "billing_mode": "one_time",
                    "price": {
                        "amount_cents": 2500,
                        "currency": "usd",
                        "display": "$25",
                        "interval": "one_time",
                    },
                    "required_capability": "reports.generate",
                    "capability_groups": ["reports"],
                    "duration_days": None,
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
                    {
                        "group_id": "services",
                        "label": "Services",
                        "description": "Optional report services.",
                        "kind": "add_on",
                        "plan_ids": [],
                        "capability_groups": ["reports"],
                        "add_on_ids": ["priority_review"],
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
    assert graph["subscription_contract"] == ["concept", "design_docs"]
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


def test_subscription_contract_fallback_queries_use_artifact_version_fields() -> None:
    for workflow_name in ("AgentGenerator", "AppGenerator"):
        context = yaml.safe_load(
            (WORKFLOWS_ROOT / workflow_name / "context_variables.yaml").read_text(
                encoding="utf-8"
            )
        )
        query = context["definitions"]["subscription_contract_artifact"]["source"][
            "query_template"
        ]

        assert query["build_family"] == "subscription_contract"
        assert query["build_key"] == "subscription_contract"
        assert "artifact_kind" not in query
        assert "artifact_key" not in query


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
    assert "AddOnProduct" in models
    assert "AddOnProductPrice" in models

    subscription_fields = models["SubscriptionConfigFile"]["fields"]
    assert subscription_fields["pricing_catalog"]["variants"] == ["PricingCatalog", "null"]
    assert subscription_fields["usage_charge_policies"]["items"] == "UsageChargePolicy"
    assert subscription_fields["top_up_products"]["items"] == "TokenTopUpProduct"
    assert subscription_fields["add_on_products"]["items"] == "AddOnProduct"
    assert models["TokenWallet"]["fields"]["depleted_balance"]["variants"] == ["TokenWalletRecovery", "null"]
    assert models["TokenTopUpProduct"]["fields"]["price"]["type"] == "TokenTopUpPrice"
    assert models["AddOnProduct"]["fields"]["price"]["variants"] == ["AddOnProductPrice", "null"]

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
    assert "add_on_products" in agents_text
    assert "pricing_catalog.groups[].add_on_ids" in agents_text


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
    assert "Add-on products" in ui_source
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
    assert trimmed["subscription_config_file"]["add_on_products"][0]["add_on_id"] == "priority_review"
    assert trimmed["subscription_config_file"]["pricing_catalog"]["groups"][2]["add_on_ids"] == ["priority_review"]
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
    assert "add_on_products" in agents_text
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
                "monetization_provider": "entitlement_dispatch",
                "capability_packs": [
                    {
                        "capability_pack_id": "entitlement_dispatch",
                        "capability_source": "generated_module",
                    }
                ],
                "external_integrations": [],
            "agent_backend_required": False,
            "build_tasks": [
                {
                    "task_id": "task_subscription_config",
                    "task_type": "subscription_config",
                    "capability_pack_id": None,
                    "surface_id": "subscription_contract",
                    "surface_kind": "app_policy",
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
@pytest.mark.parametrize("read_only", [False, True])
async def test_save_subscription_contract_validates_and_persists_provider_neutral_config(monkeypatch: pytest.MonkeyPatch, read_only: bool) -> None:
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
        **factory_context({"app_id": "app_test"}),
        "chat_id": "chat_1",
        "user_id": "user_1",
        "structured_output": _sample_contract(),
    }

    if read_only:
        from mozaiksai.core.workflow.context.frozen import freeze
        context["structured_output"] = freeze(context["structured_output"])
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
    assert config.add_on_products[0].add_on_id == "priority_review"
    assert config.add_on_products[0].price is not None
    assert config.add_on_products[0].price.amount_cents == 2500
    assert config.add_on_products_for_group("services")[0].add_on_id == "priority_review"
    assert config.pricing_catalog is not None
    assert config.pricing_catalog.default_group_id == "platform"
    assert config.pricing_catalog.groups[1].group_id == "ai_usage"
    assert config.usage_charge_policies[0].markup_percent == 35
    assert config.plans[1].token_allowances[0].amount == 250000
    assert context["subscription_contract"]["plan_design_rationale"][0]["source_context"] == "concept_blueprint"

    assert persisted["artifact_kind"] == "subscription_contract"
    assert persisted["artifact_key"] == "subscription_contract"
    assert persisted["input_artifact_kinds"] == ("concept", "design_docs")


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
        **factory_context({"app_id": "app_test"}),
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


def test_transition_graph_routes_changes_requested_back_to_designer() -> None:
    """The HITL revision loop must exist: changes_requested re-enters the designer."""
    graph = _read_yaml(SUBSCRIPTION_WORKFLOW / "transition_graph.yaml")
    rules = graph["transition_rules"]

    loop_back = next(
        (
            rule
            for rule in rules
            if rule["source_agent"] == "ContractDesignerAgent"
            and rule["target_agent"] == "ContractDesignerAgent"
        ),
        None,
    )
    assert loop_back is not None, (
        "SubscriptionContractDesigner must loop back to the designer on "
        "changes_requested instead of terminating without an approved contract"
    )
    assert loop_back["transition_type"] == "condition"
    assert loop_back["condition_type"] == "context_equals"
    assert loop_back["condition_value"] == "changes_requested"
    assert loop_back["condition_key"] == "subscription_contract_review_status"

    terminate = [
        rule
        for rule in rules
        if rule["source_agent"] == "ContractDesignerAgent" and rule["target_agent"] == "terminate"
    ]
    assert terminate, "designer must still terminate once review is not requesting changes"

    # The declared rules must compile into a valid AG2 TransitionGraph.
    from mozaiksai.core.workflow.execution.network_graph import (
        compile_transition_rules_to_graph,
    )

    compiled = compile_transition_rules_to_graph(
        rules,
        initial_agent_name="ContractDesignerAgent",
        agent_id_by_name={"ContractDesignerAgent": "contract_designer"},
    )
    assert compiled is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["review", "persistence"])
async def test_subscription_save_does_not_publish_success_after_dependency_failure(monkeypatch, failure):
    from factory_app.workflows.SubscriptionContractDesigner.tools import (
        save_subscription_contract as module,
    )

    async def review(*_args, **_kwargs):
        if failure == "review":
            raise module.UIToolError("review transport unavailable")
        return {"action": "confirm", "approved": True}

    async def persist(**_kwargs):
        if failure == "review":
            raise AssertionError("must not save before review")
        raise RuntimeError("artifact store unavailable")

    monkeypatch.setattr(module, "use_ui_tool", review)
    monkeypatch.setattr(module, "persist_summary_artifact", persist)
    context = {**factory_context({"app_id": "app_test"}), "chat_id": "chat", "structured_output": _sample_contract()}
    with pytest.raises((module.UIToolError, RuntimeError), match="unavailable"):
        await module.save_subscription_contract(context)
    assert not context.get("subscription_contract")
    assert context.get("subscription_contract_review_status") != "confirmed"


def _generator_agent_stub(name: str, context: dict) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        context_variables=SimpleNamespace(data=context),
        system_message="base",
    )


def test_inject_subscription_contract_context_raises_on_unapproved_changes_requested() -> None:
    from factory_app.workflows._shared.subscription_contract_context import (
        inject_subscription_contract_context,
    )

    agent = _generator_agent_stub(
        "AppPlanAgent",
        {
            "subscription_contract": None,
            "subscription_contract_review_status": "changes_requested",
        },
    )

    with pytest.raises(RuntimeError, match="requested changes"):
        inject_subscription_contract_context(agent, [])


def test_inject_subscription_contract_context_noop_without_contract_or_rejection() -> None:
    from factory_app.workflows._shared.subscription_contract_context import (
        inject_subscription_contract_context,
    )

    agent = _generator_agent_stub("AppPlanAgent", {})
    inject_subscription_contract_context(agent, [])
    assert agent.system_message == "base"


def test_inject_subscription_contract_context_ignores_rejection_for_untargeted_agents() -> None:
    from factory_app.workflows._shared.subscription_contract_context import (
        inject_subscription_contract_context,
    )

    agent = _generator_agent_stub(
        "SomeOtherAgent",
        {
            "subscription_contract": None,
            "subscription_contract_review_status": "changes_requested",
        },
    )
    inject_subscription_contract_context(agent, [])
    assert agent.system_message == "base"


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
    config.pop("add_on_products", None)
    config.pop("usage_charge_policies", None)
    for plan in config["plans"]:
        plan["usage_limits"] = []
        plan["token_allowances"] = []

    normalized = normalize_subscription_contract(contract)

    parsed = normalized["subscription_config_file"]
    assert "token_wallets" not in parsed
    assert "top_up_products" not in parsed
    assert "add_on_products" not in parsed
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


def _normalize(config: dict) -> dict:
    from factory_app.workflows.SubscriptionContractDesigner.tools.save_subscription_contract import (  # noqa: E501
        _normalize_subscription_config,
    )

    return _normalize_subscription_config(config)


def test_assignment_store_schema_declares_the_revision_field() -> None:
    """The designer must be able to express the fence opt-out at all.

    Structured outputs are exact: an agent emitting an undeclared nested field
    is rejected before normalization, so an undeclared `revision_field` means
    the opt-out has no way into the pipeline.
    """
    structured_outputs = _read_yaml(SUBSCRIPTION_WORKFLOW / "structured_outputs.yaml")
    schema = structured_outputs["models"]["AssignmentStore"]["fields"]
    assert "revision_field" in schema
    # The finite literal the runtime accepts, not an open string — a `str`
    # variant could generate a value the runtime would reject at load. The
    # compiler resolves union variants by name, so the literal is declared as
    # its own alias and referenced; an inline `values` list beside `variants`
    # is not a shape it compiles.
    assert schema["revision_field"]["variants"] == ["BillingRevisionField", "null"]
    assert structured_outputs["models"]["BillingRevisionField"] == {
        "type": "literal",
        "values": ["billing_revision"],
    }


def test_explicit_null_revision_field_survives_normalization() -> None:
    """`revision_field: null` is meaning-bearing: it opts a store out of
    revision fencing. Dropping it as "just a null" would silently restore the
    default on the next reload, turning an opt-out into an opt-in."""
    contract = _sample_contract()
    contract["subscription_config_file"]["assignment_store"]["revision_field"] = None

    normalized = _normalize(contract["subscription_config_file"])

    assert "revision_field" in normalized["assignment_store"]
    assert normalized["assignment_store"]["revision_field"] is None


def test_absent_revision_field_normalizes_to_the_explicit_default() -> None:
    """Absence means "use the default", so the normalized contract states it
    explicitly. That is the same semantics, just no longer implicit — and it
    stays clearly distinguishable from the explicit null opt-out."""
    contract = _sample_contract()
    contract["subscription_config_file"]["assignment_store"].pop("revision_field", None)

    normalized = _normalize(contract["subscription_config_file"])

    assert normalized["assignment_store"]["revision_field"] == "billing_revision"


def test_null_revision_field_survives_the_full_generator_roundtrip() -> None:
    """design -> normalize -> materialize -> reload, with no default sneaking
    back in at any hop."""
    import yaml

    from factory_app.workflows.AppGenerator.tools.materialize_app_config_contracts import (
        _materialize_subscriptions_yaml,
    )
    from mozaiksai.core.runtime.app.subscriptions_loader import SubscriptionsConfig

    contract = _sample_contract()
    contract["subscription_config_file"]["assignment_store"]["revision_field"] = None
    normalized = _normalize(contract["subscription_config_file"])

    rendered = _materialize_subscriptions_yaml(
        context_variables={"subscription_contract": {"subscription_config_file": normalized}}
    )
    assert "revision_field: null" in rendered

    reloaded = SubscriptionsConfig.model_validate(yaml.safe_load(rendered))
    assert reloaded.assignment_store is not None
    assert reloaded.assignment_store.revision_field is None, (
        "the opt-out must survive the roundtrip"
    )


# ---------------------------------------------------------------------------
# The concept's own monetization answer has to reach the designer and bind it
# ---------------------------------------------------------------------------
#
# On a live monetized greenfield run the designer emitted contract_required=false
# for a concept whose monetization_intent recorded subscription_contract_likely
# true. The signal was in its context, nested inside concept_blueprint, but the
# prompt never named it and called "monetized" not enough. These cases drive the
# save tool through a real ContextVariablesBridge under a real
# StructuredOutputOverlay: every read is frozen, so a `dict` test anywhere in
# the guard would silently disarm it, exactly as DesignDocs' guard was until
# #708.


def _monetized_blueprint(*, likely: bool = True) -> dict:
    return {
        "app_name": "TaskTracker Pro",
        "agentic_capabilities": [],
        "monetization_intent": {
            "monetized": True,
            "likely_revenue_models": ["subscriptions"],
            "subscription_contract_likely": likely,
            "money_flow_summary": "Users can upgrade to a Pro subscription for advanced features and unlimited tasks.",
            "rationale": "Free tier plus a Pro subscription.",
        },
    }


def _no_contract_output() -> dict:
    return {
        "agent_message": "No subscription contract required for TaskTracker Pro.",
        "contract_required": False,
        "rationale": "The app does not sell recurring access or paid feature gates.",
        "plan_design_rationale": [],
        "app_id": "app_test",
        "app_name": "TaskTracker Pro",
        "subscription_config_file": None,
        "metering_declarations": [],
        "module_contract_updates": [],
        "workflow_contract_updates": [],
        "page_surface_requirements": [],
        "app_generator_instructions": [],
        "validation_notes": [],
        "forbidden_outputs": [],
    }


def _live_designer_context(*, blueprint: dict | None, monetization_enabled: bool, output: dict, chat_id: str | None = "chat_1"):
    """What the tool really receives: a bridge under an overlay, frozen on read."""
    from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
    from mozaiksai.core.workflow.context.structured_output_overlay import StructuredOutputOverlay

    data = {
        **factory_context({"app_id": "app_test"}),
        "user_id": "user_1",
        "workflow_name": "SubscriptionContractDesigner",
        "monetization_enabled": monetization_enabled,
        # The runtime seeds declared state; the outcome wrapper refuses to run
        # without a real attempt count.
        "subscription_contract_review_status": "blocked",
        "subscription_contract_review_attempts": 0,
        "subscription_contract_review_response": None,
    }
    if chat_id:
        data["chat_id"] = chat_id
    if blueprint is not None:
        data["concept_blueprint"] = blueprint
    return StructuredOutputOverlay(ContextVariablesBridge(data), output)


def _live_designer_context_with(extra: dict, **kwargs):  # noqa: ANN001, ANN003
    """Same as _live_designer_context, with extra declared state seeded."""
    from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
    from mozaiksai.core.workflow.context.structured_output_overlay import StructuredOutputOverlay

    context = _live_designer_context(**kwargs)
    base = ContextVariablesBridge({**context._base.snapshot(), **extra})  # noqa: SLF001
    return StructuredOutputOverlay(base, kwargs["output"])


@pytest.mark.asyncio
async def test_a_brownfield_path_is_not_forced_to_a_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    """The existing app may already own billing; the prompt asks for the reason instead."""
    from factory_app.workflows.SubscriptionContractDesigner.tools import (
        save_subscription_contract as module,
    )

    async def _fake_persist(**kwargs):
        return SimpleNamespace(id="av_brownfield")

    async def _fake_review(*args, **kwargs):
        return {"action": "confirm", "approved": True, "status": "approved"}

    monkeypatch.setattr(module, "persist_summary_artifact", _fake_persist)
    monkeypatch.setattr(module, "use_ui_tool", _fake_review)
    context = _live_designer_context_with(
        {"brownfield_build_path": "full_migration"},
        blueprint=_monetized_blueprint(), monetization_enabled=True, output=_no_contract_output(),
    )

    result = await module.save_subscription_contract(context)

    assert result["success"] is True
    assert result["contract_required"] is False


def _refuse_review_and_persistence(monkeypatch: pytest.MonkeyPatch, module) -> None:  # noqa: ANN001
    async def _no_review(*args, **kwargs):
        raise AssertionError("the guard must fire before the review UI is shown")

    async def _no_persist(**kwargs):
        raise AssertionError("a contradicted no-contract must not be persisted")

    monkeypatch.setattr(module, "use_ui_tool", _no_review)
    monkeypatch.setattr(module, "persist_summary_artifact", _no_persist)


def test_the_live_context_is_frozen_all_the_way_down() -> None:
    """Pin the premise: a bridge read is a mapping proxy, and so is the nested intent."""
    from types import MappingProxyType

    context = _live_designer_context(blueprint=_monetized_blueprint(), monetization_enabled=True, output=_no_contract_output())
    blueprint = context.get("concept_blueprint")
    assert isinstance(blueprint, MappingProxyType) and not isinstance(blueprint, dict)
    assert isinstance(blueprint["monetization_intent"], MappingProxyType)
    assert blueprint["monetization_intent"]["subscription_contract_likely"] is True


@pytest.mark.asyncio
async def test_a_contradicted_no_contract_is_returned_to_the_designer(monkeypatch: pytest.MonkeyPatch) -> None:
    from factory_app.workflows.SubscriptionContractDesigner.tools import (
        save_subscription_contract as module,
    )

    _refuse_review_and_persistence(monkeypatch, module)
    context = _live_designer_context(blueprint=_monetized_blueprint(), monetization_enabled=True, output=_no_contract_output())

    result = await module.save_subscription_contract(context)

    assert result["success"] is False
    assert result["review_status"] == "changes_requested"
    assert "subscription_contract_likely=true" in result["requested_changes"]
    assert "Pro subscription" in result["requested_changes"], "quote the concept's own words back"
    # Outcome validation logs a rejection only when the payload carries `error`;
    # a refusal the log never records is invisible to the next acceptance run.
    assert result["error"] == result["requested_changes"]
    # The next turn reads the reason from context, not from this return value.
    assert context.get("subscription_contract_review_status") == "changes_requested"
    response = context.get("subscription_contract_review_response")
    assert response["source"] == "concept_monetization_intent"
    assert "subscription_contract_likely" in response["requested_changes"]
    assert context.get("subscription_contract") is None
    assert list(context.get("subscription_contract_files") or []) == []


@pytest.mark.asyncio
async def test_the_guard_fires_headless_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """No chat means no review card; the contradiction is still refused."""
    from factory_app.workflows.SubscriptionContractDesigner.tools import (
        save_subscription_contract as module,
    )

    _refuse_review_and_persistence(monkeypatch, module)
    context = _live_designer_context(
        blueprint=_monetized_blueprint(), monetization_enabled=True, output=_no_contract_output(), chat_id=None,
    )

    result = await module.save_subscription_contract(context)

    assert result["review_status"] == "changes_requested"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "blueprint, monetization_enabled",
    [
        (_monetized_blueprint(likely=False), True),   # the concept did not ask for a subscription
        (_monetized_blueprint(), False),               # the operator disabled monetization for this build
        ({"app_name": "TaskTracker Pro"}, True),      # the concept never answered
        (None, True),                                  # no concept in scope
    ],
)
async def test_no_contract_stands_when_the_concept_did_not_decide(
    monkeypatch: pytest.MonkeyPatch, blueprint, monetization_enabled,
) -> None:
    """The guard binds the designer only to a decision the concept actually made."""
    from factory_app.workflows.SubscriptionContractDesigner.tools import (
        save_subscription_contract as module,
    )

    async def _fake_persist(**kwargs):
        return SimpleNamespace(id="av_noop")

    async def _fake_review(*args, **kwargs):
        return {"action": "confirm", "approved": True, "status": "approved"}

    monkeypatch.setattr(module, "persist_summary_artifact", _fake_persist)
    monkeypatch.setattr(module, "use_ui_tool", _fake_review)
    context = _live_designer_context(blueprint=blueprint, monetization_enabled=monetization_enabled, output=_no_contract_output())

    result = await module.save_subscription_contract(context)

    assert result["success"] is True
    assert result["contract_required"] is False
    assert result["review_status"] == "confirmed"


@pytest.mark.asyncio
async def test_a_required_contract_is_not_second_guessed(monkeypatch: pytest.MonkeyPatch) -> None:
    from factory_app.workflows.SubscriptionContractDesigner.tools import (
        save_subscription_contract as module,
    )

    async def _fake_persist(**kwargs):
        return SimpleNamespace(id="av_required")

    async def _fake_review(*args, **kwargs):
        return {"action": "confirm", "approved": True, "status": "approved"}

    monkeypatch.setattr(module, "persist_summary_artifact", _fake_persist)
    monkeypatch.setattr(module, "use_ui_tool", _fake_review)
    context = _live_designer_context(blueprint=_monetized_blueprint(), monetization_enabled=True, output=_sample_contract())

    result = await module.save_subscription_contract(context)

    assert result["success"] is True
    assert result["contract_required"] is True


def _review_outcome_spec():
    from mozaiksai.core.workflow.declarative.contracts import ToolOutcomeSpec

    tools = _read_yaml(SUBSCRIPTION_WORKFLOW / "tools.yaml")["tools"]
    entry = next(t for t in tools if t.get("function") == "save_subscription_contract")
    return ToolOutcomeSpec.model_validate(entry["outcome"])


@pytest.mark.asyncio
async def test_the_refusal_survives_outcome_validation_and_permits_a_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """changes_requested is the declared retryable value, so the designer gets the turn back."""
    from factory_app.workflows.SubscriptionContractDesigner.tools import (
        save_subscription_contract as module,
    )
    from mozaiksai.core.workflow.validation.tool_outcomes import wrap_tool_outcome

    _refuse_review_and_persistence(monkeypatch, module)
    spec = _review_outcome_spec()
    assert "changes_requested" in spec.retry_on and spec.max_attempts > 1
    context = _live_designer_context(blueprint=_monetized_blueprint(), monetization_enabled=True, output=_no_contract_output())
    wrapped = wrap_tool_outcome(module.save_subscription_contract, spec)

    result = await wrapped(context_variables=context)

    assert result.get("outcome_error") is None, "the payload must not be replaced"
    assert result["review_status"] == "changes_requested"
    assert context.get(spec.context_key) == "changes_requested"
    assert context.get(spec.attempts_key) == 1


@pytest.mark.asyncio
async def test_a_malformed_contract_reaches_the_designer_with_the_validator_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """A rejection with no declared outcome used to be discarded as invalid_tool_outcome."""
    from factory_app.workflows.SubscriptionContractDesigner.tools import (
        save_subscription_contract as module,
    )
    from mozaiksai.core.workflow.validation.tool_outcomes import wrap_tool_outcome

    _refuse_review_and_persistence(monkeypatch, module)
    output = _sample_contract()
    output["subscription_config_file"] = "not an object"
    context = _live_designer_context(blueprint=_monetized_blueprint(), monetization_enabled=True, output=output)
    wrapped = wrap_tool_outcome(module.save_subscription_contract, _review_outcome_spec())

    result = await wrapped(context_variables=context)

    assert result.get("outcome_error") is None
    assert result["review_status"] == "changes_requested"
    assert result["error"].startswith("invalid_subscription_contract")
    assert "subscription_config_file must be an object" in result["details"]
    assert "subscription_config_file must be an object" in context.get("subscription_contract_review_response")["requested_changes"]


def test_the_prompt_names_the_concept_signal_and_its_precedence() -> None:
    agents_text = (SUBSCRIPTION_WORKFLOW / "agents.yaml").read_text(encoding="utf-8")
    assert "concept_blueprint.monetization_intent" in agents_text, "name where the signal lives"
    assert "subscription_contract_likely" in agents_text
    assert "a\n               subscription contract is required" in agents_text.replace("\n", "\\n") or (
        "subscription contract is required" in agents_text
    ), "state that the concept's answer binds the decision"
    assert '"monetized" is only a broad intent signal' in agents_text, "the broad signal stays broad"
    assert "changes_requested" in agents_text, "tell the designer what a refusal looks like"

    context_vars = _read_yaml(SUBSCRIPTION_WORKFLOW / "context_variables.yaml")["definitions"]
    assert "monetization_intent" in context_vars["concept_blueprint"]["description"]
    assert "subscription_contract_likely" in context_vars["concept_blueprint"]["description"]
    assert "Operator switch" in context_vars["monetization_enabled"]["description"]
