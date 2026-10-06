"""Human feedback contracts, authenticated receipt persistence, and generation."""
from __future__ import annotations

import ast
import json
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from factory_app.workflows.AgentGenerator.tools.workflow_quality_gate import (
    _validate_feedback_implementation,
)
from mozaiksai.core.workflow.outcome_feedback import (
    WorkflowFeedbackEvidence,
    WorkflowFeedbackResponse,
)
from tests import test_ui_response_ownership as response_ownership

harness = response_ownership.harness
_http = response_ownership._http
_pending = response_ownership._pending


@pytest.mark.parametrize("response", [
    {"status": "submitted", "rating": 1}, {"status": "submitted", "rating": 5},
    {"status": "submitted", "helpful": False}, {"status": "submitted", "outcome": "unknown"},
    {"status": "skipped"},
])
def test_valid_feedback_preserves_missing_answers(response):
    parsed = WorkflowFeedbackResponse.model_validate(response)
    assert parsed.model_dump(exclude_none=True) == response


@pytest.mark.parametrize("response", [
    {"status": "submitted"}, {"status": "submitted", "rating": 0},
    {"status": "submitted", "rating": 6}, {"status": "submitted", "rating": True},
    {"status": "submitted", "rating": "5"}, {"status": "submitted", "rating": 4.5},
    {"status": "submitted", "helpful": "yes"}, {"status": "skipped", "rating": 5},
    {"status": "submitted", "outcome": "verified"},
    {"status": "submitted", "rating": 5, "user_id": "other"},
    {"status": "submitted", "rating": 5, "ui_event_id": "forged"},
    {"status": "submitted", "rating": 5, "comment": "private text"},
    {"status": "submitted", "rating": 5, "evidence_kind": "simulation"},
])
def test_invalid_or_attributed_browser_feedback_is_rejected(response):
    with pytest.raises(ValidationError):
        WorkflowFeedbackResponse.model_validate(response)


def _feedback_pending(harness):
    _pending(harness)
    harness.transport._ui_tool_metadata["evt-owned"].update(
        workflow_primitive="outcome_feedback", workflow_name="AnswerFlow",
        feedback_agent_name="AnswerAgent", outcome_id="result-1",
    )
    pm = harness.transport._get_or_create_persistence_manager()
    pm.save_workflow_feedback_receipt = AsyncMock(return_value=True)
    return pm


async def test_feedback_http_persists_scope_and_response_before_releasing_waiter(harness):
    pm = _feedback_pending(harness)
    future = harness.transport.pending_tool_call_responses["evt-owned"]

    async def save(receipt):
        assert not future.done()
        assert WorkflowFeedbackEvidence.model_validate(receipt).response.helpful is False
        assert {key: receipt[key] for key in (
            "app_id", "chat_id", "user_id", "workflow_name", "agent_name", "outcome_id", "ui_event_id",
        )} == dict(app_id="app-owner", chat_id="chat-owner", user_id="owner", workflow_name="AnswerFlow",
                  agent_name="AnswerAgent", outcome_id="result-1", ui_event_id="evt-owned")
        return True

    pm.save_workflow_feedback_receipt.side_effect = save
    response = await _http(harness, response_data={"status": "submitted", "rating": 2, "helpful": False})
    assert response.status_code == 200
    assert future.result()["rating"] == 2
    # Changing an answer on a retry cannot replace the recorded receipt.
    assert (await _http(harness, response_data={"status": "submitted", "rating": 5})).status_code == 200
    pm.save_workflow_feedback_receipt.assert_awaited_once()


@pytest.mark.parametrize("fault", ["invalid", "forged", "unavailable", "missing_session"])
async def test_feedback_rejection_keeps_interaction_pending(harness, fault):
    pm = _feedback_pending(harness)
    response_data = {"status": "submitted", "rating": 4}
    if fault == "invalid":
        response_data["rating"] = 8
    elif fault == "forged":
        response_data["app_id"] = "other"
    elif fault == "unavailable":
        pm.save_workflow_feedback_receipt.side_effect = RuntimeError("storage unavailable")
    else:
        pm.save_workflow_feedback_receipt.return_value = False
    assert (await _http(harness, response_data=response_data)).status_code == 404
    assert not harness.transport.pending_tool_call_responses["evt-owned"].done()
    assert not harness.transport._is_tool_call_response_resolved("evt-owned")


