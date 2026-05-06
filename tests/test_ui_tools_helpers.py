from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path


class _Logger:
    def debug(self, *args, **kwargs):
        return None

    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None


class _WorkflowManager:
    def __init__(self) -> None:
        self.records = {}

    def get_ui_tool_record(self, tool_id: str):
        return self.records.get(tool_id)

    def iter_ui_tools(self):
        return list(self.records.values())


def _stub_ui_tools_dependencies() -> _WorkflowManager:
    logs_pkg = sys.modules.get("logs")
    if logs_pkg is None:
        logs_pkg = types.ModuleType("logs")
        logs_pkg.__path__ = []
        sys.modules["logs"] = logs_pkg

    logging_config_mod = types.ModuleType("logs.logging_config")
    logging_config_mod.get_workflow_logger = lambda *args, **kwargs: _Logger()
    sys.modules["logs.logging_config"] = logging_config_mod

    for name in ("mozaiksai", "mozaiksai.core", "mozaiksai.core.workflow"):
        if name not in sys.modules:
            pkg = types.ModuleType(name)
            pkg.__path__ = []
            sys.modules[name] = pkg

    workflow_manager_mod = types.ModuleType("mozaiksai.core.workflow.workflow_manager")
    workflow_manager = _WorkflowManager()
    workflow_manager_mod.workflow_manager = workflow_manager
    sys.modules["mozaiksai.core.workflow.workflow_manager"] = workflow_manager_mod
    return workflow_manager


