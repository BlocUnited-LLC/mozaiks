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


class _Context:
    def __init__(self, initial=None) -> None:
        self.data = dict(initial or {})

    def get(self, key, default=None):
        return self.data.get(key, default)


def _load_workflow_converter_module():
    workspace = Path(__file__).resolve().parents[1]
    file_path = workspace / "factory_app" / "workflows" / "AgentGenerator" / "tools" / "workflow_converter.py"
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


def test_workflow_output_dir_uses_generated_artifact_root(monkeypatch, tmp_path: Path) -> None:
    generated_root = tmp_path / "generated"
    monkeypatch.setenv("MOZAIKS_GENERATED_ARTIFACTS_PATH", str(generated_root))
    context = _Context({"app_id": "app/one", "chat_id": "chat one"})

    output_dir = workflow_converter._resolve_workflow_output_dir(
        "Review Workflow",
        context_variables=context,
    )

    assert output_dir == generated_root / "workflows" / "app-one" / "chat-one" / "Review-Workflow"


def test_workflow_output_dir_defaults_to_repo_generated(monkeypatch) -> None:
    workspace = Path(__file__).resolve().parents[1]
    monkeypatch.delenv("MOZAIKS_GENERATED_ARTIFACTS_PATH", raising=False)

    assert workflow_converter._resolve_generated_artifacts_root() == (workspace / "generated").resolve()


def test_promote_generated_workflow_copies_to_active_workflows_root(tmp_path: Path) -> None:
    source = tmp_path / "generated" / "workflows" / "app-1" / "build-1" / "ReviewWorkflow"
    target_root = tmp_path / "active" / "workflows"
    (source / "tools").mkdir(parents=True)
    (source / "orchestrator.yaml").write_text("workflow_name: ReviewWorkflow\n", encoding="utf-8")
    (source / "tools" / "review.py").write_text("def review():\n    return None\n", encoding="utf-8")

    result = workflow_converter.promote_generated_workflow(source, target_root)

    target = target_root / "ReviewWorkflow"
    assert result["status"] == "success"
    assert result["target_dir"] == str(target.resolve())
    assert (target / "orchestrator.yaml").exists()
    assert (target / "tools" / "review.py").exists()


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


def test_orchestrator_triggers_are_normalized_to_runtime_schema() -> None:
    triggers = [
        {
            "type": "event",
            "event": "domain.documents.document_uploaded",
            "description": "Start after upload commit",
            "capability_id": "documents.review",
        },
        {"type": "unsupported", "event": "domain.bad.event"},
        {"type": "route", "endpoint": "/api/review", "method": "POST"},
        {"capability_id": "raw.workflow.name"},
    ]

    normalized = workflow_converter._normalize_orchestrator_triggers(
        triggers,
        wf_logger=_Logger(),
    )

    assert normalized == [
        {
            "type": "event",
            "event": "domain.documents.document_uploaded",
            "description": "Start after upload commit",
            "capability_id": "documents.review",
        },
        {"type": "route", "endpoint": "/api/review", "method": "POST"},
    ]


def test_split_config_preserves_orchestrator_triggers() -> None:
    sections = workflow_converter._split_config_into_sections(
        {
            "workflow_name": "ReviewWorkflow",
            "startup_mode": "BackendOnly",
            "triggers": [{"type": "event", "event": "domain.documents.document_uploaded"}],
            "agents": {"ReviewAgent": {}},
        }
    )

    assert sections["orchestrator"]["triggers"] == [
        {"type": "event", "event": "domain.documents.document_uploaded"}
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
