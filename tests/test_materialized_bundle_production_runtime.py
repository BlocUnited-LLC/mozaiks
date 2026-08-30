from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from factory_app.workflows.AppGenerator.tools.app_build_plan import app_build_plan
from factory_app.workflows.AppGenerator.tools.app_validation import run_app_bundle_acceptance_gate
from factory_app.workflows.AppGenerator.tools.assemble_app_tasks import assemble_app_tasks
from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import scan_generated_bundle
from mozaiksai.core.auth.adapters import registry as auth_registry
from mozaiksai.core.auth.adapters.registry import reset_auth_adapter
from mozaiksai.core.events.unified_event_dispatcher import get_event_dispatcher
from mozaiksai.core.runtime.app.loader import AppLoader
from mozaiksai.core.validation import (
    GeneratedAppValidationRequest,
    scan_functional_generated_app,
    validate_generated_app_bundle,
)
from tests.test_continuous_deterministic_materialization import (
    BUILD_TIMESTAMP,
    LATER_BUILD_TIMESTAMP,
    _Context,
    _FakeMongoClient,
    _load_models,
    _plan_payload,
    _selected_packs,
    _typed_task_outputs,
    _write_bundle,
)


@pytest.fixture(autouse=True)
def _restore_global_runtime_state() -> Any:
    from mozaiksai.core.workflow import workflow_manager
    from mozaiksai.core.workflow.outputs import structured

    adapter_registry = dict(auth_registry._adapter_registry)
    adapter_instance = auth_registry._adapter_instance
    workflow_instance = workflow_manager.UnifiedWorkflowManager._instance
    workflow_models = dict(structured._workflow_models)
    workflow_registries = dict(structured._workflow_registries)
    original_sys_path = list(sys.path)
    module_prefixes = ("mozaiks_runtime_module_", "services")
    module_cache = {
        name: module
        for name, module in sys.modules.items()
        if any(name == prefix or name.startswith(f"{prefix}.") for prefix in module_prefixes)
    }
    dispatcher = get_event_dispatcher()
    dispatcher_handlers = dict(getattr(dispatcher, "_event_handlers", {}))
    try:
        yield
    finally:
        auth_registry._adapter_registry.clear()
        auth_registry._adapter_registry.update(adapter_registry)
        auth_registry._adapter_instance = adapter_instance
        workflow_manager.UnifiedWorkflowManager._instance = workflow_instance
        structured._workflow_models.clear()
        structured._workflow_models.update(workflow_models)
        structured._workflow_registries.clear()
        structured._workflow_registries.update(workflow_registries)
        sys.path[:] = original_sys_path
        for name in list(sys.modules):
            if any(name == prefix or name.startswith(f"{prefix}.") for prefix in module_prefixes):
                sys.modules.pop(name, None)
        sys.modules.update(module_cache)
        dispatcher._event_handlers.clear()
        dispatcher._event_handlers.update(dispatcher_handlers)


def _file_map(code_files: list[dict[str, Any]]) -> dict[str, str]:
    return {str(item["filename"]): str(item["content"]) for item in code_files}


async def _assemble_from_payload(
    *,
    build_timestamp: str = BUILD_TIMESTAMP,
    plan_payload: dict[str, Any] | None = None,
    task_outputs: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, str], _Context]:
    models = _load_models()
    typed_plan = models["AppBuildPlanOutput"].model_validate(plan_payload or _plan_payload())
    context = _Context(
        {
            "app_id": "deterministic-reports",
            "app_name": "Deterministic Reports",
            "app_slug": "deterministic-reports",
            "build_timestamp": build_timestamp,
            "readiness_profile": "host_operator_platform",
            "evidence_mode": "local_no_spend",
            "capability_packs": _selected_packs(),
        }
    )
    plan = typed_plan.AppBuildPlan.model_dump(mode="json")
    app_build_plan(AppBuildPlan=plan, context_variables=context)
    context.set("app_task_batch_results", task_outputs or _typed_task_outputs(models))
    assembled = await assemble_app_tasks(context_variables=context)
    return _file_map(assembled["code_files"]), context


