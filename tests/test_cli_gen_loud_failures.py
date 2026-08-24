"""mozaiks gen must loudly fail on silent no-op generations (issue #379).

A run that failed, paused for user input, or never executed a single agent
turn used to print "Generation complete!" and exit 0. These tests pin the
new contract: success requires a completed run with agent activity and files
under the artifact root, and the artifact root env var points at the CLI
output directory so the empty-output check inspects the directory tools
actually write to.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from mozaiks_cli.commands.gen import _report_run_outcome, _setup_environment
from mozaiksai.core.workflow.orchestration_patterns import _assemble_result_payload


def _payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "run_completed": True,
        "awaiting_user_input": False,
        "failed": False,
        "error": None,
        "run_status": 1,
        "agents_created": 5,
        "agent_turns": 12,
    }
    base.update(overrides)
    return base


def _result(payload: dict[str, Any] | None) -> dict[str, Any]:
    return {"success": True, "result": payload}


def _write_artifact(output_dir: Path) -> None:
    (output_dir / "workflows").mkdir(parents=True, exist_ok=True)
    (output_dir / "workflows" / "orchestrator.yaml").write_text(
        "workflow_name: X\n", encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# _report_run_outcome
# ---------------------------------------------------------------------------

class TestReportRunOutcome:
    def test_zero_agent_turns_fails_with_distinct_message(self, tmp_path, capsys):
        _write_artifact(tmp_path)
        rc = _report_run_outcome(_result(_payload(agent_turns=0)), tmp_path, "workflow")
        assert rc == 1
        out = capsys.readouterr().out
        assert "379" in out
        assert "no agent" in out

    def test_awaiting_user_input_fails_with_paused_message(self, tmp_path, capsys):
        _write_artifact(tmp_path)
        rc = _report_run_outcome(
            _result(_payload(run_completed=False, awaiting_user_input=True)),
            tmp_path,
            "workflow",
        )
        assert rc == 1
        out = capsys.readouterr().out
        assert "paused" in out
        assert "not a completed" in out

    def test_failed_payload_fails(self, tmp_path, capsys):
        _write_artifact(tmp_path)
        rc = _report_run_outcome(
            _result(_payload(run_completed=False, failed=True, error="boom")),
            tmp_path,
            "workflow",
        )
        assert rc == 1
        assert "boom" in capsys.readouterr().out

    def test_not_run_completed_fails(self, tmp_path, capsys):
        _write_artifact(tmp_path)
        rc = _report_run_outcome(
            _result(_payload(run_completed=False, run_status=0)), tmp_path, "workflow"
        )
        assert rc == 1
        assert "did not complete" in capsys.readouterr().out

    def test_none_payload_fails_instead_of_succeeding(self, tmp_path):
        _write_artifact(tmp_path)
        assert _report_run_outcome(_result(None), tmp_path, "workflow") == 1

    def test_exception_result_still_fails(self, tmp_path):
        assert _report_run_outcome({"success": False, "error": "x"}, tmp_path, "workflow") == 1

    def test_empty_output_dir_fails_with_artifact_root_message(self, tmp_path, capsys):
        rc = _report_run_outcome(_result(_payload()), tmp_path, "workflow")
        assert rc == 1
        assert "artifact root" in capsys.readouterr().out

    def test_completed_run_with_files_succeeds_and_prints_evidence(self, tmp_path, capsys):
        _write_artifact(tmp_path)
        rc = _report_run_outcome(_result(_payload()), tmp_path, "workflow")
        assert rc == 0
        out = capsys.readouterr().out
        assert "agents=5" in out
        assert "turns=12" in out

    def test_legacy_payload_without_turn_evidence_still_succeeds(self, tmp_path):
        payload = _payload()
        payload.pop("agents_created")
        payload.pop("agent_turns")
        _write_artifact(tmp_path)
        assert _report_run_outcome(_result(payload), tmp_path, "workflow") == 0


# ---------------------------------------------------------------------------
# _setup_environment
# ---------------------------------------------------------------------------

def test_setup_environment_points_artifact_root_at_output_dir(tmp_path, monkeypatch):
    # Pre-seed via monkeypatch so originals are restored on teardown even
    # though _setup_environment writes os.environ directly.
    monkeypatch.setitem(os.environ, "MOZAIKS_WORKFLOWS_PATH", "sentinel")
    monkeypatch.setitem(os.environ, "MOZAIKS_GENERATED_ARTIFACTS_PATH", "sentinel")

    staging = tmp_path / "staging"
    output = tmp_path / "generated"
    _setup_environment(tmp_path, staging, output)

    assert os.environ["MOZAIKS_WORKFLOWS_PATH"] == str(staging)
    assert os.environ["MOZAIKS_GENERATED_ARTIFACTS_PATH"] == str(output)


# ---------------------------------------------------------------------------
# orchestration result payload evidence
# ---------------------------------------------------------------------------

def test_result_payload_carries_agent_evidence():
    payload = _assemble_result_payload(
        workflow_name="AgentGenerator",
        chat_id="c1",
        app_id="a1",
        user_id="u1",
        workflow_complete=True,
        awaiting_user_input=False,
        run_failed=False,
        run_error=None,
        workflow_status_value=1,
        agents_created=7,
        agent_turns=42,
        ag2_channel_id="ch",
        ag2_close_reason="done",
        structured_outputs=None,
    )
    assert payload["agents_created"] == 7
    assert payload["agent_turns"] == 42
    assert payload["run_completed"] is True
    assert payload["failed"] is False
    assert payload["run_status"] == 1


def test_result_payload_failed_run_status():
    payload = _assemble_result_payload(
        workflow_name="AgentGenerator",
        chat_id="c1",
        app_id="a1",
        user_id=None,
        workflow_complete=False,
        awaiting_user_input=False,
        run_failed=True,
        run_error="agent blew up",
        workflow_status_value=0,
        agents_created=3,
        agent_turns=0,
        ag2_channel_id=None,
        ag2_close_reason="error",
        structured_outputs=None,
    )
    assert payload["run_status"] == "failed"
    assert payload["failed"] is True
    assert payload["error"] == "agent blew up"
    assert payload["agent_turns"] == 0
