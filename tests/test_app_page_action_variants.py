"""Page actions are five closed shapes, not one shape with everything nullable.

A live build (chat 61db4014) put three independent action defects on one page --
a navigate action with no route, an event action with no event type, and modal
events with no target. The contract permitted all three: `action_type` was a
label, and `href`/`event_type`/`workflow_id` were independent nullable fields
that nothing tied to it. Each defect surfaced one at a time against a two-turn
repair budget, and the run died one fix short of a valid page.

Variants make the first two unrepresentable rather than merely rejected, and
the typed modal target makes the third a field the schema can require.
"""

from __future__ import annotations

import pydantic
import pytest

from mozaiksai.core.workflow.outputs.structured import (
    load_workflow_structured_outputs,
    supports_provider_response_format,
)


@pytest.fixture(scope="module")
def models() -> dict:
    compiled, _ = load_workflow_structured_outputs("AppGenerator")
    return compiled


VARIANTS = ("AppNavigateAction", "AppSubmitAction", "AppDeleteAction", "AppEventAction", "AppWorkflowAction")


def test_each_variant_requires_the_field_that_makes_it_work(models: dict) -> None:
    required = {name: {f for f, spec in models[name].model_fields.items() if spec.is_required()} for name in VARIANTS}
    assert "href" in required["AppNavigateAction"]
    assert "href" in required["AppSubmitAction"]
    assert "href" in required["AppDeleteAction"]
    assert "event_type" in required["AppEventAction"]
    assert "workflow_id" in required["AppWorkflowAction"]


@pytest.mark.parametrize(
    ("variant", "payload"),
    [
        ("AppNavigateAction", {"action_type": "navigate", "label": "Open"}),
        ("AppEventAction", {"action_type": "event", "label": "Close"}),
        ("AppWorkflowAction", {"action_type": "workflow", "label": "Run"}),
    ],
)
def test_the_three_live_defects_are_unrepresentable(models: dict, variant: str, payload: dict) -> None:
    """Each of these was emitted by a real run and cost it a repair attempt."""
    with pytest.raises(pydantic.ValidationError) as err:
        models[variant].model_validate(payload)
    assert any(e["type"] == "missing" for e in err.value.errors())


def test_a_variant_refuses_another_variants_field(models: dict) -> None:
    """The old flat model let an event action carry href: null. Now it cannot."""
    with pytest.raises(pydantic.ValidationError) as err:
        models["AppEventAction"].model_validate(
            {"action_type": "event", "label": "X", "event_type": "ui.modal.close", "href": "/somewhere"}
        )
    assert any(e["type"] == "extra_forbidden" for e in err.value.errors())


def test_the_union_alias_reaches_both_reference_positions(models: dict) -> None:
    """14 sites reference AppPageAction; the alias keeps every one of them working."""
    listed = str(models["AppDataTableConfig"].model_fields["actions"].annotation)
    nullable = str(models["AppButtonConfig"].model_fields["action"].annotation)
    for variant in VARIANTS:
        assert variant in listed, f"{variant} missing from an items: position"
        assert variant in nullable, f"{variant} missing from a union[..., null] position"


def test_the_action_contract_stays_provider_strict_safe(models: dict) -> None:
    """A union the provider cannot enforce would move the failure back to runtime."""
    for name in ("AppSchemaOutput", "AppPageSchema"):
        supported, reason = supports_provider_response_format(models[name])
        assert supported, f"{name}: {reason}"


def test_the_event_variant_carries_a_typed_modal_target(models: dict) -> None:
    action = models["AppEventAction"].model_validate(
        {"action_type": "event", "label": "Cancel", "event_type": "ui.modal.close", "modal_id": "edit-task-modal"}
    )
    assert action.modal_id == "edit-task-modal"
    # Optional, because not every ui.* event addresses a modal.
    assert models["AppEventAction"].model_validate(
        {"action_type": "event", "label": "Refresh", "event_type": "ui.datatable.refresh"}
    ).modal_id is None
