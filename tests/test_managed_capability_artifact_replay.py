from __future__ import annotations

import json
import textwrap
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.app_build_plan import app_build_plan
from factory_app.workflows.AppGenerator.tools.app_validation import validate_app_bundle_from_request
from factory_app.workflows.AppGenerator.tools.assemble_app_tasks import assemble_app_tasks
from factory_app.workflows.AppGenerator.tools.deployment_contract import (
    generate_deployment_artifacts,
)
from factory_app.workflows.AppGenerator.tools.generate_and_download import (
    _deployment_env_for_capability_packs,
)
from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import scan_generated_bundle
from mozaiksai.core.billing.fulfillment import BillingFulfillmentCommand, BillingFulfillmentService
from mozaiksai.core.capabilities.simple_llm import SimpleLLMCapabilityService
from mozaiksai.core.runtime.app.entitlements import ConfiguredEntitlementAdapter
from mozaiksai.core.runtime.app.loader import AppLoader
from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor, ModuleRequest
from mozaiksai.core.tokens.guard import TokenUsageDenied, TokenUsageGuard
from mozaiksai.core.tokens.wallet import TokenWalletLedger
from mozaiksai.hosts.platform import _current_user_token_wallet_summary
from tests.test_generated_saas_subscription_runtime_acceptance import (
    _Collection,
    _Database,
    _fake_provider,
    _FakeLLMClient,
    _NoopAuditLogger,
)

WORKSPACE = Path(__file__).resolve().parents[1]
MOZAIKSPAY_PACK_ROOT = WORKSPACE / "factory_app" / "build_context" / "mozaikspay"


class _Context:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = dict(data)

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value


def _file_map(result: dict[str, Any]) -> dict[str, str]:
    return {
        str(item["filename"]): str(item["content"])
        for item in result.get("code_files") or []
        if isinstance(item, dict) and item.get("filename")
    }


