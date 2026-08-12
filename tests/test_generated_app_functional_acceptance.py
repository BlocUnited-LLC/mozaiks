from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any

import anyio
import pytest
from fastapi.testclient import TestClient

from factory_app.workflows.AppGenerator.tools.app_validation import (
    run_app_bundle_acceptance_gate,
)
from mozaiksai.core.auth.adapters.registry import reset_auth_adapter
from mozaiksai.core.validation import (
    GeneratedAppValidationRequest,
    scan_functional_generated_app,
    validate_generated_app_bundle,
)


def _basic_crud_files() -> dict[str, str]:
    return {
        "app.json": json.dumps(
            {
                "appId": "support-operations",
                "appName": "Support Operations",
                "version": "1.0.0",
                "startup": {"landing_spot": "/orders"},
            }
        ),
        "config/ai.json": json.dumps({"chat": {"chat_startup_mode": "ask"}}),
        "config/shell.json": json.dumps({"navigation": {"autoFromPages": True}}),
        "ui/route_manifest.json": json.dumps(
            {
                "pages": [
                    {
                        "path": "/orders",
                        "component": "SchemaPage",
                        "label": "Orders",
                        "schema": "orders",
                    }
                ]
            }
        ),
        "ui/pages/orders.yaml": """
name: orders
route: /orders
title: Orders
sections:
  - id: orders-list
    primitive: DataTable
    config:
      api_endpoint: /api/modules/orders/list_orders
  - id: create-order
    primitive: Form
    config:
      submit_action:
        api_endpoint: /api/modules/orders/create_order
""",
        "data/contract.json": json.dumps(
            {
                "version": "1",
                "app_id": "support-operations",
                "surfaces": [
                    {
                        "surface_id": "orders",
                        "surface_kind": "module",
                        "collections": [{"name": "orders"}],
                    }
                ],
            }
        ),
        "security/secrets.yaml": "version: 1\nsecrets: []\n",
        "modules/orders/module.yaml": """
schema_version: mozaiks.module.v1
module:
  id: orders
  display_name: Orders
  version: 1.0.0
  handler: backend.handler:OrdersModule
actions:
  - id: list_orders
    description: List orders.
    handler_method: list_orders
    input_schema: {type: object, properties: {}}
    output_schema: {type: object}
  - id: create_order
    description: Create an order.
    handler_method: create_order
    input_schema:
      type: object
      required: [customer_name]
      properties:
        customer_name: {type: string}
    output_schema: {type: object}
""",
        "modules/orders/backend/__init__.py": "",
        "modules/orders/backend/handler.py": """
from .service import OrdersService


class OrdersModule:
    def __init__(self):
        self.service = OrdersService()

    async def list_orders(self, ctx, **params):
        return await self.service.list_orders(ctx, **params)

    async def create_order(self, ctx, **params):
        return await self.service.create_order(ctx, **params)
""",
        "modules/orders/backend/service.py": """
class OrdersService:
    async def list_orders(self, ctx, **params):
        return {"orders": []}

    async def create_order(self, ctx, **params):
        return {"order": {"customer_name": params.get("customer_name")}}
""",
    }


def _template_file(relative_path: str) -> str:
    return (
        Path(__file__).resolve().parents[1]
        / "factory_app"
        / "build_context"
        / "mozaikspay"
        / "templates"
        / relative_path
    ).read_text(encoding="utf-8")


