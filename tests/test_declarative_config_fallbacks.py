from __future__ import annotations

import json
from pathlib import Path

from tests.import_utils import import_module_directly

_hooks_mod = import_module_directly("mozaiksai.core.workflow.execution.hooks")
_tools_mod = import_module_directly("mozaiksai.core.workflow.agents.tools")


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
                        "filename": "legacy_hook.py",
                        "function": "legacy_set_state",
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
                        "filename": "legacy_only.py",
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

    flow_yaml = tmp_path / "FlowToolsYaml"
    flow_yaml.mkdir(parents=True)
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
