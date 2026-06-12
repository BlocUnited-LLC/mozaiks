"""Tests for hook_file_contract_context.py."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pytest
import yaml


_APPGEN_DIR = (
    Path(__file__).parent.parent
    / "factory_app"
    / "workflows"
    / "AppGenerator"
)
_APPGEN_CATALOG_DIR = (
    Path(__file__).parent.parent
    / "factory_app"
    / "build_context"
    / "AppGenerator"
)
_HOOKS_YAML = _APPGEN_DIR / "middleware.yaml"
_FILE_CONTRACTS_PATH = _APPGEN_CATALOG_DIR / "file_contracts.yaml"
_MODULE_ARCHETYPES_PATH = _APPGEN_CATALOG_DIR / "module_archetypes.yaml"


class _FakeAgent:
    def __init__(self, name: str, context_variables: Dict[str, Any] | None = None):
        self.name = name
        self.system_message = ""
        self.context_variables = context_variables or {}
        self._update_calls: List[str] = []

    def update_system_message(self, message: str) -> None:
        self.system_message = message
        self._update_calls.append(message)


class TestContractArtifacts:
    def test_file_contracts_yaml_valid(self):
        with open(_FILE_CONTRACTS_PATH, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        assert isinstance(data, dict)
        assert "task_contracts" in data
        assert "module_contract" in data["task_contracts"]
        assert "page_bundle" in data["task_contracts"]

    def test_module_archetypes_yaml_valid(self):
        with open(_MODULE_ARCHETYPES_PATH, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        assert isinstance(data, dict)
        assert "archetypes" in data
        assert {"standard", "messaging", "workflow", "transactional"}.issubset(data["archetypes"].keys())


class TestHooksYaml:
    def test_cookie_cutter_hook_entries_present(self):
        with open(_HOOKS_YAML, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        hooks = data["prompt_middleware"]

        expected_agents = {
            "AppPlanAgent",
            "AppSchemaAgent",
            "ControlPlaneAgent",
            "ConfigMiddlewareAgent",
            "ServiceAgent",
            "FrontendStubAgent",
            "ControllerAgent",
        }

        actual_agents = {
            hook.get("agent")
            for hook in hooks
            if hook.get("filename") == "hook_file_contract_context.py"
            and hook.get("function") == "inject_cookie_cutter_contracts_context"
        }

        assert expected_agents.issubset(actual_agents)


class TestInjectCookieCutterContractsContext:
    @pytest.fixture(autouse=True)
    def _import_hook(self):
        import sys

        tools_path = str(_APPGEN_DIR / "tools")
        if tools_path not in sys.path:
            sys.path.insert(0, tools_path)
        if "hook_file_contract_context" in sys.modules:
            del sys.modules["hook_file_contract_context"]
        import hook_file_contract_context as m

        self.mod = m

    def test_wrong_agent_name_is_noop(self):
        agent = _FakeAgent(name="InterviewAgent")
        self.mod.inject_cookie_cutter_contracts_context(agent, [])
        assert agent.system_message == ""

    def test_app_plan_agent_gets_planning_contracts(self):
        agent = _FakeAgent(name="AppPlanAgent")
        self.mod.inject_cookie_cutter_contracts_context(agent, [])
        msg = agent.system_message
        assert "[FILE CONTRACTS CONTEXT]" in msg
        assert "module_contract:" in msg
        assert "page_bundle:" in msg
        assert "control_plane_pack:" in msg
        assert "Runtime truth remains build_tasks" in msg

    def test_app_schema_agent_gets_page_bundle_contract(self):
        agent = _FakeAgent(name="AppSchemaAgent")
        self.mod.inject_cookie_cutter_contracts_context(agent, [])
        msg = agent.system_message
        assert "[FILE CONTRACTS CONTEXT]" in msg
        assert "page_bundle:" in msg
        assert "action_type: workflow" in msg

    def test_config_middleware_agent_gets_module_contract_and_archetypes(self):
        agent = _FakeAgent(
            name="ConfigMiddlewareAgent",
            context_variables={
                "current_build_task": {
                    "task_type": "module_contract",
                    "capability_pack_id": "orders",
                }
            },
        )
        self.mod.inject_cookie_cutter_contracts_context(agent, [])
        msg = agent.system_message
        assert "[FILE CONTRACTS CONTEXT]" in msg
        assert "module_contract:" in msg
        assert "[MODULE ARCHETYPES CONTEXT]" in msg
        assert "standard:" in msg
        assert "messaging:" in msg

    def test_control_plane_agent_gets_control_plane_pack_contract(self):
        agent = _FakeAgent(
            name="ControlPlaneAgent",
            context_variables={
                "current_build_task": {
                    "task_type": "control_plane_pack",
                }
            },
        )
        self.mod.inject_cookie_cutter_contracts_context(agent, [])
        msg = agent.system_message
        assert "[FILE CONTRACTS CONTEXT]" in msg
        assert "control_plane_pack:" in msg
        assert "control_plane/config/control_plane.yaml" in msg
        assert "module_contract:" not in msg
        assert "service_foundation:" not in msg
        assert "[MODULE ARCHETYPES CONTEXT]" not in msg

    def test_service_agent_uses_selected_module_archetype(self):
        agent = _FakeAgent(
            name="ServiceAgent",
            context_variables={
                "module_contract": {
                    "module_yaml": {
                        "module": {
                            "type": "workflow",
                        }
                    }
                }
            },
        )
        self.mod.inject_cookie_cutter_contracts_context(agent, [])
        msg = agent.system_message
        assert "[MODULE ARCHETYPES CONTEXT]" in msg
        assert "Selected module type: workflow" in msg
        assert "validate_transition" in msg

    def test_controller_agent_gets_api_surface_contract(self):
        agent = _FakeAgent(name="ControllerAgent")
        self.mod.inject_cookie_cutter_contracts_context(agent, [])
        msg = agent.system_message
        assert "[FILE CONTRACTS CONTEXT]" in msg
        assert "api_surface:" in msg
        assert "/api/modules/{module_name}/{action_name}" in msg

    def test_is_idempotent(self):
        agent = _FakeAgent(name="AppPlanAgent")
        self.mod.inject_cookie_cutter_contracts_context(agent, [])
        self.mod.inject_cookie_cutter_contracts_context(agent, [])
        assert agent.system_message.count("[FILE CONTRACTS CONTEXT]") == 1


