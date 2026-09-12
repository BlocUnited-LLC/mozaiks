from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml
from ag2 import Agent

from factory_app.workflows.ValueEngine.tools import manifest as module
from mozaiksai.core.adapters import ag2_network_runner as runner
from mozaiksai.core.ports.orchestration import RunStatus
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge, _wrap_tool_with_context
from mozaiksai.core.workflow.context.authority import build_context_authority_policy
from mozaiksai.core.workflow.contract_validation import validate_workflow_tool_outcomes
from mozaiksai.core.workflow.declarative.contracts import ToolOutcomeSpec
from mozaiksai.core.workflow.validation.tool_outcomes import wrap_tool_outcome


class Context(dict):
    def set(self, key, value):
        self[key] = value


@pytest.fixture
def review(monkeypatch):
    store = SimpleNamespace(save_concept=AsyncMock(), finish_concept_review=AsyncMock(return_value=True))
    persist = AsyncMock(return_value=SimpleNamespace(id="concept-version"))
    monkeypatch.setattr(module, "BuilderArtifactStore", lambda: store)
    monkeypatch.setattr(module, "persist_summary_artifact", persist)
    context = Context(
        app_id="build-app", chat_id="chat-review", user_id="owner", workflow_name="ValueEngine",
        concept_review_outcome="blocked", concept_review_attempts=0,
        concept_presented=False, interview_complete=True,
        structured_output={"app_name": "Customer Ledger", "concept_overview": "A private customer tracker.",
                           "core_features": ["List, add, and edit customers"], "deferred_features": []},
    )
    emitted = []

    def respond(*, action="approve", approved=True, invalid=None):
        async def ui(tool_id, payload, **kwargs):
            emitted.append((tool_id, deepcopy(payload), kwargs))
            assert store.save_concept.await_count == len(emitted)
            assert context.get("value_manifest", {}).get("status") != "approved"
            response = {"action": action, "approved": approved, "review_id": payload["review_id"],
                        "rationale": "Keep the scope small."}
            response.update(invalid or {})
            return response
        monkeypatch.setattr(module, "use_ui_tool", ui, raising=False)

    respond()
    return SimpleNamespace(context=context, store=store, persist=persist, emitted=emitted, respond=respond)


async def test_approval_is_structured_bound_to_draft_and_persisted(review):
    result = await module.save_value_manifest(review.context)
    assert result["outcome"] == "approved"
    assert result["success"] is True
    assert len(review.emitted) == 1
    tool_id, payload, kwargs = review.emitted[0]
    assert tool_id == "save_value_manifest"
    assert kwargs["display"] == "artifact"
    assert payload["blueprint"]["app_name"] == "Customer Ledger"
    finish = review.store.finish_concept_review.await_args.kwargs
    assert finish["review_id"] == payload["review_id"]
    assert finish["reviewed_by"] == "owner"
    assert finish["status"] == "approved"
    assert review.context["value_manifest"]["status"] == "approved"
    assert review.context["value_manifest"]["approved_scope"] == ["List, add, and edit customers"]
    assert review.persist.await_args.kwargs["summary_payload"]["status"] == "approved"
    assert review.context["app_id"] == "build-app"


@pytest.mark.parametrize("action,outcome", [("request_changes", "changes_requested"), ("cancel", "cancelled")])
async def test_negative_review_never_approves(review, action, outcome):
    review.respond(action=action, approved=False)
    result = await module.save_value_manifest(review.context)
    assert result["outcome"] == outcome
    assert review.context["value_manifest"]["status"] != "approved"
    assert review.context["concept_review_feedback"] == "Keep the scope small."


@pytest.mark.parametrize("invalid", [
    {"action": "looks good"}, {"approved": "true"}, {"approved": 1}, {"approved": False},
    {"review_id": "old-draft"}, {"action": "request_changes", "approved": True},
    {"rationale": {"approved": True}},
])
async def test_invalid_or_stale_response_cannot_advance(review, invalid):
    review.respond(invalid=invalid)
    result = await module.save_value_manifest(review.context)
    assert result["outcome"] == "blocked"
    review.store.finish_concept_review.assert_not_awaited()
    assert review.context.get("value_manifest", {}).get("status") != "approved"


async def test_a_newer_draft_prevents_approval_of_an_older_review(review):
    review.store.finish_concept_review.return_value = False
    result = await module.save_value_manifest(review.context)
    assert result["outcome"] == "blocked"
    assert review.context.get("value_manifest", {}).get("status") != "approved"


