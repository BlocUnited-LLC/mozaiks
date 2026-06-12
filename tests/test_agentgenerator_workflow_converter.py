from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


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

    def set(self, key, value) -> None:
        self.data[key] = value


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


def test_generated_extra_file_paths_stay_workflow_local() -> None:
    normalize = workflow_converter._normalize_workflow_extra_path

    assert normalize("tools/analyze.py") == "tools/analyze.py"
    assert normalize("ui/components/ReviewPanel.jsx") == "ui/components/ReviewPanel.jsx"
    assert normalize("extended_orchestration/task_batches.yaml") == "extended_orchestration/task_batches.yaml"

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


def test_normalize_visual_agents_backend_only_blank_to_null() -> None:
    assert workflow_converter._normalize_visual_agents(None, workflow_startup_mode="BackendOnly") is None
    assert workflow_converter._normalize_visual_agents("  ", workflow_startup_mode="BackendOnly") is None
    assert workflow_converter._normalize_visual_agents([], workflow_startup_mode="BackendOnly") is None


def test_normalize_transition_rules_preserves_clean_after_turn() -> None:
    raw_rules = [
        {
            "source_agent": "PlannerAgent",
            "target_agent": "ExecutorAgent",
            "transition_type": "after_turn",
        }
    ]

    normalized = workflow_converter._normalize_transition_rules(raw_rules)
    assert normalized == [
        {
            "source_agent": "PlannerAgent",
            "target_agent": "ExecutorAgent",
            "transition_type": "after_turn",
        }
    ]


def test_normalize_transition_rules_accepts_context_equals() -> None:
    raw_rules = [
        {
            "source_agent": "PlannerAgent",
            "target_agent": "ExecutorAgent",
            "transition_type": "condition",
            "condition_type": "Context_Equals",
            "condition_key": "route",
            "condition_value": "execute",
        }
    ]

    normalized = workflow_converter._normalize_transition_rules(raw_rules)

    assert normalized == [
        {
            "source_agent": "PlannerAgent",
            "target_agent": "ExecutorAgent",
            "transition_type": "condition",
            "condition_type": "context_equals",
            "condition_key": "route",
            "condition_value": "execute",
        }
    ]


def test_normalize_transition_rules_accepts_context_expression() -> None:
    raw_rules = [
        {
            "source_agent": "PlannerAgent",
            "target_agent": "ExecutorAgent",
            "transition_type": "condition",
            "condition_type": "Context_Expression",
            "context_expression": "${route} == 'execute' and ${ready}",
        }
    ]

    normalized = workflow_converter._normalize_transition_rules(raw_rules)

    assert normalized == [
        {
            "source_agent": "PlannerAgent",
            "target_agent": "ExecutorAgent",
            "transition_type": "condition",
            "condition_type": "context_expression",
            "context_expression": "${route} == 'execute' and ${ready}",
        }
    ]


def test_normalize_transition_rules_rejects_expression_conditions() -> None:
    with pytest.raises(ValueError, match="no longer supports expression"):
        workflow_converter._normalize_transition_rules(
            [
                {
                    "source_agent": "user",
                    "target_agent": "PlannerAgent",
                    "transition_type": "condition",
                    "condition_type": "expression",
                    "condition": "${route} == 'plan'",
                }
            ]
        )


def test_normalize_transition_rules_rejects_llm_conditions() -> None:
    with pytest.raises(ValueError, match="does not support LLM-evaluated"):
        workflow_converter._normalize_transition_rules(
            [
                {
                    "source_agent": "user",
                    "target_agent": "PlannerAgent",
                    "transition_type": "condition",
                    "condition_type": "string_llm",
                    "condition": "When the user wants changes.",
                }
            ]
        )


