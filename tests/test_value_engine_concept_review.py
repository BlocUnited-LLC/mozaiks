from __future__ import annotations

import json
import logging
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml
from ag2 import Agent
from ag2.config.openai.mappers import convert_messages
from ag2.events import ModelMessage, ModelResponse
from ag2.knowledge import MemoryKnowledgeStore

from factory_app.workflows.ValueEngine.tools import manifest as module
from mozaiksai.core.adapters import ag2_network_runner as runner
from mozaiksai.core.events.unified_event_dispatcher import UnifiedEventDispatcher
from mozaiksai.core.ports.orchestration import RunStatus
from mozaiksai.core.workflow import llm_config
from mozaiksai.core.workflow.agents import factory
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge, _wrap_tool_with_context
from mozaiksai.core.workflow.context.authority import build_context_authority_policy
from mozaiksai.core.workflow.contract_validation import validate_workflow_tool_outcomes
from mozaiksai.core.workflow.declarative.contracts import ToolOutcomeSpec
from mozaiksai.core.workflow.orchestration_patterns import _dispatch_agent_packet_output
from mozaiksai.core.workflow.outputs import structured
from mozaiksai.core.workflow.validation.tool_outcomes import wrap_tool_outcome
from tests.factory_context import factory_context
from tests.test_factory_auto_tool_acceptance import (
    _payload_for,
    factory_manager,  # noqa: F401
)


class Context(dict):
    def __init__(self, *args, **kwargs):
        super().__init__(factory_context(dict(*args, **kwargs)))

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
        concept_presented=False, interview_outcome="ready",
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
    assert review.context["app_id"] == "factory-test"
    assert review.context["run_build_binding"]["target_app_id"] == "build-app"


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
    ("request_changes", RunStatus.COMPLETED), ("invalid", RunStatus.FAILED),
    ("blank_changes", RunStatus.FAILED), ("changes_then_cancel", RunStatus.FAILED),
    ("repeated_changes", RunStatus.FAILED), ("changes_then_stale", RunStatus.FAILED),
])
async def test_review_outcome_drives_native_ag2_graph_without_extra_chat(review, action, expected):
    config, contract = _workflow_contracts()
    policy = build_context_authority_policy(
        workflow_name="ValueEngine", definitions=config["context_variables"]["definitions"],
        transition_rules=config["transition_graph"]["transition_rules"],
    )
    bridge = ContextVariablesBridge(dict(review.context), authority_policy=policy)
    review.context = bridge
    asks = []

    class ScriptedAgent(Agent):
        async def ask(self, *args, **kwargs):
            asks.append(bridge.get("concept_review_feedback"))
            return SimpleNamespace(body="Proposed concept, awaiting its structured review.")

    agents = {name: ScriptedAgent(name, prompt="Propose a concept.") for name in
              ("ValueInterviewAgent", "ResearchAgent", "GapAnalysisAgent")}
    for name, agent in agents.items():
        agent._mozaiks_context_bridge = bridge
        agent._mozaiks_tool_outcome = contract if name == "GapAnalysisAgent" else None
    invoke = _wrap_tool_with_context(wrap_tool_outcome(module.save_value_manifest, contract), bridge)

    async def before_packet(agent_name, packet):
        assert agent_name == "GapAnalysisAgent"
        next_action = action
        invalid = None
        if action in {"request_changes", "changes_then_cancel", "changes_then_stale", "repeated_changes"}:
            next_action = "request_changes" if not review.emitted or action == "repeated_changes" else "approve"
            if review.emitted:
                assert bridge.get("concept_review_feedback") == "Keep the scope small."
                assert not bridge.get("value_manifest")["approved_scope"]
                if action == "changes_then_cancel":
                    next_action = "cancel"
                elif action == "changes_then_stale":
                    invalid = {"review_id": review.emitted[0][1]["review_id"]}
        elif action == "blank_changes":
            next_action = "request_changes"
            invalid = {"rationale": " \n\t "}
        review.respond(action=next_action, approved=next_action == "approve", invalid=invalid)
        await invoke()

    result = await runner.AG2NetworkRunner().run(runner.AG2NetworkRunnerRequest(
        workflow_name="ValueEngine", app_id="build-app", chat_id="chat-review", agents=agents,
        transition_rules=config["transition_graph"]["transition_rules"], initial_agent_name="GapAnalysisAgent",
        initial_message="Propose a customer tracker.", context_variables=bridge.snapshot(),
        context_authority_policy=policy, max_turns=6, idle_timeout_seconds=float("inf"),
        agent_output_handler=before_packet,
    ))
    live_run = result.live_run
    try:
        assert result.status is expected, result.error
        if action in {"request_changes", "changes_then_cancel", "changes_then_stale"}:
            assert result.context_variables["concept_review_attempts"] == 2
            assert len(review.emitted) == 2
            assert review.emitted[0][1]["review_id"] != review.emitted[1][1]["review_id"]
            assert asks[1] == "Keep the scope small."
        elif action == "repeated_changes":
            assert result.context_variables["concept_review_attempts"] == contract.max_attempts
            assert len(review.emitted) == contract.max_attempts
            assert result.context_variables["concept_review_outcome"] == "blocked"
        elif action == "blank_changes":
            review.store.finish_concept_review.assert_not_awaited()
            assert len(review.emitted) == 1
        assert result.context_variables["app_id"] == "factory-test"
        assert result.context_variables["run_build_binding"]["target_app_id"] == "build-app"
        if result.status is RunStatus.COMPLETED:
            assert result.context_variables["value_manifest"]["status"] == "approved"
            assert result.context_variables["concept_review_feedback"] == "Keep the scope small."
        else:
            assert result.context_variables["value_manifest"]["status"] != "approved"
            assert result.context_variables["value_manifest"]["approved_scope"] == []
    finally:
        if live_run is not None:
            await live_run.close()


