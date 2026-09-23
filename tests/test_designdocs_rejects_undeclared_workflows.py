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

The guard then never ran. It tested `isinstance(concept_blueprint, dict)`, and
every live container freezes values on read, so production always handed it a
`MappingProxyType` and it returned early. The tests below passed throughout,
because they called it with plain dictionaries -- they certified the bug. The
cases at the end drive the real read path instead.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import MappingProxyType

import pytest
import yaml

from factory_app.workflows.DesignDocs.tools.save_design_doc import (
    _reject_undeclared_workflow_surfaces,
    save_design_docs_bundle,
)
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.frozen import freeze
from mozaiksai.core.workflow.context.structured_output_overlay import StructuredOutputOverlay

_DESIGN_DOCS = Path(__file__).resolve().parents[1] / "factory_app" / "workflows" / "DesignDocs"


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


# ---------------------------------------------------------------------------
# The read path production actually uses
# ---------------------------------------------------------------------------


def _live_concept(blueprint: dict) -> object:
    """What a tool really receives: a bridge read, frozen on the way out."""
    bridge = ContextVariablesBridge({"concept_blueprint": blueprint})
    return bridge.get("concept_blueprint")


def test_a_frozen_blueprint_is_still_judged() -> None:
    """The exact value the live container returns, not a plain dict."""
    live = _live_concept({"agentic_capabilities": []})
    assert isinstance(live, MappingProxyType), "guard the premise, not just the fix"
    assert not isinstance(live, dict), "a mappingproxy is why the dict test skipped every build"

    surface_map = _map(("billing_management", "workflow"))
    with pytest.raises(ValueError) as err:
        _reject_undeclared_workflow_surfaces(surface_map, live)
    assert "billing_management" in str(err.value)


def test_a_frozen_blueprint_that_asked_for_ai_still_passes() -> None:
    live = _live_concept({"agentic_capabilities": ["triage"]})
    _reject_undeclared_workflow_surfaces(_map(("triage", "workflow")), live)


def _bundle(*surfaces: tuple[str, str]) -> dict:
    return {
        "frontend_markdown": "# Frontend",
        "backend_markdown": "# Backend",
        "database_markdown": "# Database",
        "surface_map": _map(*surfaces),
        "experience_spec": {"pages": []},
        "data_contract": {"surfaces": []},
    }


def _live_context(blueprint: dict, bundle: dict) -> StructuredOutputOverlay:
    """A real bridge under a real overlay: every read goes through freeze()."""
    bridge = ContextVariablesBridge(
        {
            "run_build_binding": {
                "build_registry_id": "appreg_live",
                "target_app_id": "tool-lending-library",
                "build_id": "build_live",
                "phase": "genesis",
            },
            "concept_blueprint": blueprint,
            "chat_id": "chat-1",
            "user_id": "owner",
            # The runtime seeds declared state from context_variables.yaml; the
            # outcome wrapper refuses to run at all without a real attempt count.
            "design_docs_save_outcome": "blocked",
            "design_docs_save_attempts": 0,
            "design_docs_save_feedback": "",
        }
    )
    return StructuredOutputOverlay(bridge, bundle)


def test_the_save_path_refuses_a_workflow_surface_through_the_live_container() -> None:
    """End to end over the real read path, which is what #671 never exercised."""
    context = _live_context(
        {"agentic_capabilities": []},
        _bundle(("tool_catalog", "module"), ("billing_management", "workflow")),
    )

    result = asyncio.run(save_design_docs_bundle(context_variables=context))

    assert result["ok"] is False
    assert result["reason"] == "invalid_design_docs_bundle"
    assert "billing_management" in result["error"], "name the surface the agent must change"
    # Without a declared outcome the wrapper discards this payload entirely.
    # `revise` rather than `blocked`: the agent can fix this, and the outcome
    # contract refuses to retry on the error value.
    assert result["outcome"] == "revise"
    # The retry turn reads the reason from context, not from the return value.
    assert "billing_management" in str(context.get("design_docs_save_feedback"))


def test_the_save_path_accepts_a_concept_that_asked_for_ai() -> None:
    """The refusal must be about the concept, not about workflow surfaces at all."""
    context = _live_context(
        {"agentic_capabilities": ["billing triage"]},
        _bundle(("billing_management", "workflow")),
    )

    result = asyncio.run(save_design_docs_bundle(context_variables=context))

    # Persistence is out of scope here; what matters is which rejection it is.
    assert result.get("reason") != "invalid_design_docs_bundle" or (
        "billing_management" not in str(result.get("error") or "")
    )


