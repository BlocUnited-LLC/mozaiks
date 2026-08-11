from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from factory_app.workflows.AppGenerator.tools.app_build_plan import app_build_plan
from factory_app.workflows.AppGenerator.tools.app_validation import run_app_bundle_acceptance_gate
from factory_app.workflows.AppGenerator.tools.assemble_app_tasks import assemble_app_tasks
from factory_app.workflows.AppGenerator.tools.render_infra_scaffold import save_infra_scaffold
from mozaiksai.core.adapters.ag2_task_batch_runner import AG2TaskBatchRunnerResult
from mozaiksai.core.auth.adapters import registry as auth_registry
from mozaiksai.core.auth.adapters.base import BaseAuthAdapter, UserClaims
from mozaiksai.core.auth.adapters.registry import register_adapter, reset_auth_adapter
from mozaiksai.core.ports.orchestration import RunStatus
from mozaiksai.core.runtime.app.entitlements import ConfiguredEntitlementAdapter
from mozaiksai.core.runtime.app.loader import AppLoader
from mozaiksai.core.runtime.composition.executor_registry import ExecutorRegistry
from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor
from mozaiksai.core.validation import GeneratedAppValidationRequest, scan_functional_generated_app
from mozaiksai.core.validation.generated_app import validate_generated_app_bundle
from mozaiksai.core.workflow.generator_support.page_plan_utils import (
    _page_from_plan,
    _page_stem_from_path,
)
from mozaiksai.core.workflow.task_batches import (
    execute_task_batches_for_trigger,
    load_task_batches_config,
)
from mozaiksai.hosts import platform

WORKSPACE = Path(__file__).resolve().parents[1]
FIXTURE_PATH = WORKSPACE / "tests" / "fixtures" / "appplan_saas_entitlement_dispatch_output.json"
WORKFLOWS_ROOT = WORKSPACE / "factory_app" / "workflows"


class _Context:
    def __init__(self, initial: dict[str, Any] | None = None) -> None:
        self.data: dict[str, Any] = dict(initial or {})

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.data[key] = value


class _Collection:
    def __init__(self, docs: list[dict[str, Any]] | None = None) -> None:
        self.docs: dict[str, dict[str, Any]] = {}
        for item in docs or []:
            key = str(item.get("_id") or len(self.docs))
            self.docs[key] = deepcopy({"_id": key, **item})

    async def find_one(self, query: dict[str, Any], projection: dict[str, int] | None = None):
        for doc in self.docs.values():
            if all(doc.get(key) == value for key, value in query.items()):
                result = deepcopy(doc)
                if projection and projection.get("_id") == 0:
                    result.pop("_id", None)
                return result
        return None

    def replace_docs(self, docs: list[dict[str, Any]]) -> None:
        self.docs.clear()
        for item in docs:
            key = str(item.get("_id") or len(self.docs))
            self.docs[key] = deepcopy({"_id": key, **item})


class _TestAuthAdapter(BaseAuthAdapter):
    name = "test"

    async def validate_token(self, token: str) -> UserClaims:  # noqa: ARG002
        return UserClaims(
            user_id="user-1",
            email="user-1@generated-saas-plan.local",
            name="Test User",
            roles=["user"],
            scopes=["access_as_user"],
            raw_claims={},
            provider=self.name,
            app_id="generated-saas-plan",
        )

    def is_enabled(self) -> bool:
        return True


class _FakeMongoAdmin:
    async def command(self, *_args, **_kwargs) -> dict[str, int]:
        return {"ok": 1}


class _FakeMongoClient:
    def __init__(self) -> None:
        self.admin = _FakeMongoAdmin()

    def close(self) -> None:
        pass


def _load_fixture_plan() -> dict[str, Any]:
    raw = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return raw["AppBuildPlan"] if "AppBuildPlan" in raw else raw