def _generated_monetized_saas_files() -> dict[str, str]:
    files = {
        "app.json": json.dumps(
            {
                "appId": "generated-saas",
                "appName": "Generated SaaS",
                "version": "1.0.0",
                "startup": {"landing_spot": "/pricing"},
            }
        ),
        "config/ai.json": json.dumps({"chat": {"chat_startup_mode": "ask"}}),
        "config/shell.json": json.dumps({"navigation": {"autoFromPages": True}}),
        "ui/route_manifest.json": json.dumps(
            {
                "pages": [
                    {"path": "/pricing", "component": "SchemaPage", "label": "Pricing", "schema": "pricing"},
                    {"path": "/billing", "component": "SchemaPage", "label": "Billing", "schema": "billing"},
                    {"path": "/reports", "component": "SchemaPage", "label": "Reports", "schema": "reports"},
                ]
            }
        ),
        "ui/pages/reports.yaml": textwrap.dedent(
            """
            schema_version: mozaiks.page.v1
            name: reports
            route: /reports
            title: Reports
            page_type: dashboard
            layout: stack
            sections:
              - id: generate-report
                primitive: ActionButton
                title: Generate Report
                config:
                  label: Generate
                  api_endpoint: /api/modules/reports/generate_report
                  method: POST
            """
        ),
        "config/subscriptions.yaml": textwrap.dedent(
            """
            schema_version: mozaiks.subscriptions.v1
            label: Generated SaaS Plans
            default_plan_id: free
            assignment_store:
              data_alias: billing.subscriptions
              app_id_field: app_id
              user_id_field: user_id
              tenant_id_field: tenant_id
              plan_id_field: plan_id
              status_field: status
              capabilities_field: granted_capabilities
              plan_snapshot_field: plan_snapshot
              active_statuses: [active, trialing]
            pricing_catalog:
              default_group_id: standard
              groups:
                - group_id: standard
                  label: Standard
                  plan_ids: [free, pro]
            plans:
              - plan_id: free
                label: Free
                description: Read-only access.
                capabilities: [reports.view]
              - plan_id: pro
                label: Pro
                description: Generate reports.
                capabilities: [reports.view, reports.generate]
            """
        ),
        "data/contract.json": json.dumps(
            {
                "version": "1",
                "app_id": "generated-saas",
                "surfaces": [
                    {
                        "surface_id": "billing",
                        "surface_kind": "module",
                        "collections": [{"name": "subscriptions", "data_alias": "billing.subscriptions"}],
                    }
                ],
            }
        ),
        "security/secrets.yaml": "version: 1\nsecrets: []\n",
        "modules/reports/module.yaml": textwrap.dedent(
            """
            schema_version: mozaiks.module.v1
            module:
              id: reports
              display_name: Reports
              version: 1.0.0
              handler: backend.handler:ReportsModule
            actions:
              - id: generate_report
                description: Generate a gated report.
                handler_method: generate_report
                entitlement_gate: reports.generate
                input_schema:
                  type: object
                  required: [topic]
                  properties:
                    topic: {type: string}
                output_schema:
                  type: object
            """
        ),
        "modules/reports/backend/__init__.py": "",
        "modules/reports/backend/handler.py": textwrap.dedent(
            """
            class ReportsModule:
                async def generate_report(self, ctx, *, topic):
                    return {"report_id": "report_1", "topic": topic}
            """
        ),
    }
    files.update(
        {
            "services/integrations/mozaikspay_client.py": _template_file(
                "services/integrations/mozaikspay_client.py"
            ),
            "modules/billing_portal/module.yaml": _template_file("modules/billing_portal/module.yaml"),
            "modules/billing_portal/backend/__init__.py": "",
            "modules/billing_portal/backend/base_handler.py": _template_file(
                "modules/billing_portal/backend/base_handler.py"
            ),
            "modules/billing_portal/backend/handler.py": _template_file("modules/billing_portal/backend/handler.py"),
            "modules/billing_portal/backend/service.py": _template_file("modules/billing_portal/backend/service.py"),
            "modules/billing_portal/backend/schemas.py": _template_file("modules/billing_portal/backend/schemas.py"),
            "ui/pages/pricing.yaml": _template_file("ui/pages/pricing.yaml"),
            "ui/pages/billing.yaml": _template_file("ui/pages/billing.yaml"),
        }
    )
    return files


