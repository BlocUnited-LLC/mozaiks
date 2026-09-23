"""`app_kind` must be guided outside the brownfield section, or greenfield borrows from it.

The 2026-09-23 acceptance run at OSS c2f47558 reached AppGenerator for the
first time in this journey -- handoff intact, subscription contract accepted,
`subscription_config` present in the reviewed AppBuildPlan -- and died there:

    [TASK_BATCH] app_build_tasks produced no task items from
      app_task_batch_items; the agent that populates it did not

`review_app_build_plan` had refused the plan three times, identically:

    - pages must preserve the approved name/route inventory:
        [('Dashboard','/dashboard'), ('Subscription','/subscription'), ('Tasks','/tasks')]
    - page_bundle/AppSchemaAgent must own all page files and app.json;
        missing ['ui/pages/billing.yaml', 'ui/pages/usage.yaml']
    - billing_portal/module_contract is incomplete; missing modules/billing_portal/module.yaml

The plan carried `app_kind: "brownfield_overlay"` on a run whose every signal
said greenfield:

    app_type              greenfield_app
    build_mode            initial
    brownfield_build_path None
    "light_integration"   0 occurrences anywhere in the run

`app_kind` appeared exactly three times in AppPlanAgent's prompt, all three
inside [BROWNFIELD ADOPTION SCOPE]. That section opens "active only when
[EXISTING APP ENHANCEMENT] is present" -- but it is a static prompt section, so
its text is in the prompt on every run. With no guidance anywhere else, the
only two worked examples of the field were brownfield ones, and the agent took
one.

That value is not cosmetic. An overlay is a delta over an existing app, so
"preserve the approved page inventory" and "page_bundle tasks must own every
planned page file" both read as add-what-is-new. The agent added `billing` and
`usage` pages beyond the approved three and owned neither. The validator,
applying greenfield semantics, was right to refuse.

These are prompt-exposure regressions, not generated-code semantic tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
APPGEN = ROOT / "factory_app/workflows/AppGenerator"


def _sections(agent_name: str) -> dict[str, str]:
    agents = yaml.safe_load((APPGEN / "agents.yaml").read_text(encoding="utf-8"))
    roster = agents["agents"] if isinstance(agents, dict) else agents
    agent = next(item for item in roster if item["name"] == agent_name)
    return {
        section.get("id", ""): section.get("content", "") or ""
        for section in agent.get("prompt_sections", [])
    }


@pytest.fixture(scope="module")
def plan_sections() -> dict[str, str]:
    return _sections("AppPlanAgent")


def test_app_kind_is_guided_outside_the_brownfield_section(plan_sections: dict[str, str]) -> None:
    """The gap itself: guidance only inside a section that does not apply."""
    elsewhere = {
        section_id: body
        for section_id, body in plan_sections.items()
        if section_id != "brownfield_scope" and "app_kind" in body
    }
    assert elsewhere, (
        "app_kind was documented only in brownfield_scope. That section's text is in "
        "the prompt on every run, so a greenfield plan had nothing else to copy and "
        "took brownfield_overlay -- which reframes the plan as a delta and makes the "
        "page-ownership rules read as add-what-is-new."
    )


def test_the_brownfield_values_are_scoped_to_a_brownfield_path(plan_sections: dict[str, str]) -> None:
    guidance = "\n".join(plan_sections.values())
    assert "brownfield_build_path" in guidance and "greenfield" in guidance.lower(), (
        "say which signal makes a brownfield_* kind correct, and that its absence "
        "makes it wrong; listing the two values alone is what produced the failure"
    )
    output_format = plan_sections.get("output_format", "")
    assert "brownfield_" in output_format, (
        "the always-read section must name the values it is excluding, or the reader "
        "never connects the rule to the examples in brownfield_scope"
    )


def test_the_rule_says_what_a_wrong_kind_costs(plan_sections: dict[str, str]) -> None:
    """State the consequence: a bare 'do not' loses to two concrete examples."""
    output_format = plan_sections.get("output_format", "").lower()
    assert "delta" in output_format, (
        "explain that a brownfield kind reframes the plan as a delta over an existing "
        "app; that is why the page and module rules silently change meaning"
    )


def test_the_page_rules_this_protects_still_exist(plan_sections: dict[str, str]) -> None:
    """Guard the dependency: the app_kind rule matters because these read differently."""
    guidance = "\n".join(plan_sections.values())
    assert "Preserve the approved page inventory" in guidance
    assert "page_bundle tasks must collectively own" in guidance, (
        "the app_kind rule exists to keep these two rules meaning what they say; if "
        "they move, revisit it rather than leaving it pointing at nothing"
    )


def test_the_schema_still_accepts_a_product_kind() -> None:
    """app_kind is an open string; the fix is guidance, not a narrowed enum."""
    models = yaml.safe_load((APPGEN / "structured_outputs.yaml").read_text(encoding="utf-8"))
    field = models["models"]["AppBuildPlan"]["fields"]["app_kind"]
    assert field["type"] == "str", (
        "app_kind is deliberately open-ended for product classification; closing it to "
        "an enum would be a contract change, not a prompt fix"
    )
