from __future__ import annotations

from pathlib import Path

import pytest

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
                "workflow_startup_mode: AgentDriven",
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


def test_workflow_manager_load_summary_logs_degraded_on_partial_failure(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """WORKFLOW_LOAD_DEGRADED must be logged when any workflow fails to load."""
    import logging

    # Good workflow.
    ok_dir = tmp_path / "GoodFlow"
    ok_dir.mkdir()
    _write_yaml(
        ok_dir / "orchestrator.yaml",
        "workflow_name: GoodFlow\nmax_turns: 5\nhuman_in_the_loop: false\nworkflow_startup_mode: AgentDriven",
    )
    _write_yaml(
        ok_dir / "agents.yaml",
        "agents:\n  - name: Helper\n    prompt_sections:\n      - id: role\n        heading: \"[ROLE]\"\n        content: \"You help.\"",
    )

    # Bad workflow — missing workflow_startup_mode (will fail validation).
    bad_dir = tmp_path / "BadFlow"
    bad_dir.mkdir()
    _write_yaml(bad_dir / "orchestrator.yaml", "workflow_name: BadFlow\nmax_turns: 5")

    _workflow_manager_mod.UnifiedWorkflowManager._instance = None
    with caplog.at_level(logging.WARNING):
        _workflow_manager_mod.UnifiedWorkflowManager(workflows_base_path=str(tmp_path))

    assert any("WORKFLOW_LOAD_DEGRADED" in r.message for r in caplog.records), (
        "Expected WORKFLOW_LOAD_DEGRADED log when a workflow fails to load"
    )


def test_workflow_manager_load_summary_logs_ok_when_all_load(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """WORKFLOW_LOAD_OK must be logged when all workflows load successfully."""
    import logging

    ok_dir = tmp_path / "FlowOk"
    ok_dir.mkdir()
    _write_yaml(
        ok_dir / "orchestrator.yaml",
        "workflow_name: FlowOk\nmax_turns: 5\nhuman_in_the_loop: false\nworkflow_startup_mode: AgentDriven",
    )
    _write_yaml(
        ok_dir / "agents.yaml",
        "agents:\n  - name: Helper\n    prompt_sections:\n      - id: role\n        heading: \"[ROLE]\"\n        content: \"You help.\"",
    )

    _workflow_manager_mod.UnifiedWorkflowManager._instance = None
    with caplog.at_level(logging.INFO):
        _workflow_manager_mod.UnifiedWorkflowManager(workflows_base_path=str(tmp_path))

    assert any("WORKFLOW_LOAD_OK" in r.message for r in caplog.records), (
        "Expected WORKFLOW_LOAD_OK log when all workflows load"
    )


def test_workflow_manager_rejects_removed_startup_mode_only(tmp_path: Path) -> None:
    wf_dir = tmp_path / "FlowLegacy"
    wf_dir.mkdir(parents=True)

    _write_yaml(
        wf_dir / "orchestrator.yaml",
        "\n".join(
            [
                "workflow_name: FlowLegacy",
                "startup_mode: UserDriven",
                "human_in_the_loop: true",
            ]
        ),
    )

    _workflow_manager_mod.UnifiedWorkflowManager._instance = None
    manager = _workflow_manager_mod.UnifiedWorkflowManager(workflows_base_path=str(tmp_path))
    cfg = manager.get_config("FlowLegacy")
    info = manager.get_workflow_info("FlowLegacy")

    assert cfg == {}
    assert info is not None
    assert info.get("status") == "error"
    assert "workflow_startup_mode" in str(info.get("error") or "")

