"""A journey variable survives only if every step on the route declares it.

``_project_launch_context`` (mozaiksai/core/workflow/pack/journey_orchestrator.py)
builds each workflow's launch context from the *previous* step's chat document,
keeping a key only when the receiving workflow declares it. The relay is
therefore per-hop: one workflow in the middle that does not declare a key drops
it for that step and every step after it.

A live run proved the cost. The user chose "Build it for me" at the
participation checkpoint. ``coding_participation`` reached DesignDocs, which
declares it, and was gone by AppGenerator, which also declares it - because
SubscriptionContractDesigner and AgentGenerator sit between them and did not.
AppGenerator's prompt branch keyed to that value could never fire, so the
generation stage interviewed a user who had explicitly asked not to be.

Declaring a variable at the destination is not enough. This pins the whole
route.
"""

from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / "factory_app" / "workflows"

# The build journey, in order, from the step that sets the value through the
# step that consumes it. Mirrors workflow_sequences["build"] in
# extended_orchestration/extension_registry.json.
PARTICIPATION_RELAY = [
    "DesignDocs",
    "SubscriptionContractDesigner",
    "AgentGenerator",
    "AppGenerator",
]


def _definitions(workflow: str) -> dict:
    raw = yaml.safe_load((WORKFLOWS / workflow / "context_variables.yaml").read_text(encoding="utf-8"))
    return raw.get("definitions", raw) or {}


@pytest.mark.parametrize("workflow", PARTICIPATION_RELAY)
def test_every_step_on_the_route_relays_the_participation_choice(workflow: str) -> None:
    definitions = _definitions(workflow)

    assert "coding_participation" in definitions, (
        f"{workflow} does not declare coding_participation, so the launch-context "
        "projection drops the user's build-path answer here and at every step "
        "after it."
    )
    declared = definitions["coding_participation"]
    # The projection also requires a state-sourced declaration.
    assert (declared.get("source") or {}).get("type") == "state"


def test_app_generator_interview_can_still_read_the_relayed_value() -> None:
    """Whitelisting is the other half; neither alone makes the branch fire."""
    raw = yaml.safe_load((WORKFLOWS / "AppGenerator" / "context_variables.yaml").read_text(encoding="utf-8"))

    assert "coding_participation" in raw["agents"]["InterviewAgent"]["variables"]