async def test_native_revision_uses_feedback_and_current_model_output(request, monkeypatch):
    """Real Factory/AG2/dispatcher/tools; scripted provider and fake persistence/UI."""
    manager = request.getfixturevalue("factory_manager")
    assert not manager.reload_workflow("ValueEngine").get("error")
    _, registry = structured.load_workflow_structured_outputs("ValueEngine")
    draft = _payload_for(registry["GapAnalysisAgent"])
    draft.update(app_name="Customer Ledger", concept_overview="A customer tracker.",
                 core_features=["Save customers"], value_proposition="Track customers.")
    revised = {**draft, "value_proposition": "Track private customers after signing in."}
    responses = [draft, revised]
    feedback = "Make the benefit explicitly describe private customers and sign-in."
    prompts, emitted, validated_events = [], [], []

    class ScriptedConfig:
        model = "gpt-4.1"
        provider = "openai"

        def copy(self):
            return self

        def create(self):
            async def client(events, context, *, tools, response_schema, serializer):
                assert response_schema is not None
                assert len(prompts) < len(responses), "Unexpected additional model call"
                payload = responses[len(prompts)]
                messages = convert_messages(context.prompt, events, serializer)
                prompts.append("\n".join(str(row.get("content")) for row in messages if row.get("role") == "system"))
                return ModelResponse(ModelMessage(json.dumps(payload)))
            return client

    config = AsyncMock(return_value=(None, {"config_list": [{
        "model": "gpt-4.1", "api_type": "openai", "api_key": "offline-placeholder",
    }]}))
    monkeypatch.setattr(llm_config, "get_llm_config", config)
    monkeypatch.setattr(structured, "get_llm_for_workflow", config)
    monkeypatch.setattr(factory, "llm_config_to_ag2_config", lambda _: ScriptedConfig())
    store = SimpleNamespace(save_concept=AsyncMock(), finish_concept_review=AsyncMock(return_value=True))

    async def ui(tool_id, payload, **kwargs):
        emitted.append(deepcopy(payload))
        assert store.save_concept.await_count == len(emitted)
        approved = len(emitted) == 2
        return {"action": "approve" if approved else "request_changes", "approved": approved,
                "review_id": payload["review_id"], "rationale": feedback if not approved else ""}

    # Patch dependency boundaries before the canonical loader imports tool code.
    monkeypatch.setattr("mozaiksai.core.data.persistence.artifact_store.BuilderArtifactStore", lambda: store)
    monkeypatch.setattr("mozaiksai.core.artifacts.persist_summary_artifact", AsyncMock())
    monkeypatch.setattr("mozaiksai.core.workflow.ui_tools.use_ui_tool", ui)
    monkeypatch.setattr("mozaiksai.core.events.auto_tool_handler.AG2PersistenceManager",
                        lambda: SimpleNamespace(persist_context_variables=AsyncMock()))
    monkeypatch.setattr("mozaiksai.core.events.auto_tool_handler._get_simple_transport", AsyncMock(return_value=None))
    dispatcher = UnifiedEventDispatcher()
    dispatcher.register_runtime_handler("runtime.agent_output_validated", lambda event: validated_events.append({
        "turn_key": event["turn_idempotency_key"], "body": deepcopy(event["structured_data"]),
    }))
    monkeypatch.setattr("mozaiksai.core.events.unified_event_dispatcher.get_event_dispatcher", lambda: dispatcher)
    context = factory_context({
        "app_id": "revision-app", "chat_id": "revision-chat", "user_id": "owner",
        "workflow_name": "ValueEngine", "concept_review_feedback": None,
        "concept_review_outcome": None, "concept_review_attempts": 0,
    })
    agents = await factory.create_agents("ValueEngine", context_variables=context)
    bridge = agents["GapAnalysisAgent"]._mozaiks_context_bridge

    async def before_packet(agent_name, packet):
        await _dispatch_agent_packet_output(
            agent_name=agent_name, packet=packet, workflow_name="ValueEngine",
            chat_id="revision-chat", app_id="revision-app", user_id="owner",
            context_bridge=bridge, structured_registry=registry, auto_tool_agents={"GapAnalysisAgent"},
            wf_logger=logging.getLogger(__name__),
        )

    result = await runner.AG2NetworkRunner().run(runner.AG2NetworkRunnerRequest(
        workflow_name="ValueEngine", app_id="revision-app", chat_id="revision-chat", agents=agents,
        initial_agent_name="GapAnalysisAgent", initial_message="Propose a private customer tracker.",
        transition_rules=manager.get_config("ValueEngine")["transition_graph"]["transition_rules"],
        context_variables=bridge.snapshot(), knowledge_store=MemoryKnowledgeStore(),
        agent_output_handler=before_packet, max_turns=3, idle_timeout_seconds=10,
    ))
    try:
        assert result.status is RunStatus.COMPLETED, result.error
        assert len(prompts) == 2 and feedback not in prompts[0] and feedback in prompts[1]
        assert [event["body"] for event in validated_events] == responses
        assert len({event["turn_key"] for event in validated_events}) == 2
        assert [call.kwargs["concept_record"]["Blueprint"] for call in store.save_concept.await_args_list] == responses
        assert [payload["blueprint"]["value_proposition"] for payload in emitted] == [
            draft["value_proposition"], revised["value_proposition"],
        ]
        assert len({payload["review_id"] for payload in emitted}) == 2
        assert [call.kwargs["status"] for call in store.finish_concept_review.await_args_list] == [
            "changes_requested", "approved",
        ]
        assert result.context_variables["concept_review_attempts"] == 2
        assert result.context_variables["value_manifest"]["value_proposition"] == revised["value_proposition"]
        assert result.context_variables["value_manifest"]["status"] == "approved"
    finally:
        if result.live_run is not None:
            await result.live_run.close()


