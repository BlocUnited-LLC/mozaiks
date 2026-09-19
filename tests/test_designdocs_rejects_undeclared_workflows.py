"""A workflow surface the concept never asked for is refused where it is written.

`#653` told DesignDocsAgent not to declare a `workflow` surface when the approved
concept records no agentic capabilities. Prompt guidance is not a contract: it
held for two runs and failed on the third. A tool-lending library whose concept
recorded `agentic_capabilities: []` produced

    module    tool_catalog
    workflow  borrow_requests
    ui_only   overdue_list

Nothing validated it, so it travelled three stages before `pattern_selection`
compared the workflow partition against the map:

    TOOL_OUTCOME_REJECTED tool=pattern_selection attempt=3/3
      reason=The canonical design surface map requires at least one declared AI workflow
    -> channel closed reason=workflow_failed

That failure is terminal and has no feedback path. Refusing it at the save
boundary turns it into a rejection DesignDocs can act on in the same run.
"""

from __future__ import annotations

import pytest

from factory_app.workflows.DesignDocs.tools.save_design_doc import (
    _reject_undeclared_workflow_surfaces,
)


def _map(*surfaces: tuple[str, str]) -> dict:
    return {"surfaces": [{"surface_id": sid, "surface_kind": kind} for sid, kind in surfaces]}


def test_the_live_failure_is_refused() -> None:
    surface_map = _map(("tool_catalog", "module"), ("borrow_requests", "workflow"), ("overdue_list", "ui_only"))
    with pytest.raises(ValueError) as err:
        _reject_undeclared_workflow_surfaces(surface_map, {"agentic_capabilities": []})
    message = str(err.value)
    assert "borrow_requests" in message, "the offending surface must be named"
    assert "module or ui_only" in message, "say what to do instead, not just what is wrong"


def test_a_concept_that_asked_for_ai_may_declare_workflows() -> None:
    surface_map = _map(("triage", "workflow"))
    _reject_undeclared_workflow_surfaces(surface_map, {"agentic_capabilities": ["request triage"]})


def test_no_workflow_surface_is_always_fine() -> None:
    surface_map = _map(("tool_catalog", "module"), ("overdue_list", "ui_only"))
    _reject_undeclared_workflow_surfaces(surface_map, {"agentic_capabilities": []})


def test_an_absent_capability_list_counts_as_no_ai() -> None:
    """#653's rule reads 'empty or absent'; keep the two halves consistent."""
    surface_map = _map(("borrow_requests", "workflow"))
    with pytest.raises(ValueError):
        _reject_undeclared_workflow_surfaces(surface_map, {"agentic_capabilities": None})


def test_a_concept_without_the_field_is_not_judged() -> None:
    """No signal is not the same as a signal saying no; do not invent authority."""
    surface_map = _map(("borrow_requests", "workflow"))
    _reject_undeclared_workflow_surfaces(surface_map, {"app_name": "Tool Lending Library"})
    _reject_undeclared_workflow_surfaces(surface_map, None)


def test_the_save_path_runs_the_check_before_persisting() -> None:
    import inspect

    from factory_app.workflows.DesignDocs.tools import save_design_doc

    source = inspect.getsource(save_design_doc.save_design_docs_bundle)
    assert "_reject_undeclared_workflow_surfaces" in source
    # Persisting first would put the bad map where downstream stages read it.
    assert source.index("_reject_undeclared_workflow_surfaces") < source.index("BuilderArtifactStore"), (
        "the check must run before the bundle is stored"
    )
