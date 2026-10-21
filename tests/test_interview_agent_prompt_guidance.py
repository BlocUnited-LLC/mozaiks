from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_value_engine_prompt_stays_route_bounded_and_non_exhaustive() -> None:
    source = _read("factory_app/workflows/ValueEngine/agents.yaml")

    assert 'do NOT ask whether they already have an existing app' in source
    assert 'You do not need to ask every category directly.' in source
    assert 'Competitor/market depth belongs in the research findings.' in source


def test_appgenerator_prompt_prefers_context_and_assumption_forward_guidance() -> None:
    source = _read("factory_app/workflows/AppGenerator/agents.yaml")

    assert 'Treat upstream briefs and design docs as provisional truth.' in source
    assert '`greenfield_app`, do NOT ask whether the user already has an existing app' in source
    assert 'Choose small implementation defaults when the approved requirements permit them.' in source


def test_agentgenerator_prompt_avoids_checklist_interrogation() -> None:
    source = _read("factory_app/workflows/AgentGenerator/agents.yaml")

    assert '`concept_overview` already states the automation goal' in source
    assert 'do not ask the user to restate it' in source
    assert 'Do NOT force explicit monetization, integration, dataset, review, or startup-mode questions just to satisfy a checklist.' in source
    assert 'Usually no more than 3 targeted questions.' in source
    assert 'Return WorkflowInterviewResult with natural conversation in `agent_message` and readiness in `outcome`.' in source
    assert 'no structured outputs' not in source.lower()


def test_theme_capture_prompt_prefers_evidence_over_generic_questionnaire() -> None:
    source = _read("factory_app/workflows/ThemeCapture/agents.yaml")

    assert 'Guide lightly from existing evidence.' in source
    assert 'Prefer assumption-forward confirmations' in source
    assert 'Do not run a generic design questionnaire.' in source


def test_interview_orchestrators_use_guidance_language() -> None:
    value_orchestrator = _read("factory_app/workflows/ValueEngine/orchestrator.yaml")
    app_orchestrator = _read("factory_app/workflows/AppGenerator/orchestrator.yaml")
    agent_orchestrator = _read("factory_app/workflows/AgentGenerator/orchestrator.yaml")

    assert 'guide the user to a concrete app direction' in value_orchestrator
    assert 'guide the user through only the missing deterministic' in app_orchestrator
    assert 'only a missing question that changes the required AI workflow design' in agent_orchestrator
    assert 'WorkflowInterviewResult' in agent_orchestrator
    assert 'outcome ready' in agent_orchestrator
    assert 'outcome needs_input' in agent_orchestrator
    assert 'NEXT' not in agent_orchestrator


def _gap_analysis_prompt() -> str:
    import yaml

    from mozaiksai.core.workflow.agents.factory import _compose_prompt_sections

    agents = yaml.safe_load(_read("factory_app/workflows/ValueEngine/agents.yaml"))["agents"]
    agent = next(item for item in agents if item["name"] == "GapAnalysisAgent")
    return " ".join(_compose_prompt_sections(agent["prompt_sections"]).split())


def test_concept_revision_feedback_outranks_the_previous_draft_and_interview() -> None:
    """A live revision repeated an offline-first draft the owner had rejected.

    The owner asked for server-side storage. The prompt told the agent that
    interview facts establish intent, listed only four fields as revisable and
    said to preserve constraints, so every field, including the offline
    constraint, came back unchanged.
    """
    prompt = _gap_analysis_prompt()

    assert "Preserve unaffected scope, brand intent and constraints" not in prompt
    assert (
        "A non-empty concept_review_feedback is the owner's latest decision about that draft. "
        "It outranks the interview facts, the research findings, the previous draft and the "
        "current app_name wherever they conflict" in prompt
    )
    revisable = prompt.split("Apply the feedback to every ConceptBlueprint field it touches", 1)[1]
    revisable = revisable.split(".", 1)[0]
    for field in ("app_name", "constraints", "unique_differentiators", "api_endpoints",
                  "surface_candidate_hints", "core_features", "concept_overview"):
        assert field in revisable, field
    assert "preserve only the scope, brand intent and constraints the feedback leaves untouched" in prompt
    assert "never re-emit the previous draft" in prompt
