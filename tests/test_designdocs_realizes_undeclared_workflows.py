"""A workflow surface the concept never asked for is realized as the app surface it is.

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

`#708` refused it at the save boundary instead. That did not save the run
either: the live model changes its output at most once after a rejection and
then resubmits it unchanged until the run is blocked, and 7 recorded DesignDocs
outputs died on this refusal. The correction is determined by what the surface
declares, so the save constructs it: entities, mutations, reads or collections
of its own make it a `module`, anything else is `ui_only`, and the correction is
recorded.

The guard once never ran at all. It tested `isinstance(concept_blueprint, dict)`,
and every live container freezes values on read, so production always handed it
a `MappingProxyType` and it returned early. The cases at the end drive the real
read path instead of plain dictionaries.
"""

from __future__ import annotations

import inspect
import logging
from copy import deepcopy
from pathlib import Path
from types import MappingProxyType

import pytest
import yaml

from factory_app.workflows.DesignDocs.tools import save_design_doc
from factory_app.workflows.DesignDocs.tools.save_design_doc import (
    _realize_undeclared_workflow_surfaces,
)
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.frozen import detach, freeze
from tests import test_designdocs_capability_ownership as ownership
from tests import test_designdocs_monetization_inventory as inventory

persistence = inventory.persistence

_DESIGN_DOCS = Path(__file__).resolve().parents[1] / "factory_app" / "workflows" / "DesignDocs"


def _surface(surface_id: str, kind: str, **declared) -> dict:
    return {"surface_id": surface_id, "surface_kind": kind, "owner": "app", **declared}


def _live_failure() -> tuple[dict, dict]:
    surface_map = {"surfaces": [
        _surface("tool_catalog", "module", primary_entities=["Tool"], owned_mutations=["create_tool"]),
        _surface("borrow_requests", "workflow", primary_entities=["BorrowRequest"],
                 owned_mutations=["create_borrow_request"]),
        _surface("overdue_list", "ui_only"),
    ]}
    data_contract = {"surfaces": [
        {"surface_id": "borrow_requests", "surface_kind": "workflow", "collections": [{
            "name": "borrow_requests", "ownership": {"surface_id": "borrow_requests", "surface_kind": "workflow"},
        }]},
    ]}
    return surface_map, data_contract


def test_the_live_failure_is_realized_as_a_module() -> None:
    surface_map, data_contract = _live_failure()

    realized = _realize_undeclared_workflow_surfaces(surface_map, data_contract, {"agentic_capabilities": []})

    kinds = {surface["surface_id"]: surface["surface_kind"] for surface in surface_map["surfaces"]}
    assert kinds == {"tool_catalog": "module", "borrow_requests": "module", "overdue_list": "ui_only"}
    group = data_contract["surfaces"][0]
    assert group["surface_kind"] == "module", "data_contract mirrors the surface_map kind"
    assert group["collections"][0]["ownership"]["surface_kind"] == "module"
    assert realized == [{
        "surface_id": "borrow_requests", "owner": "app", "removed_collections": [],
        "realized_surface_kind": {"from": "workflow", "to": "module"},
    }]


@pytest.mark.parametrize("declared,expected", [
    pytest.param({"primary_entities": ["Request"]}, "module", id="entities"),
    pytest.param({"owned_mutations": ["approve_request"]}, "module", id="mutations"),
    pytest.param({"custom_reads": ["summarize_requests"]}, "module", id="reads"),
    pytest.param({}, "ui_only", id="presentation"),
])
def test_what_the_surface_declares_decides_its_kind(declared, expected) -> None:
    surface_map = {"surfaces": [_surface("requests", "workflow", **declared)]}

    _realize_undeclared_workflow_surfaces(surface_map, {"surfaces": []}, {"agentic_capabilities": []})

    assert surface_map["surfaces"][0]["surface_kind"] == expected


def test_a_collection_filed_elsewhere_but_owned_by_the_surface_makes_it_a_module() -> None:
    surface_map = {"surfaces": [_surface("reports", "module"), _surface("digest", "workflow")]}
    collection = {"name": "digests", "ownership": {"surface_id": "digest", "surface_kind": "workflow"}}
    data_contract = {"surfaces": [{"surface_id": "reports", "surface_kind": "module", "collections": [collection]}]}

    _realize_undeclared_workflow_surfaces(surface_map, data_contract, {"agentic_capabilities": []})

    assert surface_map["surfaces"][1]["surface_kind"] == "module"
    assert collection["ownership"]["surface_kind"] == "module"
    assert data_contract["surfaces"][0]["surface_kind"] == "module", "the reports group keeps its own kind"


