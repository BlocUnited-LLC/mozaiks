"""ThemeCapture must design for a new app, not interview its owner.

A founder starting from nothing cannot answer "what is your primary colour?"
in any useful way - they invent something. For a new app the agent has the
approved concept in scope and is expected to propose a complete look with a
reason drawn from how the product is used.
"""

from __future__ import annotations

from pathlib import Path

import yaml

THEME_DIR = Path(__file__).resolve().parents[1] / "factory_app" / "workflows" / "ThemeCapture"


def _agents_text() -> str:
    return (THEME_DIR / "agents.yaml").read_text(encoding="utf-8")


def test_new_app_runs_propose_a_complete_look_instead_of_surveying() -> None:
    text = _agents_text()

    assert "NEW APP" in text and "PROPOSE, DO NOT SURVEY" in text
    assert "ONE complete proposal per turn" in text
    assert "Always justify from the product's use" in text
    assert "Offer an alternative only when the user has not already chosen or delegated" in text
    assert "name a hex code" in text


def test_canned_design_questions_are_scoped_to_existing_app_evidence() -> None:
    """The stock question list assumes a brand exists to read.

    Left unscoped it fires on new apps, which is what produced
    "What color palette does your app use...?" as the opening move.
    """
    text = _agents_text()

    assert "existing-app runs only" in text
    assert "When evidence exists but a detail is genuinely missing" in text


def test_theme_interview_receives_the_approved_concept() -> None:
    """Without the concept the agent cannot justify a direction."""
    context = yaml.safe_load((THEME_DIR / "context_variables.yaml").read_text(encoding="utf-8"))

    definitions = context["definitions"]
    for key in ("concept_overview", "value_proposition", "target_user", "value_manifest"):
        assert key in definitions, f"{key} must be declared for theme reasoning"

    injected = context["agents"]["ThemeInterviewAgent"]["variables"]
    for key in ("concept_overview", "value_proposition", "target_user", "value_manifest"):
        assert key in injected, f"{key} must reach ThemeInterviewAgent"


def test_every_theme_stage_preserves_explicit_intent_and_interview_waits_for_answers():
    agents = yaml.safe_load(_agents_text())["agents"]
    for agent in agents:
        prompt = "\n".join(section["content"] for section in agent["prompt_sections"])
        assert "value_manifest.brand_intent" in prompt
        assert "palette, appearance, font and density" in prompt
    interview = "\n".join(section["content"] for section in agents[0]["prompt_sections"])
    assert "Set outcome to needs_input whenever your message asks for a decision" in interview
    assert "Set ready only when no question remains unanswered" in interview
    model = yaml.safe_load((THEME_DIR / "structured_outputs.yaml").read_text(encoding="utf-8"))
    description = model["models"]["ThemeInterviewResult"]["fields"]["outcome"]["description"]
    assert "whenever agent_message asks for a decision" in description
    assert "no question remains unanswered" in description
