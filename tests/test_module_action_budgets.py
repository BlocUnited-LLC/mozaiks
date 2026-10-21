"""A declared action budget survives generation and cannot be set by callers."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml
from pydantic import ValidationError

from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import scan_app_contracts
from mozaiksai.core.runtime.app.module_loader import ActionDef, ModuleLoader, ModuleLoadError
from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor, ModuleRequest
from mozaiksai.core.workflow.generator_support.code_files import extract_code_file_map_from_payload
from mozaiksai.core.workflow.outputs.structured import (
    build_models_from_config,
    get_provider_response_model,
)
from tests.module_authority_test_helpers import trusted_framework_authority


def _action(**extra):
    return {"id": "process", "description": "Process an export", "handler_method": "process", **extra}


def _manifest(action):
    return {
        "schema_version": "mozaiks.module.v1",
        "module": {"id": "exports", "handler": "backend.handler:ExportsModule"},
        "actions": [action],
    }


def _request(**params):
    return ModuleRequest(
        module="exports", action="process", params=params, app_id="test",
        authority=trusted_framework_authority(),
    )


class _Handler:
    async def process(self, ctx, **params):
        await asyncio.sleep(0)
        return params


def _executor(handler=None, **registration):
    executor = ModuleExecutor(audit_logger=AsyncMock())
    executor.register("exports", handler or _Handler(), action_method_map={"process": "process"}, **registration)
    return executor


@pytest.mark.parametrize("value", [None, 1, 1860, 3600])
def test_canonical_action_budget_accepts_only_supported_values(value):
    assert ActionDef.model_validate(_action(timeout_seconds=value)).timeout_seconds == value


@pytest.mark.parametrize("value", [0, -1, 3601, True, False, 1.0, 1.5, "1860", "", [], {}])
def test_invalid_budget_is_rejected_by_loader_registration_materializer_and_scanner(value, tmp_path):
    manifest = _manifest(_action(timeout_seconds=value))
    with pytest.raises(ValidationError):
        ActionDef.model_validate(manifest["actions"][0])
    executor = ModuleExecutor()
    with pytest.raises(ValidationError):
        executor.register("exports", _Handler(), action_method_map={"process": "process"},
                          action_timeouts={"process": value})
    assert executor.registered_modules() == []
    with pytest.raises(ValidationError):
        extract_code_file_map_from_payload({"module_contract": {"module_id": "exports", "module_yaml": manifest}})
    manifest_yaml = yaml.safe_dump(manifest)
    errors = scan_app_contracts({"modules/exports/module.yaml": manifest_yaml})
    assert any("timeout_seconds" in error for error in errors)
    module_dir = tmp_path / "modules" / "exports"
    module_dir.mkdir(parents=True)
    (module_dir / "module.yaml").write_text(manifest_yaml, encoding="utf-8")
    with pytest.raises(ModuleLoadError, match="timeout_seconds"):
        ModuleLoader(str(tmp_path)).load("exports")


def test_registration_rejects_unknown_budget_without_replacing_existing_module():
    executor = _executor()
    with pytest.raises(ValueError, match="declared actions"):
        executor.register("exports", object(), action_method_map={"process": "process"},
                          action_timeouts={"unknown": 1860})
    assert executor.registered_modules() == ["exports"]
    assert executor._action_methods["exports"] == {"process": "process"}


@pytest.mark.asyncio
@pytest.mark.parametrize("declared,default,expected", [(None, "30", 30.0), (None, "0", None), (1860, "30", 1860), (1, "0", 1)])
async def test_only_registered_budget_controls_wait_for(monkeypatch, declared, default, expected):
    monkeypatch.setenv("MODULE_ACTION_TIMEOUT_SECONDS", default)
    observed = []
    real_wait_for = asyncio.wait_for

    async def record_wait(coro, timeout):
        observed.append(timeout)
        return await real_wait_for(coro, timeout)

    monkeypatch.setattr(asyncio, "wait_for", record_wait)
    executor = _executor(action_timeouts={"process": declared})
    for supplied in (0, 3600, "disable"):
        result = await executor.execute(_request(timeout_seconds=supplied))
        assert result.success
        assert result.data == {"timeout_seconds": supplied}
    assert observed == ([] if expected is None else [expected] * 3)
    with pytest.raises(TypeError):
        ModuleRequest(module="exports", action="process", timeout_seconds=3600,
                      authority=trusted_framework_authority())


@pytest.mark.asyncio
async def test_reregistration_restores_default_and_copies_registration(monkeypatch):
    monkeypatch.setenv("MODULE_ACTION_TIMEOUT_SECONDS", "0.001")

    class Handler:
        async def process(self, ctx):
            await asyncio.sleep(0.02)
            return {"finished": True}

    budgets = {"process": 1}
    executor = _executor(Handler(), action_timeouts=budgets)
    budgets["process"] = None
    assert (await executor.execute(_request())).success
    executor.register("exports", Handler(), action_method_map={"process": "process"})
    assert (await executor.execute(_request())).error_code == "ACTION_TIMEOUT"


@pytest.mark.asyncio
async def test_declared_deadline_cancels_handler_and_waits_for_cleanup(monkeypatch):
    monkeypatch.setenv("MODULE_ACTION_TIMEOUT_SECONDS", "3600")
    cleaned = asyncio.Event()

    class Handler:
        async def process(self, ctx):
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0.01)
                cleaned.set()

    executor = _executor(Handler(), action_timeouts={"process": 1})
    result = await asyncio.wait_for(executor.execute(_request()), timeout=3)
    assert result.error_code == "ACTION_TIMEOUT"
    assert cleaned.is_set()


@pytest.mark.asyncio
async def test_external_cancellation_propagates_and_cleans_up():
    started, cleaned = asyncio.Event(), asyncio.Event()

    class Handler:
        async def process(self, ctx):
            try:
                started.set()
                await asyncio.Event().wait()
            finally:
                cleaned.set()

    executor = _executor(Handler(), action_timeouts={"process": 1860})
    task = asyncio.create_task(executor.execute(_request()))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleaned.is_set()


@pytest.mark.asyncio
async def test_generated_budget_survives_materialization_loader_and_actual_dispatch(tmp_path, monkeypatch):
    from jsonschema import Draft202012Validator

    config = yaml.safe_load((Path(__file__).resolve().parents[1] /
                            "factory_app/workflows/AppGenerator/structured_outputs.yaml").read_text(encoding="utf-8"))
    model = build_models_from_config(config["models"])["ModuleAction"]
    generated = model.model_validate(_action(
        timeout_seconds=1860, api_surface="internal", input_schema={"type": "object", "properties": []},
        output_schema={"type": "object", "properties": []}, permissions=[], emits=[],
    )).model_dump(mode="json")
    Draft202012Validator(get_provider_response_model(model).model_json_schema()).validate(generated)
    files = extract_code_file_map_from_payload({"module_contract": {
        "module_id": "exports", "module_yaml": _manifest(generated),
    }})
    for path, content in files.items():
        destination = tmp_path / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
    backend = tmp_path / "modules/exports/backend"
    backend.mkdir()
    (backend / "__init__.py").write_text("", encoding="utf-8")
    (backend / "handler.py").write_text(
        "import asyncio\nclass ExportsModule:\n"
        "    async def process(self, ctx):\n        await asyncio.sleep(0.02)\n        return {}\n",
        encoding="utf-8",
    )
    loaded = ModuleLoader(str(tmp_path)).load("exports")
    assert loaded.definition.action_timeout_map == loaded.action_timeout_map == {"process": 1860}
    assert not any("timeout_seconds" in error for error in scan_app_contracts(files))
    executor = ModuleExecutor(audit_logger=AsyncMock())
    executor.register_loaded_module(loaded)
    monkeypatch.setenv("MODULE_ACTION_TIMEOUT_SECONDS", "0.001")
    result = await executor.execute(_request())
    assert result.success
    assert result.data == {}


def test_ordinary_generated_action_retains_null_budget():
    assert ActionDef.model_validate(_action()).timeout_seconds is None
    files = extract_code_file_map_from_payload({"module_contract": {
        "module_id": "exports", "module_yaml": _manifest(_action()),
    }})
    assert "timeout_seconds" not in files["modules/exports/module.yaml"]
