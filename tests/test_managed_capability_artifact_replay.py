from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.app_build_plan import app_build_plan
from factory_app.workflows.AppGenerator.tools.app_validation import validate_app_bundle_from_request
from factory_app.workflows.AppGenerator.tools.assemble_app_tasks import assemble_app_tasks
from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import scan_generated_bundle
from mozaiksai.core.runtime.app.loader import AppLoader

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
                "pack": {"id": "wallet", "status": "active", "capability_source": "managed_capability"},
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


def _base_plan(*, pages: list[dict[str, Any]], capability_packs: list[dict[str, Any]], build_tasks: list[dict[str, Any]]) -> dict[str, Any]:
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
                        "primitive": "SummaryStrip",
                        "config_hint": {
                            "api_endpoint": "/api/modules/wallet/get_wallet_summary",
                            "method": "POST",
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
                    "content": "import stripe\nstripe.api_key = 'sk_test_1234567890'\n",
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
                    "content": "sections:\n  - config:\n      api_endpoint: /api/modules/wallet/get_wallet_summary\n",
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
        plans:
          - plan_id: free
            label: Free
            capabilities: [billing_portal.read]
          - plan_id: pro
            label: Pro
            capabilities: [billing_portal.read, billing_portal.manage]
            usage_limits:
              - meter_id: ai_tokens
                label: AI tokens
                unit: tokens
                monthly_limit: 1000
                capability_id: billing_portal.manage
            token_allowances:
              - wallet_id: ai_tokens
                amount: 1000
                cadence: monthly
                label: Monthly AI tokens
        """
    ).lstrip()


def _mozaikspay_replay_plan() -> dict[str, Any]:
    return _base_plan(
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
                        "primitive": "DataPanel",
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
                "capability_pack_id": "subscriptions",
                "surface_id": "subscriptions",
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
                            "workflows": {"entry_point": None, "resume_policy": "manual"},
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
                    "content": "import stripe\nstripe.api_key = 'sk_test_1234567890'\n",
                },
            ]
        },
        "pages": {
            "code_files": [
                {
                    "filename": "ui/pages/billing.yaml",
                    "content": "sections:\n  - config:\n      api_endpoint: /api/modules/mozaikspay/get_subscription_status\n",
                },
                {
                    "filename": "ui/pages/usage.yaml",
                    "content": "sections:\n  - config:\n      api_endpoint: /api/modules/mozaikspay/get_usage_status\n",
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
    assert "import stripe" not in files["services/integrations/wallet_client.py"]
    assert not any(path.startswith("modules/wallet/") for path in files)
    assert "/api/modules/wallet_dashboard/get_wallet_summary" in files["ui/pages/wallet.yaml"]
    assert scan_generated_bundle(files, capability_packs=cached_plan["capability_packs"]) == []


@pytest.mark.asyncio
async def test_mozaikspay_replay_uses_templates_and_passes_runtime_acceptance(tmp_path: Path) -> None:
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
    assert cached_plan["pages"][0]["sections_hint"][0]["config_hint"]["api_endpoint"] == (
        "/api/modules/billing_portal/get_subscription_status"
    )
    assert cached_plan["pages"][1]["sections_hint"][0]["config_hint"]["api_endpoint"] == (
        "/api/modules/billing_portal/get_usage_status"
    )

    assembled = await assemble_app_tasks(context_variables=ctx)
    files = _file_map(assembled)

    assert "config/subscriptions.yaml" in files
    assert "services/integrations/mozaikspay_client.py" in files
    assert "modules/billing_portal/module.yaml" in files
    assert "ui/pages/billing.yaml" in files
    assert "ui/pages/usage.yaml" in files
    assert "import stripe" not in files["services/integrations/mozaikspay_client.py"]
    assert not any(path.startswith("modules/mozaikspay/") for path in files)
    assert "/api/modules/billing_portal/get_subscription_status" in files["ui/pages/billing.yaml"]
    assert "/api/modules/mozaikspay/" not in files["ui/pages/billing.yaml"]
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
    loaded = await AppLoader.load(str(app_root))

    assert loaded.definition.name == "MozaiksPay Replay"
    assert [module.name for module in loaded.modules] == ["billing_portal"]
    assert loaded.failed_module_names == []
    assert [page.name for page in loaded.definition.pages] == ["billing", "usage"]
    assert loaded.subscriptions_config is not None
    assert loaded.subscriptions_config.default_plan_id == "free"
