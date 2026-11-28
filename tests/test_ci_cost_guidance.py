"""Keep the shared cost checklist reachable by both coding-agent entry points."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECKLIST = "docs/ci-cost-responsibility.md"


@pytest.mark.parametrize("entrypoint", ["AGENTS.md", "CLAUDE.md"])
def test_entrypoints_require_cost_checklist(entrypoint: str) -> None:
    text = (ROOT / entrypoint).read_text(encoding="utf-8")
    link = f"[CI Cost Responsibility]({CHECKLIST})"
    assert text.count(link) == 1
    assert (ROOT / CHECKLIST).is_file()
    # App-specific guidance must survive package-managed block refreshes.
    managed_start = text.find("<!-- BEGIN MOZAIKS MANAGED: agent-guidance -->")
    assert managed_start == -1 or text.index(link) < managed_start


def test_cost_checklist_keeps_validation_and_budget_limits_explicit() -> None:
    text = " ".join((ROOT / CHECKLIST).read_text(encoding="utf-8").split())
    for requirement in (
        "Linux is an operating system, not a price tier",
        "including draft",
        "all required work must run and pass on the final head",
        "never reuse a stale commit's result",
        "not a monthly hard cap",
        "without the operator's authorization",
        "does not replace required tests, security checks, or merge authority",
    ):
        assert requirement in text, requirement
