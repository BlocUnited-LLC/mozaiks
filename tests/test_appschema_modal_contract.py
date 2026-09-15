"""AppSchemaAgent must declare any Modal it points an action at.

Two consecutive live builds failed on the same generated defect:

    ui/pages/habits.yaml:
    $.sections[0].config.actions[0].payload.modal_id
    $.sections[1].config.children[0].config.cancel_action.payload.modal_id
    page_schema.unknown_modal: Modal actions require modal_id referencing a
    Modal on this page.

The page emitted an open action and a cancel action for a dialog it never
declared. Under failure_policy fail_batch that discards every task in the run.

This is not repairable from the page alone: with no Modal declared there is
nothing to point at, and synthesizing one would invent layout the design never
approved. resolve_modal_action_targets deliberately declines this case - see
test_a_page_with_no_modals_is_untouched - so the rule has to reach the agent.
"""

from pathlib import Path

import yaml

APP_GENERATOR = Path(__file__).resolve().parents[1] / "factory_app" / "workflows" / "AppGenerator"


def _app_schema_prompt() -> str:
    agents = yaml.safe_load((APP_GENERATOR / "agents.yaml").read_text(encoding="utf-8"))
    agent = next(a for a in agents["agents"] if a["name"] == "AppSchemaAgent")
    return "\n".join(section["content"] for section in agent["prompt_sections"])


def test_the_agent_is_told_a_referenced_modal_must_exist() -> None:
    prompt = _app_schema_prompt()

    assert "MUST equal the `id` of a `Modal` section you declared on this same page" in prompt
    assert "fails the whole page build" in prompt


def test_the_agent_is_given_the_alternative() -> None:
    """A rule that only forbids leaves the agent stuck; name what to do instead."""
    prompt = _app_schema_prompt()

    assert "emit the Modal section that holds it in the same page output" in prompt
    assert "use a navigate action instead of a modal event" in prompt
