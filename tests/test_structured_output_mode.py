"""AG2 owns schema transport; the canonical model still owns acceptance."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from ag2 import Agent
from ag2.response import PromptedSchema
from pydantic import ValidationError

from mozaiksai.core.adapters.ag2_agent_runner import AG2StructuredAgentRunner
from mozaiksai.core.adapters.llm_fallback import response_schema_to_ag2
from mozaiksai.core.usage.context import AuxiliaryUsageContext
from mozaiksai.core.workflow.agents.factory import _prepare_response_schema_for_agent
from mozaiksai.core.workflow.execution.middleware import build_prompt_middleware
from mozaiksai.core.workflow.orchestration_patterns import _dispatch_agent_packet_output
from mozaiksai.core.workflow.outputs.structured import get_structured_outputs_for_workflow

_SAMPLE = {
    "agent_message": "No subscription contract is required.", "contract_required": False,
    "rationale": "This isolated transport test has no paid features.",
    "plan_design_rationale": [], "app_id": None, "app_name": "Schema transport test",
    "subscription_config_file": None, "metering_declarations": [], "page_surface_requirements": [],
    "app_generator_instructions": [], "validation_notes": [], "forbidden_outputs": [],
}


@pytest.fixture
def contract(monkeypatch):
    monkeypatch.delenv("LLM_STRUCTURED_OUTPUT_MODE", raising=False)
    monkeypatch.setenv("MOZAIKS_WORKFLOWS_PATH", str(Path(__file__).resolve().parents[1] / "factory_app/workflows"))
    return get_structured_outputs_for_workflow("SubscriptionContractDesigner")["ContractDesignerAgent"]


def _factory_schema(contract, mode=None):
    config = {"config_list": [{"api_type": "anthropic", "model": "test-model"}]}
    if mode is not None:
        config["structured_output_mode"] = mode
    return _prepare_response_schema_for_agent(
        workflow_name="SubscriptionContractDesigner", agent_name="ContractDesignerAgent",
        agent_config={"structured_outputs_required": True}, structured_model_cls=contract,
        llm_config_dict=config,
    )


def test_native_default_keeps_canonical_anthropic_model(contract):
    assert _factory_schema(contract) is contract
    assert response_schema_to_ag2(contract, {}) is contract


@pytest.mark.asyncio
async def test_prompted_preserves_exact_model_and_rejects_extra_fields(contract):
    schema = _factory_schema(contract, "prompted")
    assert isinstance(schema, PromptedSchema)
    assert schema.json_schema is None
    result = await schema.validate(json.dumps(_SAMPLE), context=None)
    assert type(result) is contract
    assert result.model_dump() == _SAMPLE
    with pytest.raises(ValidationError):
        await schema.validate(json.dumps({**_SAMPLE, "undeclared": True}), context=None)


@pytest.mark.parametrize("mode", [None, "", "automatic", False])
def test_factory_rejects_invalid_explicit_mode(contract, mode):
    with pytest.raises(ValueError, match="structured_output_mode"):
        _prepare_response_schema_for_agent(
            workflow_name="SubscriptionContractDesigner", agent_name="ContractDesignerAgent",
            agent_config={"structured_outputs_required": True}, structured_model_cls=contract,
            llm_config_dict={"structured_output_mode": mode},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["factory", "auxiliary"])
async def test_actual_anthropic_prompted_request_preserves_projection_and_parse_gate(contract, monkeypatch, entrypoint):
    pytest.importorskip("anthropic")
    httpx2 = pytest.importorskip("httpx2")
    from ag2.config import AnthropicConfig

    requests = []
    responses = [json.dumps(_SAMPLE), json.dumps({**_SAMPLE, "contract_required": "invalid"})]

    def respond(request):
        assert request.url.host == "provider.invalid"
        requests.append(json.loads(request.content))
        return httpx2.Response(200, json={
            "id": "test-response", "type": "message", "role": "assistant", "model": "test-model",
            "content": [{"type": "text", "text": responses[len(requests) - 1]}],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        })

    prompt_middleware = build_prompt_middleware(
        middleware_functions=[], agent_name="ContractDesignerAgent",
        base_system_message="Return the requested sample JSON.",
        context_bridge={"proof_marker": "projected-context-survives"}, context_variables=["proof_marker"],
    )
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        config = AnthropicConfig("test-model", api_key="test-only", base_url="https://provider.invalid",
                                 max_tokens=2000, max_retries=0, http_client=http)
        if entrypoint == "factory":
            agent = Agent("ContractDesignerAgent", config=config,
                          response_schema=_factory_schema(contract, "prompted"), middleware=[prompt_middleware])
            good = await agent.ask("Return the sample.")
            result = await good.content()
            bad = await agent.ask("Return the sample.")
            with pytest.raises(ValidationError):
                await bad.content()
            invalid_body = bad.body
        else:
            def factory(prompt, llm_config, *, middleware):
                assert llm_config == {"structured_output_mode": "prompted"}
                return Agent("ContractDesignerAgent", prompt, config=config, middleware=[prompt_middleware])

            runner = AG2StructuredAgentRunner(agent_factory=factory)
            args = dict(agent_name="ContractDesignerAgent", system_prompt="Return JSON", user_prompt="Return sample",
                        llm_config={"structured_output_mode": "prompted"}, response_schema=contract,
                        usage_context=AuxiliaryUsageContext(app_id="test-app", user_id="test-user"),
                        retry_count=0, schema_validation_retries=0)
            result = await runner.run(**args)
            with pytest.raises(ValidationError):
                await runner.run(**args)
            invalid_body = responses[1]

    assert type(result) is contract
    assert result.model_dump() == _SAMPLE
    assert len(requests) == 2
    for request in requests:
        assert "output_config" not in request
        system = json.dumps(request["system"])
        assert "projected-context-survives" in system
        assert "You must respond with valid JSON" in system
        assert "contract_required" in system

    dispatcher = AsyncMock()
    monkeypatch.setattr("mozaiksai.core.events.unified_event_dispatcher.get_event_dispatcher", lambda: dispatcher)
    with pytest.raises(ValueError, match="structured output validation failed"):
        await _dispatch_agent_packet_output(
            agent_name="ContractDesignerAgent",
            packet=SimpleNamespace(causation_id="input-1", channel_id="channel-1", event_data={"body": invalid_body}),
            workflow_name="SubscriptionContractDesigner", chat_id="test-chat", app_id="test-app", user_id="test-user",
            context_bridge=None, structured_registry={"ContractDesignerAgent": contract},
            auto_tool_agents={"ContractDesignerAgent"}, wf_logger=logging.getLogger(__name__),
        )
    dispatcher.emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_auxiliary_invalid_mode_fails_before_agent_creation(contract):
    def forbidden_factory(*args, **kwargs):
        raise AssertionError("Invalid mode must fail before agent creation")

    with pytest.raises(ValueError, match="structured_output_mode"):
        await AG2StructuredAgentRunner(agent_factory=forbidden_factory).run(
            agent_name="Test", system_prompt="system", user_prompt="user",
            llm_config={"structured_output_mode": "automatic"}, response_schema=contract,
            usage_context=AuxiliaryUsageContext(app_id="test-app", user_id="test-user"),
        )
