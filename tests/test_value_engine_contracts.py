from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
VALUE_ENGINE_DIR = REPO_ROOT / "factory_app" / "workflows" / "ValueEngine"


def test_value_engine_interview_agent_uses_bare_next_completion_contract() -> None:
    agents_text = (VALUE_ENGINE_DIR / "agents.yaml").read_text(encoding="utf-8")

    assert "provide a concise summary" not in agents_text
    assert "output EXACTLY:" in agents_text
    assert "Do not add any summary, punctuation, or extra words in that message." in agents_text


def test_value_engine_interview_agent_infers_recognizable_concept_shorthand() -> None:
    agents_text = (VALUE_ENGINE_DIR / "agents.yaml").read_text(encoding="utf-8")
    orchestrator = yaml.safe_load((VALUE_ENGINE_DIR / "orchestrator.yaml").read_text(encoding="utf-8"))

    assert "Polymarket for AI startups" in agents_text
    assert 'Do NOT ask "what niche or user group?"' in agents_text
    assert "For recognizable shorthand, you MUST include concrete pain points and app directions" in agents_text
    assert "Present one flexible working direction plus 1-2 lighter suggestion angles" in agents_text
    assert "infer the likely niche" in orchestrator["initial_message"]


def test_value_engine_interview_agent_keeps_domain_signal_instead_of_generic_fallback() -> None:
    agents_text = (VALUE_ENGINE_DIR / "agents.yaml").read_text(encoding="utf-8")

    assert "Never discard a domain signal" in agents_text
    assert '"Polymarket for AI startups" + "gamblers"' in agents_text
    assert "Do not propose unrelated categories such as mental health, personal finance, or remote collaboration." in agents_text


def test_value_engine_interview_agent_bans_generic_questions_after_shorthand() -> None:
    agents_text = (VALUE_ENGINE_DIR / "agents.yaml").read_text(encoding="utf-8")

    assert "BANNED after recognizable shorthand" in agents_text
    assert "What specific problem do you want to solve?" in agents_text
    assert "What pain points are you considering?" in agents_text
    assert "What target user?" in agents_text
    assert "What niche?" in agents_text
    assert "Which one should I use?" in agents_text
    assert "Which of these resonates?" in agents_text
    assert "fragmented startup signal" in agents_text
    assert "market-implied confidence around AI companies" in agents_text


def test_value_engine_interview_agent_never_invents_an_app_the_user_did_not_describe() -> None:
    """A live run produced a full concept for an app nobody asked for.

    The user described a habit tracker. The agent never registered it, kept
    asking "What do you want to build?", and on bare affirmations emitted a
    finished blueprint for "AI Startup Market" - a Polymarket-style investment
    platform - which was then recorded as approved. The only concrete product
    in this prompt is its own shorthand example, and the output matched it,
    down to the "market prediction analysis" anchor.

    The example stays, because it teaches real shorthand handling. What must
    hold is that it can never become the product.
    """
    agents_text = (VALUE_ENGINE_DIR / "agents.yaml").read_text(encoding="utf-8")

    assert "are illustrations of how to read" in agents_text
    assert "They are never candidate products." in agents_text
    assert "blueprint an app the user has not described" in agents_text
    assert "you have nothing to propose" in agents_text


def test_value_engine_affirmation_does_not_authorise_an_invented_direction() -> None:
    """"Yes" to nothing is not consent to choose the product for the user."""
    agents_text = (VALUE_ENGINE_DIR / "agents.yaml").read_text(encoding="utf-8")

    # The old rule read: short affirmations "= user is happy. Emit NEXT." with
    # no requirement that anything had been established first, so a user who
    # only ever said "yes" advanced straight into a fabricated concept.
    assert "happy with what is ALREADY on the table" in agents_text
    assert "Emit NEXT only if a concrete app direction actually exists" in agents_text
    assert "an affirmation is not an answer" in agents_text
    assert "Agreement is never permission to pick the product for them." in agents_text


