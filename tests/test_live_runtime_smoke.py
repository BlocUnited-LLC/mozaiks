"""
Live runtime smoke tests for the four core smoke workflows.

Each workflow exercises a distinct runtime capability with real AG2/LLM calls:
  - RuntimeSmoke              — single-agent structured output path
  - RuntimeToolCallSmoke      — tool call + connector readiness path
  - RuntimeUIPrimitiveSmoke   — HITL composer reply + ApprovalCard + artifact viewer
  - RuntimeTaskBatchSmoke      — workflow-local AG2 task batch execution

These tests are skipped by default.  Run them manually:

  python scripts/run_live_workflow_smoke.py --workflow RuntimeSmoke
  python scripts/run_live_workflow_smoke.py --workflow RuntimeToolCallSmoke
  python scripts/run_live_workflow_smoke.py --workflow RuntimeUIPrimitiveSmoke
  python scripts/run_live_workflow_smoke.py \\
      --workflow RuntimeTaskBatchSmoke \\
      --prompt "Build a task management app with projects, tasks, comments, notifications, and team onboarding."

Or invoke via pytest with the MOZAIKS_LIVE_SMOKE env var set:

  MOZAIKS_LIVE_SMOKE=1 pytest tests/test_live_runtime_smoke.py -v -s
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS_ROOT = REPO_ROOT / "factory_app" / "workflows"

_LIVE_SMOKE_ENABLED = os.getenv("MOZAIKS_LIVE_SMOKE", "").strip().lower() in ("1", "true", "yes")
_HAS_ENV = bool(os.getenv("OPENAI_API_KEY") and os.getenv("MONGO_URI"))


def _read_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


# ---------------------------------------------------------------------------
# Non-live: structure validation for all four smoke workflows
# ---------------------------------------------------------------------------

class TestRuntimeSmokeStructure:
    def test_orchestrator_is_agentdriven(self) -> None:
        data = _read_yaml(WORKFLOWS_ROOT / "RuntimeSmoke" / "orchestrator.yaml")
        assert data["workflow_startup_mode"] == "AgentDriven"
        assert data["human_in_the_loop"] is False
        assert data["initial_agent"] == "RuntimeSmokeAgent"

    def test_structured_output_defines_runtime_smoke_result(self) -> None:
        data = _read_yaml(WORKFLOWS_ROOT / "RuntimeSmoke" / "structured_outputs.yaml")
        assert data["registry"]["RuntimeSmokeAgent"] == "RuntimeSmokeResult"
        fields = data["models"]["RuntimeSmokeResult"]["fields"]
        assert {"agent_message", "result", "confidence"} <= fields.keys()


class TestRuntimeToolCallSmokeStructure:
    def test_orchestrator_is_agentdriven(self) -> None:
        data = _read_yaml(WORKFLOWS_ROOT / "RuntimeToolCallSmoke" / "orchestrator.yaml")
        assert data["workflow_startup_mode"] == "AgentDriven"
        assert data["human_in_the_loop"] is False

    def test_structured_output_defines_both_agent_results(self) -> None:
        data = _read_yaml(WORKFLOWS_ROOT / "RuntimeToolCallSmoke" / "structured_outputs.yaml")
        assert "RuntimeToolCallSmokeResult" in data["models"]
        assert "RuntimeConnectorSmokeResult" in data["models"]
        # optional_list fields — fixed in previous session for OpenAI strict mode
        connector_fields = data["models"]["RuntimeConnectorSmokeResult"]["fields"]
        for field_name in ("checked_services", "ready_services", "unresolved_required_services"):
            assert connector_fields[field_name]["type"] == "optional_list"


class TestRuntimeUIPrimitiveSmokeStructure:
    def test_orchestrator_is_hitl(self) -> None:
        data = _read_yaml(WORKFLOWS_ROOT / "RuntimeUIPrimitiveSmoke" / "orchestrator.yaml")
        assert data["human_in_the_loop"] is True
        assert data["initial_agent"] == "IntakeAgent"

    def test_context_variable_trigger_uses_contains(self) -> None:
        data = _read_yaml(WORKFLOWS_ROOT / "RuntimeUIPrimitiveSmoke" / "context_variables.yaml")
        trigger = data["definitions"]["intake_complete"]["source"]["triggers"][0]
        # Must use 'contains' not 'equals' — LLMs pad output around the NEXT token.
        assert "contains" in trigger["match"], (
            "intake_complete trigger must use 'contains: NEXT', not 'equals: NEXT'. "
            "LLMs rarely emit bare tokens without surrounding text."
        )
        assert trigger["match"]["contains"] == "NEXT"

    def test_structured_output_defines_smoke_result(self) -> None:
        data = _read_yaml(WORKFLOWS_ROOT / "RuntimeUIPrimitiveSmoke" / "structured_outputs.yaml")
        assert "RuntimeUIPrimitiveSmokeResult" in data["models"]


class TestRuntimeTaskBatchSmokeStructure:
    def test_orchestrator_is_fully_autonomous(self) -> None:
        data = _read_yaml(WORKFLOWS_ROOT / "RuntimeTaskBatchSmoke" / "orchestrator.yaml")
        assert data["human_in_the_loop"] is False
        assert data["initial_agent"] == "TaskPlannerAgent"

    def test_structured_output_defines_plan_and_result(self) -> None:
        data = _read_yaml(WORKFLOWS_ROOT / "RuntimeTaskBatchSmoke" / "structured_outputs.yaml")
        assert data["registry"]["TaskPlannerAgent"] == "RuntimeTaskBatchPlanOutput"
        assert data["registry"]["SynthesisAgent"] == "RuntimeTaskBatchSmokeResult"
        models = data["models"]
        assert "RuntimeTaskBatchPlan" in models
        assert "RuntimeTaskBatchWorkUnit" in models

    def test_task_batches_yaml_exists(self) -> None:
        task_batches = (
            WORKFLOWS_ROOT / "RuntimeTaskBatchSmoke" / "extended_orchestration" / "task_batches.yaml"
        )
        assert task_batches.exists(), "task_batches.yaml must exist for declarative task batch execution"
        data = _read_yaml(task_batches)
        batch = data["batches"][0]
        assert batch["source"]["path"] == "RuntimeTaskBatchPlan.work_units"
        assert batch["result"]["context_key"] == "task_batch_results"

    def test_handoffs_terminate_after_synthesis(self) -> None:
        data = _read_yaml(WORKFLOWS_ROOT / "RuntimeTaskBatchSmoke" / "handoffs.yaml")
        rules = {r["source_agent"]: r["target_agent"] for r in data["handoff_rules"]}
        assert rules["TaskPlannerAgent"] == "SynthesisAgent"
        assert rules["SynthesisAgent"] == "terminate"


# ---------------------------------------------------------------------------
# Live: RuntimeSmoke
# ---------------------------------------------------------------------------

@pytest.mark.skip(
    reason=(
        "Live LLM smoke is manual only. Run: "
        "python scripts/run_live_workflow_smoke.py --workflow RuntimeSmoke"
    )
)
class TestLiveRuntimeSmoke:
    @pytest.mark.asyncio
    async def test_runtime_smoke_completes_with_structured_output(self) -> None:
        from scripts.run_live_workflow_smoke import run_live_workflow_smoke

        result = await run_live_workflow_smoke(
            prompt="Write a one-sentence runtime smoke confirmation.",
            workflow_name="RuntimeSmoke",
            timeout_seconds=90.0,
        )

        assert result.success is True
        assert result.workflow_name == "RuntimeSmoke"
        output = result.structured_output or {}
        assert output.get("agent_message"), "RuntimeSmokeResult.agent_message must be non-empty"
        assert output.get("result"), "RuntimeSmokeResult.result must be non-empty"
        confidence = output.get("confidence")
        assert confidence is not None and 0.0 <= float(confidence) <= 1.0


# ---------------------------------------------------------------------------
# Live: RuntimeToolCallSmoke
# ---------------------------------------------------------------------------

@pytest.mark.skip(
    reason=(
        "Live LLM smoke is manual only. Run: "
        "python scripts/run_live_workflow_smoke.py --workflow RuntimeToolCallSmoke"
    )
)
class TestLiveRuntimeToolCallSmoke:
    @pytest.mark.asyncio
    async def test_tool_call_smoke_confirms_response(self) -> None:
        from scripts.run_live_workflow_smoke import run_live_workflow_smoke

        result = await run_live_workflow_smoke(
            prompt="Ask for a runtime tool call confirmation, then report the confirmed response.",
            workflow_name="RuntimeToolCallSmoke",
            timeout_seconds=120.0,
        )

        assert result.success is True
        assert result.workflow_name == "RuntimeToolCallSmoke"
        output = result.structured_output or {}
        assert output.get("agent_message"), "RuntimeToolCallSmokeResult.agent_message must be non-empty"
        assert output.get("confirmed") is True, (
            "Tool call smoke must confirm the response via the tool call path. "
            "If False, the structured output schema fix for optional_list may have regressed."
        )


# ---------------------------------------------------------------------------
# Live: RuntimeUIPrimitiveSmoke
# ---------------------------------------------------------------------------

@pytest.mark.skip(
    reason=(
        "Live LLM smoke is manual only. Run: "
        "python scripts/run_live_workflow_smoke.py --workflow RuntimeUIPrimitiveSmoke"
    )
)
class TestLiveRuntimeUIPrimitiveSmoke:
    @pytest.mark.asyncio
    async def test_ui_primitive_smoke_completes_all_three_lanes(self) -> None:
        """
        Tests the three runtime UI lanes in sequence:
          1. ComposerReply   — IntakeAgent asks a question; user answers via composer.
          2. ApprovalCard    — ReviewAgent calls request_acceptance_approval; smoke approves.
          3. ArtifactViewer  — ReviewAgent calls show_acceptance_diagram; artifact is emitted.

        The intake_complete context variable must fire (contains: NEXT trigger) to route
        IntakeAgent → ReviewAgent.  If this test hangs, the trigger mechanism is broken.
        """
        from scripts.run_live_workflow_smoke import run_live_workflow_smoke

        result = await run_live_workflow_smoke(
            prompt="Validate the composer reply, approval card, and artifact viewer runtime UI lanes in order.",
            workflow_name="RuntimeUIPrimitiveSmoke",
            timeout_seconds=180.0,
            user_replies=["I want to validate all three runtime UI lanes work correctly."],
            tool_response_payloads={
                "ApprovalCard": {
                    "action": "approve",
                    "approved": True,
                    "rationale": "Approval smoke test — all lanes validated.",
                },
            },
        )

        assert result.success is True
        assert result.workflow_name == "RuntimeUIPrimitiveSmoke"
        output = result.structured_output or {}
        assert output.get("composer_reply_seen") is True, (
            "ReviewAgent must see the composer reply. "
            "If False, intake_complete trigger did not fire and routing to ReviewAgent failed."
        )
        assert output.get("artifact_emitted") is True, (
            "show_acceptance_diagram must emit the artifact. "
            "If False, the artifact viewer UI lane did not complete."
        )


# ---------------------------------------------------------------------------
# Live: RuntimeTaskBatchSmoke
# ---------------------------------------------------------------------------

@pytest.mark.skip(
    reason=(
        "Live LLM smoke is manual only. Run: "
        "python scripts/run_live_workflow_smoke.py "
        "--workflow RuntimeTaskBatchSmoke "
        '--prompt "Build a task management app with projects, tasks, comments, notifications, and team onboarding."'
    )
)
class TestLiveRuntimeTaskBatchSmoke:
    @pytest.mark.asyncio
    async def test_task_batch_spawns_multiple_workers(self) -> None:
        """
        Validates the declarative task_batches.yaml execution pipeline:
          1. TaskPlannerAgent produces RuntimeTaskBatchPlanOutput with 3-6 work units.
          2. task_batches.yaml executor picks up RuntimeTaskBatchPlan.work_units.
          3. TaskWorkerAgent instances run with bounded concurrency.
          4. SynthesisAgent synthesizes results from task_batch_results context.

        This verifies the canonical workflow-local task batch execution path.
        """
        from scripts.run_live_workflow_smoke import run_live_workflow_smoke

        result = await run_live_workflow_smoke(
            prompt=(
                "Build a task management app with projects, tasks, comments, "
                "notifications, and team onboarding."
            ),
            workflow_name="RuntimeTaskBatchSmoke",
            timeout_seconds=300.0,
        )

        assert result.success is True
        assert result.workflow_name == "RuntimeTaskBatchSmoke"
        output = result.structured_output or {}
        assert output.get("task_batch_execution_used") is True, (
            "RuntimeTaskBatchSmokeResult.task_batch_execution_used must be True. "
            "SynthesisAgent must confirm the task batch executor ran."
        )
        work_unit_count = output.get("work_unit_count", 0)
        assert work_unit_count >= 3, (
            f"Expected at least 3 work units for a multi-surface product request, got {work_unit_count}. "
            "TaskPlannerAgent may have under-planned the intent."
        )
        assert output.get("all_units_succeeded") is True, (
            "All task batch workers must succeed. "
            "Check task_batches.yaml executor logs for worker errors."
        )
        assert output.get("failure_count", -1) == 0
        executed_kinds = output.get("executed_kinds") or []
        assert len(executed_kinds) >= 2, (
            f"Expected at least 2 distinct work-unit kinds, got {executed_kinds}. "
            "The task batch should span modules, pages, services, or workflows."
        )
