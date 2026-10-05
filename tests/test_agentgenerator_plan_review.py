from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml

from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.frozen import detach, freeze
from mozaiksai.core.workflow.declarative.contracts import ToolOutcomeSpec
from mozaiksai.core.workflow.outputs.structured import build_models_from_config
from mozaiksai.core.workflow.validation.tool_outcomes import wrap_tool_outcome
from tests.factory_context import factory_context

partition = importlib.import_module("factory_app.workflows.AgentGenerator.tools.pattern_selection")
review = importlib.import_module("factory_app.workflows.AgentGenerator.tools.mermaid_sequence_diagram")
export = importlib.import_module("factory_app.workflows.AgentGenerator.tools.generate_and_download")


class Context:
    def __init__(self, data):
        self.data = factory_context(data)

    def get(self, key, default=None):
        return freeze(self.data.get(key, default))

    def set(self, key, value):
        self.data[key] = detach(value)


@pytest.fixture(scope="module")
def models():
    path = Path(__file__).resolve().parents[1] / "factory_app/workflows/AgentGenerator/structured_outputs.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))["models"]
    return build_models_from_config(config, exact_model_ids=frozenset(config))


@pytest.fixture(autouse=True)
def model_loader(monkeypatch, models):
    path = Path(__file__).resolve().parents[1] / "factory_app/workflows/DesignDocs/structured_outputs.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))["models"]
    design_models = build_models_from_config(config, exact_model_ids=frozenset(config))
    for module in (partition, review):
        monkeypatch.setattr(module, "load_workflow_structured_outputs", lambda name: (design_models if name == "DesignDocs" else models, {}))


def selection(workflows=None):
    return {
        "pack_name": "Client Ledger",
        "is_multi_workflow": False,
        "pack_partition_reason": "Records use deterministic modules; no AI execution is needed.",
        "workflows": workflows or [],
    }


def workflow():
    return {
        "name": "Triage", "role": "primary", "description": "Classify requests.",
        "pattern_id": 1, "pattern_name": "Pipeline", "initial_agent": "WorkflowBundleBuilderAgent",
        "initial_message": "Classify user requests.", "depends_on": [],
    }


def priced_workflow_contract():
    return {
        "contract_required": True,
        "selected_features_by_plan": {"free": [], "pro": ["workflow.customers"]},
        "subscription_config_file": {
            "schema_version": "mozaiks.subscriptions.v1",
            "label": "Customer plans",
            "default_plan_id": "free",
            "plans": [
                {"plan_id": "free", "label": "Free", "capabilities": []},
                {"plan_id": "pro", "label": "Pro", "capabilities": ["feature.workflow.customers"]},
            ],
        },
        "workflow_contract_updates": [{
            "design_surface_id": "customers",
            "capability_id": "feature.workflow.customers",
            "workflow_name": None,
        }],
    }


def surface_map(kind):
    return {"surfaces": [{
        "surface_id": "customers", "label": "Customers", "surface_kind": kind, "owner": "app",
        "source_capability_packs": [], "primary_entities": [], "owned_pages": [],
        "owned_mutations": [], "events_emitted": [], "workflow_triggers": [],
        "integrations": [], "notes": None,
    }]}


def context(workflows=None):
    return Context({
        "chat_id": "chat-test", "user_id": "user-test", "workflow_name": "AgentGenerator",
        "PatternSelection": selection(workflows),
        "structured_output": {"MermaidSequenceDiagram": {
            "workflow_name": "Client Ledger", "diagram": "", "legend": [],
            "notes": None, "agent_message": "Review the plan.",
        }},
    })


def test_empty_selection_is_explicit_and_handles_read_only_output():
    ctx = Context({"structured_output": {"PatternSelection": selection()},
                   "design_surface_map": surface_map("module")})
    result = partition.pattern_selection(context_variables=ctx)
    assert result == {"outcome": "selected", "workflow_count": 0}
    assert ctx.data["workflows_spec"] == []
    assert ctx.data["workflow_plan_review"] is None


