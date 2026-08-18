from __future__ import annotations

# ruff: noqa: E402
import argparse
import asyncio
import json
import os
import re
import sys
import tempfile
import textwrap
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory_app.workflows.AppGenerator.tools.app_validation import run_app_bundle_acceptance_gate
from factory_app.workflows.AppGenerator.tools.export_app_code import resolve_export_gate
from factory_app.workflows.AppGenerator.tools.validate_wiring import validate_wiring
from mozaiksai.core.runtime.app.loader import AppLoader
from mozaiksai.core.runtime.app.subscriptions_loader import SubscriptionsConfig
from scripts.smoke_appgenerator_live_acceptance import SmokeContext

DEFAULT_APP_ID = "subscription-reporting-live-smoke"
WORKFLOWS_ROOT = REPO_ROOT / "factory_app" / "workflows"
SUBSCRIPTION_PATH = "config/subscriptions.yaml"
MODULE_PATH = "modules/reports/module.yaml"

_PY_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_FORBIDDEN_PROVIDER_TERMS = (
    "mozaikspay",
    "price_id",
    "payment_intent",
    "checkout",
    "invoice",
    "provider_customer_id",
    "hosted_billing",
    "contracts/subscriptions.yaml",
    "tokenwalletledger",
    "record_usage_debit",
    "ensure_plan_allowances",
)


def _configure_event_loop_policy() -> None:
    if os.name != "nt":
        return
    selector_policy = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if selector_policy is not None:
        asyncio.set_event_loop_policy(selector_policy())


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump(mode="python"))
    return str(value)


def _yaml_text(value: dict[str, Any]) -> str:
    return yaml.safe_dump(
        value,
        sort_keys=False,
        allow_unicode=False,
        default_flow_style=False,
    ).strip() + "\n"


def sample_subscription_contract() -> dict[str, Any]:
    return {
        "contract_required": True,
        "rationale": "The app sells plan-gated AI report generation and grants monthly AI token allowances.",
        "app_name": "Subscription Reporting",
        "subscription_config_file": {
            "schema_version": "mozaiks.subscriptions.v1",
            "label": "Subscription Reporting Plans",
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
                    "capabilities": ["reports.view"],
                    "token_allowances": [],
                },
                {
                    "plan_id": "pro",
                    "label": "Pro",
                    "capabilities": ["reports.view", "reports.generate"],
                    "usage_limits": [
                        {
                            "meter_id": "ai_tokens",
                            "label": "AI tokens",
                            "unit": "tokens",
                            "monthly_limit": 1000,
                            "capability_id": "reports.generate",
                        }
                    ],
                    "token_allowances": [
                        {
                            "wallet_id": "ai_tokens",
                            "amount": 1000,
                            "cadence": "monthly",
                            "label": "Monthly AI tokens",
                        }
                    ],
                },
            ],
        },
        "module_contract_updates": [
            {
                "module_id": "reports",
                "action_id": "generate_report",
                "entitlement_gate": "reports.generate",
            }
        ],
        "metering_declarations": [
            {
                "meter_id": "ai_tokens",
                "wallet_id": "ai_tokens",
                "unit": "tokens",
                "scope": "user",
                "consumed_by": ["reports.generate"],
            }
        ],
        "page_surface_requirements": [
            {
                "page_id": "usage",
                "required_platform_endpoints": [
                    "/api/me/usage",
                    "/api/me/tokens",
                    "/api/me/tokens/ledger",
                ],
            }
        ],
        "app_generator_instructions": [
            "Emit one config/subscriptions.yaml file from subscription_config_file.",
            "Set reports.generate entitlement_gate on modules/reports/module.yaml generate_report.",
            "Usage pages read platform-owned /api/me/usage and /api/me/tokens endpoints.",
        ],
        "validation_notes": [
            "No MozaiksPay resources, checkout routes, invoices, or custom token ledgers belong in the generated app bundle.",
        ],
    }


def _subscription_task() -> dict[str, Any]:
    return {
        "task_id": "task_subscription_config",
        "task_type": "subscription_config",
        "capability_pack_id": None,
        "surface_id": "subscription_contract",
        "surface_kind": "refinement",
        "execution_target": "app_bundle",
        "initial_agent": "ConfigMiddlewareAgent",
        "description": "Materialize the provider-neutral subscription plan catalog.",
        "initial_message": (
            "Serialize only subscription_contract.subscription_config_file to "
            "config/subscriptions.yaml. Emit no module files, backend Python, "
            "MozaiksPay resources, checkout behavior, invoice logic, or token ledger code."
        ),
        "owned_paths": [SUBSCRIPTION_PATH],
        "depends_on": [],
        "acceptance_criteria": [
            "config/subscriptions.yaml validates as mozaiks.subscriptions.v1.",
            "The generated file contains no provider-specific payment or ledger implementation.",
        ],
    }


