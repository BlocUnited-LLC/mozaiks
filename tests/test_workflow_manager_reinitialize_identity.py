from __future__ import annotations

from pathlib import Path

import pytest

from tests.import_utils import import_module_directly

_workflow_manager_mod = import_module_directly("mozaiksai.core.workflow.workflow_manager")
WORKFLOWS_ROOT = Path(__file__).resolve().parents[1] / "factory_app" / "workflows"


@pytest.fixture(autouse=True)
def _restore_default_workflows() -> None:
    yield
    _workflow_manager_mod.initialize_workflows(base_path=str(WORKFLOWS_ROOT))


def _write_yaml(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_minimal_workflow(root: Path, workflow_name: str) -> None:
    wf_dir = root / workflow_name
    wf_dir.mkdir(parents=True, exist_ok=True)

    _write_yaml(
        wf_dir / "orchestrator.yaml",
        "\n".join(
            [
                f"workflow_name: {workflow_name}",
                "max_turns: 4",
                "human_in_the_loop: false",
                "workflow_startup_mode: AgentDriven",
                "orchestration_pattern: ag2_network",
                f"initial_message: {workflow_name} initial message",
                "initial_agent: DemoAgent",
            ]
        ),
    )
    _write_yaml(
        wf_dir / "agents.yaml",
        "\n".join(
            [
                "agents:",
                "  - name: DemoAgent",
                "    structured_outputs_required: false",
                "    prompt_sections:",
                "      - id: role",
                "        heading: \"[ROLE]\"",
                "        content: \"You are DemoAgent.\"",
            ]
        ),
    )
    _write_yaml(
        wf_dir / "transition_graph.yaml",
        "\n".join(
            [
                "transition_rules:",
                "  - source_agent: DemoAgent",
                "    target_agent: terminate",
                "    transition_type: after_turn",
            ]
        ),
    )
    _write_yaml(
        wf_dir / "context_variables.yaml",
        "\n".join(
            [
                "definitions: {}",
                "",
                "agents:",
                "  DemoAgent:",
                "    variables: []",
            ]
        ),
    )
    _write_yaml(wf_dir / "middleware.yaml", "prompt_middleware: []\n")
    _write_yaml(wf_dir / "tools.yaml", "tools: []\nlifecycle_tools: []\n")


def test_initialize_workflows_preserves_manager_identity(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"

    _write_minimal_workflow(first_root, "FlowOne")
    _write_minimal_workflow(second_root, "FlowTwo")

    _workflow_manager_mod.initialize_workflows(base_path=str(first_root))
    stale_ref = _workflow_manager_mod.workflow_manager
    assert stale_ref.get_config("FlowOne")
    assert stale_ref.get_config("FlowTwo") == {}

    _workflow_manager_mod.initialize_workflows(base_path=str(second_root))
    current_ref = _workflow_manager_mod.workflow_manager

    # Identity must remain stable so modules that imported `workflow_manager`
    # by value still observe reloaded workflow state.
    assert stale_ref is current_ref
    assert stale_ref.get_config("FlowOne") == {}
    assert stale_ref.get_config("FlowTwo")


def test_initialize_workflows_rejects_multiple_roots(tmp_path: Path) -> None:
    local_root = tmp_path / "local"
    shared_root = tmp_path / "shared"

    _write_minimal_workflow(local_root, "LocalOnly")
    _write_minimal_workflow(shared_root, "SharedOnly")

    try:
        _workflow_manager_mod.initialize_workflows(base_path=[str(local_root), str(shared_root)])
    except ValueError as exc:
        assert "one workflow root" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("initialize_workflows accepted multiple workflow roots")

