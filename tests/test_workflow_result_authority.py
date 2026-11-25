from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager
from mozaiksai.core.runtime.app.module_loader import ModuleLoader
from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor
from mozaiksai.core.runtime.composition.workflow_result import (
    bind_result_event,
    issue_workflow_result_grant,
)
from mozaiksai.core.runtime.persistence.request_scope import bind_persistence_principal
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge, _workflow_tool_invocation
from mozaiksai.core.workflow.context.authority import build_context_authority_policy
from mozaiksai.core.workflow.context.structured_output_overlay import StructuredOutputOverlay
from mozaiksai.core.workflow.module_tools import commit_workflow_result
from tests.test_module_reaction_result_write import bundle as bundle
from tests.test_workflow_result_persistence import _OWNER, fake_collection

_RUN = ("ReviewWorkflow", "app", "chat-1", "owner")
_EVENT = ("app", "tasks", "create", "domain.tasks.task_created", "evt-1")


def _source(loaded):
    workflow, app_id, chat_id, actor = _RUN
    _, _, _, event_type, event_id = _EVENT
    reaction = loaded.manifests.reactions.reactions[0]
    return {
        "app_id": app_id, "user_id": actor, "workflow": workflow, "chat_id": chat_id,
        "capability_id": reaction.target.capability_id, "invocation_id": "inv-1",
        "source_event": {"type": event_type, "id": event_id, "tenant": {"tenant_id": "tenant", "workspace_id": "workspace"},
                         "payload": {"task_id": "task-1"}},
        "subscription": {"module_id": "tasks", **reaction.model_dump(mode="json", exclude_none=True)},
    }


@pytest.mark.parametrize("case", ["legacy_trigger", "missing_insert", "wrong_actor", "other_record", "wrong_event"])
async def test_only_matching_live_module_insert_and_event_can_issue_result_authority(bundle, tmp_path, case):
    *_, write = bundle
    write()
    source = _source(ModuleLoader(str(tmp_path)).load("tasks"))
    if case == "legacy_trigger":
        with pytest.raises(PermissionError):
            issue_workflow_result_grant(**source)
        return
    rows, _ = fake_collection()
    with bind_persistence_principal("app", _OWNER, module_id="tasks", action_id="create", result_record_key="task_id"):
        if case != "missing_insert":
            await rows.insert_one({"task_id": "task-1"})
        with bind_result_event(*_EVENT):
            if case == "other_record":
                source["source_event"]["payload"]["task_id"] = "task-2"
            elif case == "wrong_actor":
                source["user_id"] = "another-user"
            elif case == "wrong_event":
                source["source_event"]["id"] = "different-event"
            with pytest.raises(PermissionError):
                issue_workflow_result_grant(**source)


@pytest.fixture
async def result_run(bundle, tmp_path, monkeypatch):
    _, _, _, data, write = bundle
    write()
    loaded = ModuleLoader(str(tmp_path)).load("tasks")
    rows, _ = fake_collection()
    with bind_persistence_principal("app", _OWNER, module_id="tasks", action_id="create", result_record_key="task_id"):
        await rows.insert_one({"task_id": "task-1"})
        with bind_result_event(*_EVENT):
            grant = issue_workflow_result_grant(**_source(loaded))
    executor = ModuleExecutor(data_contract=data)
    executor.register_loaded_module(loaded)
    read = AsyncMock(return_value=grant)
    persist = AsyncMock(return_value="applied")
    monkeypatch.setattr(AG2PersistenceManager, "read_workflow_result_grant", read)
    monkeypatch.setattr("mozaiksai.core.runtime.composition.module_dispatch._resolve_module_executor", lambda _: executor)
    monkeypatch.setattr("mozaiksai.core.runtime.persistence.workflow_result.commit_workflow_result", persist)
    policy = build_context_authority_policy(workflow_name=_RUN[0], definitions={})
    bridge = ContextVariablesBridge({"user_id": _RUN[3]}, authority_policy=policy)
    bridge._bind_run(_RUN[:3], policy)
    return bridge, loaded, executor, read, persist


@pytest.mark.parametrize("case", ["manual", "hitl", "changed_value", "wrong_type", "undeclared_field"])
async def test_invalid_tool_output_never_reaches_result_persistence(result_run, case):
    bridge, _, _, read, persist = result_run
    output = {"summary": "A result"}
    if case == "wrong_type":
        output["summary"] = 17
    if case == "undeclared_field":
        output["task_id"] = "task-2"
    sent = {"summary": "changed"} if case == "changed_value" else output
    if case == "manual":
        with pytest.raises(PermissionError):
            await commit_workflow_result(sent)
    else:
        projection = None if case == "hitl" else StructuredOutputOverlay(bridge, output)
        with _workflow_tool_invocation(bridge, validated_output=projection), pytest.raises((PermissionError, ValueError)):
            await commit_workflow_result(sent)
    persist.assert_not_awaited()
    if case in {"manual", "hitl", "changed_value"}:
        read.assert_not_awaited()