def _generated_workflow_agent_files() -> dict[str, str]:
    return {
        "app.json": json.dumps(
            {
                "appId": "workflow-agent-app",
                "appName": "Workflow Agent App",
                "version": "1.0.0",
                "startup": {"landing_spot": "/research"},
            }
        ),
        "config/ai.json": json.dumps({"chat": {"chat_startup_mode": "ask"}}),
        "config/shell.json": json.dumps({"navigation": {"autoFromPages": True}}),
        "ui/route_manifest.json": json.dumps(
            {
                "pages": [
                    {"path": "/research", "component": "SchemaPage", "label": "Research", "schema": "research"}
                ]
            }
        ),
        "ui/pages/research.yaml": textwrap.dedent(
            """
            schema_version: mozaiks.page.v1
            name: research
            route: /research
            title: Research
            page_type: dashboard
            layout: stack
            sections:
              - id: summary
                primitive: ActionButton
                config:
                  label: Summarize
                  api_endpoint: /api/modules/research/summarize_source
                  method: POST
            """
        ),
        "modules/research/module.yaml": textwrap.dedent(
            """
            schema_version: mozaiks.module.v1
            module:
              id: research
              display_name: Research
              version: 1.0.0
              handler: backend.handler:ResearchModule
            actions:
              - id: summarize_source
                description: Summarize a source for the workflow.
                handler_method: summarize_source
                input_schema:
                  type: object
                  required: [source_text]
                  properties:
                    source_text: {type: string}
                output_schema:
                  type: object
            """
        ),
        "modules/research/backend/__init__.py": "",
        "modules/research/backend/handler.py": textwrap.dedent(
            """
            class ResearchModule:
                async def summarize_source(self, ctx, *, source_text):
                    return {"summary": source_text[:32], "source_length": len(source_text)}
            """
        ),
        "workflows/ResearchWorkflow/orchestrator.yaml": textwrap.dedent(
            """
            workflow_name: ResearchWorkflow
            max_turns: 3
            human_in_the_loop: false
            workflow_startup_mode: AgentDriven
            orchestration_pattern: ag2_network
            initial_message: Produce a research summary.
            initial_agent: ResearchAgent
            """
        ),
        "workflows/ResearchWorkflow/agents.yaml": textwrap.dedent(
            """
            agents:
              - name: ResearchAgent
                prompt_sections:
                  - id: role
                    heading: "[ROLE]"
                    content: You summarize source material.
                  - id: output
                    heading: "[OUTPUT]"
                    content: Return valid JSON matching ResearchSummary.
                max_consecutive_auto_reply: 1
                structured_outputs_required: true
            """
        ),
        "workflows/ResearchWorkflow/context_variables.yaml": textwrap.dedent(
            """
            definitions:
              source_text:
                source:
                  type: state
                  default: "Local deterministic source"
            agents:
              ResearchAgent:
                variables: [source_text]
            """
        ),
        "workflows/ResearchWorkflow/structured_outputs.yaml": textwrap.dedent(
            """
            registry:
              ResearchAgent: ResearchSummary
            models:
              ResearchSummary:
                type: model
                fields:
                  agent_message:
                    type: str
                    description: User-facing summary message.
                  summary:
                    type: str
                    description: Source summary.
            """
        ),
        "workflows/ResearchWorkflow/tools.yaml": textwrap.dedent(
            """
            tools:
              - agent: ResearchAgent
                file: summarize.py
                function: summarize_source
                tool_type: Agent_Tool
                auto_tool_call: false
            lifecycle_tools: []
            """
        ),
        "workflows/ResearchWorkflow/tools/__init__.py": "",
        "workflows/ResearchWorkflow/tools/summarize.py": textwrap.dedent(
            """
            async def summarize_source(source_text: str):
                return {"summary": source_text[:32], "source_length": len(source_text)}
            """
        ),
        "workflows/ResearchWorkflow/transition_graph.yaml": textwrap.dedent(
            """
            transition_rules:
              - source_agent: ResearchAgent
                target_agent: terminate
                transition_type: after_turn
            """
        ),
        "workflows/ResearchWorkflow/ui_config.yaml": textwrap.dedent(
            """
            visual_agents:
              - ResearchAgent
            """
        ),
    }


