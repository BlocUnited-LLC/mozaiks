"""Every workflow that pauses to the user must be able to resume from a reply.

A graph that reverts to the user but declares no ``source_agent: user`` rule is
unsatisfiable: the builder's reply matches no transition, so the compiled graph
falls through to its ``no_transition_matched`` default. That default is now a
FAILED run, so the reply *fails the build it was sent to answer* — and before
that it silently truncated the run as "complete" and the build sequence skipped
the stage. Both readings are wrong; the graph is the defect.

Found live on PR #521: AppGenerator (twelve revert paths, zero resume routes),
AppReview (the build journey's final gate — unsatisfiable by any answer),
ExistingAppDiscovery (could never complete at all), and
BrandAssetGeneratorWorkflow (died on the first reply). None of it was visible to
the existing suite, which is why this structural guard exists.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

FACTORY_WORKFLOWS = Path(__file__).resolve().parents[1] / "factory_app" / "workflows"


def _workflow_dirs() -> list[Path]:
    return sorted(
        d for d in FACTORY_WORKFLOWS.iterdir()
        if d.is_dir() and (d / "transition_graph.yaml").exists()
    )


def _rules(workflow_dir: Path) -> list[dict]:
    data = yaml.safe_load((workflow_dir / "transition_graph.yaml").read_text(encoding="utf-8"))
    return [r for r in ((data or {}).get("transition_rules") or []) if isinstance(r, dict)]


def _is_revert_to_user(rule: dict) -> bool:
    """A rule that hands control back to the user, in either declared form."""
    return (
        str(rule.get("target_agent", "")).strip().lower() == "user"
        or str(rule.get("transition_target", "")).strip().lower() == "reverttousertarget"
    )


def _is_user_resume(rule: dict) -> bool:
    return str(rule.get("source_agent", "")).strip().lower() == "user"


@pytest.mark.parametrize("workflow_dir", _workflow_dirs(), ids=lambda p: p.name)
def test_workflow_that_pauses_to_user_declares_a_resume_route(workflow_dir: Path) -> None:
    rules = _rules(workflow_dir)
    reverts = [r for r in rules if _is_revert_to_user(r)]
    if not reverts:
        pytest.skip("workflow never reverts to the user")

    resumes = [r for r in rules if _is_user_resume(r)]
    assert resumes, (
        f"{workflow_dir.name} reverts to the user {len(reverts)}x but declares no "
        f"'source_agent: user' transition. Every builder reply will match no "
        f"transition and close the run on the graph's no_transition_matched "
        f"default, which the runtime treats as FAILED."
    )


@pytest.mark.parametrize("workflow_dir", _workflow_dirs(), ids=lambda p: p.name)
def test_user_resume_targets_are_declared_agents(workflow_dir: Path) -> None:
    """A resume route pointing at a non-existent agent fails the same way."""
    agents_file = workflow_dir / "agents.yaml"
    if not agents_file.exists():
        pytest.skip("no agents.yaml")
    loaded = yaml.safe_load(agents_file.read_text(encoding="utf-8"))
    roster = loaded.get("agents") if isinstance(loaded, dict) else loaded
    if not isinstance(roster, list):
        pytest.skip("unrecognized agents.yaml shape")
    names = {a.get("name") for a in roster if isinstance(a, dict)}

    unknown = [
        rule.get("target_agent")
        for rule in _rules(workflow_dir)
        if _is_user_resume(rule)
        and str(rule.get("target_agent", "")).strip().lower() not in {"terminate", "user"}
        and rule.get("target_agent") not in names
    ]
    assert not unknown, (
        f"{workflow_dir.name} resumes user replies at undeclared agents: {unknown}. "
        f"Declared agents: {sorted(n for n in names if n)}"
    )