def _validation_pages(plan: dict[str, Any], files: dict[str, str] | None = None) -> list[dict[str, Any]]:
    pages = []
    for page in plan.get("pages") or []:
        if not isinstance(page, dict):
            continue
        stem = None
        if files:
            for rel_path in files:
                page_stem = _page_stem_from_path(rel_path)
                if page_stem and page_stem == _page_stem_from_path(f"ui/pages/{str(page.get('name') or '').strip()}.yaml"):
                    stem = page_stem
                    break
        if stem is None:
            route = str(page.get("route") or "").strip()
            if route and route != "/":
                stem = route.strip("/").split("/")[-1]
            else:
                stem = str(page.get("name") or "page").strip()
        pages.append(_page_from_plan(page, stem=stem))
    return pages


def _file_map(assembled: dict[str, Any]) -> dict[str, str]:
    return {
        str(item["filename"]): str(item["content"])
        for item in assembled.get("code_files") or []
        if isinstance(item, dict) and item.get("filename")
    }


def _write_files(root: Path, files: dict[str, str]) -> None:
    for rel_path, content in files.items():
        path = root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _app_builder_runner() -> Any:
    async def _fake_run(self, request):  # noqa: ANN001, ARG001
        task = dict(request.context_variables.get("current_build_task") or {})
        task_id = str(request.context_variables.get("current_build_task_id") or task.get("task_id") or "")
        task_type = str(request.context_variables.get("current_build_task_type") or task.get("task_type") or "")
        output = _task_output(task_id=task_id, task_type=task_type, task=task)
        return AG2TaskBatchRunnerResult(
            status=RunStatus.COMPLETED,
            output=output,
            channel_id=f"{request.batch_id}:{request.task_id}",
            close_reason="deterministic_test_runner",
        )

    return _fake_run


