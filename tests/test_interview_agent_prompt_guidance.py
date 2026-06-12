from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_value_engine_prompt_stays_route_bounded_and_non_exhaustive() -> None:
    source = _read("factory_app/workflows/ValueEngine/agents.yaml")

    assert 'do NOT ask whether they already have an existing app' in source
    assert 'You do not need to ask every category directly.' in source
    assert 'Competitor/market depth belongs to ResearchAgent.' in source


def test_appgenerator_prompt_prefers_context_and_assumption_forward_guidance() -> None:
    source = _read("factory_app/workflows/AppGenerator/agents.yaml")

    assert 'Treat `concept_overview`, `value_manifest`, and any design docs as provisional truth.' in source
    assert '`greenfield_app`: do NOT ask whether the user already has an existing app' in source
    assert 'Prefer assumption-forward guidance over open-ended interviewing.' in source


def test_agentgenerator_prompt_avoids_checklist_interrogation() -> None:
    source = _read("factory_app/workflows/AgentGenerator/agents.yaml")

    assert '`concept_overview` already states the automation goal' in source
    assert 'do not ask the user to restate it' in source
    assert 'Do NOT force explicit monetization, integration, dataset, review, or startup-mode questions just to satisfy a checklist.' in source
    assert 'Usually no more than 3 targeted questions.' in source


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
    assert 'guide the user with assumptions or suggestions' in agent_orchestrator
