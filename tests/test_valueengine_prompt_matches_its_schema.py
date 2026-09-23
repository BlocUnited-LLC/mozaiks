"""A prompt cannot ask the model for a field its schema will not carry.

ValueEngine's prompt demanded `pricing_recommendation` from 2026-07-24 (#74)
and its few-shot example emitted a full priced ladder. `ConceptBlueprint` never
declared the field -- `git log -S` on structured_outputs.yaml finds nothing.
Structured outputs are generated under OpenAI strict mode with
`additionalProperties: false` and `extra="forbid"`, so the model was
structurally unable to comply: the field never appeared, never failed
validation, and never reached the three AppGenerator rules that read it as
"the authoritative tier structure". Every one of those branches was dead prose
for two months, on the one turn that matters most to a monetized build.

The live symptom, on the 2026-09-22 acceptance run at OSS 856f90ab:
`pricing_recommendation` was null on a fully monetized concept, so the tier
structure AppGenerator was told to treat as authoritative did not exist.

The fix is an ownership boundary, not a new field. SubscriptionContractDesigner
designs the plan ladder and collects the user's approval on its own review
card; the concept carries only the advisory shape in `monetization_intent`.
ValueEngine decides what the app is, the contract designer decides what it
costs.

These are prompt/schema agreement tests, not generated-code semantic tests.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
VALUE_ENGINE = ROOT / "factory_app/workflows/ValueEngine"
APP_GENERATOR = ROOT / "factory_app/workflows/AppGenerator"
CONTRACT_DESIGNER = ROOT / "factory_app/workflows/SubscriptionContractDesigner"


def _prompt_text(workflow: Path, agent_name: str | None = None) -> str:
    agents = yaml.safe_load((workflow / "agents.yaml").read_text(encoding="utf-8"))
    roster = agents["agents"] if isinstance(agents, dict) else agents
    if agent_name is not None:
        roster = [item for item in roster if item["name"] == agent_name]
    return "\n\n".join(
        f"{section.get('heading', '')}\n{section.get('content', '')}"
        for item in roster
        for section in (item.get("prompt_sections") or [])
    )


@pytest.fixture(scope="module")
def concept_blueprint_fields() -> set[str]:
    models = yaml.safe_load((VALUE_ENGINE / "structured_outputs.yaml").read_text(encoding="utf-8"))
    return set(models["models"]["ConceptBlueprint"]["fields"])


@pytest.fixture(scope="module")
def value_engine_prompt() -> str:
    return _prompt_text(VALUE_ENGINE)


def _example_blueprint(prompt: str) -> dict:
    """The few-shot JSON is what the model copies, so it IS the effective contract."""
    blocks = re.findall(r"```json\s*(\{.*?\})\s*```", prompt, re.S)
    candidates = [json.loads(block) for block in blocks]
    blueprints = [obj for obj in candidates if "app_name" in obj and "concept_overview" in obj]
    assert len(blueprints) == 1, (
        f"expected exactly one ConceptBlueprint example in the prompt, found {len(blueprints)}"
    )
    return blueprints[0]


def test_the_example_only_uses_fields_the_schema_can_carry(
    value_engine_prompt: str, concept_blueprint_fields: set[str]
) -> None:
    """The generalizable guard: this is the check that was missing for two months.

    Not specific to pricing -- any future field added to the example without a
    matching schema entry fails here, before a build discovers it as a null.
    """
    example = _example_blueprint(value_engine_prompt)
    phantom = sorted(set(example) - concept_blueprint_fields)
    assert not phantom, (
        f"the few-shot example emits {phantom}, which ConceptBlueprint does not declare. "
        "Strict-mode decoding forbids extra properties, so the model cannot produce these "
        "and every downstream reader sees null. Either declare the field or stop asking for it."
    )


def test_the_example_covers_the_fields_the_schema_requires(
    value_engine_prompt: str, concept_blueprint_fields: set[str]
) -> None:
    """The other direction: an example that omits a declared field teaches omission."""
    example = _example_blueprint(value_engine_prompt)
    missing = sorted(concept_blueprint_fields - set(example))
    assert not missing, (
        f"ConceptBlueprint declares {missing} but the few-shot example never shows them; "
        "the example is what the model imitates"
    )


def test_pricing_is_not_a_concept_field(concept_blueprint_fields: set[str]) -> None:
    """Pin the ownership decision, so a future change is deliberate rather than drift."""
    assert "pricing_recommendation" not in concept_blueprint_fields, (
        "pricing belongs to SubscriptionContractDesigner, which has a review card where the "
        "user approves the real plans; a priced ladder on the concept is a second authority "
        "that can contradict the contract the user actually approved"
    )


def test_the_prompt_says_who_owns_pricing(value_engine_prompt: str) -> None:
    """Silence would let the model reintroduce prices through another field."""
    assert "SubscriptionContractDesigner" in value_engine_prompt, (
        "the prompt must name the workflow that owns pricing, or the model has no reason "
        "not to put a ladder somewhere else"
    )
    lowered = value_engine_prompt.lower()
    assert "do not emit concrete prices" in lowered, (
        "state the prohibition explicitly; 'pricing is owned elsewhere' alone reads as a "
        "note about downstream, not an instruction about this output"
    )
    assert "money_flow_summary" in value_engine_prompt, (
        "name the fields a price would leak into, since removing the pricing field only "
        "redirects the behaviour it was driving"
    )


def test_appgenerator_reads_the_contract_not_the_concept() -> None:
    """The three rules that read a permanently-absent field now read the real source."""
    prompt = _prompt_text(APP_GENERATOR)
    assert "pricing_recommendation" not in prompt, (
        "AppGenerator must not branch on a field ConceptBlueprint cannot carry"
    )
    assert "subscription_contract.subscription_config_file` is the authoritative tier structure" in prompt, (
        "it must name what IS authoritative, not merely drop what was not"
    )


def test_the_contract_designer_still_owns_the_ladder() -> None:
    """Guard the dependency: this whole boundary is void if the designer stops designing."""
    prompt = _prompt_text(CONTRACT_DESIGNER)
    assert "Plan ladder:" in prompt, (
        "ValueEngine now defers pricing to SubscriptionContractDesigner; if the designer's "
        "ladder guidance moves or is renamed, that deferral points at nothing"
    )
    review = CONTRACT_DESIGNER / "ui/SubscriptionContractDesigner/SubscriptionContractReview.jsx"
    assert review.exists(), (
        "the deferral is justified by the designer having a review card where the user "
        "approves real plans; without it the user approves pricing nowhere"
    )