@pytest.mark.parametrize("raw", [None, {}, {**selection(), "pack_partition_reason": ""},
                                {**selection(), "is_multi_workflow": True}])
def test_missing_or_invalid_partition_is_not_a_no_workflow_decision(raw):
    with pytest.raises(ValueError):
        partition.validate_selection(raw, Context({}))


@pytest.mark.parametrize(("kind", "workflows"), [("module", [workflow()]), ("workflow", [])])
def test_partition_cannot_contradict_canonical_surface_map(kind, workflows):
    ctx = Context({"design_surface_map": surface_map(kind)})
    with pytest.raises(ValueError, match="surface map"):
        partition.validate_selection(selection(workflows), ctx)


def test_workflow_pricing_selection_requires_designer_rerun():
    ctx = Context({
        "design_surface_map": surface_map("workflow"),
        "concept_blueprint": {"agentic_capabilities": ["Classify requests"]},
        "subscription_contract": priced_workflow_contract(),
    })
    with pytest.raises(ValueError, match="Re-run SubscriptionContractDesigner"):
        partition.validate_selection(
            selection([{**workflow(), "design_surface_id": "customers"}]), ctx,
        )


def test_module_pricing_selection_does_not_revalidate_without_data_contract():
    design = surface_map("module")
    design["surfaces"][0]["surface_id"] = "tasks"
    design["surfaces"][0]["owned_mutations"] = ["complete_task"]
    context = ContextVariablesBridge(factory_context({
        "design_surface_map": design,
        "subscription_contract": {
            "contract_required": True,
            "selected_features_by_plan": {"free": [], "pro": ["module.tasks.create_task"]},
            "subscription_config_file": {"plans": [
                {"plan_id": "free", "capabilities": []},
                {"plan_id": "pro", "capabilities": ["feature.module.tasks.create_task"]},
            ]},
            "module_contract_updates": [{
                "module_id": "tasks", "action_id": "create_task",
                "entitlement_gate": "feature.module.tasks.create_task",
            }],
            "workflow_contract_updates": [],
        },
    }))

    assert context.get("data_contract") is None
    assert partition.pattern_selection(
        PatternSelection=selection([]), context_variables=context,
    ) == {"outcome": "selected", "workflow_count": 0}
    assert detach(context.get("workflows_spec")) == []


def test_unknown_design_surface_kind_is_rejected():
    with pytest.raises(ValueError):
        partition.validate_selection(selection(), Context({"design_surface_map": surface_map("invented")}))


def test_invalid_partition_has_bounded_correction_and_cannot_reuse_approval():
    path = Path(__file__).resolve().parents[1] / "factory_app/workflows/AgentGenerator/tools.yaml"
    # Select by function, not position: the tools list is ordered by workflow
    # stage, so inserting an earlier-stage tool silently retargeted this.
    tools = yaml.safe_load(path.read_text(encoding="utf-8"))["tools"]
    spec = next(t for t in tools if t["function"] == "pattern_selection")["outcome"]
    wrapped = wrap_tool_outcome(partition.pattern_selection, ToolOutcomeSpec.model_validate(spec))
    ctx = Context({"structured_output": {"PatternSelection": selection([workflow()])},
                   "design_surface_map": surface_map("module"), "pattern_selection_attempts": 0,
                   "workflow_plan_review": {"status": "approved"}, "PatternSelection": selection()})
    for _ in range(spec["max_attempts"]):
        assert wrapped(context_variables=ctx)["outcome"] == "invalid"
        assert ctx.data["PatternSelection"] is None
        assert ctx.data["workflow_plan_review"] is None
        assert "workflows: []" in ctx.data["pattern_selection_feedback"]
    assert wrapped(context_variables=ctx)["outcome"] == "blocked"
    assert ctx.data["pattern_selection_attempts"] == spec["max_attempts"]


def test_corrected_partition_clears_validation_feedback():
    ctx = Context({"structured_output": {"PatternSelection": selection()},
                   "pattern_selection_feedback": "Previous failure"})
    assert partition.pattern_selection(context_variables=ctx)["outcome"] == "selected"
    assert ctx.data["pattern_selection_feedback"] == ""


