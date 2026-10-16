"""Scripted provider-visible UI context through real Factory agents and AG2 tasks.

These witnesses stop at a schema-valid scripted reply. They do not materialize a
generated app, prove model design quality, or exercise the refinement entrypoint.
Only provider/config and external tool loading are replaced; the canonical YAML
views, registered prompt middleware, Agent.ask and task adapter execute normally.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import subprocess
from copy import deepcopy
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from ag2 import Agent
from ag2.config.openai.mappers import convert_messages
from ag2.events import ModelMessage, ModelResponse

from mozaiksai.core.adapters.ag2_task_batch_runner import (
    AG2TaskBatchRunner,
    AG2TaskBatchRunnerRequest,
)
from mozaiksai.core.ports.orchestration import RunStatus
from mozaiksai.core.workflow.agents import factory
from mozaiksai.core.workflow.context.adapter import create_context_container
from mozaiksai.core.workflow.context.authority import (
    ContextAuthorityError,
    build_context_authority_policy,
)
from mozaiksai.core.workflow.outputs.structured import build_models_from_config
from mozaiksai.core.workflow.task_batches import (
    _build_scoped_worker_prompt,
    _build_task_context,
    _normalize_task_items,
    load_task_batches_config,
)

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / "factory_app/workflows"
REQUEST = "On mobile, open task details in a bottom panel; preserve desktop, routes, brand and task behavior."
SOURCE = "export default function Board() { return <main>accepted-board-source-v1</main>; }"
PRIVATE = "TOOL_ONLY_BINDING_SENTINEL"
UNRELATED = "UNRELATED_CATALOG_SENTINEL"


class _ScriptedProvider:
    model = "offline-ui-context-witness"
    provider = "openai"

    def __init__(self, output):
        self.output = output
        self.calls = []

    def copy(self):
        return self

    def create(self):
        async def client(events, context, *, tools, response_schema, serializer):
            self.calls.append({
                "prompt": "\n".join(context.prompt),
                "messages": convert_messages(context.prompt, events, serializer),
                "response_schema": response_schema,
            })
            return ModelResponse(ModelMessage(json.dumps(self.output)))

        return client


def _context():
    page = {
        "name": "Board", "route": "/board", "ui_surface": "custom_react_page",
        "layout": "full-width", "intent": "Move tasks by pointer and keyboard", "sections": [],
    }
    return {
        "build_mode": "revision",
        "revision_id": "accepted-revision-v1",
        "current_app_context_version_id": "accepted-context-v1",
        "concept_overview": "Approved task workspace with a visual board and declarative Projects route.",
        "experience_spec": {"navigation_model": "workspace", "brand_direction": "quiet", "pages": [page]},
        "experience_spec_document": json.dumps({"approved_design_id": "design-v1", "pages": [page]}),
        "frontend_design_document": "APPROVED_FRONTEND_DESIGN: preserve a three-lane board on desktop.",
        "captured_theme_config": {
            "theme": {"font": "system", "font_heading": "oxanium"},
            "identity": {"app_name": "APPROVED_THEME_ID"},
            "fonts": {"heading": {"family": "Oxanium"}, "body": {"family": "system-ui"}},
        },
        "app_build_plan": {
            "pages": [{"name": "Board", "route": "/board", "purpose": page["intent"],
                       "ui_surface": "custom_react_page", "design_intent": page["intent"], "sections_hint": []}],
        },
        "generated_files": {"ui/pages/custom/board.jsx": SOURCE, "ui/pages/projects.yaml": "sibling-source-v1"},
        "refinement_request": REQUEST,
        "run_build_binding": {
            "target_app_id": PRIVATE, "build_registry_id": "registry-ui-context",
            "build_id": "build-ui-context", "phase": "refinement",
        },
        "unrelated_catalog": {"content": UNRELATED},
        "unexposed_secret": "SYNTHETIC_SECRET_SENTINEL",
    }


async def _agent(monkeypatch, workflow, name, context, output):
    """Select one actual roster entry; retain its schema, views and hook order."""
    from mozaiksai.core.tokens.guard import TokenUsageGuard
    from mozaiksai.core.workflow import llm_config
    from mozaiksai.core.workflow.agents import tools
    from mozaiksai.core.workflow.outputs import structured

    config = factory.workflow_manager._load_modular_workflow_config(WORKFLOWS / workflow)
    config["agents"]["agents"] = {name: config["agents"]["agents"][name]}
    outputs = config["structured_outputs"]
    models = build_models_from_config(outputs["models"], exact_model_ids=frozenset(outputs["models"]))
    model = models[outputs["registry"][name]]
    provider = _ScriptedProvider(model.model_validate(output).model_dump(mode="json"))
    local_config = AsyncMock(return_value=(None, {"config_list": [{"model": provider.model, "api_type": "openai"}]}))
    monkeypatch.setattr(factory.workflow_manager, "get_config", lambda _: config)
    monkeypatch.setattr(factory.workflow_manager, "resolve_workflow_path", lambda _: WORKFLOWS / workflow)
    monkeypatch.setattr(factory, "get_structured_outputs_for_workflow", lambda _: {name: model})
    monkeypatch.setattr(factory, "llm_config_to_ag2_config", lambda _: provider)
    monkeypatch.setattr(llm_config, "get_llm_config", local_config)
    monkeypatch.setattr(structured, "get_llm_for_workflow", local_config)
    monkeypatch.setattr(tools, "load_agent_tool_functions", lambda _: {})
    monkeypatch.setattr(TokenUsageGuard, "check_or_raise", AsyncMock())
    policy = build_context_authority_policy(workflow_name=workflow, definitions=config["context_variables"]["definitions"])
    container = create_context_container(initial=context, authority_policy=policy)
    agents = await factory.create_agents(workflow, context_variables=container)
    agent = agents[name]
    assert type(agent) is Agent
    assert agent._mozaiks_prompt_middleware
    return agent, provider, model, policy


async def _run(agent, model, policy, context, *, workflow="AppGenerator", prompt="Inspect the approved UI context."):
    result = await AG2TaskBatchRunner().run(AG2TaskBatchRunnerRequest(
        workflow_name=workflow, batch_id=context.get("current_task_batch_id", "ui_context_witness"),
        task_id=context.get("current_task_id", "context-only"),
        chat_id=None, app_id=None, agent_name=agent.name, agent=agent, prompt=prompt,
        context_variables=context, structured_registry={agent.name: model},
        context_authority_policy=policy, timeout_seconds=10,
    ))
    assert result.status is RunStatus.COMPLETED, result.error
    assert result.lifecycle_status == "completed"
    assert result.task_id
    assert result.lifecycle_events
    return result


def _receipt(path, workflow, name, provider, results):
    """Keep synthetic effective prompts inspectable without saving repeated text."""
    unique = {}
    calls = []
    for call in provider.calls:
        payload = {"prompt": call["prompt"], "messages": call["messages"]}
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        unique[digest] = payload
        calls.append(digest)
    path.write_text(json.dumps({
        "evidence_kind": "scripted_provider_context_only",
        "workflow": workflow, "agent": name,
        "source_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "test_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "ag2_version": importlib.metadata.version("ag2"),
        "provider_call_count": len(calls), "calls": calls, "unique_effective_requests": unique,
        "task_lifecycle": [{"task_id": result.task_id, "status": result.lifecycle_status,
                            "events": result.lifecycle_events} for result in results],
        "limits": ["no live model", "no generated bundle acceptance", "no refinement entrypoint"],
    }, indent=2), encoding="utf-8")


@pytest.mark.asyncio
async def test_page_worker_actual_ag2_prompt_retains_approved_context_for_ten_passes(monkeypatch, tmp_path):
    base = _context()
    before = deepcopy(base)
    task = {
        "task_id": "board-mobile", "task_type": "page_bundle", "initial_agent": "AppSchemaAgent",
        "initial_message": "Implement only the approved mobile inspector change.",
        "owned_paths": ["ui/pages/custom/board.jsx"], "depends_on": ["module-contract"],
        "acceptance_criteria": ["Desktop board remains three lanes", "Keyboard status changes persist"],
    }
    batch = load_task_batches_config("AppGenerator", workflows_root=WORKFLOWS).batches[0]
    context = _build_task_context(
        base_context=base, task=task, all_task_items=[task], batch=batch,
        completed_task_outputs={
            "module-contract": {"code_files": [{
                "filename": "modules/tasks/module.yaml",
                "content": "schema_version: mozaiks.module.v1\nmodule:\n  id: tasks\n"
                           "  handler: backend.handler:TasksModule\nactions:\n"
                           "- id: update_task\n  handler_method: update_task\n"
                           "  permissions: [tasks:write]\n# owner-only task mutations\n",
            }]},
            "unrelated-task": {"content": "UNRELATED_DEPENDENCY_SENTINEL"},
        }, current_batch_outputs={}, chat_id=None, app_id=None, user_id=None,
    )
    scoped = _build_scoped_worker_prompt(task["initial_message"], context)
    agent, provider, model, policy = await _agent(
        monkeypatch, "AppGenerator", "AppSchemaAgent", context,
        {"agent_message": "Scripted context-only witness; no generated application.", "pages": []},
    )
    bridge = agent._mozaiks_context_bridge
    snapshot = bridge.snapshot()
    results = []
    for _ in range(10):
        results.append(await _run(agent, model, policy, context, prompt=scoped))
        assert bridge.snapshot() == snapshot
        assert bridge.consume_context_updates() == {"set": {}, "delete": []}
    assert len(provider.calls) == 10
    assert len({call["prompt"] for call in provider.calls}) == 1
    for call in provider.calls:
        prompt = call["prompt"]
        wire = json.dumps(call["messages"])
        for value in ("design-v1", "custom_react_page", "APPROVED_FRONTEND_DESIGN", "APPROVED_THEME_ID", SOURCE, REQUEST):
            assert value in prompt
        for value in ("board-mobile", "ui/pages/custom/board.jsx", "Keyboard status changes persist", "owner-only",
                      "modules/tasks/module.yaml", "update_task", "tasks:write"):
            assert value in wire
        design_line = next(line for line in prompt.splitlines() if line.startswith("EXPERIENCE_SPEC_DOCUMENT: "))
        approved_page = json.loads(design_line.removeprefix("EXPERIENCE_SPEC_DOCUMENT: "))["pages"][0]
        assert approved_page["ui_surface"] == "custom_react_page" and approved_page["sections"] == []
        assert prompt.splitlines().count("[SHIPPED PAGE PRIMITIVES]") == 1
        assert prompt.count("\nREFINEMENT_REQUEST:") == 1
        assert "PageHeader" in prompt and "moduleAction" in prompt
        for excluded in (PRIVATE, UNRELATED, "SYNTHETIC_SECRET_SENTINEL", "UNRELATED_DEPENDENCY_SENTINEL"):
            assert excluded not in wire
        assert call["response_schema"] is not None
    with pytest.raises(ContextAuthorityError):
        bridge.set("run_build_binding", {"target_app_id": "unauthorized-target"})
    assert bridge.snapshot() == snapshot
    assert base == before
    _receipt(tmp_path / "worker-context.json", "AppGenerator", agent.name, provider, results)


@pytest.mark.asyncio
async def test_page_worker_next_authorized_context_replaces_prior_request(monkeypatch, tmp_path):
    context = _context()
    agent, provider, model, policy = await _agent(monkeypatch, "AppGenerator", "AppSchemaAgent", context, {
        "agent_message": "Scripted context-only witness.", "pages": [],
    })
    first = await _run(agent, model, policy, context)
    updated = {**context, "refinement_request": "Latest approved change: mobile inspector opens below the task list."}
    agent._mozaiks_context_bridge._hydrate_channel_context(
        updated, policy=policy, run_identity=("AppGenerator", "context-app", "context-run"),
    )
    second = await _run(agent, model, policy, updated)
    assert len(provider.calls) == 2
    assert REQUEST in provider.calls[0]["prompt"]
    assert REQUEST not in provider.calls[1]["prompt"]
    assert updated["refinement_request"] in provider.calls[1]["prompt"]
    assert provider.calls[1]["prompt"].count("\nREFINEMENT_REQUEST:") == 1
    assert agent._mozaiks_context_bridge.snapshot() == updated
    _receipt(tmp_path / "worker-context.json", "AppGenerator", agent.name, provider, [first, second])


@pytest.mark.asyncio
@pytest.mark.parametrize(("workflow", "name", "output", "required"), [
    ("AppGenerator", "AppPlanAgent", {"AppBuildPlan": {
        "agent_message": "Scripted planning context witness.", "app_kind": "workspace",
        "agent_backend_required": False, "build_tasks": [], "readiness_profile": "none", "monetization_provider": "null",
    }}, ["APPROVED_FRONTEND_DESIGN", "design-v1", "custom_react_page", SOURCE, REQUEST, "accepted-revision-v1"]),
    ("DesignDocs", "DesignDocsAgent", {
        "agent_message": "Scripted design context witness.", "frontend_markdown": "Synthetic frontend.",
        "backend_markdown": "Synthetic backend.", "database_markdown": "Synthetic database.",
        "experience_spec": {"navigation_model": "workspace", "brand_direction": "quiet", "pages": []},
        "surface_map": {"surfaces": []}, "data_contract": {
            "version": "1", "surfaces": [], "policies": {"default_scope_field": "app_id", "allow_destructive_migrations": False},
        },
    }, ["Approved task workspace", "APPROVED_THEME_ID", REQUEST, "accepted-revision-v1"]),
    ("AgentGenerator", "WorkflowBundleBuilderAgent", {
        "workflow_name": "TaskReview", "agent_message": "Scripted workflow context witness.",
        "pattern_id": 1, "pattern_name": "Pipeline", "outcome_plans": [], "files": [],
    }, ["design-v1", "custom_react_page", "accepted-context-v1"]),
])
async def test_canonical_design_planning_and_workflow_workers_reach_scripted_provider(
    monkeypatch, tmp_path, workflow, name, output, required,
):
    context = _context()
    before = deepcopy(context)
    agent, provider, model, policy = await _agent(monkeypatch, workflow, name, context, output)
    result = await _run(agent, model, policy, context, workflow=workflow)
    assert len(provider.calls) == 1
    prompt = provider.calls[0]["prompt"]
    for value in required:
        assert value in prompt
    for excluded in (PRIVATE, UNRELATED, "SYNTHETIC_SECRET_SENTINEL"):
        assert excluded not in json.dumps(provider.calls[0]["messages"])
    assert agent._mozaiks_context_bridge.snapshot() == before
    _receipt(tmp_path / "worker-context.json", workflow, name, provider, [result])


@pytest.mark.asyncio
async def test_typed_workflow_assignment_carries_selected_ui_context_through_real_worker(monkeypatch, tmp_path):
    """A scripted complete brief survives; planner completeness is not asserted."""
    from factory_app.workflows.AgentGenerator.tools.pattern_selection import pattern_selection
    from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge

    base = _context()
    base["generated_files"]["ui/pages/projects.yaml"] = "UNASSIGNED_SIBLING_SOURCE_SENTINEL"
    source = "export default function TaskReviewCard() { return <section>accepted-review-v1</section>; }"
    brief = {
        "workflow_name": "TaskReview", "approved_design": "Review task recommendations before applying them.",
        "owned_files": ["ui/TaskReview/components/TaskReviewCard.jsx"],
        "acceptance_criteria": ["Preserve the declared approve/revise actions", "Mobile details use a bottom panel"],
        "captured_theme": base["captured_theme_config"],
        "current_accepted_source": source, "current_request": REQUEST,
        "accepted_revision": "accepted-revision-v1",
    }
    batch = load_task_batches_config("AgentGenerator", workflows_root=WORKFLOWS).batches[0]

    def assignment(current_brief):
        bridge = ContextVariablesBridge(deepcopy(base))
        protected = bridge.get("run_build_binding")
        result = pattern_selection(PatternSelection={
            "pack_name": "Task Review", "is_multi_workflow": False, "pack_partition_reason": None,
            "workflows": [{
                "name": "TaskReview", "design_surface_id": None, "role": "primary",
                "description": "Review task recommendations", "pattern_id": 1, "pattern_name": "Pipeline",
                "initial_agent": "WorkflowBundleBuilderAgent", "depends_on": [],
                "initial_message": "Implement this approved workflow UI assignment.\n" + json.dumps(current_brief),
            }],
        }, context_variables=bridge)
        assert result == {"outcome": "selected", "workflow_count": 1}
        assert bridge.get("run_build_binding") == protected
        items = _normalize_task_items(bridge.snapshot()["workflows_spec"])
        assert len(items) == 1 and items[0]["task_id"] == "TaskReview"
        context = _build_task_context(
            base_context=bridge.snapshot(), task=items[0], all_task_items=items, batch=batch,
            completed_task_outputs={}, current_batch_outputs={}, chat_id=None, app_id=None, user_id=None,
        )
        return context, _build_scoped_worker_prompt(items[0]["initial_message"], context)

    context, scoped = assignment(brief)
    agent, provider, model, policy = await _agent(monkeypatch, "AgentGenerator", "WorkflowBundleBuilderAgent", context, {
        "workflow_name": "TaskReview", "agent_message": "Scripted assignment witness, no generated bundle.",
        "pattern_id": 1, "pattern_name": "Pipeline", "outcome_plans": [], "files": [],
    })
    settled = agent._mozaiks_context_bridge.snapshot()
    results = []
    for _ in range(10):
        results.append(await _run(agent, model, policy, context, workflow="AgentGenerator", prompt=scoped))
        assert agent._mozaiks_context_bridge.snapshot() == settled
        assert agent._mozaiks_context_bridge.consume_context_updates() == {"set": {}, "delete": []}
    assert len(provider.calls) == 10
    assert len({json.dumps(call["messages"], sort_keys=True) for call in provider.calls}) == 1
    for call in provider.calls:
        wire = json.dumps(call["messages"])
        for value in ("accepted-review-v1", "APPROVED_THEME_ID", "TaskReviewCard.jsx", REQUEST,
                      "Preserve the declared approve/revise actions", "accepted-revision-v1"):
            assert value in wire
        for excluded in (PRIVATE, UNRELATED, "SYNTHETIC_SECRET_SENTINEL", "UNASSIGNED_SIBLING_SOURCE_SENTINEL"):
            assert excluded not in wire

    updated_request = "Latest approved workflow correction: preserve desktop review; mobile details open below recommendations."
    revised, revised_prompt = assignment({**brief, "current_request": updated_request})
    agent._mozaiks_context_bridge._hydrate_channel_context(
        revised, policy=policy, run_identity=("AgentGenerator", "context-app", "context-run"),
    )
    results.append(await _run(agent, model, policy, revised, workflow="AgentGenerator", prompt=revised_prompt))
    assert len(provider.calls) == 11
    latest_wire = json.dumps(provider.calls[-1]["messages"])
    assert updated_request in latest_wire and REQUEST not in latest_wire
    assert "accepted-review-v1" in latest_wire
    assert agent._mozaiks_context_bridge.snapshot() == revised
    _receipt(tmp_path / "worker-context.json", "AgentGenerator", agent.name, provider, results)