def _evidence(**updates):
    return WorkflowFeedbackEvidence(
        app_id="app-owner", chat_id="chat-owner", user_id="owner", workflow_name="AnswerFlow",
        agent_name="AnswerAgent", outcome_id="result-1", ui_event_id="evt-owned",
        observed_at="2026-10-06T12:00:00+00:00",
        response=WorkflowFeedbackResponse(status="submitted", rating=4), **updates,
    )


async def test_collector_uses_runtime_identity_and_only_accepts_persisted_receipt(monkeypatch):
    from mozaiksai.core.workflow import outcome_feedback, ui_tools
    from mozaiksai.core.workflow.agents import factory

    identity = ("AnswerFlow", "app-owner", "chat-owner", "owner")
    monkeypatch.setattr(factory, "active_workflow_tool_run", lambda: identity)
    prompt = AsyncMock(return_value={"status": "submitted", "rating": 4, "ui_event_id": "evt-owned"})
    monkeypatch.setattr(ui_tools, "use_ui_tool", prompt)
    resolver = AsyncMock(return_value=_evidence())
    monkeypatch.setattr(outcome_feedback, "resolve_workflow_feedback", resolver)
    context = dict(zip(("workflow_name", "app_id", "chat_id", "user_id"), identity, strict=True))
    result = await outcome_feedback.collect_workflow_feedback(
        context, tool_id="rate_result", agent_name="AnswerAgent", outcome_id="result-1",
    )
    assert result == _evidence()
    assert prompt.await_args.args[1] == {"outcome_id": "result-1", "agent_name": "AnswerAgent"}
    for receipt in (None, _evidence().model_copy(update={"workflow_name": "OtherFlow"})):
        resolver.return_value = receipt
        with pytest.raises(PermissionError, match="receipt_missing"):
            await outcome_feedback.collect_workflow_feedback(
                context, tool_id="rate_result", agent_name="AnswerAgent", outcome_id="result-1",
            )
    context["user_id"] = "attacker"
    with pytest.raises(PermissionError, match="scope_mismatch"):
        await outcome_feedback.collect_workflow_feedback(
            context, tool_id="rate_result", agent_name="AnswerAgent", outcome_id="result-1",
        )
    assert prompt.await_count == 3


async def test_public_resolver_exact_scopes_the_receipt(monkeypatch):
    from mozaiksai.core.data import persistence
    from mozaiksai.core.workflow.outcome_feedback import resolve_workflow_feedback

    read = AsyncMock(return_value=_evidence().model_dump(mode="json"))
    monkeypatch.setattr(persistence, "AG2PersistenceManager", lambda: SimpleNamespace(get_workflow_feedback_receipt=read))
    scope = dict(app_id="app-owner", chat_id="chat-owner", user_id="owner", ui_event_id="evt-owned", outcome_id="result-1")
    assert await resolve_workflow_feedback(**scope) == _evidence()
    for field in scope:
        assert await resolve_workflow_feedback(**{**scope, field: "other"}) is None
    read.assert_awaited()


_TOOL = '''from mozaiksai.core.workflow.outcome_feedback import collect_workflow_feedback
from mozaiksai.core.workflow.module_tools import dispatch_workflow_module_action

async def rate_result(context_variables):
    evidence = await collect_workflow_feedback(context_variables, tool_id="rate_result",
        agent_name="AnswerAgent", outcome_id=context_variables.get("result_id"))
    result = await dispatch_workflow_module_action("outcome_feedback", "record_workflow_feedback",
        {"ui_event_id": evidence.ui_event_id, "outcome_id": evidence.outcome_id})
    return result
'''


def test_generator_validates_reused_feedback_tool_and_rejects_fabricated_ratings():
    binding = {"ui": {"workflow_primitive": "outcome_feedback", "component": "OutcomeFeedback", "realization": "shipped_component"}}
    parsed = ast.parse(_TOOL)
    assert _validate_feedback_implementation("tools/rate.py", parsed, parsed.body[-1], binding) == []
    for bad in (
        _TOOL.replace("def rate_result(context_variables)", "def rate_result(context_variables, rating)"),
        _TOOL.replace("await collect_workflow_feedback(context_variables", "dict(context_variables"),
        _TOOL.replace("await dispatch_workflow_module_action(", "dict("),
    ):
        parsed = ast.parse(bad)
        assert _validate_feedback_implementation("tools/rate.py", parsed, parsed.body[-1], binding)