@pytest.mark.parametrize(("action", "outcome"), [
    ("approve", "no_workflows"), ("request_changes", "changes_requested"), ("cancel", "cancelled"),
])
def test_review_routes_only_correlated_structured_decisions(monkeypatch, action, outcome):
    ctx = context()
    persist = AsyncMock()
    monkeypatch.setattr(export, "_record_context_and_artifacts", persist)

    async def respond(**kwargs):
        payload = kwargs["payload"]
        assert payload["workflow_count"] == 0
        assert payload["checkpoints"]
        return {"action": action, "approved": action == "approve",
                "review_id": payload["review_id"], "rationale": "Human feedback"}

    monkeypatch.setattr(review, "use_ui_tool", respond)
    result = asyncio.run(review.mermaid_sequence_diagram(context_variables=ctx))
    assert result["outcome"] == outcome
    assert ctx.data["workflow_review_feedback"] == "Human feedback"
    assert persist.await_count == (1 if action == "approve" else 0)
    if action == "approve":
        assert persist.call_args.kwargs["app_id"] == "generated-app"
        assert persist.call_args.kwargs["bundle_entries"] == []
        assert persist.call_args.kwargs["zip_path"] is None


@pytest.mark.parametrize("failure", ["stale_id", "changed_plan", "replaced_review", "text", "contradictory"])
def test_invalid_approval_cannot_authorize_generation(monkeypatch, failure):
    ctx = context()
    persist = AsyncMock()
    monkeypatch.setattr(export, "_record_context_and_artifacts", persist)

    async def respond(**kwargs):
        if failure == "text":
            return "APPROVE"
        if failure == "changed_plan":
            ctx.data["PatternSelection"]["pack_name"] = "Changed"
        if failure == "replaced_review":
            ctx.data["workflow_plan_review"] = {"review_id": "replacement"}
        return {"action": "approve", "approved": failure != "contradictory",
                "review_id": "old" if failure == "stale_id" else kwargs["payload"]["review_id"]}

    monkeypatch.setattr(review, "use_ui_tool", respond)
    with pytest.raises(ValueError):
        asyncio.run(review.mermaid_sequence_diagram(context_variables=ctx))
    persist.assert_not_awaited()


def test_failed_empty_partition_save_cannot_complete(monkeypatch):
    ctx = context()
    monkeypatch.setattr(export, "_record_context_and_artifacts", AsyncMock(side_effect=RuntimeError("storage failed")))

    async def respond(**kwargs):
        return {"action": "approve", "approved": True, "review_id": kwargs["payload"]["review_id"]}

    monkeypatch.setattr(review, "use_ui_tool", respond)
    with pytest.raises(RuntimeError, match="storage failed"):
        asyncio.run(review.mermaid_sequence_diagram(context_variables=ctx))


def test_approved_nonempty_partition_dispatches_instead_of_recording_empty_bundle(monkeypatch):
    ctx = context([workflow()])
    persist = AsyncMock()
    monkeypatch.setattr(export, "_record_context_and_artifacts", persist)

    async def respond(**kwargs):
        assert kwargs["payload"]["workflow_count"] == 1
        return {"action": "approve", "approved": True, "review_id": kwargs["payload"]["review_id"]}

    monkeypatch.setattr(review, "use_ui_tool", respond)
    result = asyncio.run(review.mermaid_sequence_diagram(context_variables=ctx))
    assert result["outcome"] == "approved"
    persist.assert_not_awaited()


def review_outcome_spec():
    tools = yaml.safe_load((Path(__file__).resolve().parents[1] / "factory_app/workflows/AgentGenerator/tools.yaml").read_text(encoding="utf-8"))["tools"]
    return ToolOutcomeSpec.model_validate(next(tool["outcome"] for tool in tools if tool["function"] == "mermaid_sequence_diagram"))


