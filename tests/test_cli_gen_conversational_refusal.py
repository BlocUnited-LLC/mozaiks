"""mozaiks gen must refuse conversational workflows before spending tokens.

AgentGenerator opens with InterviewAgent, which asks a clarifying question and
hands control to ``user``. The CLI has one --prompt and no way to reply, so the
run always stalls at WORKFLOW_AWAITING_INPUT with zero files written — after
burning real LLM tokens (issue #383).

These tests pin the contract: the refusal is derived from the workflow's own
declarative config (not a name match), it happens *before* the orchestration
entry point is ever called, and --allow-interactive bypasses it.
"""
from __future__ import annotations

from argparse import Namespace
from pathlib import Path

import pytest

from mozaiks_cli.commands import gen as gen_module
from mozaiks_cli.commands.gen import (
    ALLOW_INTERACTIVE_FLAG,
    STUDIO_COMMAND,
    _detect_conversational_signals,
)

_INTERVIEW_GRAPH = """\
transition_rules:
- source_agent: InterviewAgent
  target_agent: user
  transition_type: after_turn
  transition_target: RevertToUserTarget
- source_agent: PatternAgent
  target_agent: ProjectOverviewAgent
  transition_type: after_turn
  transition_target: AgentTarget
"""

_ONE_SHOT_GRAPH = """\
transition_rules:
- source_agent: PlanAgent
  target_agent: BuildAgent
  transition_type: after_turn
  transition_target: AgentTarget
- source_agent: BuildAgent
  target_agent: WriteAgent
  transition_type: after_turn
  transition_target: AgentTarget
"""