def test_pack_and_active_builder_agree_on_feedback_owner():
    import yaml

    root = Path(__file__).parents[1]
    context_root = root / "factory_app/build_context/outcome_feedback"
    contract = yaml.safe_load((context_root / "contract.yaml").read_text())
    assert all((context_root / "templates" / item["path"]).is_file() for item in contract["required_outputs"])
    module = yaml.safe_load((context_root / "templates/modules/outcome_feedback/module.yaml").read_text())
    record = next(action for action in module["actions"] if action["id"] == "record_workflow_feedback")
    assert set(record["input_schema"]["properties"]) == {"ui_event_id", "outcome_id"}
    from factory_app.workflows.AgentGenerator.tools.hook_primitive_catalog import (
        inject_primitive_catalog,
    )
    agent = SimpleNamespace(name="WorkflowBundleBuilderAgent", _system_message="Generate approved workflow")
    inject_primitive_catalog(agent, [])
    assert "outcome_feedback" in agent._system_message
    assert "collect_workflow_feedback" in agent._system_message


async def test_receipt_storage_is_immutable_and_scoped(monkeypatch):
    from mozaiksai.core.data.persistence import AG2PersistenceManager

    collection = SimpleNamespace(update_one=AsyncMock(return_value=SimpleNamespace(matched_count=1)))
    pm = AG2PersistenceManager()
    monkeypatch.setattr(pm, "_coll", AsyncMock(return_value=collection))
    receipt = _evidence().model_dump(mode="json")
    assert await pm.save_workflow_feedback_receipt(receipt)
    query, update = collection.update_one.await_args.args
    assert query["_id"] == "chat-owner" and query["user_id"] == "owner" and query["app_id"] == "app-owner"
    path = next(iter(update["$set"]))
    assert query[path] == {"$exists": False}
    collection.update_one.return_value.matched_count = 0
    monkeypatch.setattr(pm, "get_workflow_feedback_receipt", AsyncMock(return_value=deepcopy(receipt)))
    assert await pm.save_workflow_feedback_receipt({**receipt, "observed_at": "later"})
    assert not await pm.save_workflow_feedback_receipt({**receipt, "response": {"status": "submitted", "rating": 5}})


@pytest.mark.parametrize("failure,existing_kind,duplicate", [
    ("duplicate", "matching", True),
    ("duplicate", "missing", False),
    ("duplicate", "conflicting", False),
    ("connection", "matching", False),
])
async def test_generated_repository_recovers_only_typed_scoped_uniqueness_conflicts(failure, existing_kind, duplicate):
    from pymongo.errors import AutoReconnect, DuplicateKeyError

    from mozaiksai.core.runtime.persistence import is_unique_constraint_violation

    error = DuplicateKeyError("conflict") if failure == "duplicate" else AutoReconnect("unavailable")
    assert is_unique_constraint_violation(error) is (failure == "duplicate")
    assert not is_unique_constraint_violation(RuntimeError("duplicate key"))
    source = Path(__file__).parents[1] / "factory_app/build_context/outcome_feedback/templates/modules/outcome_feedback/backend/repo.py"
    namespace = {}
    exec(compile(source.read_text(), str(source), "exec"), namespace)
    record = {"feedback_id": "owned-id", "app_id": "app", "chat_id": "chat", "user_id": "owner", "outcome_id": "result"}
    existing = None if existing_kind == "missing" else {**record, **({"outcome_id": "other"} if existing_kind == "conflicting" else {})}
    rows = SimpleNamespace(update_one=AsyncMock(side_effect=error), find_one=AsyncMock(return_value=existing))
    ctx = SimpleNamespace(user_id="owner", persistence=SimpleNamespace(collection=lambda *_: rows))
    if duplicate:
        assert await namespace["insert_once"](ctx, record=record) is False
        rows.find_one.assert_awaited_once_with({"feedback_id": "owned-id", "user_id": "owner"})
    else:
        with pytest.raises(type(error)):
            await namespace["insert_once"](ctx, record=record)
        if failure == "connection":
            rows.find_one.assert_not_awaited()