def _load_ui_tools_module():
    original_logs_pkg = sys.modules.get("logs")
    original_logging_config_mod = sys.modules.get("logs.logging_config")
    original_mozaiksai_pkg = sys.modules.get("mozaiksai")
    original_mozaiksai_core_pkg = sys.modules.get("mozaiksai.core")
    original_mozaiksai_workflow_pkg = sys.modules.get("mozaiksai.core.workflow")
    original_workflow_manager_mod = sys.modules.get("mozaiksai.core.workflow.workflow_manager")
    workflow_manager = _stub_ui_tools_dependencies()
    workspace = Path(__file__).resolve().parents[1]
    file_path = workspace / "mozaiksai" / "core" / "workflow" / "ui_tools.py"
    module_name = "tests.ui_tools_direct"
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load module spec for {file_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    if original_logs_pkg is None:
        sys.modules.pop("logs", None)
    else:
        sys.modules["logs"] = original_logs_pkg

    if original_logging_config_mod is None:
        sys.modules.pop("logs.logging_config", None)
    else:
        sys.modules["logs.logging_config"] = original_logging_config_mod

    if original_mozaiksai_pkg is None:
        sys.modules.pop("mozaiksai", None)
    else:
        sys.modules["mozaiksai"] = original_mozaiksai_pkg

    if original_mozaiksai_core_pkg is None:
        sys.modules.pop("mozaiksai.core", None)
    else:
        sys.modules["mozaiksai.core"] = original_mozaiksai_core_pkg

    if original_mozaiksai_workflow_pkg is None:
        sys.modules.pop("mozaiksai.core.workflow", None)
    else:
        sys.modules["mozaiksai.core.workflow"] = original_mozaiksai_workflow_pkg

    if original_workflow_manager_mod is None:
        sys.modules.pop("mozaiksai.core.workflow.workflow_manager", None)
    else:
        sys.modules["mozaiksai.core.workflow.workflow_manager"] = original_workflow_manager_mod
    return module, workflow_manager


ui_tools_module, workflow_manager = _load_ui_tools_module()


def test_emit_ui_surface_uses_registry_resolved_display_and_no_response() -> None:
    workflow_manager.records["ConceptBlueprint"] = {
        "mode": "artifact",
        "agent": "GapAnalysisAgent",
        "component": "ConceptBlueprint",
        "workflow_primitive": "document_preview",
        "realization": "generated_component",
    }

    captured = {}

    async def _fake_emit(**kwargs):
        captured.update(kwargs)
        return "evt_artifact_1"

    ui_tools_module._emit_tool_call_core = _fake_emit

    event_id = asyncio.run(
        ui_tools_module.emit_ui_surface(
            "ConceptBlueprint",
            {"title": "Concept Blueprint"},
            chat_id="chat_123",
            workflow_name="ValueEngine",
        )
    )

    assert event_id == "evt_artifact_1"
    assert captured["tool_id"] == "ConceptBlueprint"
    assert captured["display"] == "artifact"
    assert captured["agent_name"] == "GapAnalysisAgent"
    assert captured["awaiting_response"] is False
    assert captured["component_name"] == "ConceptBlueprint"
    assert captured["payload"]["workflow_primitive"] == "document_preview"
    assert captured["payload"]["ui_realization"] == "generated_component"


def test_use_ui_tool_requests_response_and_returns_event_id() -> None:
    workflow_manager.records["ActionPlan"] = {
        "mode": "inline",
        "agent": "DownloadAgent",
        "component": "ActionPlan",
        "workflow_primitive": "action_plan_review",
        "realization": "generated_component",
        "ui_contract": {
            "surface_kind": "agent_tool",
            "payload_schema": {"type": "object", "properties": {"title": {"type": "string"}}, "additionalProperties": True},
            "actions_schema": [
                {
                    "id": "approve",
                    "label": "Approve Plan",
                    "variant": "primary",
                    "approved": True,
                    "payload_schema": {"type": "object", "properties": {}, "additionalProperties": True},
                }
            ],
        },
    }

    captured = {}

    async def _fake_emit(**kwargs):
        captured.update(kwargs)
        return "evt_interactive_1"

    async def _fake_wait(event_id, timeout=None):
        assert event_id == "evt_interactive_1"
        return {"status": "ok"}

    ui_tools_module._emit_tool_call_core = _fake_emit
    ui_tools_module._wait_for_tool_call_response_internal = _fake_wait

    response = asyncio.run(
        ui_tools_module.use_ui_tool(
            "ActionPlan",
            {"title": "Review"},
            chat_id="chat_456",
            workflow_name="AgentGenerator",
        )
    )

    assert response["status"] == "ok"
    assert response["ui_event_id"] == "evt_interactive_1"
    assert captured["tool_id"] == "ActionPlan"
    assert captured["display"] == "inline"
    assert captured["agent_name"] == "DownloadAgent"
    assert captured["awaiting_response"] is True
    assert captured["component_name"] == "ActionPlan"
    assert captured["payload"]["workflow_primitive"] == "action_plan_review"
    assert captured["payload"]["ui_realization"] == "generated_component"
    assert captured["payload"]["ui_contract"]["actions_schema"][0]["label"] == "Approve Plan"
    assert captured["payload"]["actions"][0]["id"] == "approve"
    assert captured["payload"]["actions"][0]["variant"] == "primary"


def test_use_ui_tool_resolves_manifest_component_when_called_by_function_name() -> None:
    workflow_manager.records["build_plan_card"] = {
        "mode": "inline",
        "agent": "PlannerAgent",
        "fn": "build_plan_card",
        "component": "ApprovalCard",
        "workflow_primitive": "approval_card",
        "realization": "shipped_component",
        "ui_contract": {
            "surface_kind": "agent_tool",
            "payload_schema": {"type": "object", "properties": {}, "additionalProperties": True},
            "actions_schema": [
                {"id": "approve", "payload_schema": {"type": "object", "properties": {}, "additionalProperties": True}},
            ],
        },
    }

    captured = {}

    async def _fake_emit(**kwargs):
        captured.update(kwargs)
        return "evt_component_lookup"

    async def _fake_wait(event_id, timeout=None):
        assert event_id == "evt_component_lookup"
        return {"status": "ok"}

    ui_tools_module._emit_tool_call_core = _fake_emit
    ui_tools_module._wait_for_tool_call_response_internal = _fake_wait

    response = asyncio.run(
        ui_tools_module.use_ui_tool(
            "build_plan_card",
            {"title": "Review"},
            chat_id="chat_789",
            workflow_name="PlannerFlow",
        )
    )

    assert response["ui_event_id"] == "evt_component_lookup"
    assert captured["tool_id"] == "build_plan_card"
    assert captured["component_name"] == "ApprovalCard"
    assert captured["payload"]["component_type"] == "ApprovalCard"
    assert captured["payload"]["workflow_primitive"] == "approval_card"
    assert captured["payload"]["ui_realization"] == "shipped_component"