def _write_workflow(root: Path, orchestrator: str, graph: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "orchestrator.yaml").write_text(orchestrator, encoding="utf-8")
    (root / "transition_graph.yaml").write_text(graph, encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# _detect_conversational_signals — property, not name
# ---------------------------------------------------------------------------

class TestDetectConversationalSignals:
    def test_user_transition_edge_is_detected(self, tmp_path: Path) -> None:
        wf = _write_workflow(
            tmp_path / "AnyWorkflow",
            "workflow_name: AnyWorkflow\ninitial_agent: InterviewAgent\n",
            _INTERVIEW_GRAPH,
        )
        signals = _detect_conversational_signals(wf)
        assert signals
        assert any("hands control to the user" in s for s in signals)

    def test_human_in_the_loop_declaration_is_detected(self, tmp_path: Path) -> None:
        wf = _write_workflow(
            tmp_path / "HitlWorkflow",
            "workflow_name: HitlWorkflow\nhuman_in_the_loop: true\n",
            _ONE_SHOT_GRAPH,
        )
        signals = _detect_conversational_signals(wf)
        assert any("human_in_the_loop" in s for s in signals)

    def test_one_shot_workflow_yields_no_signals(self, tmp_path: Path) -> None:
        wf = _write_workflow(
            tmp_path / "OneShot",
            "workflow_name: OneShot\nhuman_in_the_loop: false\n",
            _ONE_SHOT_GRAPH,
        )
        assert _detect_conversational_signals(wf) == []

    def test_detection_does_not_key_off_workflow_name(self, tmp_path: Path) -> None:
        """A workflow *named* AgentGenerator with a one-shot graph still runs."""
        wf = _write_workflow(
            tmp_path / "AgentGenerator",
            "workflow_name: AgentGenerator\n",
            _ONE_SHOT_GRAPH,
        )
        assert _detect_conversational_signals(wf) == []

    def test_missing_config_files_are_not_conversational(self, tmp_path: Path) -> None:
        empty = tmp_path / "Empty"
        empty.mkdir()
        assert _detect_conversational_signals(empty) == []

    def test_unreadable_yaml_does_not_raise(self, tmp_path: Path) -> None:
        wf = tmp_path / "Broken"
        wf.mkdir()
        (wf / "orchestrator.yaml").write_text("::: not [ yaml", encoding="utf-8")
        assert _detect_conversational_signals(wf) == []

    def test_real_agent_generator_is_conversational(self) -> None:
        """Guard the live signal: shipped AgentGenerator must be detected."""
        from mozaiksai.resources import resolve_factory_workflows_root

        wf = resolve_factory_workflows_root() / "AgentGenerator"
        if not wf.is_dir():  # pragma: no cover - source checkout only
            pytest.skip("factory workflows not available")
        assert _detect_conversational_signals(wf)


# ---------------------------------------------------------------------------
# run() — refusal happens before the orchestration entry point
# ---------------------------------------------------------------------------

@pytest.fixture
def gen_args(tmp_path: Path) -> Namespace:
    return Namespace(
        mode="workflow",
        prompt="Build a customer support triage workflow with escalation rules",
        output=str(tmp_path / "generated"),
        validation_strategy="skip",
        allow_interactive=False,
    )


@pytest.fixture
def staged_recorder(monkeypatch, tmp_path: Path):
    """Stub out staging, env setup, logging, and the API-key check.

    ``holder['graph']`` selects the staged workflow's transition graph;
    ``holder['ran']`` records whether the orchestration runner was reached.
    """
    holder: dict = {"graph": _INTERVIEW_GRAPH, "orchestrator": "workflow_name: X\n", "ran": False}

    def fake_stage(source_dir, staging_root, workflow_name):
        return _write_workflow(
            Path(staging_root) / workflow_name, holder["orchestrator"], holder["graph"]
        )

    async def fake_run_generator(**kwargs):
        holder["ran"] = True
        out = Path(kwargs["output_dir"])
        out.mkdir(parents=True, exist_ok=True)
        (out / "orchestrator.yaml").write_text("workflow_name: Y\n", encoding="utf-8")
        return {"success": True, "result": {"run_completed": True, "agent_turns": 1}}

    monkeypatch.setattr(gen_module, "_stage_workflow", fake_stage)
    monkeypatch.setattr(gen_module, "_run_generator", fake_run_generator)
    monkeypatch.setattr(gen_module, "_find_generator_source", lambda: tmp_path / "src")
    monkeypatch.setattr(gen_module, "_check_api_key", lambda: True)
    monkeypatch.setattr(gen_module, "_init_logging", lambda: None)
    monkeypatch.setattr(gen_module, "_setup_environment", lambda *a, **k: None)
    monkeypatch.setattr(gen_module, "RICH_AVAILABLE", False)
    monkeypatch.setattr(gen_module, "console", None)
    return holder


def test_conversational_workflow_refuses_without_calling_the_runner(
    gen_args, staged_recorder, capsys
) -> None:
    rc = gen_module.run(gen_args)

    assert rc == 1
    # The token-spend guarantee: orchestration was never entered.
    assert staged_recorder["ran"] is False
    out = capsys.readouterr().out
    assert "conversational workflow" in out


def test_refusal_message_names_studio_and_the_escape_hatch(
    gen_args, staged_recorder, capsys
) -> None:
    gen_module.run(gen_args)
    out = capsys.readouterr().out

    assert STUDIO_COMMAND in out
    assert "Studio" in out
    assert ALLOW_INTERACTIVE_FLAG in out
    # The refusal explains itself with the config signal it actually found.
    assert "hands control to the user" in out


def test_allow_interactive_bypasses_the_refusal_and_reaches_execution(
    gen_args, staged_recorder
) -> None:
    gen_args.allow_interactive = True

    rc = gen_module.run(gen_args)

    assert staged_recorder["ran"] is True
    assert rc == 0


def test_one_shot_workflow_is_unaffected_and_proceeds(gen_args, staged_recorder) -> None:
    staged_recorder["graph"] = _ONE_SHOT_GRAPH

    rc = gen_module.run(gen_args)

    assert staged_recorder["ran"] is True
    assert rc == 0


def test_missing_allow_interactive_attribute_defaults_to_refusing(
    gen_args, staged_recorder
) -> None:
    """Callers that never set the flag still get the safe behavior."""
    del gen_args.allow_interactive

    assert gen_module.run(gen_args) == 1
    assert staged_recorder["ran"] is False
