"""Actual build diagnostics share the existing approved-task repair policy."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from factory_app.workflows.AppGenerator.tools import app_validation
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge, _workflow_tool_invocation
from mozaiksai.core.workflow.context.frozen import detach
from tests.test_run_termination import _appgenerator_contract

PAGE = "ui/pages/custom/focus.jsx"
VITE_ERROR = "\x1b[31m[UNRESOLVED_IMPORT] \x1b[0mCould not resolve '../../lib/moduleApi.js' in ../../../workspace/app/ui/pages/custom/focus.jsx\n"


MISSING_EXPORT = (
    '[MISSING_EXPORT] "default" is not exported by "../../../workspace/app/ui/lib/moduleApi.js".\n'
    '   ╭─[ ../../../workspace/app/ui/pages/custom/focus.jsx:5:8 ]\n'
    '   │\n'
    ' 5 │ import moduleAction from "../../lib/moduleApi.js";\n'
)


def test_observed_vite_missing_export_targets_importer_not_shared_helper():
    errors = app_validation.parse_build_errors(
        MISSING_EXPORT, app_root="/workspace/app", cwd="/opt/mozaiks/web_shell",
    )
    assert errors == [{
        "file": PAGE, "line": 5, "column": 8,
        "message": '"default" is not exported by "../../../workspace/app/ui/lib/moduleApi.js".',
    }]


def test_vite_ansi_diagnostic_resolves_only_the_staged_app_path():
    parsed = app_validation.parse_build_errors(
        VITE_ERROR, app_root="/workspace/app", cwd="/opt/mozaiks/web_shell",
    )
    assert parsed == [{"file": PAGE, "message": "Could not resolve '../../lib/moduleApi.js'"}]
    outside = app_validation.parse_build_errors(
        VITE_ERROR.replace("workspace/app/", "other/app/"),
        app_root="/workspace/app", cwd="/opt/mozaiks/web_shell",
    )
    assert outside[0]["file"] != PAGE


@pytest.mark.parametrize("filename,root,cwd,expected", [
    ("/workspace/app/ui/pages/custom/focus.jsx", "/workspace/app", "/opt/mozaiks/web_shell", PAGE),
    ("ui/pages/custom/focus.jsx", "/workspace/app", "/opt/mozaiks/web_shell", "/opt/mozaiks/web_shell/ui/pages/custom/focus.jsx"),
    ("../app-other/ui/pages/custom/focus.jsx", "/workspace/app", "/workspace/app", "/workspace/app-other/ui/pages/custom/focus.jsx"),
    ("C:/temp/app/ui/pages/custom/focus.jsx", "C:/temp/app", "C:/shell", PAGE),
    ("/workspace/app/ui/../../../foreign.jsx", "/workspace/app", "/opt/mozaiks/web_shell", "/foreign.jsx"),
])
def test_build_error_paths_use_exact_staging_root_not_suffix_matching(filename, root, cwd, expected):
    errors = app_validation.parse_build_errors(
        f'\x1b[31m[vite]: Rollup failed to resolve import "./Widget" from "{filename}".\x1b[0m',
        app_root=root, cwd=cwd,
    )
    assert errors[0]["file"] == expected
    assert "\x1b" not in errors[0]["message"]


@pytest.mark.parametrize("case", ["owned", "owned_missing_export", "unowned", "missing_evidence", "exhausted", "unparsed", "infrastructure"])
@pytest.mark.asyncio
async def test_build_failure_after_passed_acceptance_uses_existing_repair_or_ends(monkeypatch, case):
    files = {"app.json": "{}", PAGE: "export default function Focus() { return null; }"}
    _, policy, _ = _appgenerator_contract()
    task = {"task_id": "page", "task_type": "page_bundle", "initial_agent": "AppSchemaAgent",
            "owned_paths": [PAGE], "depends_on": []}
    context = ContextVariablesBridge({
        "generated_files": files, "app_assembly_status": "passed",
        "app_build_plan": {"build_tasks": [] if case == "unowned" else [task]},
        "app_task_batch_results": {} if case == "missing_evidence" else {"page": {"code_files": []}},
        "bundle_repair_attempt_count": 2 if case == "exhausted" else 0,
    }, authority_policy=policy)
    context._bind_run(("AppGenerator", "failed-build", "build-repair"), policy)
    passed_check = {"passed": True}
    acceptance = {key: passed_check for key in (
        "bundle_scan", "agent_backend", "module_wiring", "module_implementation",
        "module_runtime_quality", "functional_completeness", "workflow_integration",
        "app_runtime_load", "app_runtime_smoke",
    )}
    acceptance.update(status="passed", passed=True, skipped_checks=[],
                      bundle_repair={"status": "passed", "target_agent": None})
    validation = {
        "validation_status": "failed", "validation_strategy": "docker", "success": False,
        "errors": ["Build failed: dependency could not be resolved"],
        "parsed_errors": [] if case == "unparsed" else [
            {"file": PAGE, "message": "Could not resolve '../../lib/moduleApi.js'"},
        ],
        "infrastructure_failure": case == "infrastructure",
    }
    if case == "owned_missing_export":
        validation["parsed_errors"] = app_validation.parse_build_errors(
            MISSING_EXPORT, app_root="/workspace/app", cwd="/opt/mozaiks/web_shell",
        )
    monkeypatch.setattr(app_validation, "save_auth_scaffold", AsyncMock())
    monkeypatch.setattr(app_validation, "run_app_bundle_acceptance_gate", AsyncMock(return_value=acceptance))
    monkeypatch.setattr(app_validation, "validate_app_build", AsyncMock(return_value=validation))
    with _workflow_tool_invocation(context):
        result = await app_validation.validate_app_bundle_from_request({}, context_variables=context)
    assert result["app_bundle_acceptance_result"]["passed"] is True
    assert result["app_bundle_acceptance_result"]["bundle_repair"]["status"] == "passed"
    assert result["integration_tests_passed"] is False
    repair = result["bundle_repair"]
    assert detach(context.get("bundle_repair_result")) == repair
    if case in {"owned", "owned_missing_export"}:
        assert repair["target_agent"] == "AppSchemaAgent"
        assert repair["active"]["allowed_paths"] == [PAGE]
        assert "moduleApi.js" in repair["repair_request"]
        assert context.get("app_validation_ends_run") is False
        assert context.get("app_build_failure_message") is None
    else:
        assert repair["target_agent"] is None
        assert repair["status"] == "blocked"
        assert context.get("app_validation_ends_run") is True
        assert context.get("app_build_failure_message")