def _module_contract_task() -> dict[str, Any]:
    return {
        "task_id": "task_reports_module_contract",
        "task_type": "module_contract",
        "capability_pack_id": "reports",
        "surface_id": "reports",
        "surface_kind": "module",
        "execution_target": "app_bundle",
        "initial_agent": "ConfigMiddlewareAgent",
        "description": "Declare the reports module contract with SaaS entitlement gates.",
        "initial_message": textwrap.dedent(
            """
            Generate only the reports module YAML contract for a SaaS reporting app.
            Module id: reports.
            Module handler: backend.handler:ReportsModule.
            Actions:
            - list_reports: handler_method list_reports, no entitlement_gate, output object with reports array.
            - generate_report: handler_method generate_report, input topic string, output report_id and topic strings.
            Capabilities:
            - reports.view grants list_reports.
            - reports.generate grants generate_report.
            Subscription contract update is authoritative: set generate_report entitlement_gate to reports.generate exactly.
            No domain events or workflow trigger events are declared for this task; actions[].emits must be empty and events_yaml.events must be empty.
            module_contract must be a ModuleContractBundle wrapper: put module.yaml fields under module_contract.module_yaml, not directly under module_contract.
            Emit no backend Python files.
            """
        ).strip(),
        "owned_paths": [
            MODULE_PATH,
            "modules/reports/contracts/events.yaml",
            "modules/reports/contracts/reactions.yaml",
            "modules/reports/contracts/notifications.yaml",
            "modules/reports/contracts/settings.yaml",
            "modules/reports/contracts/admin.yaml",
        ],
        "depends_on": ["task_subscription_config"],
        "acceptance_criteria": [
            "modules/reports/module.yaml declares generate_report entitlement_gate: reports.generate.",
            "No backend Python files are emitted by the module_contract task.",
        ],
    }


def _build_plan(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "agent_message": "Plan ready for subscription smoke.",
        "app_kind": "saas",
        "pages": [
            {
                "name": "Reports",
                "route": "/reports",
                "purpose": "List and generate AI reports.",
            },
            {
                "name": "Usage",
                "route": "/usage",
                "purpose": "Review usage and token balances.",
            },
        ],
        "entities": [{"name": "Report", "operations": ["read", "create"], "notes": None}],
        "roles": ["user"],
        "auth_strategy": "basic",
        "service_scope": ["reports"],
        "frontend_scope": ["reports", "usage"],
        "capability_packs": [
            {
                "capability_pack_id": "reports",
                "surface_id": "reports",
                "surface_kind": "module",
                "label": "Reports",
                "operations": ["list_reports", "generate_report"],
            }
        ],
        "external_integrations": [],
        "agent_backend_required": False,
        "build_tasks": tasks,
        "generation_order": [task["task_id"] for task in tasks],
    }


def _task_context(task: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    tasks = [_subscription_task(), _module_contract_task()]
    return {
        "workflow_name": "AppGenerator",
        "app_id": DEFAULT_APP_ID,
        "task_run_mode": True,
        "current_build_task_id": task["task_id"],
        "current_build_task_type": task["task_type"],
        "current_build_task": task,
        "subscription_contract": contract,
        "app_build_plan": _build_plan(tasks),
    }


def _collect_file_map(payload: dict[str, Any]) -> dict[str, str]:
    files: dict[str, str] = {}

    def add_items(items: Any) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("filename") or item.get("path") or "").replace("\\", "/").strip()
            if name:
                files[name] = str(item.get("content") or "")

    add_items(payload.get("code_files"))
    bundle = payload.get("subscription_config_bundle")
    if isinstance(bundle, dict):
        add_items(bundle.get("files"))
    service_bundle = payload.get("service_foundation_bundle")
    if isinstance(service_bundle, dict):
        add_items(service_bundle.get("files"))
    return files


def _forbidden_drift_errors(files: dict[str, str]) -> list[str]:
    errors: list[str] = []
    for path, content in files.items():
        lowered = f"{path}\n{content}".lower()
        for term in _FORBIDDEN_PROVIDER_TERMS:
            if term in lowered:
                errors.append(f"{path} contains forbidden provider/runtime drift term {term!r}.")
    return errors


def _extract_json_object_from_text(value: Any) -> dict[str, Any]:
    if not isinstance(value, str) or not value.strip():
        return {}
    stripped = value.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped, flags=re.IGNORECASE).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end < start:
        return {}
    try:
        parsed = json.loads(stripped[start : end + 1])
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _coerce_structured_output(content: Any, response_schema: Any) -> dict[str, Any]:
    if hasattr(content, "model_dump"):
        return content.model_dump(mode="json")
    if isinstance(content, dict):
        try:
            return response_schema.model_validate(content).model_dump(mode="json")
        except Exception as exc:
            raw = dict(content)
            raw["_schema_validation_error"] = str(exc)
            return raw
    parsed = _extract_json_object_from_text(content)
    if parsed:
        try:
            return response_schema.model_validate(parsed).model_dump(mode="json")
        except Exception as exc:
            parsed["_schema_validation_error"] = str(exc)
            return parsed
    return {}


def _structured_output_errors(output: dict[str, Any], *, mode_label: str) -> list[str]:
    errors: list[str] = []
    schema_error = output.get("_schema_validation_error")
    if schema_error:
        errors.append(f"{mode_label} structured output failed ConfigMiddlewareOutput validation: {schema_error}")
    if "agent_message" not in output:
        errors.append(f"{mode_label} output must include required top-level agent_message.")
    return errors