async def _compile_feedback_app():
    """Run production assembly with a selected pack and no model-owned feedback paths."""
    from factory_app.workflows.AppGenerator.tools.assemble_app_tasks import assemble_app_tasks
    from tests.factory_context import factory_context

    collection = {
        "name": "records", "entity": "WorkflowFeedback", "scope": "app", "tenancy": "per_user",
        "owner_field": "user_id", "ownership": {"surface_id": "outcome_feedback", "surface_kind": "module"},
        "fields": [
            {"name": field, "type": "object" if field == "response" else "string", "required": True}
            for field in [*WorkflowFeedbackEvidence.model_fields, "feedback_id"]
        ],
        "lifecycle": {"write_mode": "workflow_write", "migration_policy": "additive_only"},
        "search_by": "feedback_id", "indexes": [],
    }
    contract = {"version": "1", "surfaces": [{
        "surface_id": "outcome_feedback", "surface_kind": "module", "collections": [collection],
    }], "shared_collections": []}
    plan = {"capability_packs": [{
        "capability_pack_id": "outcome_feedback", "capability_source": "generated_module",
        "primary_entities": ["WorkflowFeedback"],
    }]}
    root = Path(__file__).parents[1] / "factory_app/build_context/outcome_feedback"

    class Context(dict):
        def set(self, key, value):
            self[key] = value

    context = Context(factory_context({
        "app_id": "feedback-fixture", "app_build_plan": plan, "data_contract": contract,
        "build_timestamp": "2026-10-06T12:00:00Z",
        "capability_packs": [{"id": "outcome_feedback", "capability_source": "generated_module", "pack_source_path": str(root)}],
        "generated_files": {"data/contract.json": json.dumps(contract)},
    }))
    result = await assemble_app_tasks(context_variables=context)
    assert result["success"], result
    files = {item["filename"]: item["content"] for item in result["code_files"]}
    return files, json.loads(files["data/contract.json"])


async def test_feedback_pack_compiles_with_canonical_ownership_and_no_arbitrary_evidence_crud(tmp_path, monkeypatch):
    import yaml

    from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import (
        _scan_outcome_feedback_contract,
    )
    from mozaiksai.core.account import account_data_registry
    from mozaiksai.core.runtime.app.module_loader import ModuleLoader

    files, contract = await _compile_feedback_app()
    assert _scan_outcome_feedback_contract(files) == []
    actions = yaml.safe_load(files["modules/outcome_feedback/module.yaml"])["actions"]
    assert {action["id"] for action in actions} == {"record_workflow_feedback", "list_my_feedback", "get_records", "list_records"}
    assert "def scoped_query" in files["modules/outcome_feedback/backend/policy.py"]
    assert "class WorkflowFeedbackRecord" in files["modules/outcome_feedback/backend/schemas.py"]
    assert "class AccountDataHandler" in files["modules/outcome_feedback/backend/account_data_handler.py"]
    for filename, content in files.items():
        target = tmp_path / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    package_prefix = "mozaiks_runtime_module_outcome_feedback"
    existing_modules = {key: value for key, value in sys.modules.items() if key.startswith(package_prefix)}
    monkeypatch.setattr(account_data_registry, "_handlers", dict(account_data_registry._handlers))
    try:
        loaded = ModuleLoader(str(tmp_path)).load("outcome_feedback")
        assert loaded.definition.module.user_data_scope
        assert set(loaded.action_method_map) == {action["id"] for action in actions}
        assert all(callable(getattr(loaded.handler, method)) for method in loaded.action_method_map.values())
        assert "outcome_feedback" in account_data_registry._handlers
    finally:
        for name in list(sys.modules):
            if name.startswith(package_prefix):
                sys.modules.pop(name)
        sys.modules.update(existing_modules)
    contract["surfaces"][0]["collections"][0]["lifecycle"]["write_mode"] = "module_action"
    assert any("arbitrary evidence CRUD" in error for error in _scan_outcome_feedback_contract({
        **files, "data/contract.json": json.dumps(contract),
    }))
    assert _scan_outcome_feedback_contract({key: value for key, value in files.items() if key != "data/contract.json"})


@pytest.mark.parametrize("writer", ["workflow_write", "platform_sync", "module_action"])
def test_structured_evidence_is_required_only_from_capable_writers(writer):
    from mozaiksai.core.workflow.generator_support.data_contract_fields import (
        DataContractFieldError,
        validate_collection_fields,
    )

    collection = {"fields": [{"name": "response", "type": "object", "required": True}],
                  "lifecycle": {"write_mode": writer}}
    if writer == "module_action":
        with pytest.raises(DataContractFieldError, match="canonical create input cannot carry"):
            validate_collection_fields(collection, "feedback")
    else:
        validate_collection_fields(collection, "feedback")
