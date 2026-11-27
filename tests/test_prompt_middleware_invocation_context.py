from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.execution.middleware import MozaiksPromptMiddleware


def test_first_planner_call_carries_verified_login_and_module_task_ownership(tmp_path, monkeypatch):
    from factory_app.workflows.AppGenerator.tools.hook_file_contract_context import (
        inject_cookie_cutter_contracts_context,
    )
    from tests.test_revision_result_module_coverage import _case

    root = Path(__file__).resolve().parents[1] / "factory_app"
    monkeypatch.setenv("MOZAIKS_WORKFLOWS_PATH", str(root / "workflows"))
    monkeypatch.setenv("MOZAIKS_BUILD_CONTEXT_PATH", str(root / "build_context"))
    ctx, _ = _case(tmp_path)
    ctx["upstream_workflow_bundle_artifact_version_id"] = ctx["workflow_bundle_artifact_version_id"]
    ctx.update(revision_source_artifact_version_id="approved-source", retained_feature_login={
        "source_artifact_version_id": "approved-source", "auth_strategy": "basic-login", "auth_mode": "brokered_oidc",
    }, revision_result_module_coverage={"tasks": {"module_contract": True, "data_models": True, "business_services": True}})

    async def outbound(state):
        hook = MozaiksPromptMiddleware(None, None, middleware_functions=[inject_cookie_cutter_contracts_context],
                                       agent_name="AppPlanAgent", base_system_message="Plan the approved app.",
                                       context_bridge=ContextVariablesBridge(state))
        async def receive(_events, run):
            return run.prompt[0]
        # Runtime-only state supplied in the invocation cannot replace the closed hook inputs.
        run = SimpleNamespace(variables={"retained_feature_login": {"auth_strategy": "third-party"}},
                              dependencies={}, prompt=[])
        return await hook.on_llm_call(receive, [], run)

    text = asyncio.run(outbound(ctx))
    auth = json.dumps({"auth_strategy": "basic-login", "auth_mode": "brokered_oidc", "roles": []}, sort_keys=True)
    assert auth in text
    assert "data_models can own only modules/tasks/backend/schemas.py" in text
    assert "capability_pack_id and surface_id tasks, surface_kind module" in text
    assert "modules/tasks/backend/account_data_handler.py belongs to business_services" in text
    assert "approved-source" not in text
    assert auth not in asyncio.run(outbound({**ctx, "revision_source_artifact_version_id": "different-source"}))


def test_prompt_hooks_receive_task_scoped_variables_without_mutating_workflow_state(monkeypatch):
    from factory_app.workflows.AppGenerator.tools.hook_file_contract_context import (
        inject_cookie_cutter_contracts_context,
    )

    repo_root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("MOZAIKS_WORKFLOWS_PATH", str(repo_root / "factory_app" / "workflows"))
    monkeypatch.setenv("MOZAIKS_BUILD_CONTEXT_PATH", str(repo_root / "factory_app" / "build_context"))

    workflow_context = ContextVariablesBridge({"workflow_value": "root", "current_build_task": None})
    task_context = {
        "current_build_task": {
            "task_type": "business_services",
            "capability_pack_id": "notes",
            "owned_paths": [
                "modules/notes/backend/policy.py",
                "modules/notes/backend/service.py",
            ],
        },
        "unlisted_value": "must-not-reach-prompt-hooks",
    }
    captured_context = {}

    def capture_context(agent, _messages):
        captured_context.update(agent.context_variables)

    runner = MozaiksPromptMiddleware(
        None,
        None,
        middleware_functions=[inject_cookie_cutter_contracts_context, capture_context],
        agent_name="ServiceAgent",
        base_system_message="Generate the assigned backend files.",
        context_bridge=workflow_context,
        context_variables=["current_build_task"],
    )

    prompt = asyncio.run(
        runner._run_prompt_middleware(SimpleNamespace(variables=task_context, prompt=[]))
    )

    assert "final artifact inventory, not a request to author every file" in prompt
    assert (
        "Code-rendered policy artifacts; omit from python_files:\n"
        "  - modules/notes/backend/policy.py"
    ) in prompt
    assert captured_context["workflow_value"] == "root"
    assert captured_context["current_build_task"] == task_context["current_build_task"]
    assert "unlisted_value" not in captured_context
    assert workflow_context.snapshot() == {"workflow_value": "root", "current_build_task": None}
