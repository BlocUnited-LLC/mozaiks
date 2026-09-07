"""Auto-tool binding cache self-validation and in-flight turn idempotency.

The AutoToolEventHandler binding cache resolves the CURRENT structured-output
registry and tool-declaration authority before any cached binding is reused:
a cached workflow binding is reusable only while its authority fingerprint
(exact model class identity per agent, tools.yaml bytes, referenced tool file
bytes, workflow path) still matches. reload/unload/refresh/failed-reload can
never leave a stale binding or stale callable executing.

Turn idempotency claims each ``chat_id + turn_idempotency_key`` atomically
BEFORE the first await: concurrent duplicate deliveries observe IN_FLIGHT (or
COMPLETED) and never execute; an unexpected interruption before a truthful
terminal result releases the claim so a legitimate retry can run.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from mozaiksai.core.events import auto_tool_handler as _auto_tool_mod
from mozaiksai.core.events.runtime_events import (
    build_runtime_agent_output_validated_event,
)
from mozaiksai.core.workflow.outputs import structured as _so
from mozaiksai.core.workflow.workflow_manager import workflow_manager

AutoToolBinding = _auto_tool_mod.AutoToolBinding
AutoToolEventHandler = _auto_tool_mod.AutoToolEventHandler

WORKFLOW = "CacheProbeWf"

_AGENT_TEMPLATE = (
    "  - name: CacheAgent\n"
    "    structured_outputs_required: true\n"
    "    prompt_sections:\n"
    "      - id: role\n"
    "        heading: ROLE\n"
    "        content: probe\n"
)

_TOOL_SOURCE_TEMPLATE = '''
async def record_run(context_variables=None):
    runs = context_variables.get("cache_probe_runs") or 0
    context_variables.set("cache_probe_runs", runs + 1)
    context_variables.set("cache_probe_marker", "{marker}")
    return {{"status": "ok"}}


async def record_run_alt(context_variables=None):
    context_variables.set("cache_probe_marker", "alt-function")
    return {{"status": "ok"}}
'''

_TOOLS_YAML_TEMPLATE = """tools:
- agent: CacheAgent
  file: cache_tool.py
  function: {function}
  description: Cache probe auto tool.
  tool_type: Agent_Tool
  auto_tool_call: true
