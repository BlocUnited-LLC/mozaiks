"""A journey without a participation choice must retain human review."""

import importlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context import variables
from mozaiksai.core.workflow.pack.journey_orchestrator import _project_launch_context
from tests.factory_context import factory_context
from tests.test_agentgenerator_plan_review import context as workflow_review_context
from tests.test_factory_auto_tool_acceptance import factory_manager  # noqa: F401
from tests.test_subscription_contract_designer import (
    _live_designer_context_with,
    _no_contract_output,
)

WORKFLOWS = Path(__file__).resolve().parents[1] / "factory_app" / "workflows"
RELAY = ("DesignDocs", "SubscriptionContractDesigner", "AgentGenerator", "AppGenerator")
SELECTOR_FREE_JOURNEYS = (
    "brownfield_overlay_generation",
    "brownfield_module_generation",
    "full_rebuild",
    "conceptual_replan",
    "design_revision",
)


@pytest.fixture
def load_participation_relay(request, monkeypatch):
    """Use real YAML defaults and the journey's receiving-workflow projection.

    Only data-reference reads are replaced: this checks launch policy without a
    database, model call, or generated artifact. No participation value is
    manufactured by this fixture.
    """
    manager = request.getfixturevalue("factory_manager")
    for workflow in RELAY:
        info = manager.reload_workflow(workflow)
        assert not info.get("error"), info
    monkeypatch.setenv("CONTEXT_INCLUDE_SCHEMA", "false")
    monkeypatch.setattr(variables, "_load_data_reference_value", AsyncMock(return_value=None))
    registry = json.loads(
        (WORKFLOWS / "extended_orchestration" / "extension_registry.json").read_text(encoding="utf-8")
    )

    async def load(sequence_id, choice=None):
        sequence = next(item for item in registry["workflow_sequences"] if item["id"] == sequence_id)
        steps = sequence["steps"]
        workflow_order = [workflow for step in steps for workflow in step.get("workflows", [])]
        start = workflow_order.index("DesignDocs")
        assert workflow_order[start:start + len(RELAY)] == list(RELAY)
        seed = {"workflow_sequence": sequence_id}
        if choice is None:
            assert not any(step.get("transition") == "coding_journey_selector" for step in steps)
        else:
            assert any(step.get("transition") == "coding_journey_selector" for step in steps)
            selector = next(item for item in registry["transitions"] if item["id"] == "coding_journey_selector")
            option = next(item for item in selector["options"] if item["id"] == choice)
            assert option["route_to"] == "DesignDocs"
            seed.update(option["context_variables"])

        contexts = {}
        runtime = factory_context({"app_id": "app_test"})
        for workflow in RELAY:
            context = await variables._load_context_async(
                workflow, runtime["app_id"], runtime_context=runtime,
            )
            for key, value in _project_launch_context(seed, workflow).items():
                context.set(key, value)
            seed = context.snapshot()
            contexts[workflow] = seed
        return contexts

    return load


@pytest.mark.asyncio
@pytest.mark.parametrize(("sequence_id", "choice"), [
    *((sequence, None) for sequence in SELECTOR_FREE_JOURNEYS),
    ("build", "guided"),
    ("build", "autonomous"),
])
async def test_relayed_participation_controls_both_empty_decision_reviews(
    load_participation_relay, monkeypatch, sequence_id, choice,
):
    """Exercise both consumers, not only their independent safe defaults.

    The regression was DesignDocs introducing autonomy before these tools saw
    the context, so manually giving either tool a missing mode missed it.
    """
    contexts = await load_participation_relay(sequence_id, choice)
    subscription = importlib.import_module(
        "factory_app.workflows.SubscriptionContractDesigner.tools.save_subscription_contract"
    )
    workflow_review = importlib.import_module(
        "factory_app.workflows.AgentGenerator.tools.mermaid_sequence_diagram"
    )
    workflow_export = importlib.import_module(
        "factory_app.workflows.AgentGenerator.tools.generate_and_download"
    )
    subscription_ui = AsyncMock(return_value={
        "action": "request_changes", "approved": False, "requested_changes": "Keep reviewing",
    })
    subscription_save = AsyncMock(return_value=SimpleNamespace(id="av_no_contract"))

    async def reject_workflow(**kwargs):
        return {"action": "cancel", "approved": False, "review_id": kwargs["payload"]["review_id"]}

    workflow_ui = AsyncMock(side_effect=reject_workflow)
    workflow_save = AsyncMock()
    monkeypatch.setattr(subscription, "use_ui_tool", subscription_ui)
    monkeypatch.setattr(subscription, "persist_summary_artifact", subscription_save)
    monkeypatch.setattr(workflow_review, "use_ui_tool", workflow_ui)
    monkeypatch.setattr(workflow_export, "_record_context_and_artifacts", workflow_save)
    subscription_context = _live_designer_context_with(
        {"coding_participation": contexts["SubscriptionContractDesigner"]["coding_participation"]},
        blueprint=None, monetization_enabled=False, output=_no_contract_output(),
    )
    workflow_context = ContextVariablesBridge({
        **workflow_review_context().data,
        "coding_participation": contexts["AgentGenerator"]["coding_participation"],
    })
    subscription_result = await subscription.save_subscription_contract(subscription_context)
    workflow_result = await workflow_review.mermaid_sequence_diagram(context_variables=workflow_context)

    autonomous = choice == "autonomous"
    expected_reviews = 0 if autonomous else 1
    assert (subscription_ui.await_count, workflow_ui.await_count) == (expected_reviews, expected_reviews)
    assert [contexts[workflow]["coding_participation"] for workflow in RELAY] == [
        "autonomous" if autonomous else "guided"
    ] * len(RELAY)
    if autonomous:
        assert subscription_result["review_status"] == "not_required"
        assert subscription_save.call_args.kwargs["summary_payload"]["user_confirmed"] is False
        assert workflow_result["outcome"] == "no_workflows"
        assert workflow_context.get("workflow_plan_review")["status"] == "not_required"
        subscription_save.assert_awaited_once()
        workflow_save.assert_awaited_once()
    else:
        assert subscription_result["review_status"] == "changes_requested"
        assert workflow_result["outcome"] == "cancelled"
        subscription_save.assert_not_awaited()
        workflow_save.assert_not_awaited()
