"""Every transition type must be documented where authors will look for it.

`user_choice_context` and `user_choice_route` were valid values in the
`transition_type` Literal but appeared nowhere in the docstring that documents
every other type. The two types the factory journey actually uses were the two
nobody wrote down.

That is worse than a doc gap. An author reading `user_choice_context`
reasonably assumes the name selects behaviour -- that this type is what makes
the option seed context. It is not: the router branches only on `chat_session`,
and all three `user_choice*` variants are handled identically. The option's
`route_to` and `context_variables` do all the work. Someone relying on the type
to do something would write a gate that silently does nothing.
"""

from __future__ import annotations

import typing

from mozaiksai.core.workflow.pack.schema import WorkflowTransition


def _declared_transition_types() -> list[str]:
    annotation = WorkflowTransition.model_fields["transition_type"].annotation
    return [arg for arg in typing.get_args(annotation) if isinstance(arg, str)]


def test_every_declared_transition_type_is_documented() -> None:
    doc = WorkflowTransition.__doc__ or ""
    undocumented = [name for name in _declared_transition_types() if name not in doc]
    assert not undocumented, (
        "transition types are valid but undocumented, so an author cannot learn "
        f"what they do from the contract itself: {undocumented}"
    )


def test_the_choice_variants_are_marked_as_intent_not_behaviour() -> None:
    doc = WorkflowTransition.__doc__ or ""
    # If this ever becomes false, the docstring is lying and a gate author will
    # expect a code path that does not exist.
    assert "authoring intent" in doc, (
        "user_choice_context / user_choice_route must be documented as naming "
        "intent, not as selecting different runtime behaviour"
    )


def test_the_documented_claim_still_matches_the_router() -> None:
    """Guard the claim itself: only chat_session may be branched on."""
    import inspect

    from mozaiksai.core.workflow.pack import journey_orchestrator

    source = inspect.getsource(journey_orchestrator)
    branched = {
        name
        for name in _declared_transition_types()
        if f'transition_type == "{name}"' in source
    }
    assert branched <= {"chat_session"}, (
        "the docstring tells authors the router branches only on chat_session; "
        f"it now also branches on {sorted(branched - {'chat_session'})}"
    )
