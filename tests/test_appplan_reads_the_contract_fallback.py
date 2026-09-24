"""A null `subscription_contract` is not a missing contract.

The 2026-09-24 acceptance run at OSS 2e7c9388 reached AppGenerator with
`app_kind: saas` (#715 held, and the page-inventory complaints it caused were
gone), and `review_app_build_plan` refused the plan three times on a new,
narrower contradiction:

    AppBuildPlan.monetization_provider is only valid when build_tasks include
    task_type='subscription_config'.

The agent had set a provider and planned no `subscription_config` task, on a run
where `contract_required` was true and the designer had produced
`config/subscriptions.yaml`. It was following the prompt exactly. In the
AppGenerator chat:

    subscription_contract          => None      (all three attempts)
    subscription_contract_artifact => {... 'contract_required': True ...}

The contract does not always survive into this workflow's state, which is
precisely what `subscription_contract_artifact` is declared for -- "used as a
fallback when the current sequence did not carry subscription_contract in
state". The tools honour that: `assemble_app_tasks.py` and
`materialize_app_config_contracts.py` both iterate
`("subscription_contract", "subscription_contract_artifact")`.

The planning rules did not. They named only `subscription_contract`, and one
said "false **or absent** -> do not plan the task". With state null, the agent
read absent, planned nothing, and still set `monetization_provider` from
`monetization_enabled` -- producing exactly the contradiction the validator
caught.

Same shape as #709: the signal was present, and the deciding rule named the
wrong variable.

These are prompt-exposure regressions, not generated-code semantic tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
APPGEN = ROOT / "factory_app/workflows/AppGenerator"


@pytest.fixture(scope="module")
def appgen_prompts() -> str:
    agents = yaml.safe_load((APPGEN / "agents.yaml").read_text(encoding="utf-8"))
    roster = agents["agents"] if isinstance(agents, dict) else agents
    return "\n\n".join(
        f"{section.get('heading', '')}\n{section.get('content', '')}"
        for item in roster
        for section in (item.get("prompt_sections") or [])
    )


def _rules_mentioning(prompt: str, needle: str) -> list[str]:
    return [line.strip() for line in prompt.splitlines() if needle in line]


def test_the_task_rule_reads_the_fallback(appgen_prompts: str) -> None:
    """The rule that gates the subscription_config task must know about the artifact."""
    gating = "\n".join(
        _rules_mentioning(appgen_prompts, "contract_required")
        + _rules_mentioning(appgen_prompts, "subscription_config` task")
    )
    assert "subscription_contract_artifact" in appgen_prompts, (
        "the planning rules named only subscription_contract; with state null the agent "
        "concluded there was no contract while the artifact beside it said otherwise"
    )
    assert "summary_payload" in appgen_prompts, (
        "name where the contract actually sits inside the artifact, or the agent has to "
        "guess the shape"
    )
    assert gating, "the contract_required rules must still exist for this test to mean anything"


def test_null_state_is_not_treated_as_no_contract(appgen_prompts: str) -> None:
    """The exact misreading that killed the run."""
    lowered = appgen_prompts.lower()
    assert "not \"absent\"" in lowered or "not a missing contract" in lowered, (
        "say explicitly that a null subscription_contract alone does not mean there is no "
        "contract; the previous wording was 'false or absent', which the agent read as absent"
    )


def test_the_provider_and_task_are_tied_together(appgen_prompts: str) -> None:
    """The validator pairs them; the prompt now says so before the plan is written."""
    assert "monetization_provider" in appgen_prompts
    lowered = appgen_prompts.lower()
    assert "never set `monetization_provider` without planning that task" in lowered, (
        "the agent set a provider with no task; stating the pairing up front is cheaper "
        "than a rejection the agent cannot act on"
    )


def test_the_tools_still_read_both_keys() -> None:
    """Guard the dependency: the prompt now points at behaviour the tools implement."""
    for rel in (
        "tools/assemble_app_tasks.py",
        "tools/materialize_app_config_contracts.py",
    ):
        source = (APPGEN / rel).read_text(encoding="utf-8")
        assert '"subscription_contract", "subscription_contract_artifact"' in source, (
            f"{rel} no longer reads both keys; the prompt guidance added here assumes it does"
        )


def test_the_fallback_is_declared_for_exactly_this_case() -> None:
    """If the artifact variable is renamed or dropped, this guidance means nothing."""
    context = yaml.safe_load((APPGEN / "context_variables.yaml").read_text(encoding="utf-8"))
    definitions = context.get("definitions") or context
    assert "subscription_contract_artifact" in definitions, (
        "AppGenerator must still declare the artifact fallback the prompt now relies on"
    )