def _write_files(root: Path, files: dict[str, str]) -> None:
    for rel_path, content in files.items():
        path = root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _write_wallet_pack(root: Path) -> dict[str, Any]:
    pack_root = root / "wallet_pack"
    template_root = pack_root / "templates" / "services" / "integrations"
    template_root.mkdir(parents=True)
    (pack_root / "context.yaml").write_text(
        yaml.safe_dump(
            {
                "context_id": "wallet",
                "assets": [{"path": "templates/", "kind": "templates"}],
                "pack": {
                    "id": "wallet",
                    "version": "0.1.0",
                    "status": "active",
                    "capability_source": "managed_capability",
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (template_root / "wallet_client.py").write_text(
        textwrap.dedent(
            """
            from __future__ import annotations

            from typing import Any


            class ManagedWalletClient:
                async def get_wallet_summary(self, **scope: Any) -> dict[str, Any]:
                    return {"success": True, "balance": 0, "scope": scope}

                async def request_payout(self, *, amount: int | None = None, **scope: Any) -> dict[str, Any]:
                    return {"success": True, "amount": amount, "scope": scope}
            """
        ).lstrip(),
        encoding="utf-8",
    )
    return {
        "id": "wallet",
        "capability_source": "managed_capability",
        "surface_kind": "external_integration",
        "implementation_mode": "external_integration",
        "pack_source_path": str(pack_root),
        "capabilities": [
            {"capability_id": "wallet.view", "module": "wallet"},
            {"capability_id": "wallet.payout", "module": "wallet"},
        ],
        "facades": [
            {
                "module_id": "wallet_dashboard",
                "provider_module": "wallet",
                "provider_actions": ["get_wallet_summary", "request_payout"],
                "pages": [
                    {
                        "name": "Wallet",
                        "route": "/wallet",
                        "primary_entities": ["wallet"],
                        "primary_actions": ["get_wallet_summary", "request_payout"],
                    }
                ],
            }
        ],
    }


def _base_plan(
    *,
    pages: list[dict[str, Any]],
    capability_packs: list[dict[str, Any]],
    build_tasks: list[dict[str, Any]],
    monetization_provider: str | None = None,
) -> dict[str, Any]:
    return {
        "agent_message": "Plan generated app artifacts.",
        "app_kind": "saas",
        "pages": pages,
        "entities": [],
        "roles": [],
        "auth_strategy": "basic-login",
        "service_scope": [],
        "frontend_scope": [],
        "theme_preferences": None,
        "brand_intent": None,
        "monetization_provider": monetization_provider,
        "capability_packs": capability_packs,
        "external_integrations": [],
        "agent_backend_required": False,
        "build_tasks": build_tasks,
        "generation_order": ["integration-adapters", "backend-foundation", "app-schema-bundle"],
    }


def _wallet_replay_plan() -> dict[str, Any]:
    return _base_plan(
        pages=[
            {
                "name": "Wallet",
                "route": "/wallet",
                "purpose": "View balance and request payouts through the app-owned wallet facade.",
                "sections_hint": [
                    {
                        "section_id_hint": "wallet-summary",
                        "primitive": "ResourceTable",
                        "config_hint": {
                            "api_endpoint": "/api/modules/wallet/get_wallet_summary",
                        },
                    }
                ],
            }
        ],
        capability_packs=[
            {
                "capability_pack_id": "wallet",
                "pack_type": "managed_capability",
            }
        ],
        build_tasks=[
            {
                "task_id": "wallet.adapter",
                "task_type": "api_surface",
                "capability_pack_id": None,
                "surface_id": "wallet",
                "surface_kind": "external_integration",
                "execution_target": "AppGenerator",
                "initial_agent": "ControllerAgent",
                "description": "Generate wallet client.",
                "initial_message": "Generate services/integrations/wallet_client.py.",
                "owned_paths": ["services/integrations/wallet_client.py"],
                "depends_on": [],
                "acceptance_criteria": [],
            },
            {
                "task_id": "wallet.facade_contract",
                "task_type": "module_contract",
                "capability_pack_id": "wallet",
                "surface_id": "wallet_dashboard",
                "surface_kind": "module",
                "execution_target": "AppGenerator",
                "initial_agent": "ConfigMiddlewareAgent",
                "description": "Generate wallet dashboard facade.",
                "initial_message": "Generate modules/wallet_dashboard/module.yaml.",
                "owned_paths": ["modules/wallet_dashboard/module.yaml"],
                "depends_on": [],
                "acceptance_criteria": [],
            },
            {
                "task_id": "wallet.pages",
                "task_type": "page_bundle",
                "capability_pack_id": "wallet_dashboard",
                "surface_id": "wallet_dashboard",
                "surface_kind": "ui_only",
                "execution_target": "AppGenerator",
                "initial_agent": "AppSchemaAgent",
                "description": "Generate wallet page.",
                "initial_message": "Generate ui/pages/wallet.yaml.",
                "owned_paths": ["ui/pages/wallet.yaml"],
                "depends_on": ["wallet.facade_contract"],
                "acceptance_criteria": [],
            },
        ],
    )


def _wallet_task_outputs() -> dict[str, Any]:
    return {
        "wallet_adapter": {
            "code_files": [
                {
                    "filename": "services/integrations/wallet_client.py",
                    "content": "import payment_provider\npayment_provider.api_key = 'provider_test_1234567890'\n",
                }
            ]
        },
        "wallet_facade": {
            "code_files": [
                {
                    "filename": "modules/wallet_dashboard/module.yaml",
                    "content": textwrap.dedent(
                        """
                        schema_version: mozaiks.module.v1
                        module:
                          id: wallet_dashboard
                          handler: backend.handler:WalletDashboardHandler
                        actions:
                          - id: get_wallet_summary
                            handler_method: get_wallet_summary
                            input_schema: {type: object, properties: {}}
                            output_schema: {type: object}
                        """
                    ).lstrip(),
                },
                {
                    "filename": "modules/wallet_dashboard/backend/handler.py",
                    "content": "class WalletDashboardHandler:\n    async def get_wallet_summary(self, ctx, **params):\n        return {}\n",
                },
                {
                    "filename": "ui/pages/wallet.yaml",
                    "content": (
                        "sections:\n"
                        "  - id: wallet-summary\n"
                        "    primitive: ResourceTable\n"
                        "    config:\n"
                        "      columns:\n"
                        "        - key: id\n"
                        "          label: ID\n"
                        "      api_endpoint: /api/modules/wallet_dashboard/get_wallet_summary\n"
                    ),
                },
            ]
        },
    }


def _load_mozaikspay_descriptor() -> tuple[dict[str, Any], dict[str, Any]]:
    context = yaml.safe_load((MOZAIKSPAY_PACK_ROOT / "context.yaml").read_text(encoding="utf-8"))
    contract = yaml.safe_load((MOZAIKSPAY_PACK_ROOT / "contract.yaml").read_text(encoding="utf-8"))
    pack = dict(context.get("pack") or {})
    descriptor = {
        **pack,
        "id": pack["id"],
        "capability_source": "managed_capability",
        "surface_kind": "external_integration",
        "implementation_mode": "external_integration",
        "pack_source_path": str(MOZAIKSPAY_PACK_ROOT),
        "capabilities": context.get("capabilities") or [],
        "facades": context.get("facades") or [],
        "required_integrations": pack.get("required_integrations") or [],
    }
    return descriptor, contract


def _subscriptions_yaml() -> str:
    return textwrap.dedent(
        """
        schema_version: mozaiks.subscriptions.v1
        label: Replay SaaS Plans
        default_plan_id: free
        assignment_store:
          data_alias: billing.subscriptions
        token_wallets:
          - wallet_id: ai_tokens
            label: AI tokens
            unit: tokens
            usage_meter_id: ai_tokens
            scope: user
            auto_debit_usage: true
            depleted_balance:
              recovery_action: top_up
              billing_route: /billing
              top_up_route: /billing
              upgrade_route: /pricing
              message: Add tokens or upgrade to keep using AI features.
        top_up_products:
          - product_id: ai_tokens_10k
            label: 10K AI tokens
            wallet_id: ai_tokens
            token_amount: 10000
            price:
              amount_cents: 500
              currency: usd
              display: $5
        plans:
          - plan_id: free
            label: Free
            capabilities: [billing_portal.read]
          - plan_id: pro
            label: Pro
            capabilities: [billing_portal.read, billing_portal.manage, reports.generate]
            usage_limits:
              - meter_id: ai_tokens
                label: AI tokens
                unit: tokens
                monthly_limit: 1000
                capability_id: reports.generate
            token_allowances:
              - wallet_id: ai_tokens
                amount: 1000
                cadence: monthly
                label: Monthly AI tokens
        """
    ).lstrip()


def _mozaikspay_replay_plan() -> dict[str, Any]:
    return _base_plan(
        monetization_provider="mozaiks_pay",
        pages=[
            {
                "name": "Billing",
                "route": "/billing",
                "purpose": "View subscription status.",
                "sections_hint": [
                    {
                        "section_id_hint": "billing-status",
                        "primitive": "SummaryStrip",
                        "config_hint": {
                            "api_endpoint": "/api/modules/mozaikspay/get_subscription_status",
                            "method": "POST",
                        },
                    }
                ],
            },
            {
                "name": "Usage",
                "route": "/usage",
                "purpose": "View runtime usage.",
                "sections_hint": [
                    {
                        "section_id_hint": "usage-status",
                        "primitive": "SummaryStrip",
                        "config_hint": {
                            "api_endpoint": "/api/modules/mozaikspay/get_usage_status",
                            "method": "POST",
                        },
                    }
                ],
            },
        ],
        capability_packs=[
            {
                "capability_pack_id": "mozaikspay",
                "pack_type": "managed_capability",
            }
        ],
        build_tasks=[
            {
                "task_id": "subscriptions.config",
                "task_type": "subscription_config",
                "capability_pack_id": None,
                "surface_id": "subscriptions",
                "surface_kind": "app_policy",
                "execution_target": "AppGenerator",
                "initial_agent": "ConfigMiddlewareAgent",
                "description": "Generate SaaS subscription config.",
                "initial_message": "Generate config/subscriptions.yaml.",
                "owned_paths": ["config/subscriptions.yaml"],
                "depends_on": [],
                "acceptance_criteria": [],
            },
            {
                "task_id": "mozaikspay.adapter",
                "task_type": "api_surface",
                "capability_pack_id": None,
                "surface_id": "mozaikspay",
                "surface_kind": "external_integration",
                "execution_target": "AppGenerator",
                "initial_agent": "ControllerAgent",
                "description": "Generate MozaiksPay client.",
                "initial_message": "Generate services/integrations/mozaikspay_client.py.",
                "owned_paths": ["services/integrations/mozaikspay_client.py"],
                "depends_on": [],
                "acceptance_criteria": [],
            },
            {
                "task_id": "mozaikspay.billing_facade_contract",
                "task_type": "module_contract",
                "capability_pack_id": "mozaikspay",
                "surface_id": "billing_portal",
                "surface_kind": "module",
                "execution_target": "AppGenerator",
                "initial_agent": "ConfigMiddlewareAgent",
                "description": "Generate billing portal facade.",
                "initial_message": "Generate modules/billing_portal/module.yaml.",
                "owned_paths": ["modules/billing_portal/module.yaml"],
                "depends_on": [],
                "acceptance_criteria": [],
            },
            {
                "task_id": "mozaikspay.pages",
                "task_type": "page_bundle",
                "capability_pack_id": "billing_portal",
                "surface_id": "billing_portal",
                "surface_kind": "ui_only",
                "execution_target": "AppGenerator",
                "initial_agent": "AppSchemaAgent",
                "description": "Generate billing and usage pages.",
                "initial_message": "Generate ui/pages/billing.yaml and ui/pages/usage.yaml.",
                "owned_paths": ["ui/pages/billing.yaml", "ui/pages/usage.yaml"],
                "depends_on": ["mozaikspay.billing_facade_contract"],
                "acceptance_criteria": [],
            },
        ],
    )


def _mozaikspay_task_outputs() -> dict[str, Any]:
    return {
        "app_shell": {
            "code_files": [
                {
                    "filename": "app.json",
                    "content": json.dumps(
                        {
                            "appId": "mozaikspay-replay",
                            "appName": "MozaiksPay Replay",
                            "version": "1.0.0",
                            "startup": {"landing_spot": "/billing"},
                        }
                    ),
                },
                {
                    "filename": "config/ai.json",
                    "content": json.dumps(
                        {
                            "chat": {"chat_startup_mode": "ask"},
                            "workflows": {"entry_point": None},
                        }
                    ),
                },
                {
                    "filename": "config/shell.json",
                    "content": json.dumps(
                        {
                            "navigation": {"autoFromPages": True},
                            "header": {"show": True},
                        }
                    ),
                },
                {"filename": "services/__init__.py", "content": ""},
                {"filename": "services/integrations/__init__.py", "content": ""},
                {"filename": "modules/billing_portal/backend/__init__.py", "content": ""},
                {"filename": "modules/reports/backend/__init__.py", "content": ""},
            ]
        },
        "subscriptions": {
            "code_files": [
                {"filename": "config/subscriptions.yaml", "content": _subscriptions_yaml()},
            ]
        },
        "adapter": {
            "code_files": [
                {
                    "filename": "services/integrations/mozaikspay_client.py",
                    "content": "import payment_provider\npayment_provider.api_key = 'provider_test_1234567890'\n",
                },
            ]
        },
        "reports": {
            "code_files": [
                {
                    "filename": "modules/reports/module.yaml",
                    "content": textwrap.dedent(
                        """
                        schema_version: mozaiks.module.v1
                        module:
                          id: reports
                          display_name: Reports
                          version: 1.0.0
                          handler: backend.handler:ReportsModule
                        actions:
                          - id: generate_report
                            description: Generate an AI report.
                            handler_method: generate_report
                            entitlement_gate: reports.generate
                            input_schema:
                              type: object
                              required: [topic]
                              properties:
                                topic:
                                  type: string
                            output_schema:
                              type: object
                              required: [report_id]
                              properties:
                                report_id:
                                  type: string
                                topic:
                                  type: string
                        capabilities:
                          - capability_id: reports.generate
                            kind: action
                            target: generate_report
                            title: Generate Report
                        """
                    ).lstrip(),
                },
                {
                    "filename": "modules/reports/backend/handler.py",
                    "content": textwrap.dedent(
                        """
                        class ReportsModule:
                            async def generate_report(self, ctx, *, topic):
                                return {"report_id": "report_1", "topic": topic}
                        """
                    ).lstrip(),
                },
            ]
        },
        "pages": {
            "code_files": [
                {
                    "filename": "ui/pages/billing.yaml",
                    "content": (
                        "schema_version: mozaiks.app_page.v1\n"
                        "name: Billing\n"
                        "route: /billing\n"
                        "title: Billing\n"
                        "page_type: analytics_dashboard\n"
                        "layout: full-width\n"
                        "shell_mode: workspace\n"
                        "sections:\n"
                        "  - id: billing-status\n"
                        "    primitive: SummaryStrip\n"
                        "    config:\n"
                        "      items:\n"
                        "        - label: Status\n"
                        "          value_key: status\n"
                    ),
                },
                {
                    "filename": "ui/pages/usage.yaml",
                    "content": (
                        "schema_version: mozaiks.app_page.v1\n"
                        "name: Usage\n"
                        "route: /usage\n"
                        "title: Usage\n"
                        "page_type: analytics_dashboard\n"
                        "layout: full-width\n"
                        "shell_mode: workspace\n"
                        "sections:\n"
                        "  - id: usage-status\n"
                        "    primitive: SummaryStrip\n"
                        "    config:\n"
                        "      items:\n"
                        "        - label: Usage\n"
                        "          value_key: runtime_ai_usage\n"
                    ),
                },
            ]
        },
    }


@pytest.mark.asyncio
async def test_managed_wallet_replay_normalizes_assembles_and_scans(tmp_path: Path) -> None:
    wallet_descriptor = _write_wallet_pack(tmp_path)
    ctx = _Context(
        {
            "app_id": "managed-wallet-replay",
            "capability_packs": [wallet_descriptor],
            "app_task_batch_results": _wallet_task_outputs(),
        }
    )

    app_build_plan(AppBuildPlan=_wallet_replay_plan(), context_variables=ctx)
    cached_plan = ctx.get("app_build_plan")

    adapter_task = next(
        task for task in cached_plan["build_tasks"] if task["task_id"] == "wallet.adapter"
    )
    facade_task = next(
        task for task in cached_plan["build_tasks"] if task["task_id"] == "wallet.facade_contract"
    )
    page = cached_plan["pages"][0]

    assert adapter_task["capability_pack_id"] == "wallet"
    assert facade_task["capability_pack_id"] == "wallet_dashboard"
    assert "wallet.adapter" in facade_task["depends_on"]
    assert page["sections_hint"][0]["config_hint"]["api_endpoint"] == "/api/modules/wallet_dashboard/get_wallet_summary"

    assembled = await assemble_app_tasks(context_variables=ctx)
    files = _file_map(assembled)

    assert "services/integrations/wallet_client.py" in files
    assert "import payment_provider" not in files["services/integrations/wallet_client.py"]
    assert not any(path.startswith("modules/wallet/") for path in files)
    assert "/api/modules/wallet_dashboard/get_wallet_summary" in files["ui/pages/wallet.yaml"]
    assert scan_generated_bundle(files, capability_packs=cached_plan["capability_packs"]) == []


@pytest.mark.asyncio
async def test_mozaikspay_replay_uses_templates_and_passes_runtime_acceptance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    descriptor, contract = _load_mozaikspay_descriptor()
    ctx = _Context(
        {
            "app_id": "mozaikspay-replay",
            "capability_packs": [descriptor],
            "operator_contracts": [contract],
            "app_task_batch_results": _mozaikspay_task_outputs(),
        }
    )

    app_build_plan(AppBuildPlan=_mozaikspay_replay_plan(), context_variables=ctx)
    cached_plan = ctx.get("app_build_plan")

    adapter_task = next(
        task for task in cached_plan["build_tasks"] if task["task_id"] == "mozaikspay.adapter"
    )
    facade_task = next(
        task for task in cached_plan["build_tasks"] if task["task_id"] == "mozaikspay.billing_facade_contract"
    )

    assert adapter_task["capability_pack_id"] == "mozaikspay"
    assert facade_task["capability_pack_id"] == "billing_portal"
    assert cached_plan["pages"][0]["page_type_hint"] == "analytics_dashboard"
    assert cached_plan["pages"][1]["page_type_hint"] == "analytics_dashboard"
    assert cached_plan["pages"][0]["sections_hint"][0]["config_hint"]["api_endpoint"] == (
        "/api/modules/billing_portal/get_subscription_status"
    )
    assert cached_plan["pages"][1]["sections_hint"][0]["config_hint"]["api_endpoint"] == (
        "/api/modules/billing_portal/get_usage_status"
    )

    assembled = await assemble_app_tasks(context_variables=ctx)
    files = _file_map(assembled)
    deployment_env = _deployment_env_for_capability_packs(ctx.get("capability_packs"))
    files.update(
        generate_deployment_artifacts(
            app_id="mozaikspay-replay",
            deployment_profile="generic_container",
            include_dockerfiles=True,
            include_workflow=False,
            include_compose=False,
            extra_required_variables=deployment_env["required"],
            extra_optional_variables=deployment_env["optional"],
            extra_secret_variables=deployment_env["secret"],
            extra_public_variables=deployment_env["public"],
        )["artifacts"]
    )

    assert "config/subscriptions.yaml" in files
    assert "services/integrations/mozaikspay_client.py" in files
    assert "modules/billing_portal/module.yaml" in files
    assert "modules/reports/module.yaml" in files
    assert "ui/pages/billing.yaml" in files
    assert "ui/pages/pricing.yaml" in files
    assert "ui/pages/usage.yaml" in files
    assert ".env.example" in files
    assert "import payment_provider" not in files["services/integrations/mozaikspay_client.py"]
    assert not any(path.startswith("modules/mozaikspay/") for path in files)
    assert not any(path.startswith("modules/wallet/") for path in files)
    assert "/api/modules/billing_portal/get_subscription_status" in files["ui/pages/billing.yaml"]
    assert "/api/modules/billing_portal/list_plans" in files["ui/pages/pricing.yaml"]
    assert "/api/modules/billing_portal/open_billing_portal" in files["ui/pages/pricing.yaml"]
    assert "/api/modules/mozaikspay/" not in files["ui/pages/billing.yaml"]
    assert "page_type: analytics_dashboard" in files["ui/pages/billing.yaml"]
    assert "page_type: analytics_dashboard" in files["ui/pages/usage.yaml"]
    for name in (
        "MOZAIKS_APP_URL",
        "MOZAIKSPAY_API_BASE",
        "MOZAIKSPAY_CLIENT_ID",
        "MOZAIKSPAY_CLIENT_SECRET",
        "MOZAIKSPAY_API_KEY",
    ):
        assert f"{name}=" in files[".env.example"]
    assert scan_generated_bundle(files, capability_packs=cached_plan["capability_packs"]) == []

    validation = await validate_app_bundle_from_request(
        {
            "validation_strategy": "skip",
            "start_dev_server": False,
        },
        context_variables=ctx,
    )

    assert validation["status"] == "success"
    assert validation["app_bundle_acceptance_result"]["status"] == "passed"
    assert ctx.get("app_bundle_acceptance_status") == "passed"
    assert ctx.get("bundle_scan_result")["passed"] is True
    assert ctx.get("wiring_validation_result")["passed"] is True
    assert ctx.get("module_implementation_validation_result")["passed"] is True
    assert ctx.get("module_runtime_quality_result")["passed"] is True
    assert ctx.get("app_runtime_load_result")["passed"] is True

    app_root = tmp_path / "app"
    _write_files(app_root, files)
    monkeypatch.setenv("PLATFORM_PATH", str(app_root))
    loaded = await AppLoader.load(str(app_root))

    assert loaded.definition.name == "MozaiksPay Replay"
    assert [module.name for module in loaded.modules] == ["billing_portal", "reports"]
    assert loaded.failed_module_names == []
    assert [page.name for page in loaded.definition.pages] == ["billing", "pricing", "usage"]
    assert loaded.subscriptions_config is not None
    assert loaded.subscriptions_config.default_plan_id == "free"
    wallet = loaded.subscriptions_config.token_wallet_by_id("ai_tokens")
    assert wallet is not None
    assert wallet.depleted_balance is not None
    assert wallet.depleted_balance.billing_route == "/billing"

    assignments = _Collection([])
    adapter = ConfiguredEntitlementAdapter(
        config=loaded.subscriptions_config,
        collection_resolver=lambda alias: assignments,
    )
    executor = ModuleExecutor(entitlement_checker=adapter)
    monkeypatch.setattr(
        "mozaiksai.core.runtime.composition.module_executor.get_audit_logger",
        lambda: _NoopAuditLogger(),
    )
    # Freeze wallet "now" to July 2026 so ensure_plan_allowances uses the same
    # monthly period key (2026-07) as the fulfillment command's occurred_at,
    # preventing a spurious second allocation when the test runs in a later month.
    monkeypatch.setattr(
        "mozaiksai.core.tokens.wallet._now",
        lambda: datetime(2026, 7, 15, tzinfo=UTC),
    )
    for module in loaded.modules:
        executor.register(
            module.name,
            module.handler,
            action_method_map=module.action_method_map,
            action_permissions=module.action_permissions_map,
            action_schemas=module.action_schemas_map,
            action_entitlements=module.action_entitlement_map,
        )

    denied = await executor.execute(
        ModuleRequest(
            module="reports",
            action="generate_report",
            params={"topic": "retention"},
            app_id="mozaikspay-replay",
            user_id="user_1",
            granted_permissions=[],
        )
    )
    assert denied.success is False
    assert denied.error_code == "ENTITLEMENT_REQUIRED"

    plan_catalog = await executor.execute(
        ModuleRequest(
            module="billing_portal",
            action="list_plans",
            params={},
            app_id="mozaikspay-replay",
            user_id="user_1",
            granted_permissions=["billing_portal.read"],
        )
    )
    assert plan_catalog.success is True
    assert [plan["plan_id"] for plan in plan_catalog.data["plans"]] == ["free", "pro"]
    assert plan_catalog.data["top_up_products"][0]["product_id"] == "ai_tokens_10k"

    ledger = TokenWalletLedger(database=_Database())
    monkeypatch.setattr(
        "mozaiksai.core.tokens.wallet.get_token_wallet_ledger",
        lambda: ledger,
    )
    fulfillment = BillingFulfillmentService(
        config=loaded.subscriptions_config,
        ledger=ledger,
        collection_resolver=lambda alias: assignments,
    )
    fulfillment_result = await fulfillment.apply(
        BillingFulfillmentCommand(
            command_id="cmd_mozaikspay_replay_activate_1",
            event_type="subscription_activated",
            source="test",
            app_id="mozaikspay-replay",
            user_id="user_1",
            plan_id="pro",
            occurred_at=datetime(2026, 7, 1, tzinfo=UTC),
        )
    )
    assert fulfillment_result.success is True

    granted = await executor.execute(
        ModuleRequest(
            module="reports",
            action="generate_report",
            params={"topic": "retention"},
            app_id="mozaikspay-replay",
            user_id="user_1",
            granted_permissions=[],
        )
    )
    assert granted.success is True
    assert granted.data == {"report_id": "report_1", "topic": "retention"}

    tokens_payload = await _current_user_token_wallet_summary(
        loaded.subscriptions_config,
        app_id="mozaikspay-replay",
        user_id="user_1",
        ensure_allowances=False,
    )
    assert tokens_payload["wallets"][0]["wallet_id"] == "ai_tokens"
    assert tokens_payload["wallets"][0]["balance"]["balance"] == 1000

    token_guard = TokenUsageGuard(
        config=loaded.subscriptions_config,
        ledger=ledger,
        collection_resolver=lambda alias: assignments,
    )
    llm_service = SimpleLLMCapabilityService(token_usage_guard=token_guard)
    fake_client = _FakeLLMClient()
    llm_service._client = fake_client  # type: ignore[assignment]
    llm_service._select_provider = _fake_provider  # type: ignore[method-assign]

    llm_result = await llm_service.generate_chat_completion(
        messages=[{"role": "user", "content": "Write a short retention report."}],
        llm_config={"model": "stub-generated-saas-model"},
        app_id="mozaikspay-replay",
        user_id="user_1",
    )
    assert llm_result["content"] == "stubbed generated SaaS report"
    assert len(fake_client.calls) == 1

    await ledger.record_usage_debit(
        {
            "event_id": "usage_evt_mozaikspay_report_1",
            "app_id": "mozaikspay-replay",
            "user_id": "user_1",
            "total_tokens": llm_result["usage"]["total_tokens"],
        },
        wallet=wallet,
    )
    balance = await ledger.query_balance(app_id="mozaikspay-replay", user_id="user_1")
    assert balance["balance"] == 875

    await ledger.record_usage_debit(
        {
            "event_id": "usage_evt_mozaikspay_report_exhaust",
            "app_id": "mozaikspay-replay",
            "user_id": "user_1",
            "total_tokens": 875,
        },
        wallet=wallet,
    )

    calls_before_denial = len(fake_client.calls)
    with pytest.raises(TokenUsageDenied) as exc_info:
        await llm_service.generate_chat_completion(
            messages=[{"role": "user", "content": "This should not reach the provider."}],
            llm_config={"model": "stub-generated-saas-model"},
            app_id="mozaikspay-replay",
            user_id="user_1",
        )
    decision = exc_info.value.decision
    assert decision.error_code == "INSUFFICIENT_TOKENS"
    assert decision.wallet_id == "ai_tokens"
    assert decision.balance == 0
    assert decision.recovery_action == "top_up"
    assert decision.billing_route == "/billing"
    assert decision.top_up_route == "/billing"
    assert decision.upgrade_route == "/pricing"
    assert decision.top_up_product_ids == ("ai_tokens_10k",)
    assert len(fake_client.calls) == calls_before_denial