class _RuntimeAcceptanceCollection:
    async def find_one(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def update_one(self, *args: Any, **kwargs: Any) -> None:
        return None


class _RuntimeAcceptancePersistence:
    def __init__(self) -> None:
        self.created_sessions: list[dict[str, Any]] = []

    async def create_chat_session(self, **kwargs: Any) -> None:
        self.created_sessions.append(dict(kwargs))

    async def get_or_assign_cache_seed(self, chat_id: str, app_id: str | None = None) -> int:
        return 12345

    async def load_run_history(self, chat_id: str, app_id: str | None = None) -> list[dict[str, Any]]:
        return []


class _RuntimeAcceptanceMongoClient:
    def __init__(self) -> None:
        self.admin = self

    async def command(self, *args: Any, **kwargs: Any) -> dict[str, int]:
        return {"ok": 1}

    def close(self) -> None:
        return None


class _FakeMozaiksPayResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeMozaiksPayAsyncClient:
    def __init__(self, requests: list[dict[str, Any]]) -> None:
        self.requests = requests

    async def __aenter__(self) -> _FakeMozaiksPayAsyncClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def request(self, method: str, url: str, **kwargs: Any) -> _FakeMozaiksPayResponse:
        self.requests.append({"method": method, "url": url, **kwargs})
        if url.endswith("/api/mozaikspay/v1/subscription/status"):
            return _FakeMozaiksPayResponse(
                {
                    "success": True,
                    "found": True,
                    "plan_id": "pro",
                    "plan_name": "Pro",
                    "status": "active",
                    "starts_at": "2026-07-01T00:00:00+00:00",
                    "expires_at": None,
                    "on_free_plan": False,
                }
            )
        if url.endswith("/api/mozaikspay/v1/subscription/checkout-session"):
            payload = kwargs.get("json") or {}
            return _FakeMozaiksPayResponse(
                {
                    "success": True,
                    "checkout_url": "https://pay.example.test/checkout/session_1",
                    "success_url": payload.get("success_url"),
                    "cancel_url": payload.get("cancel_url"),
                    "plan_id": payload.get("plan_id"),
                }
            )
        if url.endswith("/api/mozaikspay/v1/billing-portal/session"):
            payload = kwargs.get("json") or {}
            return _FakeMozaiksPayResponse(
                {
                    "success": True,
                    "portal_url": "https://pay.example.test/portal/session_1",
                    "return_url": payload.get("return_url"),
                }
            )
        return _FakeMozaiksPayResponse({"success": False, "error": "unknown endpoint"}, status_code=404)


def _diagnostic_codes(files: dict[str, str], *, capability_packs: list[dict] | None = None) -> set[str]:
    return {item.code for item in scan_functional_generated_app(files, capability_packs=capability_packs)}


def _materialize_bundle(root: Path, files: dict[str, str]) -> None:
    for relative_path, content in files.items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


class _FakeMongoAdmin:
    async def command(self, *_args: Any, **_kwargs: Any) -> dict[str, str]:
        return {"ok": "1"}


class _FakeMongoClient:
    def __init__(self) -> None:
        self.admin = _FakeMongoAdmin()

    def __getitem__(self, name: str) -> _FakeMongoClient:  # noqa: ARG002
        return self

    def __getattr__(self, name: str) -> _FakeMongoClient:  # noqa: ARG002
        return self

    async def command(self, *_args: Any, **_kwargs: Any) -> dict[str, str]:
        return {"ok": "1"}

    async def close(self) -> None:
        return None


def _assert_not_missing_or_placeholder(response, *, surface: str) -> None:
    body = response.text.lower()
    assert response.status_code != 404, f"DECLARED_SURFACE_404 surface={surface} body={response.text}"
    assert response.status_code != 501, f"DECLARED_SURFACE_501 surface={surface} body={response.text}"
    assert "not implemented" not in body, f"DECLARED_SURFACE_PLACEHOLDER surface={surface} body={response.text}"
    assert "not_implemented" not in body, f"DECLARED_SURFACE_PLACEHOLDER surface={surface} body={response.text}"


def _prepare_platform_test_runtime(platform: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform.runtime_app, "mongo_client", _RuntimeAcceptanceMongoClient())


def test_functional_scanner_accepts_basic_authenticated_crud_bundle() -> None:
    assert scan_functional_generated_app(_basic_crud_files()) == []


def test_public_generated_app_validation_runs_functional_completeness() -> None:
    files = _basic_crud_files()
    files["ui/route_manifest.json"] = json.dumps(
        {"pages": [{"path": "/missing", "component": "MissingPage"}]}
    )

    result = validate_generated_app_bundle(GeneratedAppValidationRequest(files=files))

    assert result.passed is False
    assert any(item.code == "MISSING_ROUTE_COMPONENT" for item in result.diagnostics)


def test_schema_route_requires_matching_declarative_page_schema() -> None:
    files = _basic_crud_files()
    files["ui/route_manifest.json"] = json.dumps(
        {"pages": [{"path": "/orders", "component": "SchemaPage", "schema": "missing"}]}
    )

    assert "MISSING_SCHEMA_PAGE" in _diagnostic_codes(files)


def test_page_module_endpoint_must_resolve_to_declared_action() -> None:
    files = _basic_crud_files()
    files["ui/pages/orders.yaml"] = files["ui/pages/orders.yaml"].replace(
        "/api/modules/orders/create_order",
        "/api/modules/orders/archive_order",
    )

    assert "MISSING_MODULE_ACTION" in _diagnostic_codes(files)


def test_declared_module_action_requires_implemented_handler_method() -> None:
    files = _basic_crud_files()
    files["modules/orders/module.yaml"] = files["modules/orders/module.yaml"].replace(
        "handler_method: create_order",
        "handler_method: archive_order",
    )

    assert "MISSING_MODULE_ACTION" in _diagnostic_codes(files)


def test_workflow_module_action_reference_must_resolve() -> None:
    files = _basic_crud_files()
    files["workflows/OrderWorkflow/tools.yaml"] = """
tools:
  - id: archive
    module_id: orders
    action_id: archive_order
"""

    assert "MISSING_MODULE_ACTION" in _diagnostic_codes(files)


def test_placeholder_implementation_is_blocking() -> None:
    files = _basic_crud_files()
    files["modules/orders/backend/service.py"] += """

async def legacy_placeholder():
    raise NotImplementedError("not ready")
"""

    assert "PLACEHOLDER_IMPLEMENTATION" in _diagnostic_codes(files)


def test_mozaikspay_selected_capability_requires_public_facade_files() -> None:
    files = _basic_crud_files()

    codes = _diagnostic_codes(files, capability_packs=[{"id": "mozaikspay"}])

    assert "CAPABILITY_FACADE_MISSING" in codes


def test_mozaikspay_facade_supports_inherited_base_handler_methods() -> None:
    files = _basic_crud_files()
    files.update(
        {
            "services/integrations/mozaikspay_client.py": "class MozaiksPayClient: pass\n",
            "modules/billing_portal/module.yaml": """
schema_version: mozaiks.module.v1
module:
  id: billing_portal
  display_name: Billing Portal
  version: 1.0.0
  handler: backend.handler:BillingPortalHandler
actions:
  - id: list_plans
    description: List plans.
    handler_method: list_plans
    input_schema: {type: object, properties: {}}
    output_schema: {type: object}
  - id: get_subscription_status
    description: Get subscription status.
    handler_method: get_subscription_status
    input_schema: {type: object, properties: {}}
    output_schema: {type: object}
  - id: open_billing_portal
    description: Open billing portal.
    handler_method: open_billing_portal
    input_schema: {type: object, properties: {}}
    output_schema: {type: object}
""",
            "modules/billing_portal/backend/__init__.py": "",
            "modules/billing_portal/backend/base_handler.py": """
class BillingPortalBaseHandler:
    async def list_plans(self, ctx, **params):
        return {"plans": []}

    async def get_subscription_status(self, ctx, **params):
        return {"found": False}

    async def open_billing_portal(self, ctx, **params):
        return {"portal_url": None}
""",
            "modules/billing_portal/backend/handler.py": """
from .base_handler import BillingPortalBaseHandler


class BillingPortalHandler(BillingPortalBaseHandler):
    pass
""",
        }
    )

    assert scan_functional_generated_app(files, capability_packs=[{"id": "mozaikspay"}]) == []


@pytest.mark.asyncio
async def test_app_generator_acceptance_gate_includes_functional_completeness() -> None:
    result = await run_app_bundle_acceptance_gate(files=_basic_crud_files())

    assert result["passed"] is True
    assert result["functional_completeness"]["passed"] is True
    assert "functional_completeness" in result["validation_evidence"]["completed"]


def test_generated_crud_bundle_boots_and_serves_declared_http_surfaces(tmp_path, monkeypatch) -> None:
    app_root = tmp_path / "app"
    _materialize_bundle(app_root, _basic_crud_files())
    fake_mongo_client = _FakeMongoClient()
    monkeypatch.setenv("PLATFORM_PATH", str(app_root))
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    monkeypatch.setenv("MOZAIKS_DATABASE_STARTUP_POLICY", "best_effort")
    monkeypatch.setattr("mozaiksai.hosts.runtime.get_mongo_client", lambda: fake_mongo_client)
    monkeypatch.setattr("mozaiksai.core.startup.validation.get_mongo_client", lambda: fake_mongo_client)
    reset_auth_adapter()

    from mozaiksai.hosts import platform

    _prepare_platform_test_runtime(platform, monkeypatch)
    with TestClient(platform.app, raise_server_exceptions=False) as client:
        health = client.get("/health")
        assert health.status_code == 200, health.text
        assert health.json()["status"] == "ok"

        shell_config = client.get("/api/shell-config")
        _assert_not_missing_or_placeholder(shell_config, surface="/api/shell-config")
        assert shell_config.status_code == 200, shell_config.text
        pages = shell_config.json().get("pages", [])
        assert any(page.get("path") == "/orders" for page in pages)

        page_schema = client.get("/api/pages/orders")
        _assert_not_missing_or_placeholder(page_schema, surface="/api/pages/orders")
        assert page_schema.status_code == 200, page_schema.text
        assert page_schema.json()["route"] == "/orders"

        list_response = client.post("/api/modules/orders/list_orders", json={})
        _assert_not_missing_or_placeholder(list_response, surface="/api/modules/orders/list_orders")
        assert list_response.status_code == 200, list_response.text
        assert list_response.json() == {"orders": []}

        create_response = client.post(
            "/api/modules/orders/create_order",
            json={"params": {"customer_name": "Ada"}},
        )
        _assert_not_missing_or_placeholder(create_response, surface="/api/modules/orders/create_order")
        assert create_response.status_code == 200, create_response.text
        assert create_response.json() == {"order": {"customer_name": "Ada"}}


def test_generated_monetized_saas_bundle_boots_and_serves_mozaikspay_runtime_surfaces(
    tmp_path,
    monkeypatch,
) -> None:
    files = _generated_monetized_saas_files()
    assert scan_functional_generated_app(files, capability_packs=[{"id": "mozaikspay"}]) == []

    app_root = tmp_path / "app"
    _materialize_bundle(app_root, files)
    monkeypatch.setenv("PLATFORM_PATH", str(app_root))
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    monkeypatch.setenv("MOZAIKS_DATABASE_STARTUP_POLICY", "best_effort")
    monkeypatch.setenv("MOZAIKSPAY_API_BASE", "https://mozaikspay-compatible.test")
    monkeypatch.setenv("MOZAIKSPAY_API_KEY", "mzk_test_generated_saas")
    reset_auth_adapter()

    provider_requests: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "httpx.AsyncClient",
        lambda *args, **kwargs: _FakeMozaiksPayAsyncClient(provider_requests),
    )

    from mozaiksai.core.runtime.app.entitlements import ConfiguredEntitlementAdapter
    from mozaiksai.hosts import platform

    _prepare_platform_test_runtime(platform, monkeypatch)
    empty_assignments = _RuntimeAcceptanceCollection()
    monkeypatch.setattr(
        platform,
        "_load_entitlement_adapter",
        lambda config: ConfiguredEntitlementAdapter(
            config=config,
            collection_resolver=lambda alias: empty_assignments,
        ),
    )

    with TestClient(platform.app, raise_server_exceptions=False) as client:
        health = client.get("/health")
        assert health.status_code == 200, health.text
        assert health.json()["status"] == "ok"

        for route, schema_name in (("/pricing", "pricing"), ("/billing", "billing")):
            page_schema = client.get(f"/api/pages/{schema_name}")
            _assert_not_missing_or_placeholder(page_schema, surface=f"/api/pages/{schema_name}")
            assert page_schema.status_code == 200, page_schema.text
            assert page_schema.json()["route"] == route

        plans_response = client.post("/api/modules/billing_portal/list_plans", json={})
        _assert_not_missing_or_placeholder(plans_response, surface="/api/modules/billing_portal/list_plans")
        assert plans_response.status_code == 200, plans_response.text
        plans_payload = plans_response.json()
        assert plans_payload["success"] is True
        assert [plan["plan_id"] for plan in plans_payload["plans"]] == ["free", "pro"]

        status_response = client.post("/api/modules/billing_portal/get_subscription_status", json={})
        _assert_not_missing_or_placeholder(
            status_response,
            surface="/api/modules/billing_portal/get_subscription_status",
        )
        assert status_response.status_code == 200, status_response.text
        assert status_response.json()["plan_id"] == "pro"

        checkout_response = client.post(
            "/api/modules/billing_portal/start_subscription_checkout",
            json={
                "params": {
                    "plan_id": "pro",
                    "success_url": "https://app.example.test/billing/success",
                    "cancel_url": "https://app.example.test/billing/cancel",
                }
            },
        )
        _assert_not_missing_or_placeholder(
            checkout_response,
            surface="/api/modules/billing_portal/start_subscription_checkout",
        )
        assert checkout_response.status_code == 200, checkout_response.text
        assert checkout_response.json()["checkout_url"] == "https://pay.example.test/checkout/session_1"

        portal_response = client.post(
            "/api/modules/billing_portal/open_billing_portal",
            json={"params": {"return_url": "https://app.example.test/billing"}},
        )
        _assert_not_missing_or_placeholder(
            portal_response,
            surface="/api/modules/billing_portal/open_billing_portal",
        )
        assert portal_response.status_code == 200, portal_response.text
        assert portal_response.json()["portal_url"] == "https://pay.example.test/portal/session_1"

        gated_response = client.post(
            "/api/modules/reports/generate_report",
            json={"params": {"topic": "retention"}},
        )
        _assert_not_missing_or_placeholder(gated_response, surface="/api/modules/reports/generate_report")
        assert gated_response.status_code == 200, gated_response.text
        assert gated_response.json()["report_id"] == "report_1"

        from mozaiksai.core.runtime.composition.module_executor import ModuleRequest

        module_executor = platform.app.state.executor_registry.module_executor
        enforced_result = anyio.run(
            module_executor.execute,
            ModuleRequest(
                module="reports",
                action="generate_report",
                params={"topic": "retention"},
                app_id="generated-saas",
                user_id="anonymous",
                granted_permissions=[],
            ),
        )
        assert enforced_result.success is False
        assert enforced_result.error_code == "ENTITLEMENT_REQUIRED"

    provider_paths = {request["url"].removeprefix("https://mozaikspay-compatible.test") for request in provider_requests}
    assert provider_paths == {
        "/api/mozaikspay/v1/subscription/status",
        "/api/mozaikspay/v1/subscription/checkout-session",
        "/api/mozaikspay/v1/billing-portal/session",
    }
    for request in provider_requests:
        assert request["headers"]["Authorization"] == "Bearer mzk_test_generated_saas"


