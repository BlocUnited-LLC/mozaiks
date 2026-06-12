from __future__ import annotations

from tests.import_utils import import_module_directly

_adapter_mod = import_module_directly("mozaiksai.core.workflow.context.adapter")
_derived_mod = import_module_directly("mozaiksai.core.workflow.context.derived")
_schema_mod = import_module_directly("mozaiksai.core.workflow.context.schema")


def _build_manager():
    plan = _schema_mod.load_context_variables_config(
        {
            "definitions": {
                "review_approved": {
                    "type": "boolean",
                    "source": {
                        "type": "state",
                        "default": False,
                        "triggers": [
                            {
                                "type": "user_text",
                                "match": {"contains": "approved"},
                            }
                        ],
                    },
                },
                "review_revision_requested": {
                    "type": "boolean",
                    "source": {
                        "type": "state",
                        "default": False,
                        "triggers": [
                            {
                                "type": "user_text",
                                "match": {"contains": "revise"},
                            }
                        ],
                    },
                },
            },
            "agents": {},
        }
    )
    context = _adapter_mod.create_context_container(initial={})
    setattr(context, "_mozaiks_context_definitions", plan.definitions)
    return _derived_mod.DerivedContextManager("FlowUserText", {}, context), context


def test_apply_user_text_updates_matching_state_variable() -> None:
    manager, context = _build_manager()

    updated = manager.apply_user_text("Approved. Proceed with implementation.")

    assert updated == {"review_approved": True}
    assert context.get("review_approved") is True
    assert context.get("review_revision_requested") is False


def test_apply_user_text_ignores_non_matching_reply() -> None:
    manager, context = _build_manager()

    updated = manager.apply_user_text("Share another diagram option before we continue.")

    assert updated == {}
    assert context.get("review_approved") is False
    assert context.get("review_revision_requested") is False

