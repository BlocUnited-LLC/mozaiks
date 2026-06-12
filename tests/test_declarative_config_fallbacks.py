from __future__ import annotations

import json
from pathlib import Path
import sys
import types
import asyncio

from mozaiksai.core.workflow.execution import middleware as _middleware_mod
from tests.import_utils import import_module_directly
_tools_mod = import_module_directly("mozaiksai.core.workflow.agents.tools")
_context_graph_hook_mod = import_module_directly(
    "factory_app.workflows._shared.context_graph.hook_context_graph"
)


def test_prompt_middleware_loader_reads_yaml_only(tmp_path: Path) -> None:
    flow_yaml = tmp_path / "FlowYaml"
    flow_yaml.mkdir(parents=True)
    (flow_yaml / "middleware.yaml").write_text(
        "prompt_middleware:\n"
        "  - agent: Planner\n"
        "    filename: hook_file.py\n"
        "    function: set_state\n",
        encoding="utf-8",
    )
    (flow_yaml / "hooks.json").write_text(
        json.dumps(
            {
                "prompt_middleware": [
                    {
                        "agent": "Planner",
                        "filename": "removed_hook.py",
                        "function": "removed_set_state",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    entries_yaml = _middleware_mod.load_prompt_middleware_entries("FlowYaml", base_path=str(tmp_path))
    assert entries_yaml and entries_yaml[0]["filename"] == "hook_file.py"

    flow_json = tmp_path / "FlowJson"
    flow_json.mkdir(parents=True)
    (flow_json / "hooks.json").write_text(
        json.dumps(
            {
                "prompt_middleware": [
                    {
                        "agent": "Narrator",
                        "filename": "removed_only.py",
                        "function": "before_send",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    entries_json = _middleware_mod.load_prompt_middleware_entries("FlowJson", base_path=str(tmp_path))
    assert entries_json == []


def test_prompt_middleware_loader_accepts_import_path_without_filename(tmp_path: Path) -> None:
    flow = tmp_path / "FlowImportPath"
    flow.mkdir(parents=True)
    (flow / "middleware.yaml").write_text(
        "prompt_middleware:\n"
        "  - agent: Planner\n"
        "    function: mozaiksai.core.workflow.context.projection.inject_build_context_projections\n",
        encoding="utf-8",
    )

    entries = _middleware_mod.load_prompt_middleware_entries("FlowImportPath", base_path=str(tmp_path))
    assert entries == [
        {
            "agent": "Planner",
            "filename": None,
            "function": "mozaiksai.core.workflow.context.projection.inject_build_context_projections",
        }
    ]

    fn, qualname = _middleware_mod._resolve_import(
        "FlowImportPath",
        None,
        "mozaiksai.core.workflow.context.projection.inject_build_context_projections",
        flow,
    )
    assert callable(fn)
    assert qualname == "mozaiksai.core.workflow.context.projection.inject_build_context_projections"


def test_prompt_middleware_updates_current_turn_prompt() -> None:
    def inject_prompt(agent, _messages):
        agent.update_system_message(f"{agent.system_message}\n\nInjected for {agent.name}.")

    middleware_factory = _middleware_mod.build_prompt_middleware(
        middleware_functions=[inject_prompt],
        agent_name="Planner",
        base_system_message="Base prompt",
        context_bridge={},
    )

    from autogen.beta import Context, MemoryStream
    from autogen.beta.events import ModelRequest, ModelResponse

    context = Context(MemoryStream(), prompt=["Base prompt"], variables={})
    middleware = middleware_factory(ModelRequest("go"), context)

    async def call_next(_events, call_context):
        assert call_context.prompt == ["Base prompt\n\nInjected for Planner."]
        return ModelResponse()

    asyncio.run(middleware.on_llm_call(call_next, [], context))


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


def test_middleware_loader_resolves_shared_context_graph_hook_from_repo() -> None:
    workspace = Path(__file__).resolve().parents[1]
    workflows_root = workspace / "factory_app" / "workflows"
    workflow_path = workflows_root / "AppGenerator"

    fn, qualname = _middleware_mod._resolve_import(
        "AppGenerator",
        "../_shared/context_graph/hook_context_graph.py",
        "inject_context_graph_context",
        workflow_path,
    )

    assert callable(fn)
    assert qualname.endswith("hook_context_graph.py:inject_context_graph_context")


def test_shared_context_graph_hook_injects_preloaded_graph_pack() -> None:
    captured: dict[str, str] = {}

    class _Agent:
        name = "ServiceAgent"
        system_message = "Base prompt"
        context_variables = {
            "context_graph_pack": {
                "graph_id": "graph_1",
                "stale_status": "current",
                "candidate_files": [{"path": "modules/tasks/backend/service.py"}],
                "matched_nodes": [{"node_id": "module:tasks", "node_type": "module", "label": "tasks"}],
            }
        }

        def update_system_message(self, message):
            captured["message"] = message
            self.system_message = message

    agent = _Agent()
    _context_graph_hook_mod.inject_context_graph_context(agent, [])

    assert "[CONTEXT GRAPH]" in captured["message"]
    assert "modules/tasks/backend/service.py" in captured["message"]


def test_shared_context_graph_hook_injects_unavailable_status_pack() -> None:
    captured: dict[str, str] = {}

    class _Agent:
        name = "ServiceAgent"
        system_message = "Base prompt"
        context_variables = {
            "context_graph_pack": {
                "pack_kind": "context_graph_prompt_pack",
                "present": False,
                "status": "unavailable",
                "reason": "current_app_context_graph_unavailable",
                "warnings": ["No current app context version is registered."],
            }
        }

        def update_system_message(self, message):
            captured["message"] = message
            self.system_message = message

    agent = _Agent()
    _context_graph_hook_mod.inject_context_graph_context(agent, [])

    assert "Status: unavailable" in captured["message"]
    assert "Reason: current_app_context_graph_unavailable" in captured["message"]


def test_shared_context_graph_hook_does_not_treat_catalog_as_prompt_pack() -> None:
    captured: dict[str, str] = {}

    class _Agent:
        name = "ServiceAgent"
        system_message = "Base prompt"
        context_variables = {
            "context_graph_catalog": {
                "graph_id": "graph_1",
                "candidate_files": [{"path": "modules/tasks/backend/service.py"}],
            }
        }

        def update_system_message(self, message):
            captured["message"] = message
            self.system_message = message

    agent = _Agent()
    _context_graph_hook_mod.inject_context_graph_context(agent, [])

    assert captured == {}
    assert agent.system_message == "Base prompt"

def test_shared_context_graph_hook_ignores_control_plane_scope_payload() -> None:
    captured: dict[str, str] = {}

    class _Agent:
        name = "ServiceAgent"
        system_message = "Base prompt"
        context_variables = {
            "context_graph_scope": {
                "graph_id": "graph_1",
                "selected_file_paths": ["modules/tasks/backend/service.py"],
            }
        }

        def update_system_message(self, message):
            captured["message"] = message
            self.system_message = message

    agent = _Agent()
    _context_graph_hook_mod.inject_context_graph_context(agent, [])

    assert captured == {}
    assert agent.system_message == "Base prompt"