async def _render_agent_system_prompt(agent: Any, context: Any) -> str:
    class PromptCapture:
        def __init__(self, name: str, context_variables: Any, base_message: str) -> None:
            self.name = name
            self.context_variables = context_variables
            self.system_message = base_message
            self._system_message = base_message
            self._captured: str | None = None

        def update_system_message(self, message: str) -> None:
            self.system_message = message
            self._system_message = message
            self._captured = message

    base_message = str(getattr(agent, "_mozaiks_base_system_message", "") or "")
    capture = PromptCapture("ConfigMiddlewareAgent", context, base_message)
    for middleware_fn in getattr(agent, "_mozaiks_prompt_middleware", []) or []:
        result = middleware_fn(capture, [])
        if hasattr(result, "__await__"):
            await result
    return capture._captured or capture.system_message or base_message


def validate_subscription_output(
    output: dict[str, Any],
    contract: dict[str, Any],
) -> tuple[str | None, list[str]]:
    errors: list[str] = _structured_output_errors(output, mode_label="subscription_config")
    if output.get("mode") != "subscription_config":
        errors.append(f"Expected subscription_config mode, got {output.get('mode')!r}.")
    if output.get("module_contract") is not None:
        errors.append("subscription_config mode must not emit module_contract.")
    if output.get("service_foundation_bundle") is not None:
        errors.append("subscription_config mode must not emit service_foundation_bundle.")

    files = _collect_file_map(output)
    errors.extend(_forbidden_drift_errors(files))
    content = files.get(SUBSCRIPTION_PATH)
    if not content:
        errors.append(f"Missing {SUBSCRIPTION_PATH}.")
        return None, errors

    bundle = output.get("subscription_config_bundle")
    bundle_files = []
    if isinstance(bundle, dict) and isinstance(bundle.get("files"), list):
        bundle_files = bundle["files"]
    if len(bundle_files) != 1:
        errors.append("subscription_config_bundle.files must contain exactly one file.")

    try:
        parsed = yaml.safe_load(content)
    except Exception as exc:
        errors.append(f"{SUBSCRIPTION_PATH} is not valid YAML: {exc}")
        return content, errors
    if not isinstance(parsed, dict):
        errors.append(f"{SUBSCRIPTION_PATH} must parse to a YAML object.")
        return content, errors

    allowed = {
        "schema_version",
        "label",
        "default_plan_id",
        "assignment_store",
        "token_wallets",
        "plans",
    }
    extra = sorted(set(parsed) - allowed)
    if extra:
        errors.append(f"{SUBSCRIPTION_PATH} contains non-OSS subscription keys: {extra}.")

    expected = contract.get("subscription_config_file")
    if parsed != expected:
        errors.append("Generated subscription config drifted from subscription_contract.subscription_config_file.")

    try:
        SubscriptionsConfig.model_validate(parsed)
    except Exception as exc:
        errors.append(f"{SUBSCRIPTION_PATH} failed SubscriptionsConfig validation: {exc}")
    return content, errors


def _module_yaml_from_output(output: dict[str, Any], files: dict[str, str]) -> str | None:
    if files.get(MODULE_PATH):
        return files[MODULE_PATH]
    module_contract = output.get("module_contract")
    if not isinstance(module_contract, dict):
        return None
    module_yaml = module_contract.get("module_yaml")
    if not isinstance(module_yaml, dict):
        return None
    return _yaml_text(module_yaml)


def validate_module_contract_output(output: dict[str, Any]) -> tuple[str | None, list[str]]:
    errors: list[str] = _structured_output_errors(output, mode_label="module_contract_bundle")
    if output.get("mode") != "module_contract_bundle":
        errors.append(f"Expected module_contract_bundle mode, got {output.get('mode')!r}.")
    if output.get("service_foundation_bundle") is not None:
        errors.append("module_contract_bundle mode must not emit service_foundation_bundle.")
    if output.get("subscription_config_bundle") is not None:
        errors.append("module_contract_bundle mode must not emit subscription_config_bundle.")

    files = _collect_file_map(output)
    errors.extend(_forbidden_drift_errors(files))
    backend_files = sorted(path for path in files if path.startswith("modules/reports/backend/") and path.endswith(".py"))
    if backend_files:
        errors.append(f"module_contract task emitted backend Python files: {backend_files}.")

    content = _module_yaml_from_output(output, files)
    if not content:
        errors.append(f"Missing {MODULE_PATH}.")
        return None, errors

    try:
        parsed = yaml.safe_load(content)
    except Exception as exc:
        errors.append(f"{MODULE_PATH} is not valid YAML: {exc}")
        return content, errors
    if not isinstance(parsed, dict):
        errors.append(f"{MODULE_PATH} must parse to a YAML object.")
        return content, errors

    module = parsed.get("module")
    if not isinstance(module, dict) or module.get("id") != "reports":
        errors.append("modules/reports/module.yaml must declare module.id: reports.")

    actions = parsed.get("actions")
    if not isinstance(actions, list):
        errors.append("modules/reports/module.yaml must declare actions[].")
        return content, errors

    by_id = {str(action.get("id")): action for action in actions if isinstance(action, dict)}
    generate = by_id.get("generate_report")
    if not isinstance(generate, dict):
        errors.append("reports module must declare generate_report.")
    elif generate.get("entitlement_gate") != "reports.generate":
        errors.append("generate_report must copy entitlement_gate: reports.generate exactly.")

    list_reports = by_id.get("list_reports")
    if not isinstance(list_reports, dict):
        errors.append("reports module must declare list_reports.")
    elif list_reports.get("entitlement_gate") not in (None, ""):
        errors.append("list_reports must not inherit the generate_report entitlement gate.")

    for action_id, action in by_id.items():
        handler_method = str(action.get("handler_method") or "").strip()
        if handler_method != action_id:
            errors.append(f"Action {action_id!r} must use handler_method equal to its id.")
        if handler_method and not _PY_IDENTIFIER_RE.match(handler_method):
            errors.append(f"Action {action_id!r} has invalid Python handler_method {handler_method!r}.")
        for event_type in action.get("emits") or []:
            event_text = str(event_type or "").strip()
            if event_text and not event_text.startswith("domain."):
                errors.append(
                    f"Action {action_id!r} emits non-canonical event {event_text!r}; generated app events must use domain.*."
                )

    return content, errors