def test_value_engine_interview_agent_must_recommend_when_user_delegates_choice() -> None:
    """Delegation must produce a decision, never the question handed back."""
    agents_text = (VALUE_ENGINE_DIR / "agents.yaml").read_text(encoding="utf-8")

    assert "make the call yourself and say what you chose" in agents_text
    assert "Never hand the decision back" in agents_text
    assert "decide and move on. Do not hand the choice back" in agents_text


def test_value_engine_interview_agent_proposes_with_a_default_and_an_alternative() -> None:
    """The advisory shape: carry a default, and leave the door open.

    A recommendation with no default is a survey question in disguise; a
    recommendation with no alternative railroads the user. Both halves are
    load-bearing for the conversation feeling like expertise.
    """
    agents_text = (VALUE_ENGINE_DIR / "agents.yaml").read_text(encoding="utf-8")

    assert "ALWAYS carry a default" in agents_text
    assert "OPEN THE DOOR" in agents_text
    assert "Never present a numbered menu and ask them to choose" in agents_text
    assert "I would assume [target user]" not in agents_text
    assert "traders bet on verifiable launch/funding/traction milestones" not in agents_text


def test_value_engine_interview_agent_is_grounded_in_the_buildable_menu() -> None:
    """Proposals must name things the generator can actually build.

    The menu is injected by prompt middleware; without that wiring the agent
    has no inventory and falls back to generic discovery questions.
    """
    agents_text = (VALUE_ENGINE_DIR / "agents.yaml").read_text(encoding="utf-8")
    middleware = yaml.safe_load((VALUE_ENGINE_DIR / "middleware.yaml").read_text(encoding="utf-8"))

    assert "[BUILDABLE MENU]" in agents_text
    assert "Never promise anything absent from [BUILDABLE MENU]" in agents_text

    hooks = [
        entry for entry in middleware["prompt_middleware"]
        if entry.get("function") == "inject_buildable_menu_context"
    ]
    assert len(hooks) == 1, "the buildable menu hook must be wired exactly once"
    assert hooks[0]["agent"] == "ValueInterviewAgent"


def test_value_engine_interview_complete_trigger_still_uses_exact_next() -> None:
    context_config = yaml.safe_load((VALUE_ENGINE_DIR / "context_variables.yaml").read_text(encoding="utf-8"))
    trigger = context_config["definitions"]["interview_complete"]["source"]["triggers"][0]

    assert trigger["type"] == "agent_text"
    assert trigger["agent"] == "ValueInterviewAgent"
    assert trigger["match"]["equals"] == "NEXT"


def test_value_engine_existing_app_mode_overrides_greenfield_openers() -> None:
    agents_text = (VALUE_ENGINE_DIR / "agents.yaml").read_text(encoding="utf-8")

    assert "This section overrides [OPENING THE CONVERSATION] for existing-app runs." in agents_text
    assert "This section applies only to greenfield app starts." in agents_text
    assert 'Never ask "What do you want to build?", "What problem are you trying to solve?", or "Who are you building this for?" for an existing-app run.' in agents_text
    assert "Do not ask the user to pick from detected opportunities before research." in agents_text
    assert "ValueEngine's downstream agents own that value analysis." in agents_text
    assert 'what would you suggest' in agents_text


def test_value_engine_has_no_dead_build_plan_persistence_branch() -> None:
    tools_config = yaml.safe_load((VALUE_ENGINE_DIR / "tools.yaml").read_text(encoding="utf-8"))
    context_config = yaml.safe_load((VALUE_ENGINE_DIR / "context_variables.yaml").read_text(encoding="utf-8"))
    decompose_source = (VALUE_ENGINE_DIR / "tools" / "decompose.py").read_text(encoding="utf-8")

    tool_names = {tool["function"] for tool in tools_config["tools"]}
    assert "save_build_plan" not in tool_names
    assert "get_build_plan" not in tool_names
    assert "build_plan" not in context_config["definitions"]
    assert "save_build_plan" not in decompose_source
    assert "get_build_plan" not in decompose_source
