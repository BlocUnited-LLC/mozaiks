from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.execution.middleware import MozaiksPromptMiddleware


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