def deterministic_subscription_output() -> dict[str, Any]:
    content = _yaml_text(sample_subscription_contract()["subscription_config_file"])
    return {
        "mode": "subscription_config",
        "module_contract": None,
        "service_foundation_bundle": None,
        "subscription_config_bundle": {
            "files": [
                {
                    "filename": SUBSCRIPTION_PATH,
                    "content": content,
                }
            ]
        },
        "code_files": [
            {
                "filename": SUBSCRIPTION_PATH,
                "content": content,
            }
        ],
        "agent_message": "Generated provider-neutral subscription config.",
    }


def deterministic_module_contract_output() -> dict[str, Any]:
    module_yaml = textwrap.dedent(
        """
        schema_version: mozaiks.module.v1
        module:
          id: reports
          display_name: Reports
          version: 1.0.0
          type: standard
          description: AI report generation.
          owner: app
          visibility: internal
          handler: backend.handler:ReportsModule
        permissions: []
        actions:
          - id: list_reports
            description: List reports visible to the current user.
            handler_method: list_reports
            input_schema:
              type: object
              properties: {}
            output_schema:
              type: object
              properties:
                reports:
                  type: array
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
              required: [report_id, topic]
              properties:
                report_id:
                  type: string
                topic:
                  type: string
        capabilities:
          - capability_id: reports.view
            kind: action
            target: list_reports
            title: View reports
          - capability_id: reports.generate
            kind: action
            target: generate_report
            title: Generate reports
        """
    ).strip() + "\n"
    return {
        "mode": "module_contract_bundle",
        "module_contract": {
            "module_id": "reports",
            "module_yaml": yaml.safe_load(module_yaml),
            "events_yaml": {"schema_version": "mozaiks.events.v1", "events": []},
            "reactions_yaml": {"schema_version": "mozaiks.reactions.v1", "reactions": []},
            "notifications_yaml": {"schema_version": "mozaiks.notifications.v1", "rules": []},
            "settings_yaml": {"schema_version": "mozaiks.settings.v1", "settings": [], "features": []},
            "admin_yaml": {"schema_version": "mozaiks.admin.v2", "panels": []},
            "profile_yaml": None,
            "python_stubs": [],
            "js_stubs": [],
            "runtime_extensions_yaml": None,
        },
        "service_foundation_bundle": None,
        "subscription_config_bundle": None,
        "code_files": [{"filename": MODULE_PATH, "content": module_yaml}],
        "agent_message": "Generated module contract.",
    }


def _handler_class(module_data: dict[str, Any]) -> str:
    handler = str((module_data.get("module") or {}).get("handler") or "backend.handler:ReportsModule")
    class_name = handler.rsplit(":", 1)[-1].strip() or "ReportsModule"
    class_name = re.sub(r"[^A-Za-z0-9_]", "", class_name)
    if not class_name or class_name[0].isdigit():
        return "ReportsModule"
    return class_name


def _action_methods(module_data: dict[str, Any]) -> list[str]:
    methods: list[str] = []
    for action in module_data.get("actions") or []:
        if not isinstance(action, dict):
            continue
        method = str(action.get("handler_method") or action.get("id") or "").strip()
        if method and _PY_IDENTIFIER_RE.match(method) and method not in methods:
            methods.append(method)
    return methods or ["list_reports", "generate_report"]


