"""A workflow tool can provide an exact native chat reply without a model call."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from ag2.knowledge import MemoryKnowledgeStore

from mozaiksai.core.adapters.ag2_network_runner import AG2NetworkRunner, AG2NetworkRunnerRequest
from mozaiksai.core.ports.orchestration import RunStatus
from mozaiksai.core.workflow.agents import factory
from mozaiksai.core.workflow.agents.reply_tool import create_reply_tool_agent
from mozaiksai.core.workflow.context.adapter import create_context_container
from mozaiksai.core.workflow.context.authority import build_context_authority_policy
from mozaiksai.core.workflow.contract_validation import validate_workflow_context_contract
from mozaiksai.core.workflow.declarative.contracts import AgentSpec
from mozaiksai.core.workflow.orchestration_patterns import _project_ag2_wal_to_mozaiks_transport


def config():
    return {
        "agents": {"Receipt": {"name": "Receipt", "reply_tool": "render_receipt", "structured_outputs_required": False}},
        "tools": [{"agent": "Receipt", "function": "render_receipt", "file": "tools/receipt.py", "tool_type": "Agent_Tool"}],
        "structured_outputs": {"registry": {"Receipt": None}},
        "transition_graph": {"transition_rules": [
            {"source_agent": "Receipt", "target_agent": "user", "transition_type": "after_turn"},
            {"source_agent": "user", "target_agent": "Receipt", "transition_type": "after_turn"},
        ]},
    }


@pytest.mark.asyncio
async def test_scoped_exact_text_reaches_wal_transport_and_replay_and_accepts_followup(monkeypatch):
    cfg = config()
    calls = []

    async def render_receipt(context_variables):
        identity = factory.active_workflow_tool_run()
        calls.append(identity)
        return f"**Quotes** · turn {len(calls)}\n\n| read | 3 |"

    def forbidden(*args, **kwargs):
        pytest.fail("A reply-only workflow attempted model configuration")

    monkeypatch.setattr(factory.workflow_manager, "get_config", lambda workflow: cfg)
    monkeypatch.setattr(factory.workflow_manager, "get_auto_tool_agents", lambda workflow: set())
    monkeypatch.setattr(factory, "get_structured_outputs_for_workflow", lambda workflow: {})
    monkeypatch.setattr("mozaiksai.core.workflow.agents.tools.load_agent_tool_functions",
                        lambda workflow: {"Receipt": [render_receipt]})
    monkeypatch.setattr("mozaiksai.core.workflow.llm_config.get_llm_config", forbidden)
    monkeypatch.setattr("mozaiksai.core.workflow.llm_config.resolve_workflow_model", forbidden)
    monkeypatch.setattr(factory, "llm_config_to_ag2_config", forbidden)
    request_context = {"user_id": "test-owner"}
    policy = build_context_authority_policy(workflow_name="ReceiptWorkflow", definitions={})
    context = create_context_container(request_context, authority_policy=policy)
    agents = await factory.create_agents("ReceiptWorkflow", context, workflow_model="any-owner-preference")
    request = AG2NetworkRunnerRequest(
        workflow_name="ReceiptWorkflow", chat_id="test-chat", app_id="test-app", agents=agents,
        initial_agent_name="Receipt", initial_message="Show receipt.", transition_rules=cfg["transition_graph"]["transition_rules"],
        context_variables=request_context, context_authority_policy=policy,
        knowledge_store=MemoryKnowledgeStore(), idle_timeout_seconds=5,
    )
    first = await AG2NetworkRunner().run(request)
    assert first.status is RunStatus.PAUSED, first.error
    assert first.live_run is not None
    second = await first.live_run.continue_with_user_message("Show it again.")
    assert second.status is RunStatus.PAUSED, second.error
    for turn, result in enumerate([first, second], 1):
        text = f"**Quotes** · turn {turn}\n\n| read | 3 |"
        assert [e["event_data"]["body"] for e in result.wal if e["event_type"] == "ag2.packet"] == [text]
        transport, recorder = AsyncMock(), AsyncMock()
        await _project_ag2_wal_to_mozaiks_transport(
            runner_result=result, transport=transport, persistence_manager=recorder, chat_id="test-chat",
            app_id="test-app", agent_name_by_id=result.agent_name_by_id, initial_sequence=0)
        assert transport.send_event_to_ui.await_args.args[0]["content"] == text
        assert recorder.append_run_assistant_message.await_args.kwargs["content"] == text
    assert calls == [("ReceiptWorkflow", "test-app", "test-chat", "test-owner")] * 2


@pytest.mark.asyncio
@pytest.mark.parametrize("returned", ["", {"untrusted": "not text"}, ValueError("private diagnostic")])
async def test_tool_failure_aborts_once_without_exposing_exception_or_text(returned):
    calls = []
    def render_receipt():
        calls.append(True)
        if isinstance(returned, Exception):
            raise returned
        return returned

    agent = create_reply_tool_agent("Receipt", "render_receipt", [render_receipt])
    result = await AG2NetworkRunner().run(AG2NetworkRunnerRequest(
        workflow_name="ReceiptWorkflow", chat_id="test-chat", app_id="test-app", agents={"Receipt": agent},
        initial_agent_name="Receipt", initial_message="Show receipt.", transition_rules=config()["transition_graph"]["transition_rules"],
        knowledge_store=MemoryKnowledgeStore(), idle_timeout_seconds=5))
    assert result.status is RunStatus.FAILED
    assert "could not produce a reply" in result.error
    assert "private diagnostic" not in result.error
    assert calls == [True]
    assert not [e for e in result.wal if e["event_type"] == "ag2.packet"]


@pytest.mark.parametrize("invalid", ["extra", "unbound", "auto", "wrong_name", "ui", "remote", "structured"])
def test_reply_tool_resolves_one_bound_local_text_tool(invalid):
    cfg = config()
    validate_workflow_context_contract(workflow_name="ReceiptWorkflow", workflow_config=cfg)
    tool = cfg["tools"][0]
    if invalid == "extra":
        cfg["tools"].append({**tool, "function": "other"})
    elif invalid == "remote":
        cfg["a2a"] = {"agents": [{"name": "Receipt", "enabled": True}]}
    elif invalid == "structured":
        cfg["structured_outputs"]["registry"]["Receipt"] = "Other"
    else:
        tool.update({"unbound": {"bind_to_agent": False}, "auto": {"auto_tool_call": True},
                     "wrong_name": {"function": "other"}, "ui": {"tool_type": "UI_Surface"}}[invalid])
    with pytest.raises(ValueError, match="reply_tool"):
        validate_workflow_context_contract(workflow_name="ReceiptWorkflow", workflow_config=cfg)


def test_reply_tool_rejects_model_arguments_and_unavailable_implementation():
    def render_receipt(owner):
        return owner
    with pytest.raises(ValueError, match="reply_tool"):
        create_reply_tool_agent("Receipt", "render_receipt", [render_receipt])
    with pytest.raises(ValueError, match="reply_tool"):
        create_reply_tool_agent("Receipt", "render_receipt", [])
    for extra in ({"web_search": True}, {"system_message": "Unapplied"}, {"structured_outputs_required": True}):
        with pytest.raises(ValueError, match="reply_tool"):
            AgentSpec.model_validate({**config()["agents"]["Receipt"], **extra})


@pytest.mark.parametrize("argument, valid", [("context_variables", True), ("owner", False)])
def test_generated_workflow_accepts_only_context_bound_reply_tool_sources(argument, valid):
    import yaml

    from factory_app.workflows.AgentGenerator.tools.outcome_materialization import (
        materialize_workflow_outcomes,
    )
    from factory_app.workflows.AgentGenerator.tools.workflow_quality_gate import (
        validate_workflow_bundle_structure,
    )
    from tests.test_workflow_tool_outcomes import _entry

    entry = _entry()
    for file in entry["files"]:
        if file["filename"] == "agents.yaml":
            doc = yaml.safe_load(file["content"])
            receipt = next(agent for agent in doc["agents"] if agent["name"] == "DoneAgent")
            receipt.pop("system_message")
            receipt["reply_tool"] = "render_receipt"
            file["content"] = yaml.safe_dump(doc)
        elif file["filename"] == "tools.yaml":
            doc = yaml.safe_load(file["content"])
            doc["tools"].append({"agent": "DoneAgent", "file": "tools/receipt.py",
                                 "function": "render_receipt", "tool_type": "Agent_Tool"})
            file["content"] = yaml.safe_dump(doc)
    entry["files"].append({"filename": "tools/receipt.py", "content":
                           f"def render_receipt({argument}):\n    return 'Received'\n"})
    report = validate_workflow_bundle_structure(bundle_entries=[materialize_workflow_outcomes(entry)])
    assert report["valid"] is valid, report["errors"]
    if not valid:
        assert any("reply_tool" in error and "context_variables" in error for error in report["errors"])