# ---------------------------------------------------------------------------
# The rejection has to reach the agent, and the agent has to get another turn
# ---------------------------------------------------------------------------


def _yaml(name: str) -> dict:
    return yaml.safe_load((_DESIGN_DOCS / name).read_text(encoding="utf-8")) or {}


def _save_outcome_spec() -> dict:
    tools = _yaml("tools.yaml")
    entries = tools if isinstance(tools, list) else tools.get("tools") or []
    for entry in entries:
        if isinstance(entry, dict) and entry.get("function") == "save_design_docs_bundle":
            return entry.get("outcome") or {}
    raise AssertionError("save_design_docs_bundle is not declared in tools.yaml")


def test_a_rejection_survives_tool_outcome_validation() -> None:
    """A payload with no declared outcome is replaced, and the reason is lost."""
    from mozaiksai.core.workflow.declarative.contracts import ToolOutcomeSpec
    from mozaiksai.core.workflow.validation.tool_outcomes import wrap_tool_outcome

    spec = ToolOutcomeSpec.model_validate(_save_outcome_spec())
    context = _live_context(
        {"agentic_capabilities": []},
        _bundle(("billing_management", "workflow")),
    )
    wrapped = wrap_tool_outcome(save_design_docs_bundle, spec)

    result = asyncio.run(wrapped(context_variables=context))

    assert result.get("outcome_error") is None, "the payload must not be discarded"
    assert result["outcome"] == "revise"
    assert "billing_management" in result["error"]
    assert context.get(spec.context_key) == "revise"
    assert context.get(spec.attempts_key) == 1
    assert "billing_management" in str(context.get("design_docs_save_feedback"))


def test_the_retryable_outcome_is_not_the_error_value() -> None:
    """The contract forbids retrying on error_value, so the two must differ."""
    from mozaiksai.core.workflow.declarative.contracts import ToolOutcomeSpec

    spec = ToolOutcomeSpec.model_validate(_save_outcome_spec())
    assert spec.max_attempts > 1, "one attempt cannot act on feedback"
    assert spec.retry_on == ["revise"]
    assert spec.error_value == "blocked"
    assert spec.error_value not in spec.retry_on


def test_a_refused_save_gets_another_turn_and_then_stops() -> None:
    """max_attempts alone is not a retry: the graph has to route back."""
    rules = _yaml("transition_graph.yaml")["transition_rules"]
    conditional = [r for r in rules if r.get("transition_type") == "condition"]
    by_value = {
        r.get("condition_value"): r
        for r in conditional
        if r.get("condition_key") == "design_docs_save_outcome"
    }

    retry = by_value.get("revise")
    assert retry is not None, "the retryable outcome needs a route or the agent never retries"
    assert retry["target_agent"] == "DesignDocsAgent"
    assert retry["transition_target"] == "AgentTarget"

    terminal = by_value.get("blocked")
    assert terminal is not None, "an exhausted rejection must still terminate"
    assert terminal["termination_reason"] == "workflow_failed"

    saved = by_value.get("saved")
    assert saved is not None and saved["termination_reason"] == "workflow_complete"


def test_the_feedback_key_is_declared_and_visible_to_the_agent() -> None:
    context_vars = _yaml("context_variables.yaml")
    definitions = context_vars["definitions"]
    assert "design_docs_save_feedback" in definitions, "a tool cannot write an undeclared key"
    assert definitions["design_docs_save_feedback"]["type"] == "string"
    agent_vars = context_vars["agents"]["DesignDocsAgent"]["variables"]
    assert "design_docs_save_feedback" in agent_vars, "the agent cannot act on what it cannot see"


def test_the_concept_outranks_a_conflicting_hint() -> None:
    """A retry that re-reads the hints as authoritative would loop until exhausted."""
    prompt = (_DESIGN_DOCS / "agents.yaml").read_text(encoding="utf-8")
    assert "the concept wins" in prompt, "state which input wins when they conflict"
    assert "outranks `surface_candidate_hints[]`" in prompt


def test_freeze_of_a_blueprint_is_not_a_dict() -> None:
    """Pin the premise directly, so a change in freeze() surfaces here."""
    assert not isinstance(freeze({"agentic_capabilities": []}), dict)
    assert isinstance(freeze({"agentic_capabilities": []}), MappingProxyType)