def _save_platform_state(platform: Any) -> dict[str, Any]:
    return {
        "executor_registry": platform.executor_registry,
        "app_executor_registry": getattr(platform.app.state, "executor_registry", None),
        "subscriptions_config": getattr(platform.app.state, "subscriptions_config", None),
        "startup_degraded": getattr(platform.app.state, "startup_degraded", False),
        "startup_degraded_reason": getattr(platform.app.state, "startup_degraded_reason", None),
        "failed_module_names": list(getattr(platform.app.state, "failed_module_names", [])),
        "module_action_surfaces": deepcopy(getattr(platform.app.state, "module_action_surfaces", {})),
        "workflow_capability_routes": deepcopy(getattr(platform.app.state, "workflow_capability_routes", {})),
        "module_event_router": getattr(platform.app.state, "module_event_router", None),
    }


def _restore_platform_state(platform: Any, state: dict[str, Any]) -> None:
    platform.executor_registry = state["executor_registry"]
    platform.app.state.executor_registry = state["app_executor_registry"]
    platform.app.state.subscriptions_config = state["subscriptions_config"]
    platform.app.state.startup_degraded = state["startup_degraded"]
    platform.app.state.startup_degraded_reason = state["startup_degraded_reason"]
    platform.app.state.failed_module_names = state["failed_module_names"]
    platform.app.state.module_action_surfaces = state["module_action_surfaces"]
    platform.app.state.workflow_capability_routes = state["workflow_capability_routes"]
    if state["module_event_router"] is None:
        if hasattr(platform.app.state, "module_event_router"):
            delattr(platform.app.state, "module_event_router")
    else:
        platform.app.state.module_event_router = state["module_event_router"]


def _assert_timestamp_owned_only(
    files_one: dict[str, str],
    files_later: dict[str, str],
) -> None:
    changed_paths = {
        path
        for path in files_one
        if files_one[path] != files_later[path]
    }
    assert changed_paths == {"provenance.yaml", ".mozaiks/pack_provenance.json"}

    app_provenance = yaml.safe_load(files_one["provenance.yaml"])
    later_app_provenance = yaml.safe_load(files_later["provenance.yaml"])
    assert app_provenance["created_with"].pop("timestamp") == BUILD_TIMESTAMP
    assert later_app_provenance["created_with"].pop("timestamp") == LATER_BUILD_TIMESTAMP
    assert app_provenance == later_app_provenance

    pack_provenance = json.loads(files_one[".mozaiks/pack_provenance.json"])
    later_pack_provenance = json.loads(files_later[".mozaiks/pack_provenance.json"])
    assert pack_provenance.pop("generated_at") == BUILD_TIMESTAMP
    assert later_pack_provenance.pop("generated_at") == LATER_BUILD_TIMESTAMP
    assert pack_provenance == later_pack_provenance