def test_autonomous_empty_partition_persists_truthful_not_required_decision(monkeypatch):
    ctx = ContextVariablesBridge({**context().data, "coding_participation": "autonomous", "workflow_review_attempts": 0})
    persist = AsyncMock()
    ui = AsyncMock(side_effect=AssertionError("No empty-plan approval is needed"))
    monkeypatch.setattr(export, "_record_context_and_artifacts", persist)
    monkeypatch.setattr(review, "use_ui_tool", ui)
    spec = review_outcome_spec()
    result = asyncio.run(wrap_tool_outcome(review.mermaid_sequence_diagram, spec)(context_variables=ctx))
    assert result["outcome"] == ctx.get(spec.context_key) == "no_workflows"
    assert ctx.get(spec.attempts_key) == 1
    assert ctx.get("workflow_plan_review")["status"] == "not_required"
    assert ctx.get("workflow_plan_review")["selection_hash"] == review._fingerprint(selection())
    ui.assert_not_awaited()
    persist.assert_awaited_once()
    assert persist.call_args.kwargs["bundle_entries"] == []
    assert persist.call_args.kwargs["zip_path"] is None


@pytest.mark.parametrize("mode", [None, "collaborative", "unknown", "Autonomous", "autonomous "])
def test_empty_partition_keeps_review_without_exact_autonomous_mode(monkeypatch, mode):
    ctx = ContextVariablesBridge({**context().data, "coding_participation": mode})
    persist = AsyncMock()
    monkeypatch.setattr(export, "_record_context_and_artifacts", persist)

    async def reject(**kwargs):
        return {"action": "cancel", "approved": False, "review_id": kwargs["payload"]["review_id"]}

    ui = AsyncMock(side_effect=reject)
    monkeypatch.setattr(review, "use_ui_tool", ui)
    assert asyncio.run(review.mermaid_sequence_diagram(context_variables=ctx))["outcome"] == "cancelled"
    ui.assert_awaited_once()
    persist.assert_not_awaited()


def test_autonomous_nonempty_partition_still_requires_review(monkeypatch):
    ctx = ContextVariablesBridge({**context([workflow()]).data, "coding_participation": "autonomous"})
    persist = AsyncMock()
    monkeypatch.setattr(export, "_record_context_and_artifacts", persist)

    async def request_changes(**kwargs):
        return {"action": "request_changes", "approved": False, "review_id": kwargs["payload"]["review_id"]}

    ui = AsyncMock(side_effect=request_changes)
    monkeypatch.setattr(review, "use_ui_tool", ui)
    assert asyncio.run(review.mermaid_sequence_diagram(context_variables=ctx))["outcome"] == "changes_requested"
    ui.assert_awaited_once()
    persist.assert_not_awaited()


def test_autonomous_empty_partition_remains_blocked_when_design_requires_workflow(monkeypatch):
    ctx = ContextVariablesBridge({**context().data, "coding_participation": "autonomous",
                                  "design_surface_map": surface_map("workflow"), "workflow_review_attempts": 0})
    persist, ui = AsyncMock(), AsyncMock()
    monkeypatch.setattr(export, "_record_context_and_artifacts", persist)
    monkeypatch.setattr(review, "use_ui_tool", ui)
    result = asyncio.run(wrap_tool_outcome(review.mermaid_sequence_diagram, review_outcome_spec())(context_variables=ctx))
    assert result["outcome"] == "blocked"
    persist.assert_not_awaited()
    ui.assert_not_awaited()


def test_autonomous_empty_partition_cannot_complete_when_save_fails(monkeypatch):
    ctx = ContextVariablesBridge({**context().data, "coding_participation": "autonomous", "workflow_review_attempts": 0})
    persist = AsyncMock(side_effect=RuntimeError("storage unavailable"))
    ui = AsyncMock(side_effect=AssertionError("No empty-plan approval is needed"))
    monkeypatch.setattr(export, "_record_context_and_artifacts", persist)
    monkeypatch.setattr(review, "use_ui_tool", ui)
    result = asyncio.run(wrap_tool_outcome(review.mermaid_sequence_diagram, review_outcome_spec())(context_variables=ctx))
    assert result["outcome"] == ctx.get("workflow_review_outcome") == "blocked"
    persist.assert_awaited_once()
    ui.assert_not_awaited()
