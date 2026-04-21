from __future__ import annotations

import importlib.util
from pathlib import Path

from tests.import_utils import import_module_directly


_schema = import_module_directly("mozaiksai.core.workflow.pack.schema")
parse_workflow_pack_graph = _schema.parse_workflow_pack_graph


class _Logger:
    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None


def _load_workflow_converter_module():
    workspace = Path(__file__).resolve().parents[1]
    file_path = workspace / "mozaiks-platform" / "app" / "workflows" / "AgentGenerator" / "tools" / "workflow_converter.py"
    module_name = "tests.workflow_converter_direct"
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load module spec for {file_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


workflow_converter = _load_workflow_converter_module()


def test_build_workflow_local_pack_graph_for_decomposition() -> None:
    graph = workflow_converter._build_workflow_local_pack_graph(
        workflow_name="ReviewWorkflow",
        workflow_strategy_output={
            "WorkflowStrategy": {
                "workflow_name": "Review Workflow",
                "decomposition": {
                    "required": True,
                    "mode": "single_stage_mfj",
                    "work_unit": "document",
                    "decomposition_agent": "DecompositionAgent",
                    "child_initial_agent": "ReviewWorkerAgent",
                    "resume_entry_agent": "ResumeRouterAgent",
                    "resume_agent": "SynthesisAgent",
                    "inject_as": "document_results",
                    "max_children": 6,
                    "contracts": {
                        "input_required": ["concept_overview", "document_batch"],
                        "input_optional": ["style_guide"],
                        "output_required": ["review_summary"],
                        "output_optional": ["citations"],
                    },
                },
            }
        },
        wf_logger=_Logger(),
    )

    assert graph is not None
    assert graph["version"] == 3
    journey = graph["mid_flight_journeys"][0]
    assert journey["id"] == "document_cycle"
    assert journey["decomposition_agent"] == "DecompositionAgent"
    assert journey["fan_out"]["spawn_mode"] == "workflow"
    assert journey["fan_out"]["child_initial_agent"] == "ReviewWorkerAgent"
    assert journey["fan_out"]["max_children"] == 6
    assert journey["fan_in"]["resume_agent"] == "SynthesisAgent"
    assert journey["fan_in"]["inject_as"] == "mfj_document_results"

    parsed = parse_workflow_pack_graph(graph)
    assert parsed.mid_flight_journeys[0].fan_in.inject_as == "mfj_document_results"


def test_build_workflow_local_pack_graph_returns_none_without_decomposition() -> None:
    graph = workflow_converter._build_workflow_local_pack_graph(
        workflow_name="LinearWorkflow",
        workflow_strategy_output={
            "WorkflowStrategy": {
                "workflow_name": "Linear Workflow",
                "decomposition": {
                    "required": False,
                    "mode": "none",
                    "contracts": None,
                },
            }
        },
        wf_logger=_Logger(),
    )

    assert graph is None


def test_generated_extra_file_paths_stay_workflow_local() -> None:
    normalize = workflow_converter._normalize_workflow_extra_path

    assert normalize("tools/analyze.py") == "tools/analyze.py"
    assert normalize("ui/components/ReviewPanel.jsx") == "ui/components/ReviewPanel.jsx"
    assert normalize("extended_orchestration/mfj_extension.json") == "extended_orchestration/mfj_extension.json"

    assert normalize("../outside.py") is None
    assert normalize("tools/../outside.py") is None
    assert normalize("_shared/helper.py") is None
    assert normalize("workflows/_shared/helper.py") is None


def test_runtime_extensions_stay_workflow_local() -> None:
    extensions = [
        {"kind": "api_router", "entrypoint": "workflows.ReviewWorkflow.tools.api:get_router"},
        {"kind": "startup_service", "entrypoint": "workflows._shared.tools.service:Service"},
        {"kind": "lifecycle_hooks", "entrypoint": "workflows.OtherWorkflow.tools.lifecycle:get_hooks"},
        {"kind": "unsupported", "entrypoint": "workflows.ReviewWorkflow.tools.bad:get_hooks"},
    ]

    normalized = workflow_converter._normalize_runtime_extensions(
        extensions,
        workflow_name="ReviewWorkflow",
        wf_logger=_Logger(),
    )

    assert normalized == [
        {"kind": "api_router", "entrypoint": "workflows.ReviewWorkflow.tools.api:get_router"}
    ]


def test_normalize_visual_agents_backend_only_blank_to_null() -> None:
    assert workflow_converter._normalize_visual_agents(None, startup_mode="BackendOnly") is None
    assert workflow_converter._normalize_visual_agents("  ", startup_mode="BackendOnly") is None
    assert workflow_converter._normalize_visual_agents([], startup_mode="BackendOnly") is None


def test_normalize_handoff_rules_blank_condition_defaults_to_after_work() -> None:
    raw_rules = [
        {
            "source_agent": "PlannerAgent",
            "target_agent": "ExecutorAgent",
            "handoff_type": "condition",
            "condition": " ",
            "condition_scope": "",
            "condition_type": "  ",
        }
    ]

    normalized = workflow_converter._normalize_handoff_rules(raw_rules)
    assert len(normalized) == 1
    assert normalized[0]["condition"] is None
    assert normalized[0]["condition_scope"] is None
    assert normalized[0]["condition_type"] is None
    assert normalized[0]["handoff_type"] == "after_work"


def test_normalize_tools_manifest_stamps_default_ui_contract_for_ui_tools() -> None:
    normalized = workflow_converter._normalize_tools_manifest(
        {
            "tools": [
                {
                    "agent": "PlannerAgent",
                    "file": "tools/plan.py",
                    "function": "plan",
                    "tool_type": "UI_Tool",
                    "ui": {"component": "PlanPanel", "mode": "artifact"},
                }
            ]
        },
        _Logger(),
    )

    assert len(normalized["tools"]) == 1
    tool = normalized["tools"][0]
    assert tool["tool_type"] == "UI_Tool"
    assert tool["ui_contract"]["surface_kind"] == "agent_tool"
    assert tool["ui_contract"]["payload_schema"]["type"] == "object"
    assert tool["ui_contract"]["actions_schema"] == []


def test_normalize_tools_manifest_removes_ui_contract_for_agent_tools() -> None:
    normalized = workflow_converter._normalize_tools_manifest(
        {
            "tools": [
                {
                    "agent": "PlannerAgent",
                    "file": "tools/analyze.py",
                    "function": "analyze",
                    "tool_type": "Agent_Tool",
                    "ui_contract": {"surface_kind": "agent_tool"},
                }
            ]
        },
        _Logger(),
    )

    assert len(normalized["tools"]) == 1
    tool = normalized["tools"][0]
    assert tool["tool_type"] == "Agent_Tool"
    assert "ui_contract" not in tool


def test_normalize_tools_manifest_preserves_ui_surface_and_strips_ui_contract() -> None:
    normalized = workflow_converter._normalize_tools_manifest(
        {
            "tools": [
                {
                    "agent": "PlannerAgent",
                    "file": "tools/render_preview.py",
                    "function": "render_preview",
                    "tool_type": "ui_surface",
                    "ui": {"component": "PreviewPanel", "mode": "artifact"},
                    "ui_contract": {"surface_kind": "agent_tool"},
                }
            ]
        },
        _Logger(),
    )

    assert len(normalized["tools"]) == 1
    tool = normalized["tools"][0]
    assert tool["tool_type"] == "UI_Surface"
    assert tool["ui"]["component"] == "PreviewPanel"
    assert "ui_contract" not in tool


def test_normalize_tools_manifest_strips_planning_only_integration_metadata() -> None:
    normalized = workflow_converter._normalize_tools_manifest(
        {
            "tools": [
                {
                    "agent": "PlannerAgent",
                    "file": "tools/render_preview.py",
                    "function": "render_preview",
                    "tool_type": "UI_Tool",
                    "integration": "Slack",
                    "ui": {"component": "PreviewPanel", "mode": "artifact"},
                }
            ],
            "lifecycle_tools": [
                {
                    "agent": "PlannerAgent",
                    "file": "tools/preflight.py",
                    "function": "preflight",
                    "tool_type": "Agent_Tool",
                    "trigger": "before_agent",
                    "integration": "OpenAI",
                }
            ],
        },
        _Logger(),
    )

    tool = normalized["tools"][0]
    lifecycle_tool = normalized["lifecycle_tools"][0]
    assert "integration" not in tool
    assert "integration" not in lifecycle_tool
