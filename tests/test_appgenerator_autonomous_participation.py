"""AppGenerator must honour the build-path the user already chose.

A live run of a habit tracker chose "Build it for me" at the participation
checkpoint, then reached AppGenerator and was met with nine scope questions:
whether the app needed a marketplace, campaigns, promotions and discounts,
extra user roles, authorization beyond "the already integrated JWT tokens",
and requirements "beyond what is already outlined in the frontend design
document". It prefaced them by stating the app already had a concept overview
and design surface map - it had what it needed and asked anyway. No app was
ever generated.

The cause was not the wording. ``coding_participation`` was absent from both
the AppGenerator context definitions and the InterviewAgent whitelist, so the
agent could not see which path the user picked. The same trap already claimed
``task_run_mode``: its branch exists in the prompt and is unreachable because
the variable is not whitelisted, which is why the whitelist assertion below
matters more than the prompt text.
"""

from pathlib import Path

import yaml

APP_GENERATOR_DIR = Path(__file__).resolve().parents[1] / "factory_app" / "workflows" / "AppGenerator"


def _context_variables() -> dict:
    return yaml.safe_load((APP_GENERATOR_DIR / "context_variables.yaml").read_text(encoding="utf-8"))


def test_coding_participation_is_declared_for_app_generator() -> None:
    definitions = _context_variables()["definitions"]

    assert "coding_participation" in definitions
    declared = definitions["coding_participation"]
    assert declared["type"] == "string"
    # A missing answer must mean "ask", never "skip the human". Twelve journeys
    # reach AppGenerator without passing coding_journey_selector - every revision
    # and refinement journey, plus both brownfield generation paths - so an absent
    # value is the common case. Defaulting to autonomous silently suppressed the
    # interview on all of them; only an explicit choice may do that.
    assert declared["source"]["default"] == "guided"


def test_interview_agent_can_actually_read_the_participation_choice() -> None:
    """The prompt branch is inert unless the variable is whitelisted."""
    whitelist = _context_variables()["agents"]["InterviewAgent"]["variables"]

    assert "coding_participation" in whitelist


def test_interview_agent_does_not_re_interview_after_the_user_delegated() -> None:
    agents_text = (APP_GENERATOR_DIR / "agents.yaml").read_text(encoding="utf-8")

    assert "`ContextVariables.coding_participation` equals `autonomous`" in agents_text
    assert "Do NOT present a list of open scope questions." in agents_text
    assert "answer yourself from" in agents_text
    assert "Emit ONLY the token: `NEXT`" in agents_text


def test_interview_agent_does_not_offer_scope_the_concept_never_asked_for() -> None:
    """The live run offered a habit tracker a marketplace and campaigns."""
    agents_text = (APP_GENERATOR_DIR / "agents.yaml").read_text(encoding="utf-8")

    assert "Optional capability packs the concept does not call for are OUT of" in agents_text
    assert "marketplace, campaigns, or advanced analytics" in agents_text
    assert "never to widen scope" in agents_text


def test_only_an_explicit_choice_can_suppress_a_human_turn() -> None:
    """The bypass must fire on a chosen value, never on an absent one."""
    import json

    registry = json.loads(
        (APP_GENERATOR_DIR.parents[0] / "extended_orchestration" / "extension_registry.json").read_text(
            encoding="utf-8"
        )
    )
    selector = next(t for t in registry["transitions"] if t["id"] == "coding_journey_selector")
    chosen = {o["id"]: o["context_variables"]["coding_participation"] for o in selector["options"]}
    assert chosen == {"autonomous": "autonomous", "guided": "guided"}

    rules = yaml.safe_load((APP_GENERATOR_DIR / "transition_graph.yaml").read_text(encoding="utf-8"))
    bypass = [r for r in rules["transition_rules"] if r["source_agent"] == "user"][0]
    assert bypass["condition_value"] == "autonomous"

    # The bypass value and the default must differ, or an absent answer bypasses.
    definitions = _context_variables()["definitions"]
    assert definitions["coding_participation"]["source"]["default"] != bypass["condition_value"]