def _task_output(*, task_id: str, task_type: str, task: dict[str, Any]) -> dict[str, Any]:
    if task_type == "persistence_contract":
        return {
            "agent_message": "Staged the reports data contract.",
            "code_files": [
                {
                    "filename": "data/contract.json",
                    "content": json.dumps(
                        {
                            "version": "1",
                            "app_id": "generated-saas-plan",
                            "surfaces": [
                                {
                                    "surface_id": "reports",
                                    "surface_kind": "module",
                                    "collections": [
                                        {
                                            "module_id": "reports",
                                            "name": "reports",
                                            "entity_name": "reports",
                                            "indexes": [
                                                {
                                                    "name": "report_created_at",
                                                    "keys": [{"field": "created_at", "order": -1}],
                                                }
                                            ],
                                        }
                                    ],
                                }
                            ],
                        },
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n",
                },
                {
                    "filename": "data/migrations/001_reports_indexes.json",
                    "content": json.dumps(
                        {
                            "migration_id": "001_reports_indexes",
                            "version": "1",
                            "operations": [
                                {
                                    "type": "ensure_collection",
                                    "module_id": "reports",
                                    "entity_name": "reports",
                                },
                                {
                                    "type": "ensure_index",
                                    "module_id": "reports",
                                    "entity_name": "reports",
                                    "index": {
                                        "name": "report_created_at",
                                        "keys": [{"field": "created_at", "order": -1}],
                                    },
                                },
                            ],
                        },
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n",
                },
            ],
        }

    if task_type == "subscription_config":
        return {
            "agent_message": "Materialized the SaaS subscription contract.",
            "code_files": [
                {
                    "filename": "config/subscriptions.yaml",
                    "content": (
                        "schema_version: mozaiks.subscriptions.v1\n"
                        "label: Generated SaaS Plan\n"
                        "default_plan_id: free\n"
                        "assignment_store:\n"
                        "  data_alias: billing.subscriptions\n"
                        "  app_id_field: app_id\n"
                        "  user_id_field: user_id\n"
                        "  status_field: status\n"
                        "  plan_id_field: plan_id\n"
                        "  capabilities_field: granted_capabilities\n"
                        "  plan_snapshot_field: plan_snapshot\n"
                        "  active_statuses: [active, trialing]\n"
                        "plans:\n"
                        "  - plan_id: free\n"
                        "    label: Free\n"
                        "    capabilities: [reports.view]\n"
                        "  - plan_id: pro\n"
                        "    label: Pro\n"
                        "    capabilities: [reports.view, reports.export]\n"
                    ),
                }
            ],
        }

    if task_type == "module_contract" and task.get("capability_pack_id") == "reports":
        return {
            "agent_message": "Materialized the reports module contract.",
            "code_files": [
                {
                    "filename": "modules/reports/module.yaml",
                    "content": (
                        "schema_version: mozaiks.module.v1\n"
                        "module:\n"
                        "  id: reports\n"
                        "  display_name: Reports\n"
                        "  version: 1.0.0\n"
                        "  handler: backend.handler:ReportsModule\n"
                        "actions:\n"
                        "  - id: view_report\n"
                        "    description: View reports.\n"
                        "    handler_method: view_report\n"
                        "    input_schema:\n"
                        "      type: object\n"
                        "      properties: {}\n"
                        "    output_schema:\n"
                        "      type: object\n"
                        "  - id: export_report\n"
                        "    description: Export a report.\n"
                        "    handler_method: export_report\n"
                        "    entitlement_gate: reports.export\n"
                        "    input_schema:\n"
                        "      type: object\n"
                        "      properties: {}\n"
                        "    output_schema:\n"
                        "      type: object\n"
                        "permissions:\n"
                        "  - id: reports.read\n"
                        "    description: Read reports.\n"
                        "capabilities:\n"
                        "  - capability_id: reports.view\n"
                        "    kind: action\n"
                        "    target: view_report\n"
                        "    title: View reports\n"
                        "  - capability_id: reports.export\n"
                        "    kind: action\n"
                        "    target: export_report\n"
                        "    title: Export reports\n"
                    ),
                },
                {
                    "filename": "modules/reports/contracts/events.yaml",
                    "content": "events: []\n",
                },
            ],
        }

    if task_type == "data_models" and task.get("capability_pack_id") == "reports":
        return {
            "agent_message": "Materialized the reports schemas.",
            "code_files": [
                {
                    "filename": "modules/reports/backend/schemas.py",
                    "content": (
                        "from __future__ import annotations\n"
                        "\n"
                        "from pydantic import BaseModel\n"
                        "\n"
                        "\n"
                        "class ReportViewRequest(BaseModel):\n"
                        "    report_id: str | None = None\n"
                        "\n"
                        "\n"
                        "class ReportExportRequest(BaseModel):\n"
                        "    report_id: str | None = None\n"
                    ),
                }
            ],
        }

    if task_type == "business_services" and task.get("capability_pack_id") == "reports":
        return {
            "agent_message": "Materialized the reports backend.",
            "code_files": [
                {
                    "filename": "modules/reports/backend/handler.py",
                    "content": (
                        "from .service import ReportsService\n"
                        "\n"
                        "\n"
                        "class ReportsModule:\n"
                        "    def __init__(self):\n"
                        "        self.service = ReportsService()\n"
                        "\n"
                        "    async def view_report(self, ctx, **params):\n"
                        "        return await self.service.view_report(ctx, **params)\n"
                        "\n"
                        "    async def export_report(self, ctx, **params):\n"
                        "        return await self.service.export_report(ctx, **params)\n"
                    ),
                },
                {
                    "filename": "modules/reports/backend/service.py",
                    "content": (
                        "from .repo import ReportsRepo\n"
                        "\n"
                        "\n"
                        "class ReportsService:\n"
                        "    async def view_report(self, ctx, **params):\n"
                        "        return {\"reports\": await ReportsRepo(ctx).list_reports()}\n"
                        "\n"
                        "    async def export_report(self, ctx, **params):\n"
                        "        report_id = str(params.get(\"report_id\") or \"report_1\")\n"
                        "        return await ReportsRepo(ctx).export_report(report_id)\n"
                    ),
                },
                {
                    "filename": "modules/reports/backend/repo.py",
                    "content": (
                        "class ReportsRepo:\n"
                        "    def __init__(self, ctx):\n"
                        "        self.ctx = ctx\n"
                        "\n"
                        "    async def list_reports(self):\n"
                        "        return [\n"
                        "            {\"id\": \"report_1\", \"title\": \"Retention\", \"created_at\": \"2026-08-11T00:00:00+00:00\"}\n"
                        "        ]\n"
                        "\n"
                        "    async def export_report(self, report_id):\n"
                        "        return {\"report_id\": report_id, \"exported\": True, \"download_url\": f\"/exports/{report_id}.csv\"}\n"
                    ),
                },
                {
                    "filename": "modules/reports/backend/policy.py",
                    "content": (
                        "def reports_scope(*, app_id=None, user_id=None):\n"
                        "    return {\"app_id\": app_id, \"user_id\": user_id}\n"
                    ),
                },
            ],
        }

    if task_type == "module_contract" and task.get("capability_pack_id") == "entitlement_dispatch":
        return {
            "agent_message": "Materialized the entitlement dispatch facade.",
            "code_files": [
                {
                    "filename": "modules/entitlement_dispatch/module.yaml",
                    "content": (
                        "schema_version: mozaiks.module.v1\n"
                        "module:\n"
                        "  id: entitlement_dispatch\n"
                        "  display_name: Entitlement Dispatch\n"
                        "  version: 1.0.0\n"
                        "  handler: backend.handler:EntitlementDispatchModule\n"
                        "actions:\n"
                        "  - id: activate_subscription\n"
                        "    description: Activate a subscription assignment.\n"
                        "    handler_method: activate_subscription\n"
                        "    input_schema:\n"
                        "      type: object\n"
                        "      properties:\n"
                        "        user_id: {type: string}\n"
                        "        plan_id: {type: string}\n"
                        "    output_schema:\n"
                        "      type: object\n"
                        "  - id: deactivate_subscription\n"
                        "    description: Deactivate a subscription assignment.\n"
                        "    handler_method: deactivate_subscription\n"
                        "    input_schema:\n"
                        "      type: object\n"
                        "      properties: {}\n"
                        "    output_schema:\n"
                        "      type: object\n"
                    ),
                }
            ],
        }

    if task_type == "business_services" and task.get("capability_pack_id") == "entitlement_dispatch":
        return {
            "agent_message": "Materialized the entitlement dispatch backend.",
            "code_files": [
                {
                    "filename": "modules/entitlement_dispatch/backend/handler.py",
                    "content": (
                        "from .service import EntitlementDispatchService\n"
                        "\n"
                        "\n"
                        "class EntitlementDispatchModule:\n"
                        "    def __init__(self):\n"
                        "        self.service = EntitlementDispatchService()\n"
                        "\n"
                        "    async def activate_subscription(self, ctx, **params):\n"
                        "        return await self.service.activate_subscription(ctx, **params)\n"
                        "\n"
                        "    async def deactivate_subscription(self, ctx, **params):\n"
                        "        return await self.service.deactivate_subscription(ctx, **params)\n"
                    ),
                },
                {
                    "filename": "modules/entitlement_dispatch/backend/service.py",
                    "content": (
                        "class EntitlementDispatchService:\n"
                        "    async def activate_subscription(self, ctx, **params):\n"
                        "        return {\"activated\": True, \"plan_id\": params.get(\"plan_id\"), \"user_id\": params.get(\"user_id\")}\n"
                        "\n"
                        "    async def deactivate_subscription(self, ctx, **params):\n"
                        "        return {\"deactivated\": True}\n"
                    ),
                },
                {
                    "filename": "modules/entitlement_dispatch/backend/repo.py",
                    "content": (
                        "class EntitlementDispatchRepo:\n"
                        "    async def write_assignment(self, **kwargs):\n"
                        "        return kwargs\n"
                    ),
                },
            ],
        }

    if task_type == "page_bundle":
        return {
            "agent_message": "Materialized the app shell and reports page.",
            "code_files": [
                {
                    "filename": "app.json",
                    "content": json.dumps(
                        {
                            "appId": "generated-saas-plan",
                            "appName": "Generated SaaS Plan",
                            "version": "1.0.0",
                            "authRequired": True,
                            "startup": {"landing_spot": "/reports"},
                        },
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n",
                },
                {
                    "filename": "ui/pages/reports.yaml",
                    "content": (
                        "name: Reports\n"
                        "route: /reports\n"
                        "title: Reports\n"
                        "sections:\n"
                        "  - id: report-table\n"
                        "    primitive: DataTable\n"
                        "    title: All Reports\n"
                        "    config:\n"
                        "      columns: [id, title, created_at]\n"
                        "      search: true\n"
                        "      selection: single\n"
                        "      api_endpoint: /api/modules/reports/view_report\n"
                        "  - id: export-action\n"
                        "    primitive: ActionButton\n"
                        "    title: null\n"
                        "    config:\n"
                        "      actions:\n"
                        "        - label: Export Report\n"
                        "          action_type: event\n"
                        "          event_type: ui.modal.open\n"
                        "          entitlement_gate: reports.export\n"
                    ),
                },
            ],
        }

    raise AssertionError(f"Unhandled task output for {task_id!r} ({task_type!r})")


def _assignment_docs(app_id: str) -> list[dict[str, Any]]:
    return [
        {
            "_id": f"{app_id}:user-1",
            "app_id": app_id,
            "user_id": "user-1",
            "tenant_id": None,
            "workspace_id": None,
            "plan_id": "pro",
            "status": "active",
            "granted_capabilities": [
                {"capability_id": "reports.view"},
                {"capability_id": "reports.export"},
            ],
            "plan_snapshot": {
                "granted_capabilities": [
                    {"capability_id": "reports.view"},
                    {"capability_id": "reports.export"},
                ]
            },
        }
    ]


async def _materialize_plan_bundle(*, tmp_path: Path) -> tuple[dict[str, str], Path, Any, _Context, _Collection]:
    plan = _load_fixture_plan()
    ctx = _Context(
        {
            "app_id": "generated-saas-plan",
            "app_name": "Generated SaaS Plan",
            "app_slug": "generated-saas-plan",
            "landing_spot": "/reports",
            "chat_id": "generated-saas-plan-chat",
            "build_task_model": "AppBuildTask",
            "app_validation_strategy_used": "skip",
            "app_validation_status": "skipped",
        }
    )

    app_build_plan(AppBuildPlan=plan, context_variables=ctx)
    assert ctx.get("app_plan_ready") is True
    assert len(ctx.get("app_task_batch_items") or []) == len(plan["build_tasks"])

    task_batches = load_task_batches_config("AppGenerator", workflows_root=WORKFLOWS_ROOT)
    assert task_batches is not None

    import mozaiksai.core.adapters.ag2_task_batch_runner as ag2_task_batch_runner

    original_run = ag2_task_batch_runner.AG2TaskBatchRunner.run
    ag2_task_batch_runner.AG2TaskBatchRunner.run = _app_builder_runner()
    try:
        await execute_task_batches_for_trigger(
            workflow_name="AppGenerator",
            trigger_agent="AppPlanAgent",
            batches_config=task_batches,
            agents={
                "ConfigMiddlewareAgent": object(),
                "DatabaseAgent": object(),
                "ModelAgent": object(),
                "ServiceAgent": object(),
                "AppSchemaAgent": object(),
            },
            context_variables=ctx.data,
            chat_id=ctx.get("chat_id"),
            app_id=ctx.get("app_id"),
            user_id="user-1",
            fresh_agents_per_task=False,
        )
    finally:
        ag2_task_batch_runner.AG2TaskBatchRunner.run = original_run

    assembled = await assemble_app_tasks(context_variables=ctx)
    files = _file_map(assembled)
    scaffold = await save_infra_scaffold(
        emit_infra=False,
        emit_auth_adapter=True,
        context_variables=ctx.data,
    )
    files.update(_file_map(scaffold))

    validation = validate_generated_app_bundle(
        GeneratedAppValidationRequest(
            files=files,
            pages=_validation_pages(plan, files),
            build_tasks=plan.get("build_tasks"),
            capability_packs=plan.get("capability_packs"),
        )
    )
    assert validation.passed is True, validation.diagnostics
    assert scan_functional_generated_app(files, capability_packs=plan.get("capability_packs")) == []

    gate = await run_app_bundle_acceptance_gate(
        files=files,
        context_variables=ctx,
        capability_packs=plan.get("capability_packs"),
    )
    assert gate["passed"] is True, gate
    assert gate["functional_completeness"]["passed"] is True

    app_root = tmp_path / "app"
    _write_files(app_root, files)

    loaded = await AppLoader.load(str(app_root))
    assert loaded.definition.name == "Generated SaaS Plan"
    assert [module.name for module in loaded.modules] == ["entitlement_dispatch", "reports"]
    assert [page.name for page in loaded.definition.pages] == ["reports"]

    return files, app_root, loaded, ctx, _Collection([])


def _configure_platform_runtime(
    *,
    app_root: Path,
    loaded: Any,
    assignments: _Collection,
    monkeypatch: pytest.MonkeyPatch,
) -> ModuleExecutor:
    monkeypatch.setenv("PLATFORM_PATH", str(app_root))
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_PROVIDER", "test")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    monkeypatch.setenv("MOZAIKS_DATABASE_STARTUP_POLICY", "best_effort")
    fake_mongo_client = _FakeMongoClient()
    monkeypatch.setattr("mozaiksai.hosts.runtime.get_mongo_client", lambda: fake_mongo_client)
    monkeypatch.setattr("mozaiksai.core.startup.validation.get_mongo_client", lambda: fake_mongo_client)
    reset_auth_adapter()
    register_adapter("test", _TestAuthAdapter)

    entitlement_adapter = ConfiguredEntitlementAdapter(
        config=loaded.subscriptions_config,
        collection_resolver=lambda alias: assignments,
    )
    module_executor = ModuleExecutor(entitlement_checker=entitlement_adapter)
    for module in loaded.modules:
        module_executor.register(
            module.name,
            module.handler,
            action_method_map=module.action_method_map,
            action_permissions=module.action_permissions_map,
            action_schemas=module.action_schemas_map,
            action_entitlements=module.action_entitlement_map,
        )

    registry = ExecutorRegistry()
    registry.register(module_executor)
    platform.app.state.executor_registry = registry
    platform.app.state.subscriptions_config = loaded.subscriptions_config
    platform.app.state.failed_module_names = []
    platform.app.state.startup_degraded = False
    platform.app.state.startup_degraded_reason = None
    platform.app.state.module_action_surfaces = {
        module.name: module.action_api_surface_map for module in loaded.modules
    }
    return module_executor


@pytest.mark.asyncio
async def test_captured_app_plan_materializes_validates_and_boots_through_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous_state = {
        "executor_registry": getattr(platform.app.state, "executor_registry", None),
        "subscriptions_config": getattr(platform.app.state, "subscriptions_config", None),
        "failed_module_names": list(getattr(platform.app.state, "failed_module_names", [])),
        "startup_degraded": getattr(platform.app.state, "startup_degraded", False),
        "startup_degraded_reason": getattr(platform.app.state, "startup_degraded_reason", None),
        "module_action_surfaces": deepcopy(getattr(platform.app.state, "module_action_surfaces", {})),
    }
    try:
        files_run_one, app_root_one, loaded_one, ctx_one, assignments_one = await _materialize_plan_bundle(
            tmp_path=tmp_path / "run1",
        )
        files_run_two, _, loaded_two, ctx_two, _ = await _materialize_plan_bundle(
            tmp_path=tmp_path / "run2",
        )

        assert files_run_one == files_run_two
        assert loaded_one.definition.config["authRequired"] is True
        assert loaded_two.definition.config["authRequired"] is True
        assert ctx_one.get("app_plan_ready") is True
        assert ctx_two.get("app_plan_ready") is True

        module_executor = _configure_platform_runtime(
            app_root=app_root_one,
            loaded=loaded_one,
            assignments=assignments_one,
            monkeypatch=monkeypatch,
        )
        assert module_executor is not None

        with TestClient(platform.app, raise_server_exceptions=False) as client:
            auth_headers = {"Authorization": "Bearer test-token"}
            health = client.get("/health")
            assert health.status_code == 200, health.text

            shell_config = client.get("/api/shell-config")
            assert shell_config.status_code == 200, shell_config.text
            shell_pages = shell_config.json().get("pages", [])
            assert any(page.get("path") == "/reports" for page in shell_pages)

            page_schema = client.get("/api/pages/reports")
            assert page_schema.status_code == 200, page_schema.text
            assert page_schema.json()["route"] == "/reports"

            list_response = client.post(
                "/api/modules/reports/view_report",
                json={"params": {}},
                headers=auth_headers,
            )
            assert list_response.status_code == 200, list_response.text
            assert list_response.json()["reports"][0]["id"] == "report_1"

            denied = client.post(
                "/api/modules/reports/export_report",
                json={"params": {"report_id": "report_1"}},
                headers=auth_headers,
            )
            assert denied.status_code == 402, denied.text
            assert denied.json()["detail"]["error_code"] == "ENTITLEMENT_REQUIRED"

            assignments_one.replace_docs(_assignment_docs("generated-saas-plan"))
            allowed = client.post(
                "/api/modules/reports/export_report",
                json={"params": {"report_id": "report_1"}},
                headers=auth_headers,
            )
            assert allowed.status_code == 200, allowed.text
            assert allowed.json() == {
                "report_id": "report_1",
                "exported": True,
                "download_url": "/exports/report_1.csv",
            }
    finally:
        platform.app.state.executor_registry = previous_state["executor_registry"]
        platform.app.state.subscriptions_config = previous_state["subscriptions_config"]
        platform.app.state.failed_module_names = previous_state["failed_module_names"]
        platform.app.state.startup_degraded = previous_state["startup_degraded"]
        platform.app.state.startup_degraded_reason = previous_state["startup_degraded_reason"]
        platform.app.state.module_action_surfaces = previous_state["module_action_surfaces"]
        auth_registry._adapter_registry.clear()
        reset_auth_adapter()


@pytest.mark.asyncio
async def test_materialized_app_plan_proof_fails_when_handler_drops_declared_action(
    tmp_path: Path,
) -> None:
    files, _, _, _, _ = await _materialize_plan_bundle(tmp_path=tmp_path / "run")
    mutated_files = dict(files)
    mutated_files["modules/reports/backend/handler.py"] = mutated_files[
        "modules/reports/backend/handler.py"
    ].replace(
        "\n    async def export_report(self, ctx, **params):\n        return await self.service.export_report(ctx, **params)\n",
        "\n",
    )

    validation = validate_generated_app_bundle(
        GeneratedAppValidationRequest(
            files=mutated_files,
            pages=_validation_pages(_load_fixture_plan(), mutated_files),
            build_tasks=_load_fixture_plan().get("build_tasks"),
        )
    )
    scan_codes = {item.code for item in scan_functional_generated_app(mutated_files)}

    assert validation.passed is False
    assert any(
        diagnostic.code in {"MISSING_MODULE_ACTION", "MISSING_MODULE_HANDLER"}
        for diagnostic in validation.diagnostics
    )
    assert "MISSING_MODULE_ACTION" in scan_codes or "MISSING_MODULE_HANDLER" in scan_codes