def test_a_shared_collection_the_surface_owns_makes_it_a_module_and_takes_its_kind() -> None:
    surface_map = {"surfaces": [_surface("borrow_flow", "workflow")]}
    collection = {"name": "borrow_requests", "ownership": {"surface_id": "borrow_flow", "surface_kind": "workflow"}}
    data_contract = {"surfaces": [], "shared_collections": [collection]}

    _realize_undeclared_workflow_surfaces(surface_map, data_contract, {"agentic_capabilities": []})

    assert surface_map["surfaces"][0]["surface_kind"] == "module"
    assert collection["ownership"]["surface_kind"] == "module"


def test_a_realized_module_writes_what_the_workflow_would_have_written() -> None:
    """No workflow exists to write the records, so the module does."""
    surface_map = {"surfaces": [_surface("borrow_flow", "workflow", primary_entities=["BorrowRequest"])]}
    collection = {
        "name": "borrow_requests", "ownership": {"surface_id": "borrow_flow", "surface_kind": "workflow"},
        "lifecycle": {"write_mode": "workflow_write", "migration_policy": "additive_only"},
    }
    data_contract = {"surfaces": [{"surface_id": "borrow_flow", "surface_kind": "workflow", "collections": [collection]}]}

    realized = _realize_undeclared_workflow_surfaces(surface_map, data_contract, {"agentic_capabilities": []})

    assert collection["lifecycle"]["write_mode"] == "module_action"
    assert realized[0]["module_written_collections"] == ["borrow_requests"]


def test_a_realized_module_drops_custom_reads_that_are_now_canonical(persistence) -> None:
    _, _, summary = persistence
    context = inventory._context(monetized=False)
    bundle = _workflow_bundle()
    bundle["surface_map"]["surfaces"][0]["custom_reads"] = ["list_reports", "summarize_reports"]
    bundle["data_contract"]["surfaces"][0]["collections"][0].update(
        tenancy="per_user", owner_field="user_id",
        fields=[{"name": "user_id", "type": "string", "required": True, "default": None, "enum": None, "nullable": False}],
    )

    result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    assert detach(context.get("design_surface_map"))["surfaces"][0]["custom_reads"] == ["summarize_reports"]
    record = next(
        entry for entry in summary.await_args.kwargs["summary_payload"]["ownership_normalizations"]
        if entry.get("realized_surface_kind")
    )
    assert record["removed_reads"] == ["list_reports"]


def test_a_concept_that_asked_for_ai_keeps_its_workflows() -> None:
    surface_map = {"surfaces": [_surface("triage", "workflow", owned_mutations=["triage"])]}
    assert _realize_undeclared_workflow_surfaces(
        surface_map, {"surfaces": []}, {"agentic_capabilities": ["request triage"]},
    ) == []
    assert surface_map["surfaces"][0]["surface_kind"] == "workflow"


def test_a_null_capability_list_counts_as_no_ai() -> None:
    """ValueEngine records no AI as an empty or null agentic_capabilities; both realize the surface."""
    surface_map = {"surfaces": [_surface("borrow_requests", "workflow")]}
    _realize_undeclared_workflow_surfaces(surface_map, {"surfaces": []}, {"agentic_capabilities": None})
    assert surface_map["surfaces"][0]["surface_kind"] == "ui_only"


def test_a_concept_without_the_field_is_not_judged() -> None:
    """No signal is not the same as a signal saying no; do not invent authority."""
    surface_map = {"surfaces": [_surface("borrow_requests", "workflow")]}
    assert _realize_undeclared_workflow_surfaces(surface_map, {}, {"app_name": "Tool Lending Library"}) == []
    assert _realize_undeclared_workflow_surfaces(surface_map, {}, None) == []
    assert surface_map["surfaces"][0]["surface_kind"] == "workflow"


def test_the_save_path_realizes_before_anything_is_persisted() -> None:
    source = inspect.getsource(save_design_doc.save_design_docs_bundle)
    assert "_realize_undeclared_workflow_surfaces" in source
    # Persisting first would put the unrealized map where downstream stages read it.
    assert source.index("_realize_undeclared_workflow_surfaces") < source.index("BuilderArtifactStore")


# ---------------------------------------------------------------------------
# The read path production actually uses
# ---------------------------------------------------------------------------


def _live_concept(blueprint: dict) -> object:
    """What a tool really receives: a bridge read, frozen on the way out."""
    bridge = ContextVariablesBridge({"concept_blueprint": blueprint})
    return bridge.get("concept_blueprint")


