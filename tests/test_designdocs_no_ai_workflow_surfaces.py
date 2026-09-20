"""A concept that declares no agentic capabilities must yield no workflow surface.

DesignDocs declared a `workflow` surface for an app whose interview said "no AI
workflows needed -- ordinary modules and pages cover everything". PatternAgent
then correctly returned `workflows: []`, and `pattern_selection` rejected the
mismatch three times until the run died:

    TOOL_OUTCOME_REJECTED tool=pattern_selection outcome=invalid attempt=1/3
      reason=The canonical design surface map requires at least one declared AI workflow
    ... -> channel closed reason=workflow_failed

The validator is correct; the surface map was wrong. The user's answer is
already carried to DesignDocs -- `agentic_capabilities` on the approved concept
blueprint -- so this is prompt guidance that was missing, not a missing signal.

These are prompt-exposure regressions, not generated-code semantic tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
DESIGNDOCS = ROOT / "factory_app/workflows/DesignDocs"


@pytest.fixture(scope="module")
def designdocs_prompt() -> str:
    agents = yaml.safe_load((DESIGNDOCS / "agents.yaml").read_text(encoding="utf-8"))
    roster = agents["agents"] if isinstance(agents, dict) else agents
    agent = next(item for item in roster if item["name"] == "DesignDocsAgent")
    return "\n\n".join(
        f"{section.get('heading', '')}\n{section.get('content', '')}"
        for section in agent.get("prompt_sections", [])
    )


def test_the_prompt_gates_workflow_surfaces_on_the_declared_concept(designdocs_prompt: str) -> None:
    assert "agentic_capabilities" in designdocs_prompt, (
        "DesignDocsAgent must be told which field records the user's decision about "
        "agentic behavior; without it the agent infers workflow surfaces the concept "
        "never asked for"
    )


def test_the_constraint_is_stated_as_binding(designdocs_prompt: str) -> None:
    lowered = designdocs_prompt.lower()
    # Deliberately not asserting "hard constraint" alone: that phrase already
    # appears elsewhere in this prompt, so it would pass with the rule absent --
    # a test certifying the bug it exists to catch. Assert the consequence, which
    # is unique to this rule.
    assert "no path back to the user" in lowered, (
        "the prompt must state the consequence -- pattern_selection rejects the "
        "mismatch and the build dies with no chance to renegotiate the decision"
    )
    agentic_rules = [
        line for line in designdocs_prompt.splitlines() if "agentic_capabilities" in line
    ]
    assert agentic_rules, "the rule must reference the field that carries the decision"


def test_the_signal_the_prompt_relies_on_still_exists() -> None:
    """Guard the dependency: the rule is useless if the field is renamed or dropped."""
    value_engine = yaml.safe_load(
        (ROOT / "factory_app/workflows/ValueEngine/structured_outputs.yaml").read_text(encoding="utf-8"),
    )
    blueprint = value_engine["models"]["ConceptBlueprint"]["fields"]
    assert "agentic_capabilities" in blueprint, (
        "DesignDocs guidance points at ConceptBlueprint.agentic_capabilities; if that "
        "field moves, the guidance silently stops meaning anything"
    )


def test_the_downstream_validator_still_enforces_the_pairing() -> None:
    """The rule exists because pattern_selection fails the build on a mismatch."""
    source = (
        ROOT / "factory_app/workflows/AgentGenerator/tools/pattern_selection.py"
    ).read_text(encoding="utf-8")
    assert 'surface_kind") == "workflow"' in source
    assert "bool(workflows) != has_workflows" in source, (
        "if this pairing check is removed the guidance is no longer load-bearing and "
        "this test should be revisited rather than left asserting a dead contract"
    )
