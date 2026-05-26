from __future__ import annotations

import json
from pathlib import Path
import sys
import types

from tests.import_utils import import_module_directly

_hooks_mod = import_module_directly("mozaiksai.core.workflow.execution.hooks")
_tools_mod = import_module_directly("mozaiksai.core.workflow.agents.tools")
_hook_code_context_mod = import_module_directly("factory_app.workflows.AppGenerator.tools.hook_code_context")


def test_hooks_loader_reads_yaml_only(tmp_path: Path) -> None:
    flow_yaml = tmp_path / "FlowYaml"
    flow_yaml.mkdir(parents=True)
    (flow_yaml / "hooks.yaml").write_text(
        "hooks:\n"
        "  - hook_type: update_agent_state\n"
        "    hook_agent: Planner\n"
        "    filename: hook_file.py\n"
        "    function: set_state\n",
        encoding="utf-8",
    )
    (flow_yaml / "hooks.json").write_text(
        json.dumps(
            {
                "hooks": [
                    {
                        "hook_type": "update_agent_state",
                        "hook_agent": "Planner",
                        "filename": "removed_hook.py",
                        "function": "removed_set_state",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    entries_yaml = _hooks_mod.load_hook_entries("FlowYaml", base_path=str(tmp_path))
    assert entries_yaml and entries_yaml[0]["filename"] == "hook_file.py"

    flow_json = tmp_path / "FlowJson"
    flow_json.mkdir(parents=True)
    (flow_json / "hooks.json").write_text(
        json.dumps(
            {
                "hooks": [
                    {
                        "hook_type": "process_message_before_send",
                        "hook_agent": "Narrator",
                        "filename": "removed_only.py",
                        "function": "before_send",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    entries_json = _hooks_mod.load_hook_entries("FlowJson", base_path=str(tmp_path))
    assert entries_json == []


def test_agent_tools_loader_reads_yaml_only(tmp_path: Path) -> None:
    _tools_mod.workflow_manager.workflows_base_path = tmp_path
    _tools_mod.workflow_manager._workflow_paths = {}

    flow_yaml = tmp_path / "FlowToolsYaml"
    flow_yaml.mkdir(parents=True)
    (flow_yaml / "orchestrator.yaml").write_text("workflow_name: FlowToolsYaml\n", encoding="utf-8")
    (flow_yaml / "tool_file.py").write_text(
        "def run_task(context_variables=None):\n"
        "    return {'ok': True}\n",
        encoding="utf-8",
    )
    (flow_yaml / "tools.yaml").write_text(
        "tools:\n"
        "  - agent: AgentA\n"
        "    file: tool_file.py\n"
        "    function: run_task\n"
        "    tool_type: Agent_Tool\n",
        encoding="utf-8",
    )

    mapping_yaml = _tools_mod.load_agent_tool_functions("FlowToolsYaml")
    assert "AgentA" in mapping_yaml
    assert mapping_yaml["AgentA"]
    assert callable(mapping_yaml["AgentA"][0])

    flow_json = tmp_path / "FlowToolsJsonOnly"
    flow_json.mkdir(parents=True)
    (flow_json / "orchestrator.yaml").write_text("workflow_name: FlowToolsJsonOnly\n", encoding="utf-8")
    (flow_json / "tool_file.py").write_text(
        "def run_task(context_variables=None):\n"
        "    return {'ok': True}\n",
        encoding="utf-8",
    )
    (flow_json / "tools.json").write_text(
        json.dumps(
            {
                "tools": [
                    {
                        "agent": "AgentA",
                        "file": "tool_file.py",
                        "function": "run_task",
                        "tool_type": "Agent_Tool",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    mapping_json = _tools_mod.load_agent_tool_functions("FlowToolsJsonOnly")
    assert mapping_json == {}


def test_agent_tools_loader_rebinds_workflows_package_to_active_root(tmp_path: Path) -> None:
    _tools_mod.workflow_manager.workflows_base_path = tmp_path
    _tools_mod.workflow_manager._workflow_paths = {}

    flow = tmp_path / "FlowIsolated"
    tools_dir = flow / "tools"
    tools_dir.mkdir(parents=True)
    (flow / "orchestrator.yaml").write_text("workflow_name: FlowIsolated\n", encoding="utf-8")
    (tools_dir / "helper.py").write_text(
        "def helper_value():\n"
        "    return 'repo-local-helper'\n",
        encoding="utf-8",
    )
    (tools_dir / "tool_file.py").write_text(
        "from .helper import helper_value\n\n"
        "def run_task(context_variables=None):\n"
        "    return {'ok': True, 'source': helper_value()}\n",
        encoding="utf-8",
    )
    (flow / "tools.yaml").write_text(
        "tools:\n"
        "  - agent: AgentA\n"
        "    file: tool_file.py\n"
        "    function: run_task\n"
        "    tool_type: Agent_Tool\n",
        encoding="utf-8",
    )

    stale_root = tmp_path / "stale_workflows_root"
    stale_root.mkdir(parents=True)
    stale_workflows = types.ModuleType("workflows")
    stale_workflows.__path__ = [str(stale_root)]
    stale_workflows.__package__ = "workflows"
    stale_flow = types.ModuleType("workflows.FlowIsolated")
    stale_flow.__path__ = [str(stale_root / "FlowIsolated")]
    stale_flow.__package__ = "workflows.FlowIsolated"
    stale_tools = types.ModuleType("workflows.FlowIsolated.tools")
    stale_tools.__path__ = [str(stale_root / "FlowIsolated" / "tools")]
    stale_tools.__package__ = "workflows.FlowIsolated.tools"
    sys.modules["workflows"] = stale_workflows
    sys.modules["workflows.FlowIsolated"] = stale_flow
    sys.modules["workflows.FlowIsolated.tools"] = stale_tools

    mapping = _tools_mod.load_agent_tool_functions("FlowIsolated")

    assert "AgentA" in mapping
    result = mapping["AgentA"][0]()
    assert result == {"ok": True, "source": "repo-local-helper"}


def test_hooks_loader_resolves_appgenerator_code_context_hook_from_repo() -> None:
    workspace = Path(__file__).resolve().parents[1]
    workflows_root = workspace / "factory_app" / "workflows"
    workflow_path = workflows_root / "AppGenerator"

    fn, qualname = _hooks_mod._resolve_import(
        "AppGenerator",
        "hook_code_context.py",
        "inject_code_context",
        workflow_path,
    )

    assert callable(fn)
    assert qualname.endswith("hook_code_context.py:inject_code_context")


def test_appgenerator_code_context_hook_uses_current_tool_contract(monkeypatch) -> None:
    captured: dict[str, str] = {}

    def _fake_get_code_context_for_agent(*, app_id, workspace_id, agent_name, version_hash=None, max_tokens=None):
        return {"success": True, "context": f"{agent_name}:{app_id}:{workspace_id}"}

    monkeypatch.setattr(
        _hook_code_context_mod,
        "get_code_context_for_agent",
        _fake_get_code_context_for_agent,
    )

    class _Agent:
        name = "ServiceAgent"
        system_message = "Base prompt"
        context_variables = {"app_id": "app_1", "workspace_id": "ws_1"}

        def update_system_message(self, message):
            captured["message"] = message
            self.system_message = message

    agent = _Agent()
    _hook_code_context_mod.inject_code_context(agent, [])

    assert "[CODE CONTEXT]" in captured["message"]
    assert "ServiceAgent:app_1:ws_1" in captured["message"]