def test_normalize_tools_manifest_stamps_default_ui_contract_for_ui_tools() -> None:
    normalized = workflow_converter._normalize_tools_manifest(
        {
            "tools": [
                {
                    "agent": "PlannerAgent",
                    "file": "tools/plan.py",
                    "function": "plan",
                    "tool_type": "UI_Tool",
                    "ui": {
                        "component": "PlanPanel",
                        "mode": "artifact",
                        "workflow_primitive": "form_card",
                        "realization": "workflow_wrapper",
                    },
                }
            ]
        },
        _Logger(),
    )

    assert len(normalized["tools"]) == 1
    tool = normalized["tools"][0]
    assert tool["tool_type"] == "UI_Tool"
    assert tool["ui"]["workflow_primitive"] == "form_card"
    assert tool["ui"]["realization"] == "workflow_wrapper"
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
                    "ui": {
                        "component": "PreviewPanel",
                        "mode": "artifact",
                        "workflow_primitive": "document_preview",
                        "realization": "generated_component",
                    },
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
    assert tool["ui"]["workflow_primitive"] == "document_preview"
    assert tool["ui"]["realization"] == "generated_component"
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
                    "ui": {
                        "component": "PreviewPanel",
                        "mode": "artifact",
                        "workflow_primitive": "document_preview",
                        "realization": "generated_component",
                    },
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


def test_collect_ui_code_files_skips_direct_shipped_component_files() -> None:
    tools_config = workflow_converter._normalize_tools_manifest(
        {
            "tools": [
                {
                    "agent": "ReviewAgent",
                    "file": "tools/request_approval.py",
                    "function": "request_approval",
                    "tool_type": "UI_Tool",
                    "ui": {
                        "component": "ApprovalCard",
                        "mode": "inline",
                        "workflow_primitive": "approval_card",
                        "realization": "shipped_component",
                    },
                }
            ]
        },
        _Logger(),
    )

    files = workflow_converter._collect_ui_code_files(
        {
            "tools": [
                {
                    "filename": "tools/request_approval.py",
                    "content": "async def request_approval():\n    return None\n",
                },
                {
                    "filename": "ui/ReviewWorkflow/ApprovalCard.jsx",
                    "content": "export default function ApprovalCard() { return null; }\n",
                },
            ]
        },
        tools_config=tools_config,
        wf_logger=_Logger(),
    )

    assert files == [
        {
            "path": "tools/request_approval.py",
            "content": "async def request_approval():\n    return None\n",
        }
    ]


def test_collect_ui_code_files_preserves_workflow_local_wrapper_and_generates_barrel() -> None:
    tools_config = workflow_converter._normalize_tools_manifest(
        {
            "tools": [
                {
                    "agent": "ReviewAgent",
                    "file": "tools/request_branded_approval.py",
                    "function": "request_branded_approval",
                    "tool_type": "UI_Tool",
                    "ui": {
                        "component": "BrandedApprovalCard",
                        "mode": "inline",
                        "workflow_primitive": "approval_card",
                        "realization": "workflow_wrapper",
                    },
                }
            ]
        },
        _Logger(),
    )

    files = workflow_converter._collect_ui_code_files(
        {
            "tools": [
                {
                    "filename": "tools/request_branded_approval.py",
                    "content": "async def request_branded_approval():\n    return None\n",
                },
                {
                    "filename": "ui/review/BrandedApprovalCard.jsx",
                    "content": "export default function BrandedApprovalCard() { return null; }\n",
                },
                {
                    "filename": "ui/review/helpers.js",
                    "content": "export const helper = true;\n",
                },
                {
                    "filename": "ui/index.js",
                    "content": "export {};\n",
                },
            ]
        },
        tools_config=tools_config,
        wf_logger=_Logger(),
    )

    assert [item["path"] for item in files] == [
        "tools/request_branded_approval.py",
        "ui/review/BrandedApprovalCard.jsx",
        "ui/review/helpers.js",
        "ui/index.js",
    ]
    assert (
        "export { default as BrandedApprovalCard } from './review/BrandedApprovalCard.jsx';"
        in files[-1]["content"]
    )


def test_collect_code_files_uses_canonical_codefile_contract() -> None:
    files = workflow_converter._collect_code_files(
        {
            "tools": [
                {
                    "filename": "tools/analyze.py",
                    "content": "async def analyze():\n    return None\n",
                },
                {
                    "filename": "../outside.py",
                    "content": "raise RuntimeError('bad path')\n",
                },
            ]
        },
        source_name="AgentToolsFileGenerator",
        wf_logger=_Logger(),
    )

    assert files == [
        {
            "path": "tools/analyze.py",
            "content": "async def analyze():\n    return None\n",
        }
    ]