@pytest.mark.asyncio
async def test_exact_materialized_file_map_validates_boots_and_executes_http(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    files_one, context = await _assemble_from_payload(build_timestamp=BUILD_TIMESTAMP)
    files_two, _ = await _assemble_from_payload(build_timestamp=BUILD_TIMESTAMP)
    files_later, _ = await _assemble_from_payload(build_timestamp=LATER_BUILD_TIMESTAMP)

    assert files_one == files_two
    _assert_timestamp_owned_only(files_one, files_later)
    assert context.get("generated_files") == files_one

    page = yaml.safe_load(files_one["ui/pages/reports.yaml"])
    assert page["sections"][0]["primitive"] == "DataTable"
    assert page["sections"][0]["config"]["api_endpoint"] == "/api/modules/reports/list_reports"

    assert scan_generated_bundle(files_one, capability_packs=_selected_packs()) == []
    assert scan_functional_generated_app(files_one, capability_packs=_selected_packs()) == []
    validation = validate_generated_app_bundle(
        GeneratedAppValidationRequest(
            files=files_one,
            build_tasks=context.get("app_build_plan")["build_tasks"],
            capability_packs=_selected_packs(),
        )
    )
    assert validation.passed is True, validation.diagnostics
    acceptance = await run_app_bundle_acceptance_gate(
        files=files_one,
        context_variables=context,
        capability_packs=_selected_packs(),
    )
    assert acceptance["passed"] is True, acceptance
    assert acceptance["functional_completeness"]["passed"] is True
    assert acceptance["app_runtime_load"]["passed"] is True

    app_root = tmp_path / "app"
    _write_bundle(app_root, files_one)
    loaded = await AppLoader.load(str(app_root))
    assert loaded.failed_module_names == []
    assert [module.name for module in loaded.modules] == ["reports"]

    fake_mongo_client = _FakeMongoClient()
    monkeypatch.setenv("PLATFORM_PATH", str(app_root))
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    monkeypatch.setenv("MOZAIKS_DATABASE_STARTUP_POLICY", "best_effort")
    monkeypatch.setattr("mozaiksai.hosts.runtime.get_mongo_client", lambda: fake_mongo_client)
    monkeypatch.setattr("mozaiksai.core.startup.validation.get_mongo_client", lambda: fake_mongo_client)
    reset_auth_adapter()

    from mozaiksai.core.runtime.composition.executor_registry import ExecutorRegistry
    from mozaiksai.hosts import platform

    saved = _save_platform_state(platform)
    platform.executor_registry = ExecutorRegistry()
    platform.app.state.executor_registry = platform.executor_registry
    monkeypatch.setattr(platform.runtime_app, "mongo_client", fake_mongo_client)
    try:
        with TestClient(platform.app, raise_server_exceptions=False) as client:
            health = client.get("/health")
            assert health.status_code == 200, health.text
            assert health.json()["status"] == "ok"

            page_response = client.get("/api/pages/reports")
            assert page_response.status_code == 200, page_response.text
            assert page_response.json()["sections"][0]["config"]["api_endpoint"] == (
                "/api/modules/reports/list_reports"
            )

            action_response = client.post(
                "/api/modules/reports/list_reports",
                json={"params": {}},
            )
            assert action_response.status_code == 200, action_response.text
            assert action_response.json() == {
                "reports": [
                    {"id": "report-1", "title": "Readiness", "status": "ready"}
                ]
            }
    finally:
        _restore_platform_state(platform, saved)


@pytest.mark.asyncio
async def test_materialized_broken_handler_fails_before_bootable(tmp_path: Path) -> None:
    models = _load_models()
    task_outputs = _typed_task_outputs(models)
    service_files = task_outputs["reports.services"]["python_files"]
    for file in service_files:
        if file["path"] == "modules/reports/backend/handler.py":
            file["content"] = (
                "from .missing_dependency import MissingService\n\n"
                "class ReportsModule:\n"
                "    async def list_reports(self, ctx, **params):\n"
                "        return await MissingService().list_reports(ctx, **params)\n"
            )

    files, context = await _assemble_from_payload(task_outputs=task_outputs)

    loaded_root = tmp_path / "app"
    _write_bundle(loaded_root, files)
    loaded = await AppLoader.load(str(loaded_root))
    assert loaded.failed_module_names == ["reports"]

    acceptance = await run_app_bundle_acceptance_gate(
        files=files,
        context_variables=context,
        capability_packs=_selected_packs(),
    )
    assert acceptance["passed"] is False
    assert acceptance["app_runtime_load"]["passed"] is False


@pytest.mark.asyncio
async def test_materialized_unresolved_page_action_fails_before_bootable() -> None:
    broken_plan = deepcopy(_plan_payload())
    section_hint = broken_plan["AppBuildPlan"]["pages"][0]["sections_hint"][0]
    section_hint["config_hint"] = json.dumps(
        {
            "columns": ["id", "title", "status"],
            "api_endpoint": "/api/modules/reports/archive_reports",
            "search": True,
        },
        sort_keys=True,
    )

    files, context = await _assemble_from_payload(plan_payload=broken_plan)

    scanner_errors = scan_generated_bundle(files, capability_packs=_selected_packs())
    assert any("archive_reports" in error for error in scanner_errors)
    diagnostics = scan_functional_generated_app(files, capability_packs=_selected_packs())
    assert {item.code for item in diagnostics} >= {"MISSING_MODULE_ACTION"}
    validation = validate_generated_app_bundle(
        GeneratedAppValidationRequest(
            files=files,
            build_tasks=context.get("app_build_plan")["build_tasks"],
            capability_packs=_selected_packs(),
        )
    )
    assert validation.passed is False
    acceptance = await run_app_bundle_acceptance_gate(
        files=files,
        context_variables=context,
        capability_packs=_selected_packs(),
    )
    assert acceptance["passed"] is False