@pytest.mark.parametrize("index", range(4), ids=["workflow", "app", "chat", "actor"])
async def test_result_run_identity_matches_the_persisted_grant_before_dispatch(result_run, index):
    _, _, _, read, persist = result_run
    requested = list(_RUN)
    requested[index] = "different"
    workflow, app_id, chat_id, actor = requested
    policy = build_context_authority_policy(workflow_name=workflow, definitions={})
    bridge = ContextVariablesBridge({"user_id": actor}, authority_policy=policy)
    bridge._bind_run((workflow, app_id, chat_id), policy)
    output = {"summary": "A result"}
    with _workflow_tool_invocation(bridge, validated_output=StructuredOutputOverlay(bridge, output)):
        with pytest.raises(PermissionError, match="approved reaction"):
            await commit_workflow_result(output)
    read.assert_awaited_once_with(workflow=workflow, app_id=app_id, chat_id=chat_id, user_id=actor)
    persist.assert_not_awaited()


@pytest.mark.parametrize("change", ["remove_reaction", "change_declaration", "retarget_capability"])
async def test_old_grant_resolves_against_current_module_registration(result_run, bundle, tmp_path, change):
    bridge, _, executor, _, persist = result_run
    module, reactions, _, _, write = bundle
    if change == "remove_reaction":
        reactions["reactions"][0]["target"].pop("result_write")
    elif change == "change_declaration":
        reactions["reactions"][0]["id"] = "replacement-writer"
    else:
        module["capabilities"][1]["target"] = "ReplacementWorkflow"
    write()
    executor.register_loaded_module(ModuleLoader(str(tmp_path)).load("tasks"))
    output = {"summary": "A result"}
    with _workflow_tool_invocation(bridge, validated_output=StructuredOutputOverlay(bridge, output)):
        with pytest.raises(PermissionError, match="reaction"):
            await commit_workflow_result(output)
    persist.assert_not_awaited()


@pytest.mark.parametrize("invalidated", ["invocation", "registration"])
async def test_result_authority_must_still_be_current_when_receiver_finishes(result_run, monkeypatch, invalidated):
    bridge, loaded, executor, _, persist = result_run
    entered, finish = asyncio.Event(), asyncio.Event()
    output = {"summary": "A result"}
    async def receiver(ctx, summary):
        entered.set()
        await finish.wait()
        return await ctx.commit_workflow_result({"summary": summary})
    monkeypatch.setattr(loaded.handler, "save_summary", receiver)
    with _workflow_tool_invocation(bridge, validated_output=StructuredOutputOverlay(bridge, output)):
        pending = asyncio.create_task(commit_workflow_result(output))
        await asyncio.wait_for(entered.wait(), 3)
        if invalidated == "registration":
            executor.register_loaded_module(loaded)
            finish.set()
            with pytest.raises(PermissionError, match="reaction changed"):
                await pending
            persist.assert_not_awaited()
            return
    finish.set()
    with pytest.raises(PermissionError):
        await pending
    persist.assert_not_awaited()


@pytest.mark.parametrize("outcome", ["applied", "wrong_output_type", "handler_failure", "no_approval", "repeated_approval"])
async def test_only_one_successful_validated_receiver_can_commit(result_run, bundle, tmp_path, monkeypatch, outcome):
    bridge, _, executor, read, persist = result_run
    module, _, _, _, write = bundle
    module["actions"][-1]["output_schema"] = {
        "type": "object", "properties": {"accepted": {"type": "boolean"}}, "required": ["accepted"],
    }
    write()
    loaded = ModuleLoader(str(tmp_path)).load("tasks")
    executor.register_loaded_module(loaded)
    output = {"summary": "A result"}

    async def receiver(ctx, summary):
        if outcome == "no_approval":
            return {"accepted": True}
        approval = await ctx.commit_workflow_result({"summary": summary})
        if outcome == "handler_failure":
            raise ValueError("Receiver failed")
        if outcome == "wrong_output_type":
            return {"accepted": "invalid"}
        if outcome == "repeated_approval":
            with pytest.raises(PermissionError):
                await ctx.commit_workflow_result({"summary": summary})
        return approval

    monkeypatch.setattr(loaded.handler, "save_summary", receiver)
    with _workflow_tool_invocation(bridge, validated_output=StructuredOutputOverlay(bridge, output)):
        if outcome == "applied":
            assert await commit_workflow_result(output) == {"status": "applied"}
            persist.assert_awaited_once()
            persisted = persist.await_args.kwargs
            assert (persisted["action_id"], persisted["values"], persisted["subject"].record_id) == (
                "save_summary", output, "task-1",
            )
        else:
            with pytest.raises(PermissionError, match="did not approve"):
                await commit_workflow_result(output)
            persist.assert_not_awaited()
    read.assert_awaited_once_with(workflow=_RUN[0], app_id=_RUN[1], chat_id=_RUN[2], user_id=_RUN[3])