def _backend_files(module_yaml: str) -> dict[str, str]:
    module_data = yaml.safe_load(module_yaml) or {}
    class_name = _handler_class(module_data)
    methods = _action_methods(module_data)
    handler_source = "from .service import ReportsService\n\n\n"
    handler_source += f"class {class_name}:\n"
    for method in methods:
        handler_source += (
            f"    async def {method}(self, ctx, **params):\n"
            f"        return await ReportsService(ctx).{method}(**params)\n\n"
        )
    generic_methods = "\n\n".join(
        textwrap.indent(
            f"async def {method}(self, **params):\n"
            f"    return {{\"action\": \"{method}\", \"params\": dict(params)}}\n",
            "    ",
        ).rstrip()
        for method in methods
        if method not in {"list_reports", "generate_report"}
    )
    service_generic = f"\n\n{generic_methods}" if generic_methods else ""

    return {
        "modules/reports/backend/__init__.py": "",
        "modules/reports/backend/handler.py": handler_source.strip() + "\n",
        "modules/reports/backend/service.py": textwrap.dedent(
            f"""
            from .repo import ReportsRepo
            from .schemas import report_document


            class ReportsService:
                def __init__(self, ctx):
                    self.ctx = ctx
                    self.repo = ReportsRepo(ctx)

                async def list_reports(self, **params):
                    return {{"reports": await self.repo.list_reports(user_id=params.get("user_id"))}}

                async def generate_report(self, **params):
                    report = report_document(topic=params.get("topic"))
                    saved = await self.repo.save_report(report)
                    return {{"report_id": saved["report_id"], "topic": saved["topic"], "report": saved}}
            {service_generic}
            """
        ).strip() + "\n",
        "modules/reports/backend/repo.py": textwrap.dedent(
            """
            class ReportsRepo:
                def __init__(self, ctx):
                    self.ctx = ctx

                def _collection(self):
                    persistence = getattr(self.ctx, "persistence", None)
                    if persistence is None:
                        return None
                    return persistence.collection("reports", "reports")

                async def list_reports(self, *, user_id=None):
                    collection = self._collection()
                    if collection is None:
                        return []
                    query = {"user_id": user_id} if user_id else {}
                    return await collection.find(query).to_list(length=100)

                async def save_report(self, record):
                    collection = self._collection()
                    if collection is None:
                        return record
                    result = await collection.insert_one(record)
                    return {**record, "report_id": str(result.inserted_id)}
            """
        ).strip() + "\n",
        "modules/reports/backend/policy.py": textwrap.dedent(
            """
            class ReportsPolicy:
                def scope_query(self, query, *, user_id=None):
                    scoped = dict(query or {})
                    if user_id:
                        scoped["user_id"] = user_id
                    return scoped
            """
        ).strip() + "\n",
        "modules/reports/backend/schemas.py": textwrap.dedent(
            """
            from uuid import uuid4


            def report_document(*, topic=None):
                normalized_topic = str(topic or "Untitled").strip() or "Untitled"
                return {
                    "report_id": uuid4().hex,
                    "topic": normalized_topic,
                    "status": "generated",
                }
            """
        ).strip() + "\n",
    }


