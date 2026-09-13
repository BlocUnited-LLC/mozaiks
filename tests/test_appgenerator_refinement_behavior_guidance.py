"""Prompt exposure and guidance regressions, not generated-code semantic tests."""

from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.hook_file_contract_context import (
    _build_file_contracts_body,
)
from mozaiksai.core.runtime.persistence.adapter import PersistenceCollection
from mozaiksai.core.workflow.context.schema import load_context_variables_config

ROOT = Path(__file__).resolve().parents[1]
APPGEN = ROOT / "factory_app/workflows/AppGenerator"
WORKERS = ("AppSchemaAgent", "ServiceAgent", "ConfigMiddlewareAgent", "ModelAgent")


@pytest.fixture(scope="module")
def contracts():
    agents = yaml.safe_load((APPGEN / "agents.yaml").read_text(encoding="utf-8"))
    context = load_context_variables_config(
        yaml.safe_load((APPGEN / "context_variables.yaml").read_text(encoding="utf-8")),
    )
    files = yaml.safe_load(
        (ROOT / "factory_app/build_context/AppGenerator/file_contracts.yaml").read_text(encoding="utf-8"),
    )
    prompts = {
        agent["name"]: "\n\n".join(
            f"{section.get('heading', '')}\n{section.get('content', '')}"
            for section in agent["prompt_sections"]
        )
        for agent in agents["agents"]
    }
    return SimpleNamespace(prompts=prompts, context=context, files=files)


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_name", WORKERS)
async def test_revision_worker_projects_original_request_and_current_scope_without_stale_text(contracts, agent_name):
    from ag2 import Context, MemoryStream
    from ag2.events import ModelRequest, ModelResponse

    from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
    from mozaiksai.core.workflow.execution.middleware import build_prompt_middleware

    owned_paths = {
        "AppSchemaAgent": "ui/pages/records.yaml",
        "ServiceAgent": "modules/records/backend/service.py",
        "ConfigMiddlewareAgent": "modules/records/module.yaml",
        "ModelAgent": "modules/records/backend/schemas.py",
    }
    task = {
        "task_id": "records-patch",
        "owned_paths": [owned_paths[agent_name]],
        "acceptance_criteria": ["Refuse foreign mutations without success events."],
    }
    bridge = ContextVariablesBridge({
        "current_build_task": task,
        "unexposed_data": "UNEXPOSED_SENTINEL",
    })
    context = Context(MemoryStream(), prompt=["initial"], variables={})
    build = build_prompt_middleware(
        middleware_functions=[], agent_name=agent_name,
        base_system_message=contracts.prompts[agent_name], context_bridge=bridge,
        context_variables=contracts.context.agents[agent_name].variables,
    )
    rendered = []

    async def capture(_events, call_context):
        rendered.append("\n".join(call_context.prompt))
        return ModelResponse()

    requests = [
        "Match title or author as literal text, including .* and [draft]; page two must reach later records.",
        "Support explicitly requested regular-expression search ^draft.*; retain the declared bounds.",
        None,
    ]
    for request in requests:
        bridge.set("refinement_request", request)
        middleware = build(ModelRequest("continue"), context)
        await middleware.on_llm_call(capture, [], context)

    for index, message in enumerate(rendered):
        assert f"REFINEMENT_REQUEST: {requests[index]}" in message
        assert message.count("REFINEMENT_REQUEST:") == 1
        assert "records-patch" in message
        assert task["owned_paths"][0] in message
        assert task["acceptance_criteria"][0] in message
        assert "UNEXPOSED_SENTINEL" not in message
        for old_request in requests[:index]:
            assert old_request not in message


@pytest.mark.parametrize("agent_name", WORKERS)
def test_revision_worker_guidance_keeps_request_below_contract_and_owned_task_authority(contracts, agent_name):
    prompt = contracts.prompts[agent_name]
    assert "`refinement_request`" in prompt
    assert "requested product behavior" in prompt
    assert "runtime/operator contracts" in prompt
    assert "authorization boundaries" in prompt
    assert "current_build_task.owned_paths" in prompt
    assert "acceptance_criteria" in prompt
    assert "report conflicts" in prompt


def test_planner_preserves_behavioral_qualifiers_in_each_relevant_task(contracts):
    prompt = contracts.prompts["AppPlanAgent"]
    for phrase in (
        "each relevant owned task", "initial_message", "acceptance_criteria",
        "literal versus regex", "later records", "mutation-success events",
        "works as required", "runtime/operator contracts",
    ):
        assert phrase in prompt
    assert "describe decisions and task acceptance criteria once" not in prompt


@pytest.mark.parametrize("agent_name", ["AppPlanAgent", "ConfigMiddlewareAgent", "ServiceAgent"])
def test_existing_contract_hook_delivers_paging_search_and_mutation_guidance(contracts, agent_name):
    agent = SimpleNamespace(name=agent_name, context_variables={
        "current_build_task": {"task_type": "module_contract", "owned_paths": []},
    })
    prompt = _build_file_contracts_body(agent, contracts.files)
    for phrase in (
        "find_many has no skip", "aggregate", "$match", "$sort", "$skip", "$limit",
        "ownership", "unique tie-breaker", "bounded", "same query",
        "When literal search is requested", "re.escape", "explicitly requested regex",
        "actual write result", "no owned mutation", "declared action contract",
        "global HTTP status",
    ):
        assert phrase in prompt


def test_paging_guidance_uses_the_existing_persistence_api():
    assert "skip" not in inspect.signature(PersistenceCollection.find_many).parameters
    assert "pipeline" in inspect.signature(PersistenceCollection.aggregate).parameters
    assert "query" in inspect.signature(PersistenceCollection.count).parameters
