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


def test_value_engine_interview_complete_trigger_still_uses_exact_next() -> None:
    context_config = yaml.safe_load((VALUE_ENGINE_DIR / "context_variables.yaml").read_text(encoding="utf-8"))
    trigger = context_config["definitions"]["interview_complete"]["source"]["triggers"][0]

    assert trigger["type"] == "agent_text"
    assert trigger["agent"] == "ValueInterviewAgent"
    assert trigger["match"]["equals"] == "NEXT"
