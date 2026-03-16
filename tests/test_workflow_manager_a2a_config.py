from __future__ import annotations

from pathlib import Path

from tests.import_utils import import_module_directly

_workflow_manager_mod = import_module_directly("mozaiksai.core.workflow.workflow_manager")


def _write_yaml(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def test_workflow_manager_loads_optional_a2a_yaml(tmp_path: Path) -> None:
    wf_dir = tmp_path / "FlowA"
    wf_dir.mkdir(parents=True)

    _write_yaml(
        wf_dir / "orchestrator.yaml",
        "\n".join(
            [
                "workflow_name: FlowA",
                "max_turns: 10",
                "human_in_the_loop: true",
                "startup_mode: AgentDriven",
            ]
        ),
    )
    _write_yaml(
        wf_dir / "agents.yaml",
        "\n".join(
            [
                "agents:",
                "  - name: RemotePlanner",
                "    prompt_sections:",
                "      - id: role",
                "        heading: \"[ROLE]\"",
                "        content: \"You are a planner.\"",
            ]
        ),
    )
    _write_yaml(
        wf_dir / "a2a.yaml",
        "\n".join(
            [
                "agents:",
                "  - name: RemotePlanner",
                "    url: https://example.com/agents/planner",
                "    max_reconnects: 4",
                "    polling_interval: 0.75",
                "    client:",
                "      streaming: true",
                "      polling: false",
            ]
        ),
    )

    _workflow_manager_mod.UnifiedWorkflowManager._instance = None
    manager = _workflow_manager_mod.UnifiedWorkflowManager(workflows_base_path=str(tmp_path))
    cfg = manager.get_config("FlowA")

    assert "a2a" in cfg
    entries = cfg["a2a"].get("agents", [])
    assert isinstance(entries, list)
    assert entries and entries[0].get("name") == "RemotePlanner"
    assert entries[0].get("url") == "https://example.com/agents/planner"