def build_acceptance_files(subscription_yaml: str, module_yaml: str) -> dict[str, str]:
    data_contract = {
        "version": "1",
        "app_id": DEFAULT_APP_ID,
        "surfaces": [
            {
                "surface_id": "reports",
                "surface_kind": "module",
                "collections": [
                    {
                        "name": "reports",
                        "ownership": {
                            "surface_id": "reports",
                            "surface_kind": "module",
                        },
                    }
                ],
            }
        ],
        "shared_collections": [
            {
                "name": "subscription_assignments",
                "data_alias": "billing.subscriptions",
            }
        ],
    }
    files = {
        "app.json": json.dumps(
            {
                "appId": DEFAULT_APP_ID,
                "appName": "Subscription Reporting",
                "version": "1.0.0",
                "startup": {"landing_spot": "/reports"},
            }
        ),
        "config/ai.json": json.dumps(
            {
                "chat": {"chat_startup_mode": "ask"},
                "workflows": {"entry_point": None},
            }
        ),
        "config/shell.json": json.dumps(
            {
                "navigation": {"autoFromPages": True},
                "header": {"show": True},
            }
        ),
        "config/subscriptions.yaml": subscription_yaml,
        "data/contract.json": json.dumps(data_contract),
        "ui/route_manifest.json": json.dumps(
            {
                "pages": [
                    {
                        "path": "/reports",
                        "component": "SchemaPage",
                        "label": "Reports",
                        "order": 10,
                        "schema": "reports",
                        "navigation": {"group": "main"},
                        "meta": {"title": "Reports"},
                    },
                    {
                        "path": "/usage",
                        "component": "SchemaPage",
                        "label": "Usage",
                        "order": 20,
                        "schema": "usage",
                        "navigation": {"group": "main"},
                        "meta": {"title": "Usage"},
                    },
                ]
            }
        ),
        "ui/pages/reports.yaml": textwrap.dedent(
            """
            schema_version: mozaiks.app_page.v1
            name: Reports
            route: /reports
            title: Reports
            page_type: record_list
            layout: full-width
            sections:
              - id: report-list
                primitive: DataTable
                config:
                  columns:
                    - key: report_id
                      label: Report
                  api_endpoint: /api/modules/reports/list_reports
              - id: report-generate
                primitive: Form
                config:
                  fields:
                    - name: report_name
                      label: Report Name
                      type: text
                  submit_action:
                    label: Generate Report
                    action_type: submit
                    href: /api/modules/reports/generate_report
            """
        ).strip() + "\n",
        "ui/pages/usage.yaml": textwrap.dedent(
            """
            schema_version: mozaiks.app_page.v1
            name: Usage
            route: /usage
            title: Usage
            page_type: analytics_dashboard
            layout: full-width
            sections:
              - id: usage-summary
                primitive: DataTable
                config:
                  columns:
                    - key: meter_id
                      label: Meter
                    - key: used
                      label: Used
                  api_endpoint: /api/me/usage
              - id: token-balances
                primitive: DataTable
                config:
                  columns:
                    - key: token_type
                      label: Token
                  api_endpoint: /api/me/tokens
              - id: token-ledger
                primitive: DataTable
                config:
                  columns:
                    - key: entry_id
                      label: Entry
                  api_endpoint: /api/me/tokens/ledger
            """
        ).strip() + "\n",
        "modules/reports/module.yaml": module_yaml,
        "modules/entitlement_dispatch/module.yaml": textwrap.dedent(
            """
            schema_version: mozaiks.module.v1
            module:
              id: entitlement_dispatch
              display_name: Entitlement Dispatch
              version: 1.0.0
              description: >
                Write-side partner for ConfiguredEntitlementAdapter. Activates and
                deactivates subscription assignment records in the billing.subscriptions
                collection so the runtime entitlement gate can enforce plan-based
                capability access on module actions.
              owner: app
              visibility: private
              type: entitlement_dispatch
              handler: backend.handler:EntitlementDispatchHandler

            permissions: []

            actions:
              - id: activate_subscription
                description: Write an active subscription assignment record.
                handler_method: activate_subscription
                input_schema:
                  type: object
                  required: [user_id, plan_id]
                  properties:
                    user_id: {type: string}
                    plan_id: {type: string}
                output_schema:
                  type: object
                  required: [activated]
                  properties:
                    activated: {type: boolean}

              - id: deactivate_subscription
                description: Mark the active subscription assignment as cancelled.
                handler_method: deactivate_subscription
                input_schema:
                  type: object
                  required: [user_id]
                  properties:
                    user_id: {type: string}
                output_schema:
                  type: object
                  required: [deactivated]
                  properties:
                    deactivated: {type: boolean}

            capabilities: []
            """
        ).strip() + "\n",
    }
    files.update(_backend_files(module_yaml))
    files.update(_entitlement_dispatch_backend_files())
    return files


def _entitlement_dispatch_backend_files() -> dict[str, str]:
    """Minimal entitlement_dispatch backend stubs for smoke bundle validation."""
    return {
        "modules/entitlement_dispatch/backend/__init__.py": "",
        "modules/entitlement_dispatch/backend/handler.py": textwrap.dedent(
            """
            from .service import EntitlementDispatchService


            class EntitlementDispatchHandler:
                def __init__(self):
                    self.service = EntitlementDispatchService()

                async def activate_subscription(self, ctx, **kwargs):
                    return await self.service.activate_subscription(ctx, **kwargs)

                async def deactivate_subscription(self, ctx, **kwargs):
                    return await self.service.deactivate_subscription(ctx, **kwargs)
            """
        ).strip() + "\n",
        "modules/entitlement_dispatch/backend/service.py": textwrap.dedent(
            """
            from .repo import EntitlementDispatchRepo


            class EntitlementDispatchService:
                def __init__(self):
                    self.repo = EntitlementDispatchRepo()

                async def activate_subscription(self, ctx, *, user_id, plan_id, **kwargs):
                    await self.repo.activate(ctx, user_id=user_id, plan_id=plan_id)
                    return {"activated": True, "plan_id": plan_id}

                async def deactivate_subscription(self, ctx, *, user_id, plan_id=None, **kwargs):
                    deactivated = await self.repo.deactivate(ctx, user_id=user_id, plan_id=plan_id)
                    return {"deactivated": deactivated}
            """
        ).strip() + "\n",
        "modules/entitlement_dispatch/backend/repo.py": textwrap.dedent(
            """
            class EntitlementDispatchRepo:
                async def activate(self, ctx, *, user_id, plan_id, **kwargs):
                    return None

                async def deactivate(self, ctx, *, user_id, plan_id=None):
                    return True
            """
        ).strip() + "\n",
    }


def _write_files(root: Path, files: dict[str, str]) -> None:
    for rel_path, content in files.items():
        target = root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(content), encoding="utf-8")


