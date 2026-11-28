"""Immutable context inputs may use only a declared router-owned saved snapshot."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from mozaiksai.core.workflow.context import variables
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.context.schema import (
    ContextVariableSource,
    load_context_variables_config,
)


def plan(root_overrides=None):
    root = {"type": "object", "authority_class": "closed_writer_routing_state", "persisted": True,
            "writer_ids": ["transition_router"], "source": {"type": "state", "default": None}}
    root.update(root_overrides or {})
    # The authority is deliberately after the reference: YAML order cannot broaden its lookup.
    return load_context_variables_config({"definitions": {
        "design": {"type": "object", "authority_class": "immutable_runtime_authority", "authorization": True,
                   "source": {"type": "data_reference", "collection": "Designs", "snapshot": {"context_key": "selected_input", "path": "context.design"}}},
        "selected_input": root,
    }})


async def load(monkeypatch, snapshot, root_overrides=None):
    parsed = plan(root_overrides)
    monkeypatch.setattr(variables, "_load_workflow_plan", lambda _: (parsed, {}))
    monkeypatch.setattr(variables, "_load_data_reference_fallbacks", lambda: {"global": {"design": {"name": "fallback-must-not-win"}}})
    adapter = SimpleNamespace(fetch_one=AsyncMock(return_value={"name": "latest-must-not-win"}))
    monkeypatch.setattr(variables, "get_db_adapter", lambda _: adapter)
    context = await variables._load_context_async("SelectedInputTest", "host", persisted_context={"selected_input": snapshot})
    return context, adapter


@pytest.mark.asyncio
async def test_selected_snapshot_precedes_latest_and_is_detached(monkeypatch):
    snapshot = {"context": {"design": {"name": "selected", "roles": ["approved"]}}}
    context, adapter = await load(monkeypatch, snapshot)
    snapshot["context"]["design"]["roles"].append("forged")
    assert detach(context.get("design")) == {"name": "selected", "roles": ["approved"]}
    adapter.fetch_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_snapshot_null_suppresses_global_and_config_fallback(monkeypatch):
    context, adapter = await load(monkeypatch, {"context": {"design": None}})
    assert context.get("design") is None
    adapter.fetch_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_snapshot_retains_existing_data_reference_path(monkeypatch):
    context, adapter = await load(monkeypatch, None)
    assert context.get("design") == {"name": "latest-must-not-win"}
    adapter.fetch_one.assert_awaited_once()


@pytest.mark.asyncio
async def test_malformed_snapshot_never_falls_back_to_newer_input(monkeypatch):
    with pytest.raises(ValueError, match="no projection"):
        await load(monkeypatch, {"context": {}})


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {"authority_class": "mutable_workflow_state"}, {"persisted": False},
    {"writer_ids": ["transition_router", "deterministic_tool"]}, {"writer_ids": ["structured_output"]},
    {"source": {"type": "computed"}},
])
async def test_snapshot_root_cannot_be_model_or_tool_writable(monkeypatch, change):
    with pytest.raises(ValueError):
        await load(monkeypatch, {"context": {"design": {"name": "forged"}}}, change)


def test_snapshot_source_contract_is_closed_and_data_reference_only():
    source = {"type": "data_reference", "snapshot": {"context_key": "selected_input", "path": "context.design"}}
    assert ContextVariableSource.model_validate(source).snapshot.path == "context.design"
    for changed in [dict(source, type="state"), {**source, "snapshot": {**source["snapshot"], "path": "context/secret"}},
                    {**source, "snapshot": {**source["snapshot"], "extra": "forged"}}]:
        with pytest.raises(ValidationError):
            ContextVariableSource.model_validate(deepcopy(changed))


@pytest.mark.parametrize("trigger_source", ["transition", "manual", "refinement"])
def test_http_trigger_label_cannot_forge_router_owned_snapshot(monkeypatch, trigger_source):
    from fastapi import HTTPException

    from mozaiksai.core.auth import UserPrincipal
    from mozaiksai.core.workflow.workflow_manager import workflow_manager
    from mozaiksai.hosts import studio
    from mozaiksai.hosts.routers import transitions
    from mozaiksai.hosts.source_path_policy import authorize_http_workflow_source_paths

    config = {"context_variables": {"definitions": {"design": {"source": {
        "type": "data_reference", "snapshot": {"context_key": "selected_input", "path": "context.design"},
    }}}}}
    monkeypatch.setattr(workflow_manager, "list_workflows", lambda: ["SelectedInputTest"])
    monkeypatch.setattr(workflow_manager, "get_config", lambda _: config)
    caller = UserPrincipal(user_id="owner", app_id="host", roles=["admin"], email=None, name=None, scopes=[], raw_claims={})
    context = {"selected_input": {"context": {"design": {"roles": ["forged-admin"]}}}}
    # Both HTTP entrypoints use the same boundary before any route/trigger-source processing.
    workflow = studio.WorkflowTriggerRequest(trigger_source=trigger_source, context_variables=context)
    transition = transitions.TransitionResolveRequest(transition_id="next_step", context_variables=context)
    for body in (workflow, transition):
        with pytest.raises(HTTPException) as failure:
            authorize_http_workflow_source_paths(body.context_variables, principal=caller)
        assert failure.value.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("root_first", [False, True])
async def test_state_default_cannot_masquerade_as_persisted_snapshot(monkeypatch, root_first):
    parsed = plan({"source": {"type": "state", "default": {"context": {"design": {"name": "untrusted-default"}}}}})
    if root_first:
        parsed.definitions = dict(reversed(list(parsed.definitions.items())))
    monkeypatch.setattr(variables, "_load_workflow_plan", lambda _: (parsed, {}))
    monkeypatch.setattr(variables, "_load_data_reference_fallbacks", lambda: {})
    adapter = SimpleNamespace(fetch_one=AsyncMock(return_value={"name": "stored-reference"}))
    monkeypatch.setattr(variables, "get_db_adapter", lambda _: adapter)
    context = await variables._load_context_async("SelectedInputTest", "host", persisted_context={})
    assert detach(context.get("design")) == {"name": "stored-reference"}
    assert context._mozaiks_snapshot_roots == frozenset()
    adapter.fetch_one.assert_awaited_once()


@pytest.mark.parametrize("key", ["selected_input", "ui_selected_input"])
def test_frontend_context_cannot_seed_declared_snapshot_root(key):
    from mozaiksai.core.workflow.orchestration_patterns import _require_frontend_context_scope

    config = {"context_variables": {"definitions": {"design": {"source": {
        "type": "data_reference", "snapshot": {"context_key": "ui_selected_input", "path": "context.design"},
    }}}}}
    with pytest.raises(ValueError, match="cannot be supplied by frontend context"):
        _require_frontend_context_scope({key: {"context": {"design": {"role": "forged"}}}}, config)
    _require_frontend_context_scope({"color": "teal"}, config)
    _require_frontend_context_scope({key: "ordinary-ui-input"}, {})