@pytest.mark.parametrize("rationale", ["", " ", "\n\t"])
async def test_blank_change_request_cannot_finish_review(review, rationale):
    review.respond(action="request_changes", approved=False, invalid={"rationale": rationale})
    result = await module.save_value_manifest(review.context)
    assert result["outcome"] == "blocked"
    review.store.finish_concept_review.assert_not_awaited()


@pytest.mark.parametrize("action", ["approve", "cancel"])
async def test_approval_and_cancel_do_not_require_a_note(review, action):
    review.respond(action=action, approved=action == "approve", invalid={"rationale": ""})
    result = await module.save_value_manifest(review.context)
    assert result["outcome"] == {"approve": "approved", "cancel": "cancelled"}[action]


async def test_change_feedback_is_trimmed_before_persistence_and_agent_context(review):
    review.respond(action="request_changes", approved=False, invalid={"rationale": "  Keep sign-in. \n"})
    result = await module.save_value_manifest(review.context)
    assert result["outcome"] == "changes_requested"
    assert review.store.finish_concept_review.await_args.kwargs["feedback"] == "Keep sign-in."
    assert review.context["concept_review_feedback"] == "Keep sign-in."


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


@pytest.mark.parametrize("key", ["chat_id", "user_id", "structured_output"])
async def test_missing_required_context_blocks(review, key):
    review.context.pop(key)
    result = await module.save_value_manifest(review.context)
    assert result["outcome"] == "blocked"
    assert review.emitted == []


async def test_missing_server_binding_cannot_save_or_request_review(review):
    from pydantic import ValidationError

    review.context.pop("run_build_binding")
    with pytest.raises(ValidationError):
        await module.save_value_manifest(review.context)
    review.store.save_concept.assert_not_awaited()
    assert review.emitted == []