async def validate_subscription_acceptance_handoff(
    *,
    subscription_yaml: str,
    module_yaml: str,
) -> dict[str, Any]:
    files = build_acceptance_files(subscription_yaml, module_yaml)
    context = SmokeContext(
        {
            "workflow_name": "AppGenerator",
            "app_id": DEFAULT_APP_ID,
            "chat_id": "subscription-reporting-live-smoke",
            "generated_files": files,
            "app_validation_status": "skipped",
            "app_validation_strategy_used": "skip",
            "app_build_plan": _build_plan([_subscription_task(), _module_contract_task()]),
        }
    )

    wiring = await validate_wiring(context_variables=context)
    acceptance = await run_app_bundle_acceptance_gate(files=files, context_variables=context)
    export_gate = resolve_export_gate(context)

    loader_result: dict[str, Any]
    try:
        with tempfile.TemporaryDirectory(prefix="mozaiks-appgenerator-subscription-") as temp_dir:
            app_root = Path(temp_dir) / "app"
            _write_files(app_root, files)
            loaded = await AppLoader.load(str(app_root))
            reports_module = next((item for item in loaded.modules if item.name == "reports"), None)
            loader_result = {
                "loaded": True,
                "app_name": loaded.definition.name,
                "module_ids": [item.name for item in loaded.modules],
                "page_names": [page.name for page in loaded.definition.pages],
                "subscriptions_loaded": loaded.subscriptions_config is not None,
                "default_plan_id": (
                    loaded.subscriptions_config.default_plan_id
                    if loaded.subscriptions_config is not None
                    else None
                ),
                "token_wallet_ids": (
                    [wallet.wallet_id for wallet in loaded.subscriptions_config.token_wallets]
                    if loaded.subscriptions_config is not None
                    else []
                ),
                "action_entitlements": (
                    reports_module.action_entitlement_map
                    if reports_module is not None
                    else {}
                ),
            }
    except Exception as exc:
        loader_result = {"loaded": False, "error": str(exc)}

    errors: list[str] = []
    if not wiring.get("passed"):
        errors.append("App page wiring did not pass.")
    platform_count = int(((wiring.get("checks") or [{}])[0].get("details") or {}).get("platform_endpoint_count") or 0)
    if platform_count < 3:
        errors.append("Usage page did not expose all platform-owned usage/token endpoints.")
    if not acceptance.get("passed"):
        errors.append("App bundle acceptance did not pass.")
    if not export_gate.get("allow_export"):
        errors.extend(str(reason) for reason in export_gate.get("reasons") or [])
    if not loader_result.get("loaded"):
        errors.append(f"Runtime app loader failed: {loader_result.get('error')}")
    if not loader_result.get("subscriptions_loaded"):
        errors.append("Runtime app loader did not load config/subscriptions.yaml.")
    if loader_result.get("default_plan_id") != "free":
        errors.append("Runtime subscription loader did not preserve default_plan_id=free.")
    if "ai_tokens" not in loader_result.get("token_wallet_ids", []):
        errors.append("Runtime subscription loader did not expose ai_tokens wallet.")
    if loader_result.get("action_entitlements", {}).get("generate_report") != "reports.generate":
        errors.append("Runtime module loader did not preserve generate_report entitlement_gate.")
    errors.extend(_forbidden_drift_errors(files))

    return _json_safe(
        {
            "success": not errors,
            "validation_errors": errors,
            "wiring": wiring,
            "acceptance": acceptance,
            "export_gate": export_gate,
            "runtime_loader": loader_result,
            "context": context.to_dict(),
        }
    )


async def run_deterministic_appgenerator_subscription_smoke() -> dict[str, Any]:
    contract = sample_subscription_contract()
    subscription_output = deterministic_subscription_output()
    module_output = deterministic_module_contract_output()

    subscription_yaml, subscription_errors = validate_subscription_output(subscription_output, contract)
    module_yaml, module_errors = validate_module_contract_output(module_output)
    validation_errors = [*subscription_errors, *module_errors]
    if validation_errors or subscription_yaml is None or module_yaml is None:
        return {
            "success": False,
            "validation_errors": validation_errors,
            "subscription_output": subscription_output,
            "module_output": module_output,
        }

    acceptance = await validate_subscription_acceptance_handoff(
        subscription_yaml=subscription_yaml,
        module_yaml=module_yaml,
    )
    return {
        "success": bool(acceptance.get("success")),
        "validation_errors": list(acceptance.get("validation_errors") or []),
        "subscription_validation": {"passed": True},
        "module_contract_validation": {"passed": True},
        "appgenerator_acceptance": acceptance,
    }


