"""Exercise the real DesignDocs automatic selector before design review."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import yaml

from factory_app.workflows.DesignDocs.tools import save_design_doc
from mozaiksai.core.events import auto_tool_handler
from mozaiksai.core.events.runtime_events import (
    build_runtime_agent_output_validated_event,
    build_turn_idempotency_key,
)
from mozaiksai.core.session.build_context import load_trusted_build_context
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.authority import build_context_authority_policy
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.generator_support.module_role_grants import verified_pack_authorization
from mozaiksai.core.workflow.outputs import structured
from mozaiksai.core.workflow.workflow_manager import workflow_manager
from tests import test_design_access_policy as access_tests
from tests import test_designdocs_capability_ownership as ownership
from tests import test_designdocs_monetization_inventory as inventory
from tests.test_designdocs_selected_pack_context import _prompt_runner

persistence = inventory.persistence

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / "factory_app/workflows/DesignDocs"


@pytest.fixture
def selector(monkeypatch, tmp_path):
    monkeypatch.setenv("MOZAIKS_FACTORY_APP_PATH", str(ROOT / "factory_app"))
    monkeypatch.setenv("MOZAIKS_WORKFLOWS_PATH", str(ROOT / "factory_app/workflows"))
    monkeypatch.setenv("MOZAIKS_BUILD_CONTEXT_PATH", str(tmp_path))
    saved = dict(workflow_manager.__dict__)
    workflow_manager.workflows_base_path = ROOT / "factory_app/workflows"
    workflow_manager._workflows = {}
    workflow_manager._workflow_paths = {}
    workflow_manager._config_cache = {}
    structured.invalidate_all_workflow_structured_outputs()
    info = workflow_manager.reload_workflow("DesignDocs")
    assert not info.get("error"), info
    declarations = yaml.safe_load((WORKFLOW / "context_variables.yaml").read_text())
    policy = build_context_authority_policy(workflow_name="DesignDocs", definitions=declarations["definitions"])
    initial = load_trusted_build_context(policy, build_context_root=tmp_path)
    initial.update(workflow_name="DesignDocs", app_id="host", chat_id="chat", user_id="owner",
                   run_build_binding={"target_app_id": "target", "build_registry_id": "registry",
                                      "build_id": "genesis", "phase": "genesis"},
                   pack_selection_outcome="blocked", pack_selection_attempts=0)
    bridge = ContextVariablesBridge(initial, authority_policy=policy)
    bridge._bind_run(("DesignDocs", "host", "chat"), policy)
    handler = auto_tool_handler.AutoToolEventHandler()
    monkeypatch.setattr(handler, "_emit_tool_call", AsyncMock())
    monkeypatch.setattr(handler, "_emit_tool_result", AsyncMock())
    monkeypatch.setattr(handler, "_persist_context_variables", AsyncMock())
    yield handler, bridge
    workflow_manager.__dict__.clear()
    workflow_manager.__dict__.update(saved)
    structured.invalidate_all_workflow_structured_outputs()


async def dispatch(handler, bridge, payload, turn):
    await handler.handle_tool_dispatch(build_runtime_agent_output_validated_event(
        agent="DesignPackSelectionAgent", model_name="DesignPackSelection", structured_data=payload,
        auto_tool_call=True, context={"workflow_name": "DesignDocs", "chat_id": "chat", "app_id": "host"},
        turn_idempotency_key=build_turn_idempotency_key("chat", turn), pattern_context_ref=bridge,
        validation_passed=True,
    ))


@pytest.mark.asyncio
async def test_actual_auto_tool_pins_content_then_prompt_and_owner_gate(selector, monkeypatch):
    handler, bridge = selector
    assert bridge.get("pack_selection") is None
    await dispatch(handler, bridge, {"pack_ids": ["content"]}, 1)
    pins = detach(bridge.get("pack_selection"))
    assert [row["pack_id"] for row in pins] == ["content"]
    assert bridge.get("pack_selection_outcome") == "selected"
    prompt = await _prompt_runner(bridge)._run_prompt_middleware(SimpleNamespace(variables={}, prompt=[]))
    assert "content.list_entries" in prompt and "content.manage" in prompt
    assert "class ContentHandler" not in prompt
    authorization = verified_pack_authorization(bridge, selection=pins)
    shown = []
    async def approve(_tool, payload, **_kwargs):
        shown.append(payload)
        return {"action": "approve", "approved": True, "review_id": payload["review_id"], "rationale": "Reviewed."}
    monkeypatch.setattr(save_design_doc, "use_ui_tool", approve)
    policy = {"roles": [], "grants": []}
    receipt = await save_design_doc._review_access_policy(
        policy, experience_spec={"pages": []}, binding=SimpleNamespace(target_app_id="target", build_id="genesis"),
        context_variables=bridge, pack_authorization=authorization,
    )
    assert receipt["status"] == "approve" and receipt["reviewed_by"] == "owner"
    assert any("Selected pack content version" in line for line in shown[0]["checkpoints"])
    assert receipt["pack_identities"] == pins


@pytest.mark.asyncio
async def test_invalid_selection_clears_prior_candidate_and_retries_are_bounded(selector):
    handler, bridge = selector
    await dispatch(handler, bridge, {"pack_ids": ["content"]}, 1)
    for turn in (2, 3, 4):
        await dispatch(handler, bridge, {"pack_ids": ["missing-pack"]}, turn)
        assert bridge.get("pack_selection") is None
    assert bridge.get("pack_selection_outcome") == "blocked"


@pytest.mark.asyncio
async def test_two_selection_repairs_leave_one_valid_attempt(selector):
    handler, bridge = selector
    for turn in (1, 2):
        await dispatch(handler, bridge, {"pack_ids": ["missing-pack"]}, turn)
        assert bridge.get("pack_selection_outcome") == "revise"
        assert bridge.get("pack_selection") is None
    await dispatch(handler, bridge, {"pack_ids": ["content"]}, 3)
    assert bridge.get("pack_selection_outcome") == "selected"
    assert [row["pack_id"] for row in bridge.get("pack_selection")] == ["content"]
    assert bridge.get("pack_selection_attempts") == 2


@pytest.mark.asyncio
async def test_owner_reselection_preserves_design_feedback_and_separate_retry_budget(selector):
    from mozaiksai.core.workflow.agents.factory import _workflow_tool_invocation

    handler, bridge = selector
    await dispatch(handler, bridge, {"pack_ids": ["content"]}, 1)
    original = detach(bridge.get("pack_selection"))
    with _workflow_tool_invocation(bridge):
        bridge.set("design_docs_save_feedback", "Keep Content; change the editor layout.")
        bridge.set("design_docs_save_attempts", 2)
        bridge.set("design_docs_save_outcome", "review_changes")
    await dispatch(handler, bridge, {"pack_ids": ["content"]}, 2)
    assert detach(bridge.get("pack_selection")) == original
    assert bridge.get("pack_selection_attempts") == 0
    assert bridge.get("design_docs_save_attempts") == 2
    assert bridge.get("design_docs_save_feedback") == "Keep Content; change the editor layout."
    rules = yaml.safe_load((WORKFLOW / "transition_graph.yaml").read_text())["transition_rules"]
    routes = {(r["source_agent"], r.get("condition_key"), r.get("condition_value")): r["target_agent"]
              for r in rules if r["transition_type"] == "condition"}
    assert routes[("DesignDocsAgent", "design_docs_save_outcome", "review_changes")] == "DesignPackSelectionAgent"
    assert routes[("DesignDocsAgent", "design_docs_save_outcome", "revise")] == "DesignDocsAgent"
    assert routes[("DesignPackSelectionAgent", "pack_selection_outcome", "blocked")] == "terminate"


@pytest.mark.asyncio
async def test_owner_feedback_survives_a_rejected_reselection(selector):
    from mozaiksai.core.workflow.agents.factory import _workflow_tool_invocation

    handler, bridge = selector
    await dispatch(handler, bridge, {"pack_ids": ["content"]}, 1)
    with _workflow_tool_invocation(bridge):
        bridge.set("design_docs_save_feedback", "Keep Content; simplify the editor.")
        bridge.set("design_docs_save_outcome", "review_changes")
        bridge.set("design_docs_save_attempts", 2)
    await dispatch(handler, bridge, {"pack_ids": ["unknown"]}, 2)
    assert bridge.get("pack_selection") is None
    assert bridge.get("pack_selection_outcome") == "revise"
    assert bridge.get("pack_selection_feedback")
    await dispatch(handler, bridge, {"pack_ids": ["content"]}, 3)
    assert bridge.get("pack_selection_outcome") == "selected"
    assert bridge.get("design_docs_save_feedback") == "Keep Content; simplify the editor."
    assert bridge.get("design_docs_save_attempts") == 2


@pytest.mark.asyncio
async def test_explicit_empty_is_not_a_missing_selection(selector):
    handler, bridge = selector
    await dispatch(handler, bridge, {}, 1)
    assert bridge.get("pack_selection") is None
    await dispatch(handler, bridge, {"pack_ids": []}, 2)
    assert detach(bridge.get("pack_selection")) == []
    assert bridge.get("pack_selection_outcome") == "selected"


@pytest.mark.asyncio
async def test_factory_agent_requests_strict_selection_and_its_output_dispatches(selector, monkeypatch):
    """Real factory + AG2 HTTP request; provider and usage side effects are mocked."""
    from ag2 import MemoryStream

    from mozaiksai.core import observability
    from mozaiksai.core.workflow import llm_config
    from mozaiksai.core.workflow.agents import factory

    handler, bridge = selector
    requests = []
    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "test", "object": "chat.completion", "created": 1,
            "model": "test-model", "choices": [{"index": 0, "message": {"role": "assistant",
            "content": '{"pack_ids":["content"]}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})
    async def config(*_args, **_kwargs):
        return None, {"config_list": [{"model": "test-model", "api_type": "openai", "api_key": "test",
                                       "base_url": "https://provider.invalid/v1"}],
                      "streaming": False, "max_completion_tokens": 128, "max_retries": 0}
    monkeypatch.setattr(llm_config, "get_llm_config", config)
    monkeypatch.setattr(structured, "get_llm_for_workflow", config)
    monkeypatch.setattr(observability, "build_ag2_usage_middleware", lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("offline")))
    convert = factory.llm_config_to_ag2_config
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(factory, "llm_config_to_ag2_config", lambda value: convert(value).copy(http_client=client))
        agents = await factory.create_agents("DesignDocs", context_variables=bridge)
        agent = agents["DesignPackSelectionAgent"]
        assert agent._mozaiks_structured_model_name == "DesignPackSelection"
        reply = await agent.ask("Select Content for this editorial app.", stream=MemoryStream())
        result = await reply.content(retries=0)
        payload = result.model_dump() if hasattr(result, "model_dump") else json.loads(result)
        assert requests[0]["response_format"]["json_schema"]["strict"] is True
        assert requests[0]["response_format"]["json_schema"]["schema"]["required"] == ["pack_ids"]
        await dispatch(handler, bridge, payload, 1)
        assert [row["pack_id"] for row in bridge.get("pack_selection")] == ["content"]


@pytest.mark.parametrize("second_action", ["approve", "request_changes"])
def test_owner_changes_then_structural_refusal_still_requires_empty_policy_review(monkeypatch, persistence, second_action):
    context = ownership._context(managed=False)
    cards = []

    async def respond(_tool, payload, **_kwargs):
        cards.append(payload)
        action = "request_changes" if len(cards) == 1 else second_action
        return {"action": action, "approved": action == "approve", "review_id": payload["review_id"],
                "rationale": "Remove the grants and retain public pages."}

    monkeypatch.setattr(save_design_doc, "use_ui_tool", AsyncMock(side_effect=respond))
    assert inventory._save(context, access_tests._bundle())["outcome"] == "review_changes"
    assert context.get("design_docs_review_requested_changes") is True
    invalid = access_tests._bundle()
    invalid["access_policy"]["grants"][0]["actions"] = ["create_report"]
    assert inventory._save(context, invalid)["outcome"] == "revise"
    assert context.get("design_docs_review_requested_changes") is True
    assert len(cards) == 1
    context.set("pack_selection", [])
    result = inventory._save(context, inventory._bundle(pricing=False))
    assert len(cards) == 2 and cards[0]["review_id"] != cards[1]["review_id"]
    assert "The revised design selects no reusable packs, grants no role permissions and has no role-gated pages." in cards[1]["checkpoints"]
    if second_action == "request_changes":
        assert result["outcome"] == "review_changes", result
        assert context.get("design_docs_review_requested_changes") is True
        persistence[1].assert_not_called()
        persistence[2].assert_not_awaited()
        return
    assert result["outcome"] == "saved", result
    assert context.get("design_docs_review_requested_changes") is False
    summary = persistence[2].await_args.kwargs["summary_payload"]
    assert summary["access_policy_review"]["status"] == "approved"
    assert summary["access_policy_review"]["review_id"] == cards[1]["review_id"]
    assert summary["access_policy_review"]["pack_identities"] == []


def test_initial_empty_policy_still_needs_no_owner_review(monkeypatch, persistence):
    context = ownership._context(managed=False)
    ui = AsyncMock(side_effect=AssertionError("Initial empty policy should not require review"))
    monkeypatch.setattr(save_design_doc, "use_ui_tool", ui)
    assert inventory._save(context, inventory._bundle(pricing=False))["outcome"] == "saved"
    ui.assert_not_awaited()
    assert persistence[2].await_args.kwargs["summary_payload"]["access_policy_review"]["status"] == "not_required"


def test_owner_removing_content_pack_must_review_the_empty_replacement(selector, monkeypatch, persistence):
    import asyncio

    handler, selection_context = selector
    context = ownership._context(managed=False)
    for key, value in selection_context.snapshot().items():
        if key not in {"run_build_binding", "chat_id", "user_id", "app_id", "pack_selection"}:
            context.set(key, value)
    context.set("pack_selection_outcome", "blocked")
    context.set("pack_selection_attempts", 0)
    asyncio.run(dispatch(handler, context, {"pack_ids": ["content"]}, 1))
    assert [row["pack_id"] for row in context.get("pack_selection")] == ["content"]
    cards = []

    async def respond(_tool, payload, **_kwargs):
        cards.append(payload)
        action = "request_changes" if len(cards) == 1 else "approve"
        return {"action": action, "approved": action == "approve", "review_id": payload["review_id"],
                "rationale": "Remove Content and keep a public page."}

    monkeypatch.setattr(save_design_doc, "use_ui_tool", AsyncMock(side_effect=respond))
    result = inventory._save(context, inventory._bundle(pricing=False))
    assert result["outcome"] == "review_changes", result
    assert any("Selected pack content version" in item for item in cards[0]["checkpoints"])
    asyncio.run(dispatch(handler, context, {"pack_ids": []}, 2))
    assert detach(context.get("pack_selection")) == []
    result = inventory._save(context, inventory._bundle(pricing=False))
    assert result["outcome"] == "saved", result
    assert len(cards) == 2 and cards[0]["review_id"] != cards[1]["review_id"]
    assert any("selects no reusable packs" in item for item in cards[1]["checkpoints"])


def test_review_latch_is_owned_by_deterministic_tool():
    from mozaiksai.core.workflow.context.authority import (
        PERSISTED_REPLAY_WRITER,
        ContextAuthorityError,
    )

    definitions = yaml.safe_load((WORKFLOW / "context_variables.yaml").read_text())["definitions"]
    policy = build_context_authority_policy(workflow_name="DesignDocs", definitions=definitions)
    policy.require_can_write("design_docs_review_requested_changes", writer_id="deterministic_tool")
    for writer in ("agent_text", "ui_response"):
        with pytest.raises(ContextAuthorityError):
            policy.require_can_write("design_docs_review_requested_changes", writer_id=writer)
    assert policy.filter_for_replay({"design_docs_review_requested_changes": True}, writer_id=PERSISTED_REPLAY_WRITER) == {
        "design_docs_review_requested_changes": True,
    }
    with pytest.raises(ContextAuthorityError):
        policy.filter_for_replay({"design_docs_review_requested_changes": "true"}, writer_id=PERSISTED_REPLAY_WRITER)
