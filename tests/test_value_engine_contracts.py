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


def test_value_engine_interview_agent_must_recommend_when_user_delegates_choice() -> None:
    agents_text = (VALUE_ENGINE_DIR / "agents.yaml").read_text(encoding="utf-8")

    assert "If the user delegates choice" in agents_text
    assert "Give one working direction as your suggested starting point" in agents_text
    assert "do not ask them to choose" in agents_text
    assert "Do not hard-code launch/funding milestones, traders, founders, or betting mechanics as the only path" in agents_text


def test_value_engine_interview_agent_uses_working_hypothesis_not_overconfident_assumption() -> None:
    agents_text = (VALUE_ENGINE_DIR / "agents.yaml").read_text(encoding="utf-8")

    assert "working direction" in agents_text
    assert "working direction as your suggested starting point" in agents_text
    assert "I would assume [target user]" not in agents_text
    assert "traders bet on verifiable launch/funding/traction milestones" not in agents_text


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
