"""Declared workflow progress crosses the real loader, helper and event boundary."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from mozaiksai.core.events.unified_event_dispatcher import UnifiedEventDispatcher
from mozaiksai.core.transport.simple_transport import SimpleTransport
from mozaiksai.core.workflow import task_batches, ui_tools
from mozaiksai.core.workflow.contract_validation import validate_workflow_context_contract
from mozaiksai.core.workflow.declarative import parse_ui_config


def _progress():
    return {"stages": [{"id": "reading", "label": "Reading documents", "unit": "documents"}]}


def _scope():
    return {"chat_id": "chat-one", "app_id": "app-one", "workflow_name": "ReadingFlow"}


@pytest.fixture
def progress_transport(monkeypatch):
    transport = SimpleNamespace(
        connections={"chat-one": {"app_id": "app-one", "workflow_name": "ReadingFlow"}},
        send_event_to_ui=AsyncMock(),
    )
    monkeypatch.setattr(SimpleTransport, "get_instance", AsyncMock(return_value=transport))
    monkeypatch.setattr(ui_tools, "workflow_manager", SimpleNamespace(get_config=lambda name: {
        "progress": _progress() if name == "ReadingFlow" else None,
    }))
    return transport


@pytest.mark.parametrize("progress", [
    {"stages": []},
    {"stages": [{"id": "reading", "label": "Read"}, {"id": "reading", "label": "Read again"}]},
    {"stages": [{"id": "Unknown Stage", "label": "Read"}]},
    {"stages": [{"id": "reading", "label": " "}]},
    {"stages": [{"id": "reading", "label": "Read\nsecret"}]},
    {"stages": [{"id": "reading", "label": "Read", "unit": ""}]},
    {"stages": [{"id": "reading", "label": "Read", "percent": 50}]},
    {"stages": [{"id": "reading", "label": "Read"}], "enabled": True},
])
def test_ui_config_rejects_invalid_progress(progress):
    with pytest.raises(ValueError, match="ui_config.yaml"):
        parse_ui_config({"visual_agents": [], "progress": progress})


def test_ui_config_progress_is_optional_and_preserves_declared_public_labels():
    assert parse_ui_config({"visual_agents": []})["progress"] is None
    assert parse_ui_config({"progress": _progress()})["progress"] == _progress()


@pytest.mark.asyncio
async def test_progress_helper_emits_actual_counts_and_declared_label(progress_transport):
    await ui_tools.emit_tool_progress_event(
        "read_documents", stage_id="reading", context_variables=_scope(), completed=2, total=5,
        correlation_id="tool-one",
    )
    event, chat_id = progress_transport.send_event_to_ui.call_args.args
    assert chat_id == "chat-one"
    assert event.items() >= {
        "kind": "tool_progress", "chat_id": "chat-one", "workflow_name": "ReadingFlow",
        "tool_name": "read_documents", "stage_id": "reading", "stage_label": "Reading documents",
        "completed": 2, "total": 5, "unit": "documents", "corr": "tool-one",
    }.items()
    assert "progress_percent" not in event
    assert "status_message" not in event
    envelope = UnifiedEventDispatcher().build_outbound_event_envelope(
        raw_event=event, chat_id=chat_id, workflow_name="ReadingFlow", get_sequence_cb=lambda _: 19,
    )
    assert envelope["type"] == "chat.tool_progress"
    assert envelope["data"]["sequence"] == 19
    assert envelope["data"]["stage_label"] == "Reading documents"


@pytest.mark.asyncio
async def test_progress_can_be_indeterminate_or_finish_a_stage_without_finishing_run(progress_transport):
    await ui_tools.emit_tool_progress_event("read_documents", stage_id="reading", context_variables=_scope())
    event = progress_transport.send_event_to_ui.call_args.args[0]
    assert event["completed"] is None and event["total"] is None
    await ui_tools.emit_tool_progress_event(
        "read_documents", stage_id="reading", context_variables=_scope(), completed=5, total=5,
    )
    assert progress_transport.send_event_to_ui.call_args.args[0]["kind"] == "tool_progress"


@pytest.mark.parametrize("completed,total", [(1, None), (None, 3), (-1, 3), (4, 3), (0, 0), (True, 3), (1, False), (1.0, 3), ("1", 3)])
@pytest.mark.asyncio
async def test_progress_rejects_invalid_counts_before_emitting(progress_transport, completed, total):
    with pytest.raises(ValueError, match="Progress"):
        await ui_tools.emit_tool_progress_event(
            "read_documents", stage_id="reading", context_variables=_scope(), completed=completed, total=total,
        )
    progress_transport.send_event_to_ui.assert_not_called()


@pytest.mark.asyncio
async def test_progress_rejects_unknown_stage_or_workflow(progress_transport):
    for stage_id, workflow_name in [("invented", "ReadingFlow"), ("reading", "OtherFlow")]:
        with pytest.raises(ValueError, match="no declared progress stage"):
            await ui_tools.emit_tool_progress_event(
                "read_documents", stage_id=stage_id, context_variables={**_scope(), "workflow_name": workflow_name},
            )
    progress_transport.send_event_to_ui.assert_not_called()


@pytest.mark.parametrize("key", ["chat_id", "app_id", "workflow_name"])
@pytest.mark.asyncio
async def test_progress_requires_runtime_scope(progress_transport, key):
    scope = _scope()
    scope.pop(key)
    with pytest.raises(ValueError, match="requires runtime"):
        await ui_tools.emit_tool_progress_event("read_documents", stage_id="reading", context_variables=scope)
    progress_transport.send_event_to_ui.assert_not_called()


@pytest.mark.parametrize("key,value", [("app_id", "other-app"), ("workflow_name", "OtherFlow")])
@pytest.mark.asyncio
async def test_progress_rejects_different_connection_owner(progress_transport, key, value):
    progress_transport.connections["chat-one"][key] = value
    with pytest.raises(ValueError, match="connected chat scope"):
        await ui_tools.emit_tool_progress_event("read_documents", stage_id="reading", context_variables=_scope())
    progress_transport.send_event_to_ui.assert_not_called()


def test_progress_and_lifecycle_boundaries_share_transport_sequence():
    sequence = iter(range(1, 10))
    dispatcher = UnifiedEventDispatcher()
    kinds = ["run_start", "tool_progress", "tool_call", "awaiting_reply", "resume_boundary", "tool_progress", "error", "run_complete"]
    for expected, kind in enumerate(kinds, start=1):
        envelope = dispatcher.build_outbound_event_envelope(
            raw_event={"kind": kind, "chat_id": "chat-one", "sequence": 999},
            chat_id="chat-one", get_sequence_cb=lambda _: next(sequence),
        )
        assert envelope["type"] == f"chat.{kind}"
        assert envelope["data"]["sequence"] == expected


def _batch_payload():
    return {"batches": [{
        "id": "reviews", "trigger_agent": "Planner", "progress_stage": "reading",
        "source": {"kind": "context_variable", "path": "tasks", "task_model": "Task"},
        "execution": {"failure_policy": "continue_with_available"},
        "result": {"context_key": "results", "status_key": "status"},
    }]}


def test_batch_progress_reference_closes_in_loader_and_cross_file_validator(tmp_path):
    workflow = tmp_path / "ReadingFlow"
    (workflow / "extended_orchestration").mkdir(parents=True)
    (workflow / "extended_orchestration" / "task_batches.yaml").write_text(yaml.safe_dump(_batch_payload()), encoding="utf-8")
    (workflow / "ui_config.yaml").write_text(yaml.safe_dump({"progress": _progress()}), encoding="utf-8")
    assert task_batches.load_task_batches_config("ReadingFlow", tmp_path).batches[0].progress_stage == "reading"
    config = {"progress": _progress(), "context_variables": {"definitions": {"results": {}, "status": {}}}}
    validate_workflow_context_contract(workflow_name="ReadingFlow", workflow_config=config, workflow_path=workflow)
    config["progress"]["stages"][0]["id"] = "other"
    with pytest.raises(ValueError, match="undeclared progress stage"):
        validate_workflow_context_contract(workflow_name="ReadingFlow", workflow_config=config, workflow_path=workflow)
    (workflow / "ui_config.yaml").write_text("visual_agents: []\n", encoding="utf-8")
    with pytest.raises(ValueError, match="undeclared progress stage"):
        task_batches.load_task_batches_config("ReadingFlow", tmp_path)


def test_conveyor_preserves_progress_reference():
    config = task_batches.parse_task_batches_config({"conveyors": [{
        "id": "reviews", "decomposition_agent": "Planner", "execution_agents": ["Reader"],
        "progress_stage": "reading",
    }]})
    assert config.batches[0].progress_stage == "reading"
    task_batches.validate_task_batch_progress(config, {"progress": _progress()})


@pytest.mark.parametrize("batch_source", ["parsed", "disk"])
@pytest.mark.parametrize("defect", [None, "unknown_progress_stage", "hidden_statusbar_variable"])
def test_progress_and_statusbar_contracts_close_for_both_batch_sources(tmp_path, batch_source, defect):
    payload = _batch_payload()
    if defect == "unknown_progress_stage":
        payload["batches"][0]["progress_stage"] = "undeclared"
    config = {
        "agents": {"Planner": {}},
        "progress": _progress(),
        "middleware": {"statusbar_middleware": [{"agent": "Planner", "variables": ["status"]}]},
        "context_variables": {
            "definitions": {
                "results": {"type": "object", "source": {"type": "state", "default": {}}},
                "status": {
                    "type": "object", "source": {"type": "config", "default": {}},
                    **({"model_visible": False} if defect == "hidden_statusbar_variable" else {}),
                },
            },
            "agents": {"Planner": {"variables": ["status"]}},
        },
    }
    if batch_source == "parsed":
        batch_args = {"task_batches": task_batches.parse_task_batches_config(payload)}
    else:
        batch_file = tmp_path / "extended_orchestration" / "task_batches.yaml"
        batch_file.parent.mkdir()
        batch_file.write_text(yaml.safe_dump(payload), encoding="utf-8")
        batch_args = {"workflow_path": tmp_path}

    if defect:
        expected = "undeclared progress stage" if defect == "unknown_progress_stage" else "not model-visible"
        with pytest.raises(ValueError, match=expected):
            validate_workflow_context_contract(workflow_name="ReadingFlow", workflow_config=config, **batch_args)
    else:
        validate_workflow_context_contract(workflow_name="ReadingFlow", workflow_config=config, **batch_args)


@pytest.mark.asyncio
async def test_parallel_batch_reports_only_accepted_tasks_in_order(monkeypatch, progress_transport):
    async def run_task(**kwargs):
        await asyncio.sleep(0)
        if kwargs["task"]["task_id"] == "bad":
            raise ValueError("invalid task output")
        return {"accepted": True}

    monkeypatch.setattr(task_batches, "_run_one_task", run_task)
    items = [{"task_id": name, "depends_on": []} for name in ("one", "bad", "two")]
    result = await task_batches._execute_one_batch(
        workflow_name="ReadingFlow", batch=task_batches.parse_task_batches_config(_batch_payload()).batches[0],
        task_items=items, agents={}, context_variables={}, chat_id="chat-one", app_id="app-one", user_id="actor",
        wf_logger=None, fresh_agents_per_task=False, agents_factory=None, context_authority_policy=None,
    )
    assert result["status"] == "partial"
    events = [call.args[0] for call in progress_transport.send_event_to_ui.call_args_list]
    assert [(event["completed"], event["total"]) for event in events] == [(0, 3), (1, 3), (2, 3)]
    assert all(event["kind"] == "tool_progress" for event in events)


@pytest.mark.asyncio
async def test_progress_failure_preserves_accepted_batch_outcome(monkeypatch):
    runner = AsyncMock(return_value={"accepted": True})
    progress = AsyncMock(side_effect=ValueError("connected chat scope changed"))
    monkeypatch.setattr(task_batches, "_run_one_task", runner)
    monkeypatch.setattr(ui_tools, "emit_tool_progress_event", progress)
    result = await task_batches._execute_one_batch(
        workflow_name="ReadingFlow", batch=task_batches.parse_task_batches_config(_batch_payload()).batches[0],
        task_items=[{"task_id": "one", "depends_on": []}], agents={}, context_variables={},
        chat_id="chat-one", app_id="app-one", user_id="actor", wf_logger=None,
        fresh_agents_per_task=False, agents_factory=None, context_authority_policy=None,
    )
    assert result["status"] == "completed"
    assert result["completed_tasks"] == ["one"]
    assert result["failed_tasks"] == []
    assert runner.await_count == 1
    assert progress.await_count == 2


@pytest.mark.asyncio
async def test_empty_batch_has_no_progress_total(monkeypatch):
    progress = AsyncMock()
    monkeypatch.setattr(ui_tools, "emit_tool_progress_event", progress)
    result = await task_batches._execute_one_batch(
        workflow_name="ReadingFlow", batch=task_batches.parse_task_batches_config(_batch_payload()).batches[0],
        task_items=[], agents={}, context_variables={}, chat_id="chat-one", app_id="app-one", user_id="actor",
        wf_logger=None, fresh_agents_per_task=False, agents_factory=None, context_authority_policy=None,
    )
    assert result["status"] == "completed"
    progress.assert_not_called()
