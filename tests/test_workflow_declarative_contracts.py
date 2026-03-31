from __future__ import annotations

from pathlib import Path

from tests.import_utils import import_module_directly

_workflow_manager_mod = import_module_directly("mozaiksai.core.workflow.workflow_manager")


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
                "    auto_tool_mode: false",
            ]
        ),
    )


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


def test_workflow_manager_rejects_invalid_hooks_contract(tmp_path: Path) -> None:
    wf_dir = tmp_path / "FlowBadHooks"
    wf_dir.mkdir(parents=True)
    _write_minimal_orchestrator_and_agents(wf_dir, "FlowBadHooks")
    _write_yaml(
        wf_dir / "hooks.yaml",
        "\n".join(
            [
                "hooks:",
                "  - hook_type: unknown_hook",
                "    hook_agent: Planner",
                "    filename: hooks.py",
                "    function: before_send",
            ]
        ),
    )

    _workflow_manager_mod.UnifiedWorkflowManager._instance = None
    manager = _workflow_manager_mod.UnifiedWorkflowManager(workflows_base_path=str(tmp_path))
    info = manager.get_workflow_info("FlowBadHooks")

    assert info is not None
    assert info.get("status") == "error"
    assert "hooks.yaml" in str(info.get("error") or "")


def test_workflow_manager_rejects_legacy_context_variables_shape(tmp_path: Path) -> None:
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


def test_workflow_manager_accepts_valid_declarative_bundle(tmp_path: Path) -> None:
    wf_dir = tmp_path / "FlowValid"
    wf_dir.mkdir(parents=True)
    _write_minimal_orchestrator_and_agents(wf_dir, "FlowValid")
    _write_yaml(
        wf_dir / "handoffs.yaml",
        "\n".join(
            [
                "handoff_rules:",
                "  - source_agent: Planner",
                "    target_agent: user",
                "    handoff_type: after_work",
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
        wf_dir / "hooks.yaml",
        "\n".join(
            [
                "hooks:",
                "  - hook_type: update_agent_state",
                "    hook_agent: Planner",
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
                "  - Planner",
                "chat_pane_agents:",
                "  - Planner",
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
    assert isinstance(config.get("handoffs", {}).get("handoff_rules"), list)
