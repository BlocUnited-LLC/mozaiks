from __future__ import annotations

import json

from factory_app.workflows.AgentGenerator.patternbook import (
    get_pattern_by_name,
    list_patterns,
    render_pattern_example,
    render_pattern_guidance,
    render_patternbook_summary,
)


def test_patternbook_declares_canonical_ag2_network_patterns() -> None:
    patterns = list_patterns()

    assert [pattern["id"] for pattern in patterns] == list(range(1, 10))
    assert [pattern["key"] for pattern in patterns] == [
        "context_aware_routing",
        "escalation",
        "feedback_loop",
        "hierarchical",
        "coordinator",
        "pipeline",
        "redundant",
        "star",
        "triage_with_tasks",
    ]
    assert "Organic" not in {pattern["label"] for pattern in patterns}


def test_patternbook_supports_intent_aliases_without_changing_labels() -> None:
    pattern = get_pattern_by_name("organic")

    assert pattern is not None
    assert pattern["key"] == "coordinator"
    assert pattern["label"] == "Coordinator"


def test_patternbook_rendering_is_deterministic_and_beta_network_specific() -> None:
    summary = render_patternbook_summary()
    guidance = render_pattern_guidance(6)
    example = json.loads(render_pattern_example(6) or "{}")

    assert "[AG2 NETWORK PATTERNBOOK]" in summary
    assert "llm or string_llm" in summary
    assert "[support: compiled_now]" in summary
    assert "[AG2 NETWORK PATTERN - Pipeline]" in guidance
    assert "TransitionGraph.sequence" in guidance
    assert example["WorkflowStrategy"]["pattern"] == ["Pipeline"]