"""


def _write_workflow(
    root: Path,
    *,
    fields: str = "title: { type: str }",
    marker: str = "v1",
    function: str = "record_run",
    structured_outputs_text: str | None = None,
) -> None:
    workflow_dir = root / WORKFLOW
    (workflow_dir / "tools").mkdir(parents=True, exist_ok=True)
    (workflow_dir / "orchestrator.yaml").write_text(
        f"schema_version: mozaiks.orchestrator.v1\nworkflow_name: {WORKFLOW}\n"
        "workflow_startup_mode: BackendOnly\nmax_turns: 1\n",
        encoding="utf-8",
    )
    (workflow_dir / "agents.yaml").write_text("agents:\n" + _AGENT_TEMPLATE, encoding="utf-8")
    if structured_outputs_text is None:
        structured_outputs_text = (
            "schema_version: mozaiks.structured_outputs.v1\n"
            "registry:\n  CacheAgent: CacheOutput\n"
            "models:\n  CacheOutput:\n    type: model\n    fields:\n"
            f"      {fields}\n"
        )
    (workflow_dir / "structured_outputs.yaml").write_text(structured_outputs_text, encoding="utf-8")
    (workflow_dir / "context_variables.yaml").write_text(
        "definitions:\n"
        "  cache_probe_runs:\n"
        "    type: number\n"
        "    description: Executions counted by the cache probe tool.\n"
        "    writer_ids: [deterministic_tool]\n"
        "    source: { type: state, default: null }\n"
        "  cache_probe_marker:\n"
        "    type: string\n"
        "    description: Marker written by the loaded tool version.\n"
        "    writer_ids: [deterministic_tool]\n"
        "    source: { type: state, default: null }\n",
        encoding="utf-8",
    )
    (workflow_dir / "tools.yaml").write_text(
        _TOOLS_YAML_TEMPLATE.format(function=function), encoding="utf-8"
    )
    (workflow_dir / "tools" / "cache_tool.py").write_text(
        _TOOL_SOURCE_TEMPLATE.format(marker=marker), encoding="utf-8"
    )


@pytest.fixture
def probe_manager(tmp_path: Path):
    saved_manager_state = dict(workflow_manager.__dict__)
    saved_structured = (
        dict(_so._workflow_models),
        dict(_so._workflow_registries),
        dict(_so._workflow_structured_agents),
        dict(_so._provider_response_model_cache),
    )
    try:
        workflow_manager.workflows_base_path = tmp_path
        workflow_manager._workflows = {}
        workflow_manager._workflow_paths = {}
        workflow_manager._config_cache = {}
        _so.invalidate_all_workflow_structured_outputs()
        _write_workflow(tmp_path)
        info = workflow_manager.reload_workflow(WORKFLOW)
        assert not info.get("error"), info
        yield workflow_manager
    finally:
        workflow_manager.__dict__.clear()
        workflow_manager.__dict__.update(saved_manager_state)
        _so.invalidate_all_workflow_structured_outputs()
        _so._workflow_models.update(saved_structured[0])
        _so._workflow_registries.update(saved_structured[1])
        _so._workflow_structured_agents.update(saved_structured[2])
        _so._provider_response_model_cache.update(saved_structured[3])


@pytest.fixture
def quiet_side_effects(monkeypatch):
    class _FakePersistenceManager:
        async def persist_context_variables(self, **kwargs):
            return None

    async def _no_transport():
        return None

    monkeypatch.setattr(_auto_tool_mod, "AG2PersistenceManager", _FakePersistenceManager)
    monkeypatch.setattr(_auto_tool_mod, "_get_simple_transport", _no_transport)


@pytest.fixture
def build_counter(monkeypatch):
    calls = {"count": 0}
    real_loader = _auto_tool_mod.load_agent_tool_functions

    def counting_loader(*args, **kwargs):
        calls["count"] += 1
        return real_loader(*args, **kwargs)

    monkeypatch.setattr(_auto_tool_mod, "load_agent_tool_functions", counting_loader)
    return calls


class _PatternContext:
    def __init__(self) -> None:
        self.data: dict = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value

    def snapshot(self):
        return dict(self.data)

    def to_dict(self):
        return self.snapshot()


def _event(
    *,
    structured_data: dict,
    turn: str,
    chat_id: str = "chat-1",
    pattern_context=None,
):
    return build_runtime_agent_output_validated_event(
        agent="CacheAgent",
        model_name="CacheOutput",
        structured_data=structured_data,
        auto_tool_call=True,
        context={"chat_id": chat_id, "app_id": "app-1", "workflow_name": WORKFLOW},
        turn_idempotency_key=turn,
        pattern_context_ref=pattern_context,
        validation_passed=True,
    )


PAYLOAD = {"title": "Cache Probe"}


def _reload(tmp_path: Path, **kwargs) -> None:
    _write_workflow(tmp_path, **kwargs)
    info = workflow_manager.reload_workflow(WORKFLOW)
    assert not info.get("error"), info


# ---------------------------------------------------------------------------
# Binding-cache lifecycle (cases A-F)
# ---------------------------------------------------------------------------


async def test_a_unchanged_workflow_reuses_cached_binding(
    probe_manager, quiet_side_effects, build_counter
):
    handler = AutoToolEventHandler()
    pattern = _PatternContext()
    await handler.handle_tool_dispatch(
        _event(structured_data=PAYLOAD, turn="turn-a1", pattern_context=pattern)
    )
    await handler.handle_tool_dispatch(
        _event(structured_data=PAYLOAD, turn="turn-a2", pattern_context=pattern)
    )
    assert pattern.data["cache_probe_runs"] == 2
    assert build_counter["count"] == 1


async def test_b_structured_output_change_rebuilds_binding(
    probe_manager, quiet_side_effects, build_counter, tmp_path
):
    handler = AutoToolEventHandler()
    pattern = _PatternContext()
    await handler.handle_tool_dispatch(
        _event(structured_data=PAYLOAD, turn="turn-b1", pattern_context=pattern)
    )
    assert pattern.data["cache_probe_runs"] == 1

    _reload(tmp_path, fields="title: { type: str }\n      status: { type: str }")
    refreshed = {"title": "Cache Probe", "status": "ready"}
    await handler.handle_tool_dispatch(
        _event(structured_data=refreshed, turn="turn-b2", pattern_context=pattern)
    )
    assert pattern.data["cache_probe_runs"] == 2
    assert build_counter["count"] == 2

    # The stale-shape payload cannot execute against the refreshed model.
    await handler.handle_tool_dispatch(
        _event(structured_data=PAYLOAD, turn="turn-b3", pattern_context=pattern)
    )
    assert pattern.data["cache_probe_runs"] == 2


async def test_c_tool_declaration_and_code_changes_replace_the_callable(
    probe_manager, quiet_side_effects, build_counter, tmp_path
):
    handler = AutoToolEventHandler()
    pattern = _PatternContext()
    await handler.handle_tool_dispatch(
        _event(structured_data=PAYLOAD, turn="turn-c1", pattern_context=pattern)
    )
    assert pattern.data["cache_probe_marker"] == "v1"

    # Tool CODE change with an unchanged tools.yaml: the cached callable must
    # not be reused.
    _reload(tmp_path, marker="v2-longer-content")
    await handler.handle_tool_dispatch(
        _event(structured_data=PAYLOAD, turn="turn-c2", pattern_context=pattern)
    )
    assert pattern.data["cache_probe_marker"] == "v2-longer-content"
    assert build_counter["count"] == 2

    # tools.yaml binding change: the newly declared function executes.
    _reload(tmp_path, marker="v2-longer-content", function="record_run_alt")
    await handler.handle_tool_dispatch(
        _event(structured_data=PAYLOAD, turn="turn-c3", pattern_context=pattern)
    )
    assert pattern.data["cache_probe_marker"] == "alt-function"
    assert build_counter["count"] == 3


async def test_d_unload_then_reload_leaves_no_stale_binding(
    probe_manager, quiet_side_effects, tmp_path
):
    handler = AutoToolEventHandler()
    pattern = _PatternContext()
    await handler.handle_tool_dispatch(
        _event(structured_data=PAYLOAD, turn="turn-d1", pattern_context=pattern)
    )
    assert pattern.data["cache_probe_runs"] == 1

    workflow_manager.unload_workflow(WORKFLOW)
    await handler.handle_tool_dispatch(
        _event(structured_data=PAYLOAD, turn="turn-d2", pattern_context=pattern)
    )
    assert pattern.data["cache_probe_runs"] == 1  # no stale execution

    _reload(tmp_path)
    await handler.handle_tool_dispatch(
        _event(structured_data=PAYLOAD, turn="turn-d3", pattern_context=pattern)
    )
    assert pattern.data["cache_probe_runs"] == 2


async def test_e_refresh_all_rebuilds_bindings(
    probe_manager, quiet_side_effects, build_counter
):
    handler = AutoToolEventHandler()
    pattern = _PatternContext()
    await handler.handle_tool_dispatch(
        _event(structured_data=PAYLOAD, turn="turn-e1", pattern_context=pattern)
    )
    workflow_manager.refresh_all()
    await handler.handle_tool_dispatch(
        _event(structured_data=PAYLOAD, turn="turn-e2", pattern_context=pattern)
    )
    assert pattern.data["cache_probe_runs"] == 2
    assert build_counter["count"] == 2


async def test_f_failed_reload_cannot_serve_the_stale_binding(
    probe_manager, quiet_side_effects, tmp_path
):
    handler = AutoToolEventHandler()
    pattern = _PatternContext()
    await handler.handle_tool_dispatch(
        _event(structured_data=PAYLOAD, turn="turn-f1", pattern_context=pattern)
    )
    assert pattern.data["cache_probe_runs"] == 1

    # Replacement configuration fails validation: the prior binding must not
    # silently remain authority for the failed replacement.
    _write_workflow(
        tmp_path,
        structured_outputs_text="schema_version: mozaiks.structured_outputs.v1\nregistry: [broken\n",
    )
    info = workflow_manager.reload_workflow(WORKFLOW)
    assert info.get("error")
    await handler.handle_tool_dispatch(
        _event(structured_data=PAYLOAD, turn="turn-f2", pattern_context=pattern)
    )
    assert pattern.data["cache_probe_runs"] == 1  # nothing executed

    _reload(tmp_path)
    await handler.handle_tool_dispatch(
        _event(structured_data=PAYLOAD, turn="turn-f3", pattern_context=pattern)
    )
    assert pattern.data["cache_probe_runs"] == 2


# ---------------------------------------------------------------------------
# In-flight idempotency (concurrency proofs)
# ---------------------------------------------------------------------------


class _BlockingTool:
    """Event-controlled tool: deterministically holds executions in flight."""

    def __init__(self) -> None:
        self.started = 0
        self.completed = 0
        self.release = asyncio.Event()
        self.entered = asyncio.Event()

    async def __call__(self, context_variables=None):
        self.started += 1
        self.entered.set()
        await self.release.wait()
        self.completed += 1
        return {"status": "ok"}


def _handler_with_blocking_tool(monkeypatch) -> tuple[AutoToolEventHandler, _BlockingTool]:
    handler = AutoToolEventHandler()
    tool = _BlockingTool()

    class _Validated:
        @staticmethod
        def model_dump(mode="json"):
            return dict(PAYLOAD)

    class _Model:
        @staticmethod
        def model_validate(data):
            return _Validated()

    binding = AutoToolBinding(
        model_name="CacheOutput",
        agent_name="CacheAgent",
        tool_name="blocking_tool",
        function=tool,
        param_names=("context_variables",),
        accepts_context=True,
        ui_config={},
        model_cls=_Model,
    )

    async def resolve(workflow_name, model_name, agent_name):
        return [binding]

    monkeypatch.setattr(handler, "_resolve_bindings", resolve)
    monkeypatch.setattr(handler, "_emit_tool_call", _async_noop)
    monkeypatch.setattr(handler, "_emit_tool_result", _async_noop)
    monkeypatch.setattr(handler, "_persist_context_variables", _async_noop_kw)
    return handler, tool


async def _async_noop(*args, **kwargs):
    return None


async def _async_noop_kw(*args, **kwargs):
    return None


def _raw_event(turn: str, chat_id: str = "chat-1"):
    return _event(structured_data=PAYLOAD, turn=turn, chat_id=chat_id, pattern_context=_PatternContext())


async def test_two_concurrent_same_key_deliveries_execute_once(monkeypatch):
    handler, tool = _handler_with_blocking_tool(monkeypatch)
    first = asyncio.create_task(handler.handle_tool_dispatch(_raw_event("turn-x")))
    second = asyncio.create_task(handler.handle_tool_dispatch(_raw_event("turn-x")))
    await tool.entered.wait()
    tool.release.set()
    await asyncio.gather(first, second)
    assert tool.started == 1
    assert tool.completed == 1


async def test_n_concurrent_same_key_deliveries_execute_once(monkeypatch):
    handler, tool = _handler_with_blocking_tool(monkeypatch)
    tasks = [
        asyncio.create_task(handler.handle_tool_dispatch(_raw_event("turn-n")))
        for _ in range(8)
    ]
    await tool.entered.wait()
    tool.release.set()
    await asyncio.gather(*tasks)
    assert tool.started == 1
    assert tool.completed == 1


async def test_same_chat_different_turn_keys_both_execute(monkeypatch):
    handler, tool = _handler_with_blocking_tool(monkeypatch)
    tool.release.set()
    await handler.handle_tool_dispatch(_raw_event("turn-1"))
    await handler.handle_tool_dispatch(_raw_event("turn-2"))
    assert tool.started == 2


async def test_different_chat_same_turn_key_text_execute_independently(monkeypatch):
    handler, tool = _handler_with_blocking_tool(monkeypatch)
    tool.release.set()
    await handler.handle_tool_dispatch(_raw_event("turn-same", chat_id="chat-a"))
    await handler.handle_tool_dispatch(_raw_event("turn-same", chat_id="chat-b"))
    assert tool.started == 2


async def test_cancellation_releases_claim_for_legitimate_retry(monkeypatch):
    handler, tool = _handler_with_blocking_tool(monkeypatch)
    first = asyncio.create_task(handler.handle_tool_dispatch(_raw_event("turn-c")))
    await tool.entered.wait()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert handler._in_flight_keys == set()  # no permanently stuck claim

    tool.release.set()
    await handler.handle_tool_dispatch(_raw_event("turn-c"))
    assert tool.started == 2
    assert tool.completed == 1


async def test_unexpected_failure_before_terminal_result_allows_retry(monkeypatch):
    handler, tool = _handler_with_blocking_tool(monkeypatch)
    tool.release.set()
    boom = {"raise": True}

    async def exploding_emit(*args, **kwargs):
        if boom["raise"]:
            raise RuntimeError("unexpected interruption before terminal result")

    monkeypatch.setattr(handler, "_emit_tool_call", exploding_emit)
    with pytest.raises(RuntimeError):
        await handler.handle_tool_dispatch(_raw_event("turn-u"))
    assert handler._in_flight_keys == set()

    boom["raise"] = False
    await handler.handle_tool_dispatch(_raw_event("turn-u"))
    assert tool.completed == 1


async def test_completed_turn_remains_suppressed_across_resume(monkeypatch):
    handler, tool = _handler_with_blocking_tool(monkeypatch)
    tool.release.set()
    event = _raw_event("turn-done")
    await handler.handle_tool_dispatch(event)
    assert tool.completed == 1
    # Sequential duplicate, and a HITL-style resume redelivery of the same
    # completed turn, both suppress: exactly one side effect.
    await handler.handle_tool_dispatch(event)
    await handler.handle_tool_dispatch(_raw_event("turn-done"))
    assert tool.started == 1
    assert tool.completed == 1
    assert handler._in_flight_keys == set()