def test_generated_workflow_agent_bundle_loads_catalog_and_starts_runtime_session(
    tmp_path,
    monkeypatch,
) -> None:
    files = _generated_workflow_agent_files()
    assert scan_functional_generated_app(files) == []

    workspace_root = tmp_path / "workspace"
    _materialize_bundle(workspace_root, files)
    monkeypatch.setenv("PLATFORM_PATH", str(workspace_root))
    monkeypatch.setenv("MOZAIKS_WORKFLOWS_PATH", str(workspace_root / "workflows"))
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    monkeypatch.setenv("MOZAIKS_DATABASE_STARTUP_POLICY", "best_effort")
    reset_auth_adapter()

    from mozaiksai.core.workflow.workflow_manager import initialize_workflows, workflow_manager
    from mozaiksai.hosts import platform

    _prepare_platform_test_runtime(platform, monkeypatch)
    initialize_workflows(str(workspace_root / "workflows"))
    assert "ResearchWorkflow" in workflow_manager.get_all_workflow_names()
    assert workflow_manager.get_config("ResearchWorkflow")["initial_agent"] == "ResearchAgent"
    assert workflow_manager.get_structured_output_registry("ResearchWorkflow") == {
        "ResearchAgent": "ResearchSummary"
    }

    fake_persistence = _RuntimeAcceptancePersistence()
    fake_chat_collection = _RuntimeAcceptanceCollection()

    async def _fake_chat_coll() -> _RuntimeAcceptanceCollection:
        return fake_chat_collection

    monkeypatch.setattr(platform, "persistence_manager", fake_persistence)
    monkeypatch.setattr(platform.runtime_app, "persistence_manager", fake_persistence)
    monkeypatch.setattr(platform.runtime_app, "_chat_coll", _fake_chat_coll)
    monkeypatch.setattr(platform.runtime_app, "simple_transport", None)

    with TestClient(platform.app, raise_server_exceptions=False) as client:
        health = client.get("/health")
        assert health.status_code == 200, health.text
        assert health.json()["status"] == "ok"

        workflows_response = client.get("/api/workflows")
        _assert_not_missing_or_placeholder(workflows_response, surface="/api/workflows")
        assert workflows_response.status_code == 200, workflows_response.text
        workflows = workflows_response.json()["workflows"]
        workflow = next(item for item in workflows if item["name"] == "ResearchWorkflow")
        assert workflow["initial_agent"] == "ResearchAgent"

        start_response = client.post(
            "/api/chats/workflow-agent-app/ResearchWorkflow/start",
                json={
                    "force_new": True,
                    "user_id": "acceptance-user",
                    "context_variables": {"source_text": "Deterministic local research source"},
                },
            )
        _assert_not_missing_or_placeholder(
            start_response,
            surface="/api/chats/workflow-agent-app/ResearchWorkflow/start",
        )
        assert start_response.status_code == 200, start_response.text
        start_payload = start_response.json()
        assert start_payload["success"] is True
        assert start_payload["workflow_name"] == "ResearchWorkflow"
        assert start_payload["websocket_url"].startswith("/ws/ResearchWorkflow/workflow-agent-app/")
        assert fake_persistence.created_sessions[0]["workflow_name"] == "ResearchWorkflow"
        assert fake_persistence.created_sessions[0]["extra_fields"]["source_text"] == (
            "Deterministic local research source"
        )

        module_response = client.post(
            "/api/modules/research/summarize_source",
            json={"params": {"source_text": "Deterministic local research source"}},
        )
        _assert_not_missing_or_placeholder(module_response, surface="/api/modules/research/summarize_source")
        assert module_response.status_code == 200, module_response.text
        assert module_response.json() == {
            "summary": "Deterministic local research sou",
            "source_length": 35,
        }