def test_a_frozen_blueprint_is_still_judged() -> None:
    live = _live_concept({"agentic_capabilities": []})
    assert isinstance(live, MappingProxyType), "guard the premise, not just the fix"
    assert not isinstance(live, dict), "a mappingproxy is why the dict test skipped every build"

    surface_map = {"surfaces": [_surface("billing_management", "workflow")]}
    _realize_undeclared_workflow_surfaces(surface_map, {"surfaces": []}, live)
    assert surface_map["surfaces"][0]["surface_kind"] == "ui_only"


def _workflow_bundle() -> dict:
    bundle = inventory._bundle(pricing=False)
    bundle["surface_map"]["surfaces"][0]["surface_kind"] = "workflow"
    bundle["surface_map"]["surfaces"][0]["owned_mutations"] = ["create_report"]
    bundle["data_contract"]["surfaces"][0]["surface_kind"] = "workflow"
    collection = ownership._collection("reports", "reports")
    collection.update(entity="Report", ownership={"surface_id": "reports", "surface_kind": "workflow"})
    bundle["data_contract"]["surfaces"][0]["collections"] = [collection]
    return bundle


def test_the_save_path_saves_the_realized_module_through_the_live_container(persistence, caplog) -> None:
    """End to end over the real read path, which is what #671 never exercised."""
    _, _, summary = persistence
    context = inventory._context(monetized=False)
    bundle = _workflow_bundle()
    submitted = deepcopy(bundle)

    with caplog.at_level(logging.INFO):
        result = inventory._save(context, bundle)

    assert result["outcome"] == "saved", result
    assert bundle == submitted, "the submitted bundle is not rewritten in place"
    surface = detach(context.get("design_surface_map"))["surfaces"][0]
    assert (surface["surface_id"], surface["surface_kind"]) == ("reports", "module")
    group = detach(context.get("data_contract"))["surfaces"][0]
    assert group["surface_kind"] == "module"
    assert group["collections"][0]["ownership"]["surface_kind"] == "module"
    record = {
        "surface_id": "reports", "owner": "app", "removed_collections": [],
        "realized_surface_kind": {"from": "workflow", "to": "module"},
    }
    assert record in summary.await_args.kwargs["summary_payload"]["ownership_normalizations"]
    assert "surface=reports owner=app removed=[] surface_kind=workflow->module" in caplog.text


def test_the_save_path_keeps_a_workflow_the_concept_asked_for(persistence) -> None:
    context = inventory._context(monetized=False)
    blueprint = detach(context.get("concept_blueprint"))
    blueprint["agentic_capabilities"] = ["report drafting"]
    context.set("concept_blueprint", blueprint)

    result = inventory._save(context, _workflow_bundle())

    assert result["outcome"] == "saved", result
    assert detach(context.get("design_surface_map"))["surfaces"][0]["surface_kind"] == "workflow"


# ---------------------------------------------------------------------------
# A genuine rejection still reaches the agent, and the agent gets another turn
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


def test_a_rejection_survives_tool_outcome_validation(persistence) -> None:
    """A payload with no declared outcome is replaced, and the reason is lost."""
    from mozaiksai.core.workflow.declarative.contracts import ToolOutcomeSpec

    spec = ToolOutcomeSpec.model_validate(_save_outcome_spec())
    context = inventory._context(monetized=False)
    bundle = _workflow_bundle()
    bundle["experience_spec"]["pages"] = []

    result = inventory._save(context, bundle)

    assert result.get("outcome_error") is None, "the payload must not be discarded"
    assert result["outcome"] == "revise"
    assert "experience_spec.pages" in result["error"]
    assert context.get(spec.context_key) == "revise"
    assert context.get(spec.attempts_key) == 1
    assert "experience_spec.pages" in str(context.get("design_docs_save_feedback"))


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


def test_the_prompt_states_the_construction() -> None:
    """The prompt and the save agree: the concept wins, and the save realizes the kind."""
    prompt = " ".join((_DESIGN_DOCS / "agents.yaml").read_text(encoding="utf-8").split())
    assert "the concept wins" in prompt, "state which input wins when they conflict"
    assert "outranks `surface_candidate_hints[]`" in prompt
    assert "The save boundary therefore realizes a `workflow` surface" in prompt
    assert "is refused again" not in prompt, "the save no longer refuses it"


def test_freeze_of_a_blueprint_is_not_a_dict() -> None:
    """Pin the premise directly, so a change in freeze() surfaces here."""
    assert not isinstance(freeze({"agentic_capabilities": []}), dict)
    assert isinstance(freeze({"agentic_capabilities": []}), MappingProxyType)