async def test_failed_draft_persistence_does_not_request_approval(review):
    review.store.save_concept.side_effect = RuntimeError("database unavailable")
    result = await module.save_value_manifest(review.context)
    assert result["outcome"] == "blocked"
    assert review.emitted == []


def _workflow_contracts():
    root = Path(__file__).resolve().parents[1] / "factory_app" / "workflows" / "ValueEngine"
    config = {name: yaml.safe_load((root / f"{name}.yaml").read_text(encoding="utf-8"))
              for name in ("tools", "context_variables", "transition_graph")}
    validate_workflow_tool_outcomes(config)
    review_tool = next(tool for tool in config["tools"]["tools"] if tool["function"] == "save_value_manifest")
    return config, ToolOutcomeSpec.model_validate(review_tool["outcome"])


@pytest.mark.parametrize("action,expected", [
    ("approve", RunStatus.COMPLETED), ("cancel", RunStatus.FAILED),
    ("request_changes", RunStatus.PAUSED), ("invalid", RunStatus.FAILED),
])
async def test_review_outcome_drives_native_ag2_graph_and_resumes(review, action, expected):
    config, contract = _workflow_contracts()
    policy = build_context_authority_policy(
        workflow_name="ValueEngine", definitions=config["context_variables"]["definitions"],
        transition_rules=config["transition_graph"]["transition_rules"],
    )
    bridge = ContextVariablesBridge(dict(review.context), authority_policy=policy)
    review.context = bridge
    review.respond(action=action, approved=action == "approve")

    class ScriptedAgent(Agent):
        async def ask(self, *args, **kwargs):
            return SimpleNamespace(body="Proposed concept, awaiting its structured review.")

    agents = {name: ScriptedAgent(name, prompt="Propose a concept.") for name in
              ("ValueInterviewAgent", "ResearchAgent", "GapAnalysisAgent")}
    for name, agent in agents.items():
        agent._mozaiks_context_bridge = bridge
        agent._mozaiks_tool_outcome = contract if name == "GapAnalysisAgent" else None
    invoke = _wrap_tool_with_context(wrap_tool_outcome(module.save_value_manifest, contract), bridge)

    async def before_packet(agent_name, packet):
        assert agent_name == "GapAnalysisAgent"
        await invoke()

    result = await runner.AG2NetworkRunner().run(runner.AG2NetworkRunnerRequest(
        workflow_name="ValueEngine", app_id="build-app", chat_id="chat-review", agents=agents,
        transition_rules=config["transition_graph"]["transition_rules"], initial_agent_name="GapAnalysisAgent",
        initial_message="Propose a customer tracker.", context_variables=bridge.snapshot(),
        context_authority_policy=policy, max_turns=6, close_timeout_seconds=float("inf"),
        agent_output_handler=before_packet,
    ))
    live_run = result.live_run
    try:
        assert result.status is expected, result.error
        if action == "request_changes":
            review.respond()
            result = await live_run.continue_with_user_message("Use the feedback to revise the concept.")
            assert result.status is RunStatus.COMPLETED, result.error
            assert result.context_variables["concept_review_attempts"] == 2
            assert len(review.emitted) == 2
            assert review.emitted[0][1]["review_id"] != review.emitted[1][1]["review_id"]
        assert result.context_variables["app_id"] == "build-app"
        if result.status is RunStatus.COMPLETED:
            assert result.context_variables["value_manifest"]["status"] == "approved"
            assert result.context_variables["concept_review_feedback"] == "Keep the scope small."
    finally:
        if live_run is not None:
            await live_run.close()


async def test_review_attempt_budget_fails_closed_without_another_prompt(review):
    _, contract = _workflow_contracts()
    review.context.update(concept_review_attempts=contract.max_attempts, concept_review_outcome="changes_requested")
    result = await wrap_tool_outcome(module.save_value_manifest, contract)(review.context)
    assert result["outcome"] == "blocked"
    assert result["outcome_error"] == "attempts_exhausted"
    assert review.emitted == []


async def test_failed_approved_summary_persistence_does_not_advance(review):
    review.persist.side_effect = [SimpleNamespace(id="draft-version"), RuntimeError("summary unavailable")]
    result = await module.save_value_manifest(review.context)
    assert result["outcome"] == "blocked"
    assert review.context.get("value_manifest", {}).get("status") != "approved"


@pytest.mark.parametrize("key", ["app_id", "chat_id", "user_id", "structured_output"])
async def test_missing_required_context_blocks(review, key):
    review.context.pop(key)
    result = await module.save_value_manifest(review.context)
    assert result["outcome"] == "blocked"
    assert review.emitted == []