async def _run_config_task(
    *,
    task: dict[str, Any],
    contract: dict[str, Any],
    prompt: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    from ag2 import Agent, MemoryStream

    from mozaiksai.core.workflow.agents import create_agents
    from mozaiksai.core.workflow.agents.factory import (
        ContextVariablesBridge,
        llm_config_to_ag2_config,
    )
    from mozaiksai.core.workflow.outputs.structured import (
        get_llm_for_workflow,
        load_workflow_structured_outputs,
    )
    from mozaiksai.core.workflow.workflow_manager import initialize_workflows

    os.environ["MOZAIKS_WORKFLOWS_PATH"] = str(WORKFLOWS_ROOT)
    os.environ["WORKFLOW_DIR"] = str(WORKFLOWS_ROOT)
    initialize_workflows(base_path=str(WORKFLOWS_ROOT))

    context = ContextVariablesBridge(_task_context(task, contract))
    agents = await create_agents("AppGenerator", context_variables=context)
    _models, structured_registry = load_workflow_structured_outputs("AppGenerator")
    configured_agent = agents["ConfigMiddlewareAgent"]
    response_schema = structured_registry["ConfigMiddlewareAgent"]
    system_prompt = await _render_agent_system_prompt(configured_agent, context)
    _model_name, llm_config = await get_llm_for_workflow(
        "AppGenerator",
        "base",
        agent_name="ConfigMiddlewareAgent",
    )
    agent = Agent(
        "ConfigMiddlewareAgent",
        prompt=system_prompt,
        config=llm_config_to_ag2_config(llm_config),
    )
    task_prompt = "\n\n".join(
        [
            prompt,
            "[CURRENT TASK CONTEXT JSON]",
            json.dumps(_task_context(task, contract), indent=2),
            "Return only the raw ConfigMiddlewareOutput JSON object.",
        ]
    )
    try:
        reply = await asyncio.wait_for(
            agent.ask(
                task_prompt,
                stream=MemoryStream(),
            ),
            timeout=timeout_seconds,
        )
        content = await asyncio.wait_for(reply.content(), timeout=timeout_seconds)
        structured_output = _coerce_structured_output(content, response_schema)
        if not structured_output:
            raise ValueError(f"Unable to parse structured output from {type(content).__name__}: {content!r}")
        success = True
        error = None
    except Exception as exc:
        structured_output = {}
        success = False
        error = str(exc)

    return _json_safe(
        {
            "success": success,
            "app_id": DEFAULT_APP_ID,
            "chat_id": f"live_subscription_{task['task_id']}",
            "event_count": None,
            "observed_event_types": [],
            "structured_output": structured_output,
            "context_variables": context.to_dict(),
            "error": error,
        }
    )


async def run_live_appgenerator_subscription_smoke(
    *,
    timeout_seconds: float = 600.0,
) -> dict[str, Any]:
    load_dotenv(REPO_ROOT / ".env")
    contract = sample_subscription_contract()

    subscription_live = await _run_config_task(
        task=_subscription_task(),
        contract=contract,
        timeout_seconds=timeout_seconds,
        prompt=(
            "Run the current subscription_config build task. "
            "Emit only ConfigMiddlewareOutput JSON for config/subscriptions.yaml."
        ),
    )
    subscription_output = subscription_live.get("structured_output") or {}
    subscription_yaml, subscription_errors = validate_subscription_output(subscription_output, contract)
    if subscription_errors or subscription_yaml is None:
        return _json_safe(
            {
                "success": False,
                "validation_errors": subscription_errors,
                "live_subscription": subscription_live,
            }
        )

    module_live = await _run_config_task(
        task=_module_contract_task(),
        contract=contract,
        timeout_seconds=timeout_seconds,
        prompt=(
            "Run the current module_contract build task for reports. "
            "Copy subscription_contract.module_contract_updates into module.yaml exactly."
        ),
    )
    module_output = module_live.get("structured_output") or {}
    module_yaml, module_errors = validate_module_contract_output(module_output)
    if module_errors or module_yaml is None:
        return _json_safe(
            {
                "success": False,
                "validation_errors": module_errors,
                "live_subscription": {
                    "success": subscription_live.get("success"),
                    "event_count": subscription_live.get("event_count"),
                    "observed_event_types": subscription_live.get("observed_event_types"),
                    "structured_output": subscription_output,
                },
                "live_module_contract": module_live,
            }
        )

    acceptance = await validate_subscription_acceptance_handoff(
        subscription_yaml=subscription_yaml,
        module_yaml=module_yaml,
    )
    errors = list(acceptance.get("validation_errors") or [])

    return _json_safe(
        {
            "success": not errors,
            "validation_errors": errors,
            "live_subscription": {
                "success": subscription_live.get("success"),
                "app_id": subscription_live.get("app_id"),
                "chat_id": subscription_live.get("chat_id"),
                "event_count": subscription_live.get("event_count"),
                "observed_event_types": subscription_live.get("observed_event_types"),
                "structured_output": subscription_output,
            },
            "live_module_contract": {
                "success": module_live.get("success"),
                "app_id": module_live.get("app_id"),
                "chat_id": module_live.get("chat_id"),
                "event_count": module_live.get("event_count"),
                "observed_event_types": module_live.get("observed_event_types"),
                "structured_output": module_output,
            },
            "appgenerator_acceptance": acceptance,
        }
    )


def main() -> int:
    _configure_event_loop_policy()
    parser = argparse.ArgumentParser(
        description="Run the AppGenerator subscription-config live smoke and deterministic acceptance gate."
    )
    parser.add_argument("--timeout-seconds", type=float, default=600.0)
    parser.add_argument(
        "--skip-live",
        action="store_true",
        help="Run only deterministic validators and acceptance checks without LLM calls.",
    )
    args = parser.parse_args()

    if args.skip_live:
        payload = asyncio.run(run_deterministic_appgenerator_subscription_smoke())
    else:
        payload = asyncio.run(
            run_live_appgenerator_subscription_smoke(timeout_seconds=float(args.timeout_seconds))
        )

    print(json.dumps(payload, indent=2), flush=True)
    return 0 if payload.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
