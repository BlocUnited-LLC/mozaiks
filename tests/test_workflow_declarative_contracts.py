from __future__ import annotations

from pathlib import Path

import yaml

from tests.import_utils import import_module_directly

_workflow_manager_mod = import_module_directly("mozaiksai.core.workflow.workflow_manager")
from mozaiksai.core.workflow.declarative import (  # noqa: E402
    parse_agents_config,
    parse_transition_graph_config,
)


def _write_yaml(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _write_minimal_orchestrator_and_agents(wf_dir: Path, workflow_name: str) -> None:
    _write_yaml(
        wf_dir / "orchestrator.yaml",
        "\n".join(
            [
                f"workflow_name: {workflow_name}",
                "workflow_startup_mode: AgentDriven",
                "human_in_the_loop: true",
            ]
        ),
    )
    _write_yaml(
        wf_dir / "agents.yaml",
        "\n".join(
            [
                "agents:",
                "  - name: Planner",
                "    system_message: \"You are a planner.\"",
            ]
        ),
    )


def test_workflow_manager_rejects_removed_agent_auto_tool_field(tmp_path: Path) -> None:
    wf_dir = tmp_path / "FlowLegacyAutoToolField"
    wf_dir.mkdir(parents=True)
    _write_minimal_orchestrator_and_agents(wf_dir, "FlowLegacyAutoToolField")
    _write_yaml(
        wf_dir / "agents.yaml",
        "\n".join(
            [
                "agents:",
                "  - name: Planner",
                "    system_message: \"You are a planner.\"",
                "    auto_tool_mode: false",
            ]
        ),
    )

    _workflow_manager_mod.UnifiedWorkflowManager._instance = None
    manager = _workflow_manager_mod.UnifiedWorkflowManager(workflows_base_path=str(tmp_path))
    info = manager.get_workflow_info("FlowLegacyAutoToolField")

    assert info is not None
    assert info.get("status") == "error"
    assert "auto_tool_mode" in str(info.get("error") or "")


def test_workflow_manager_rejects_invalid_tools_contract(tmp_path: Path) -> None:
    wf_dir = tmp_path / "FlowBadTools"
    wf_dir.mkdir(parents=True)
    _write_minimal_orchestrator_and_agents(wf_dir, "FlowBadTools")
    _write_yaml(
        wf_dir / "tools.yaml",
        "\n".join(
            [
                "tools:",
                "  - agent: Planner",
                "    file: do_work.py",
                "    function: do_work",
                # Missing required tool_type
            ]
        ),
    )

    _workflow_manager_mod.UnifiedWorkflowManager._instance = None
    manager = _workflow_manager_mod.UnifiedWorkflowManager(workflows_base_path=str(tmp_path))
    info = manager.get_workflow_info("FlowBadTools")

    assert info is not None
    assert info.get("status") == "error"
    assert "tools.yaml" in str(info.get("error") or "")


def test_workflow_manager_rejects_invalid_structured_outputs_contract(tmp_path: Path) -> None:
    wf_dir = tmp_path / "FlowBadStructured"
    wf_dir.mkdir(parents=True)
    _write_minimal_orchestrator_and_agents(wf_dir, "FlowBadStructured")
    _write_yaml(
        wf_dir / "structured_outputs.yaml",
        "\n".join(
            [
                "registry:",
                "  Planner: MissingModel",
                "models:",
                "  ActualModel:",
                "    type: model",
                "    fields:",
                "      message:",
                "        type: str",
            ]
        ),
    )

    _workflow_manager_mod.UnifiedWorkflowManager._instance = None
    manager = _workflow_manager_mod.UnifiedWorkflowManager(workflows_base_path=str(tmp_path))
    info = manager.get_workflow_info("FlowBadStructured")

    assert info is not None
    assert info.get("status") == "error"
    assert "structured_outputs.yaml" in str(info.get("error") or "")


def test_workflow_manager_rejects_invalid_middleware_contract(tmp_path: Path) -> None:
    wf_dir = tmp_path / "FlowBadMiddleware"
    wf_dir.mkdir(parents=True)
    _write_minimal_orchestrator_and_agents(wf_dir, "FlowBadMiddleware")
    _write_yaml(
        wf_dir / "middleware.yaml",
        "\n".join(
            [
                "prompt_middleware:",
                "  - agent: Planner",
                "    filename: hooks.py",
            ]
        ),
    )

    _workflow_manager_mod.UnifiedWorkflowManager._instance = None
    manager = _workflow_manager_mod.UnifiedWorkflowManager(workflows_base_path=str(tmp_path))
    info = manager.get_workflow_info("FlowBadMiddleware")

    assert info is not None
    assert info.get("status") == "error"
    assert "middleware.yaml" in str(info.get("error") or "")


def test_workflow_manager_rejects_removed_context_variables_shape(tmp_path: Path) -> None:
    wf_dir = tmp_path / "FlowBadContext"
    wf_dir.mkdir(parents=True)
    _write_minimal_orchestrator_and_agents(wf_dir, "FlowBadContext")
    _write_yaml(
        wf_dir / "context_variables.yaml",
        "\n".join(
            [
                "definitions:",
                "  - name: flag_a",
                "    source:",
                "      type: state",
                "      default: false",
            ]
        ),
    )

    _workflow_manager_mod.UnifiedWorkflowManager._instance = None
    manager = _workflow_manager_mod.UnifiedWorkflowManager(workflows_base_path=str(tmp_path))
    info = manager.get_workflow_info("FlowBadContext")

    assert info is not None
    assert info.get("status") == "error"
    assert "context_variables.yaml" in str(info.get("error") or "")


def test_workflow_manager_rejects_undeclared_transition_context_variable(tmp_path: Path) -> None:
    wf_dir = tmp_path / "FlowUndeclaredRouteContext"
    wf_dir.mkdir(parents=True)
    _write_minimal_orchestrator_and_agents(wf_dir, "FlowUndeclaredRouteContext")
    _write_yaml(
        wf_dir / "agents.yaml",
        "\n".join(
            [
                "agents:",
                "  - name: Planner",
                "    system_message: \"You are a planner.\"",
                "  - name: Reviewer",
                "    system_message: \"You are a reviewer.\"",
            ]
        ),
    )
    _write_yaml(
        wf_dir / "context_variables.yaml",
        "\n".join(
            [
                "definitions: {}",
                "agents: {}",
            ]
        ),
    )
    _write_yaml(
        wf_dir / "transition_graph.yaml",
        "\n".join(
            [
                "transition_rules:",
                "  - source_agent: Planner",
                "    target_agent: Reviewer",
                "    transition_type: condition",
                "    condition_type: context_equals",
                "    condition_key: route_decision",
                "    condition_value: review",
            ]
        ),
    )

    _workflow_manager_mod.UnifiedWorkflowManager._instance = None
    manager = _workflow_manager_mod.UnifiedWorkflowManager(workflows_base_path=str(tmp_path))
    info = manager.get_workflow_info("FlowUndeclaredRouteContext")

    assert info is not None
    assert info.get("status") == "error"
    assert "route_decision" in str(info.get("error") or "")
    assert "context_variables.yaml contract mismatch" in str(info.get("error") or "")


def test_workflow_manager_rejects_undeclared_transition_context_expression_variable(tmp_path: Path) -> None:
    wf_dir = tmp_path / "FlowUndeclaredRouteExpressionContext"
    wf_dir.mkdir(parents=True)
    _write_minimal_orchestrator_and_agents(wf_dir, "FlowUndeclaredRouteExpressionContext")
    _write_yaml(
        wf_dir / "agents.yaml",
        "\n".join(
            [
                "agents:",
                "  - name: Planner",
                "    system_message: \"You are a planner.\"",
                "  - name: Reviewer",
                "    system_message: \"You are a reviewer.\"",
            ]
        ),
    )
    _write_yaml(
        wf_dir / "context_variables.yaml",
        "\n".join(
            [
                "definitions:",
                "  route_decision:",
                "    source:",
                "      type: state",
                "      default: draft",
                "agents: {}",
            ]
        ),
    )
    _write_yaml(
        wf_dir / "transition_graph.yaml",
        "\n".join(
            [
                "transition_rules:",
                "  - source_agent: Planner",
                "    target_agent: Reviewer",
                "    transition_type: condition",
                "    condition_type: context_expression",
                "    context_expression: \"${route_decision} == 'review' and ${approval_ready}\"",
            ]
        ),
    )

    _workflow_manager_mod.UnifiedWorkflowManager._instance = None
    manager = _workflow_manager_mod.UnifiedWorkflowManager(workflows_base_path=str(tmp_path))
    info = manager.get_workflow_info("FlowUndeclaredRouteExpressionContext")

    assert info is not None
    assert info.get("status") == "error"
    assert "approval_ready" in str(info.get("error") or "")
    assert "context_variables.yaml contract mismatch" in str(info.get("error") or "")


def test_workflow_manager_rejects_undeclared_task_batch_result_keys(tmp_path: Path) -> None:
    wf_dir = tmp_path / "FlowUndeclaredTaskBatchContext"
    (wf_dir / "extended_orchestration").mkdir(parents=True)
    _write_minimal_orchestrator_and_agents(wf_dir, "FlowUndeclaredTaskBatchContext")
    _write_yaml(
        wf_dir / "context_variables.yaml",
        "\n".join(
            [
                "definitions: {}",
                "agents: {}",
            ]
        ),
    )
    _write_yaml(
        wf_dir / "extended_orchestration" / "task_batches.yaml",
        "\n".join(
            [
                "version: 1",
                "conveyors:",
                "  - id: review_tasks",
                "    decomposition_agent: Planner",
                "    execution_agents:",
                "      - Planner",
            ]
        ),
    )

    _workflow_manager_mod.UnifiedWorkflowManager._instance = None
    manager = _workflow_manager_mod.UnifiedWorkflowManager(workflows_base_path=str(tmp_path))
    info = manager.get_workflow_info("FlowUndeclaredTaskBatchContext")

    assert info is not None
    assert info.get("status") == "error"
    assert "review_tasks_results" in str(info.get("error") or "")
    assert "review_tasks_status" in str(info.get("error") or "")


def test_workflow_manager_rejects_agent_context_exposure_to_undeclared_variable(tmp_path: Path) -> None:
    wf_dir = tmp_path / "FlowBadAgentContextExposure"
    wf_dir.mkdir(parents=True)
    _write_minimal_orchestrator_and_agents(wf_dir, "FlowBadAgentContextExposure")
    _write_yaml(
        wf_dir / "context_variables.yaml",
        "\n".join(
            [
                "definitions: {}",
                "agents:",
                "  Planner:",
                "    variables:",
                "      - missing_context",
            ]
        ),
    )

    _workflow_manager_mod.UnifiedWorkflowManager._instance = None
    manager = _workflow_manager_mod.UnifiedWorkflowManager(workflows_base_path=str(tmp_path))
    info = manager.get_workflow_info("FlowBadAgentContextExposure")

    assert info is not None
    assert info.get("status") == "error"
    assert "missing_context" in str(info.get("error") or "")


def test_workflow_manager_accepts_valid_declarative_bundle(tmp_path: Path) -> None:
    wf_dir = tmp_path / "FlowValid"
    wf_dir.mkdir(parents=True)
    _write_minimal_orchestrator_and_agents(wf_dir, "FlowValid")
    _write_yaml(
        wf_dir / "transition_graph.yaml",
        "\n".join(
            [
                "transition_rules:",
                "  - source_agent: Planner",
                "    target_agent: user",
                "    transition_type: after_turn",
            ]
        ),
    )
    _write_yaml(
        wf_dir / "tools.yaml",
        "\n".join(
            [
                "tools:",
                "  - agent: Planner",
                "    file: run_task.py",
                "    function: run_task",
                "    tool_type: Agent_Tool",
            ]
        ),
    )
    _write_yaml(
        wf_dir / "middleware.yaml",
        "\n".join(
            [
                "prompt_middleware:",
                "  - agent: Planner",
                "    filename: planner_hooks.py",
                "    function: update_state",
            ]
        ),
    )
    _write_yaml(
        wf_dir / "ui_config.yaml",
        "\n".join(
            [
                "visual_agents:",
                "  - Planner"
            ]
        ),
    )
    _write_yaml(
        wf_dir / "structured_outputs.yaml",
        "\n".join(
            [
                "registry:",
                "  Planner: PlannerResponse",
                "models:",
                "  PlannerResponse:",
                "    type: model",
                "    fields:",
                "      agent_message:",
                "        type: str",
            ]
        ),
    )

    _workflow_manager_mod.UnifiedWorkflowManager._instance = None
    manager = _workflow_manager_mod.UnifiedWorkflowManager(workflows_base_path=str(tmp_path))
    info = manager.get_workflow_info("FlowValid")
    config = manager.get_config("FlowValid")

    assert info is not None
    assert info.get("status") == "loaded"
    assert config.get("workflow_name") == "FlowValid"
    assert isinstance((config.get("agents") or {}).get("agents"), dict)
    assert isinstance(config.get("tools"), list)
    assert isinstance(config.get("transition_graph", {}).get("transition_rules"), list)


def test_workflow_manager_accepts_user_text_state_trigger(tmp_path: Path) -> None:
    wf_dir = tmp_path / "FlowUserTextTrigger"
    wf_dir.mkdir(parents=True)
    _write_minimal_orchestrator_and_agents(wf_dir, "FlowUserTextTrigger")
    _write_yaml(
        wf_dir / "context_variables.yaml",
        "\n".join(
            [
                "definitions:",
                "  review_approved:",
                "    type: boolean",
                "    source:",
                "      type: state",
                "      default: false",
                "      triggers:",
                "        - type: user_text",
                "          match:",
                "            contains: approved",
                "agents: {}",
            ]
        ),
    )

    _workflow_manager_mod.UnifiedWorkflowManager._instance = None
    manager = _workflow_manager_mod.UnifiedWorkflowManager(workflows_base_path=str(tmp_path))
    info = manager.get_workflow_info("FlowUserTextTrigger")
    config = manager.get_config("FlowUserTextTrigger")

    assert info is not None
    assert info.get("status") == "loaded"
    trigger = config["context_variables"]["definitions"]["review_approved"]["source"]["triggers"][0]
    assert trigger["type"] == "user_text"
    assert trigger["match"]["contains"] == "approved"


def test_workflow_manager_normalizes_ui_tool_contract_defaults(tmp_path: Path) -> None:
    wf_dir = tmp_path / "FlowUiContractDefaults"
    wf_dir.mkdir(parents=True)
    _write_minimal_orchestrator_and_agents(wf_dir, "FlowUiContractDefaults")
    _write_yaml(
        wf_dir / "tools.yaml",
        "\n".join(
            [
                "tools:",
                "  - agent: Planner",
                "    file: render_panel.py",
                "    function: render_panel",
                "    tool_type: UI_Tool",
                "    ui:",
                "      component: PlanPanel",
                "      mode: artifact",
                "      workflow_primitive: form_card",
                "      realization: workflow_wrapper",
            ]
        ),
    )

    _workflow_manager_mod.UnifiedWorkflowManager._instance = None
    manager = _workflow_manager_mod.UnifiedWorkflowManager(workflows_base_path=str(tmp_path))
    info = manager.get_workflow_info("FlowUiContractDefaults")
    config = manager.get_config("FlowUiContractDefaults")

    assert info is not None
    assert info.get("status") == "loaded"
    tool = (config.get("tools") or [])[0]
    ui_contract = tool.get("ui_contract") or {}
    assert ui_contract.get("surface_kind") == "agent_tool"
    assert (ui_contract.get("payload_schema") or {}).get("type") == "object"
    assert ui_contract.get("actions_schema") == []


def test_workflow_manager_rejects_agent_tool_with_ui_contract(tmp_path: Path) -> None:
    wf_dir = tmp_path / "FlowInvalidAgentUiContract"
    wf_dir.mkdir(parents=True)
    _write_minimal_orchestrator_and_agents(wf_dir, "FlowInvalidAgentUiContract")
    _write_yaml(
        wf_dir / "tools.yaml",
        "\n".join(
            [
                "tools:",
                "  - agent: Planner",
                "    file: execute.py",
                "    function: execute",
                "    tool_type: Agent_Tool",
                "    ui_contract:",
                "      surface_kind: agent_tool",
                "      payload_schema:",
                "        type: object",
                "      actions_schema: []",
            ]
        ),
    )

    _workflow_manager_mod.UnifiedWorkflowManager._instance = None
    manager = _workflow_manager_mod.UnifiedWorkflowManager(workflows_base_path=str(tmp_path))
    info = manager.get_workflow_info("FlowInvalidAgentUiContract")

    assert info is not None
    assert info.get("status") == "error"
    assert "tools.yaml" in str(info.get("error") or "")


def test_workflow_manager_rejects_agent_tool_with_ui_block(tmp_path: Path) -> None:
    wf_dir = tmp_path / "FlowInvalidAgentUi"
    wf_dir.mkdir(parents=True)
    _write_minimal_orchestrator_and_agents(wf_dir, "FlowInvalidAgentUi")
    _write_yaml(
        wf_dir / "tools.yaml",
        "\n".join(
            [
                "tools:",
                "  - agent: Planner",
                "    file: execute.py",
                "    function: execute",
                "    tool_type: Agent_Tool",
                "    ui:",
                "      component: PlanPanel",
                "      mode: inline",
            ]
        ),
    )

    _workflow_manager_mod.UnifiedWorkflowManager._instance = None
    manager = _workflow_manager_mod.UnifiedWorkflowManager(workflows_base_path=str(tmp_path))
    info = manager.get_workflow_info("FlowInvalidAgentUi")

    assert info is not None
    assert info.get("status") == "error"
    assert "tools.yaml" in str(info.get("error") or "")


def test_workflow_manager_accepts_ui_surface_without_ui_contract(tmp_path: Path) -> None:
    wf_dir = tmp_path / "FlowUiSurface"
    wf_dir.mkdir(parents=True)
    _write_minimal_orchestrator_and_agents(wf_dir, "FlowUiSurface")
    _write_yaml(
        wf_dir / "tools.yaml",
        "\n".join(
            [
                "tools:",
                "  - agent: Planner",
                "    file: render_panel.py",
                "    function: render_panel",
                "    tool_type: UI_Surface",
                "    ui:",
                "      component: PlanPanel",
                "      mode: artifact",
                "      workflow_primitive: document_preview",
                "      realization: generated_component",
            ]
        ),
    )

    _workflow_manager_mod.UnifiedWorkflowManager._instance = None
    manager = _workflow_manager_mod.UnifiedWorkflowManager(workflows_base_path=str(tmp_path))
    info = manager.get_workflow_info("FlowUiSurface")
    config = manager.get_config("FlowUiSurface")

    assert info is not None
    assert info.get("status") == "loaded"
    tool = (config.get("tools") or [])[0]
    assert tool["tool_type"] == "UI_Surface"
    assert tool.get("ui", {}).get("component") == "PlanPanel"
    assert tool.get("ui_contract") is None


def test_workflow_manager_rejects_non_renderable_workflow_primitive(tmp_path: Path) -> None:
    wf_dir = tmp_path / "FlowInvalidWorkflowPrimitive"
    wf_dir.mkdir(parents=True)
    _write_minimal_orchestrator_and_agents(wf_dir, "FlowInvalidWorkflowPrimitive")
    _write_yaml(
        wf_dir / "tools.yaml",
        "\n".join(
            [
                "tools:",
                "  - agent: Planner",
                "    file: render_panel.py",
                "    function: render_panel",
                "    tool_type: UI_Tool",
                "    ui:",
                "      component: PlanPanel",
                "      mode: inline",
                "      workflow_primitive: composer_reply",
                "      realization: shell_builtin",
            ]
        ),
    )

    _workflow_manager_mod.UnifiedWorkflowManager._instance = None
    manager = _workflow_manager_mod.UnifiedWorkflowManager(workflows_base_path=str(tmp_path))
    info = manager.get_workflow_info("FlowInvalidWorkflowPrimitive")

    assert info is not None
    assert info.get("status") == "error"
    assert "workflow_primitive" in str(info.get("error") or "")


def test_workflow_manager_rejects_ui_surface_missing_realization(tmp_path: Path) -> None:
    wf_dir = tmp_path / "FlowMissingRealization"
    wf_dir.mkdir(parents=True)
    _write_minimal_orchestrator_and_agents(wf_dir, "FlowMissingRealization")
    _write_yaml(
        wf_dir / "tools.yaml",
        "\n".join(
            [
                "tools:",
                "  - agent: Planner",
                "    file: render_panel.py",
                "    function: render_panel",
                "    tool_type: UI_Surface",
                "    ui:",
                "      component: PlanPanel",
                "      mode: artifact",
                "      workflow_primitive: document_preview",
            ]
        ),
    )

    _workflow_manager_mod.UnifiedWorkflowManager._instance = None
    manager = _workflow_manager_mod.UnifiedWorkflowManager(workflows_base_path=str(tmp_path))
    info = manager.get_workflow_info("FlowMissingRealization")

    assert info is not None
    assert info.get("status") == "error"
    assert "ui.realization" in str(info.get("error") or "")


def test_workflow_manager_rejects_manifest_tool_integration_metadata(tmp_path: Path) -> None:
    wf_dir = tmp_path / "FlowInvalidToolIntegration"
    wf_dir.mkdir(parents=True)
    _write_minimal_orchestrator_and_agents(wf_dir, "FlowInvalidToolIntegration")
    _write_yaml(
        wf_dir / "tools.yaml",
        "\n".join(
            [
                "tools:",
                "  - agent: Planner",
                "    file: execute.py",
                "    function: execute",
                "    tool_type: Agent_Tool",
                "    integration: Slack",
            ]
        ),
    )

    _workflow_manager_mod.UnifiedWorkflowManager._instance = None
    manager = _workflow_manager_mod.UnifiedWorkflowManager(workflows_base_path=str(tmp_path))
    info = manager.get_workflow_info("FlowInvalidToolIntegration")

    assert info is not None
    assert info.get("status") == "error"
    assert "tools.yaml" in str(info.get("error") or "")


def test_build_workflows_load_from_workspace_bundle() -> None:
    workflows_root = Path(__file__).resolve().parents[1] / "factory_app" / "workflows"

    _workflow_manager_mod.UnifiedWorkflowManager._instance = None
    manager = _workflow_manager_mod.UnifiedWorkflowManager(workflows_base_path=str(workflows_root))

    discovery_info = manager.get_workflow_info("ExistingAppDiscovery")
    app_generator_info = manager.get_workflow_info("AppGenerator")
    agent_generator_info = manager.get_workflow_info("AgentGenerator")
    discovery_config = manager.get_config("ExistingAppDiscovery")
    app_generator_config = manager.get_config("AppGenerator")
    agent_generator_config = manager.get_config("AgentGenerator")

    assert discovery_info is not None
    assert discovery_info.get("status") == "loaded"
    assert app_generator_info is not None
    assert app_generator_info.get("status") == "loaded"
    assert agent_generator_info is not None
    assert agent_generator_info.get("status") == "loaded"
    assert discovery_config.get("workflow_name") == "ExistingAppDiscovery"
    assert app_generator_config.get("workflow_name") == "AppGenerator"
    assert agent_generator_config.get("workflow_name") == "AgentGenerator"
    assert discovery_config.get("tools")
    assert app_generator_config.get("tools")
    assert (agent_generator_config.get("structured_outputs") or {}).get("registry")


def test_factory_workflow_agent_manifests_match_current_schema() -> None:
    workflows_root = Path(__file__).resolve().parents[1] / "factory_app" / "workflows"
    failures: list[str] = []

    for agents_path in sorted(workflows_root.rglob("agents.yaml")):
        raw = yaml.safe_load(agents_path.read_text(encoding="utf-8")) or {}
        try:
            parse_agents_config(raw)
        except ValueError as exc:
            rel_path = agents_path.relative_to(workflows_root.parent.parent)
            failures.append(f"{rel_path.as_posix()}: {exc}")

    assert not failures, "Factory workflow agents.yaml schema drift:\n" + "\n".join(failures)


def test_factory_workflow_transition_graphs_match_current_schema() -> None:
    workflows_root = Path(__file__).resolve().parents[1] / "factory_app" / "workflows"
    failures: list[str] = []

    for graph_path in sorted(workflows_root.rglob("transition_graph.yaml")):
        raw = yaml.safe_load(graph_path.read_text(encoding="utf-8")) or {}
        try:
            parse_transition_graph_config(raw)
        except ValueError as exc:
            rel_path = graph_path.relative_to(workflows_root.parent.parent)
            failures.append(f"{rel_path.as_posix()}: {exc}")

    assert not failures, "Factory workflow transition_graph.yaml schema drift:\n" + "\n".join(failures)
