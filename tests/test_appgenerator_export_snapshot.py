"""Exports require acceptance of the actual final bundle, including its ZIP bytes."""

import zipfile
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from factory_app.workflows.AppGenerator.tools import export_app_code
from factory_app.workflows.AppGenerator.tools.app_validation import run_app_bundle_acceptance_gate
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge, _wrap_tool_with_context
from tests.test_app_validation_strategy import _accept_support_tasks, _Context

_HANDLER = "modules/support_tickets/backend/handler.py"


async def _accepted_context(*, line_ending="\n"):
    from scripts.smoke_appgenerator_live_acceptance import (
        build_appgenerator_acceptance_files,
        default_workflow_integration,
    )

    integration = default_workflow_integration()
    files = build_appgenerator_acceptance_files(integration)
    files[_HANDLER] = files[_HANDLER].replace("\r\n", "\n").replace("\n", line_ending)
    context = _Context({
        "generated_files": files,
        "generated_workflow_name": integration["workflow_name"],
        "generated_workflow_capability_id": integration["capability_id"],
        "generated_workflow_startup_mode": integration["startup_mode"],
        "generated_workflow_trigger_events": integration["trigger_events"],
        "app_validation_status": "skipped", "app_validation_strategy_used": "skip",
    })
    _accept_support_tasks(context, files)
    accepted = await run_app_bundle_acceptance_gate(files=files, context_variables=context)
    assert accepted["passed"] is True, accepted
    assert export_app_code.resolve_export_gate(context)["allow_export"] is True
    return context


def test_status_only_acceptance_cannot_authorize_export():
    context = _Context({
        "app_bundle_acceptance_status": "passed", "app_validation_status": "skipped",
        "integration_tests_passed": True, "generated_files": {"app.json": "{}"},
    })

    result = export_app_code.resolve_export_gate(context)

    assert result["allow_export"] is False
    assert any("matching acceptance evidence" in reason for reason in result["reasons"])


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [False, True])
async def test_export_gate_reads_frozen_runtime_context_through_tool_injection(changed):
    context = await _accepted_context()
    if changed:
        context.get("generated_files")[_HANDLER] += "# Unvalidated addition\n"
    bridge = ContextVariablesBridge(context._data)
    before = bridge.snapshot()
    assert isinstance(bridge.get("app_build_plan"), MappingProxyType)
    assert isinstance(bridge.get("app_build_plan")["build_tasks"], tuple)
    assert isinstance(bridge.get("generated_files"), MappingProxyType)
    injected_gate = _wrap_tool_with_context(export_app_code.resolve_export_gate, bridge)

    result = injected_gate()

    assert result["allow_export"] is (not changed), result
    assert bridge.snapshot() == before
    assert bridge.consume_context_updates() == {"set": {}, "delete": []}


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["content", "plan", "task_evidence"])
async def test_changed_context_cannot_reuse_accepted_snapshot(mutation):
    context = await _accepted_context()
    if mutation == "content":
        context.get("generated_files")[_HANDLER] += "# Unvalidated addition\n"
    elif mutation == "plan":
        context.get("app_build_plan")["pages"] = [{"name": "New", "route": "/new"}]
    else:
        context.get("app_task_batch_results").pop("support_contract")

    result = export_app_code.resolve_export_gate(context)

    assert result["allow_export"] is False
    assert any("matching acceptance evidence" in reason for reason in result["reasons"])


@pytest.mark.asyncio
@pytest.mark.parametrize("line_ending", ["\n", "\r\n"])
@pytest.mark.parametrize("tamper", [None, "changed", "missing", "extra", "duplicate", "unsafe", "foreign_prefix"])
async def test_github_export_checks_the_zip_before_any_external_call(monkeypatch, tmp_path, line_ending, tamper):
    context = await _accepted_context(line_ending=line_ending)
    files = dict(context.get("generated_files"))
    if tamper == "changed":
        files[_HANDLER] += "# Unvalidated addition\n"
    elif tamper == "missing":
        files.pop("app.json")
    elif tamper == "extra":
        files["unexpected.py"] = "VALUE = 1\n"
    bundle = tmp_path / "bundle.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        for path, content in files.items():
            archive.writestr("bundle/" + path, content.encode("utf-8"))
        if tamper == "duplicate":
            with pytest.warns(UserWarning, match="Duplicate name"):
                archive.writestr("bundle/" + _HANDLER, files[_HANDLER].encode("utf-8"))
        elif tamper == "unsafe":
            archive.writestr("bundle/../outside.py", b"VALUE = 1\n")
        elif tamper == "foreign_prefix":
            archive.writestr("foreign/extra.py", b"VALUE = 1\n")
    latest = AsyncMock(return_value=None)
    external = AsyncMock(return_value=SimpleNamespace(
        success=False, model_dump=lambda: {"success": False},
    ))
    monkeypatch.setattr(export_app_code, "get_latest_workflow_export", latest)
    monkeypatch.setattr(export_app_code.export_to_github_tool, "execute", external)

    result = await export_app_code.export_app_code_to_github(
        app_id="accepted-app", bundle_path=str(bundle), context_variables=context,
    )

    if tamper is None:
        latest.assert_awaited_once()
        external.assert_awaited_once()
        assert not result.get("blocked")
    else:
        latest.assert_not_awaited()
        external.assert_not_awaited()
        assert result["blocked"] is True
        assert result["reasons"]
