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
    assert "hosted_billing" not in text
    assert "hosted billing" not in text


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
    assert "/api/me/usage" in agents_text
    assert "/api/me/tokens" in agents_text


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
    assert config.plans[1].token_allowances[0].amount == 250000

    assert persisted["artifact_kind"] == "subscription_contract"
    assert persisted["artifact_key"] == "subscription_contract"
    assert persisted["input_artifact_kinds"] == ("concept", "build_plan", "design_docs")


def test_subscription_contract_normalizer_rejects_hosted_product_terms() -> None:
    from factory_app.workflows.SubscriptionContractDesigner.tools.save_subscription_contract import (
        normalize_subscription_contract,
    )

    contract = _sample_contract()
    contract["app_generator_instructions"] = [
        {"target": "AppPlanAgent", "instruction": "Call hosted_billing.create_plan_catalog."}
    ]

    with pytest.raises(ValueError, match="provider-neutral"):
        normalize_subscription_contract(contract)
