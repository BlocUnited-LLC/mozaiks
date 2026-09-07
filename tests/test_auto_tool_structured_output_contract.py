"""structured_output is a runtime-owned transient read-only auto-tool projection.

The documented public contract — ``context_variables.get("structured_output")``
returns the exact validated ``structured_data`` for the current auto-tool
turn — is satisfied by a read-only overlay over the live workflow context.
No application-declared context variable is required, no writer can set or
replace the key, snapshots and persistence never include it, and the next
turn cannot inherit it as ordinary state. Ordinary context behavior for all
other keys is preserved, including deliberate tool writes under different
declared application keys.
"""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

import pytest

from mozaiksai.core.events import auto_tool_handler as _auto_tool_mod
from mozaiksai.core.events.runtime_events import (
    build_runtime_agent_output_validated_event,
    build_turn_idempotency_key,
)
from mozaiksai.core.workflow.context.adapter import create_context_container
from mozaiksai.core.workflow.context.structured_output_overlay import (
    STRUCTURED_OUTPUT_KEY,
    StructuredOutputOverlay,
    StructuredOutputWriteError,
)
from mozaiksai.core.workflow.outputs import structured as _so
from mozaiksai.core.workflow.workflow_manager import workflow_manager

AutoToolEventHandler = _auto_tool_mod.AutoToolEventHandler

WORKFLOW = "AutoToolProbe"

_AGENT_TEMPLATE = (
    "  - name: {name}\n"
    "    structured_outputs_required: true\n"
    "    prompt_sections:\n"
    "      - id: role\n"
    "        heading: ROLE\n"
    "        content: probe\n"
)

_TOOL_SOURCE = '''
"""Probe auto tools exercising the documented structured_output contract."""


async def save_context_only(context_variables=None):
    data = context_variables.get("structured_output")
    if not data:
        return {"status": "error", "message": "no structured_output visible"}
    context_variables.set("observed_structured_output", dict(data))
    runs = context_variables.get("context_only_runs") or 0
    context_variables.set("context_only_runs", runs + 1)
    return {"status": "ok"}


async def save_explicit(title: str, status: str, context_variables=None):
    data = context_variables.get("structured_output")
    context_variables.set(
        "explicit_matches_context",
        bool(data) and data.get("title") == title and data.get("status") == status,
    )
    return {"status": "ok"}


async def mutate_nested(meta=None, context_variables=None):
    if meta is not None:
        meta["label"] = "MUTATED"
        meta["injected_temp"] = True
    overlay_view = context_variables.get("structured_output")
    context_variables.set(
        "mutator_overlay_after_own_mutation", dict(dict(overlay_view)["meta"])
    )
    return {"status": "ok"}


async def read_nested(context_variables=None):
    data = context_variables.get("structured_output")
    context_variables.set("reader_saw_meta", dict(dict(data)["meta"]))
    return {"status": "ok"}


async def try_mutation(context_variables=None):
    outcomes = {}
    try:
        context_variables.set("structured_output", {"forged": True})
        outcomes["set"] = "allowed"
    except Exception as exc:
        outcomes["set"] = type(exc).__name__
    try:
        context_variables.remove("structured_output")
        outcomes["remove"] = "allowed"
    except Exception as exc:
        outcomes["remove"] = type(exc).__name__
    frozen = context_variables.get("structured_output")
    try:
        frozen["title"] = "mutated"
        outcomes["item_assignment"] = "allowed"
    except TypeError:
        outcomes["item_assignment"] = "TypeError"
    after = context_variables.get("structured_output")
    outcomes["value_after_attempts"] = dict(after) if after else None
    context_variables.set("mutation_outcomes", outcomes)
    return {"status": "ok"}
'''

_TOOLS_YAML = """tools:
- agent: ContextOnlyAgent
  file: probe_tools.py
  function: save_context_only
  description: Context-only auto tool.
  tool_type: Agent_Tool
  auto_tool_call: true
- agent: ExplicitAgent
  file: probe_tools.py
  function: save_explicit
  description: Explicit-param auto tool.
  tool_type: Agent_Tool
  auto_tool_call: true
- agent: MutatorAgent
  file: probe_tools.py
  function: try_mutation
  description: Mutation-attempt auto tool.
  tool_type: Agent_Tool
  auto_tool_call: true
- agent: PairMutateFirstAgent
  file: probe_tools.py
  function: mutate_nested
  description: Explicit-param tool that mutates its nested argument.
  tool_type: Agent_Tool
  auto_tool_call: true
- agent: PairMutateFirstAgent
  file: probe_tools.py
  function: read_nested
  description: Context-only tool reading structured_output after the mutator.
  tool_type: Agent_Tool
  auto_tool_call: true
- agent: PairReadFirstAgent
  file: probe_tools.py
  function: read_nested
  description: Context-only tool reading structured_output before the mutator.
  tool_type: Agent_Tool
  auto_tool_call: true
- agent: PairReadFirstAgent
  file: probe_tools.py
  function: mutate_nested
  description: Explicit-param tool that mutates its nested argument.
  tool_type: Agent_Tool
  auto_tool_call: true
"""

_STRUCTURED_OUTPUTS = """schema_version: mozaiks.structured_outputs.v1
registry:
  ContextOnlyAgent: ProbeOutput
  ExplicitAgent: ProbeOutput
  MutatorAgent: ProbeOutput
  PairMutateFirstAgent: NestedOutput
  PairReadFirstAgent: NestedOutput
models:
  ProbeOutput:
    type: model
    fields:
      title: { type: str }
      status: { type: str }
  MetaLeaf:
    type: model
    fields:
      label: { type: str }
  NestedOutput:
    type: model
    fields:
      title: { type: str }
      meta: { type: MetaLeaf }
"""

# The declared application keys the probe tools write. structured_output is
# deliberately NOT declared: it is runtime-owned and needs no declaration.
_CONTEXT_VARIABLES = """definitions:
  observed_structured_output:
    type: object
    description: Tool-saved copy of the validated output under a declared key.
    writer_ids: [deterministic_tool]
    source:
      type: state
      default: null
  context_only_runs:
    type: number
    description: How many times the context-only probe tool executed.
    writer_ids: [deterministic_tool]
    source:
      type: state
      default: null
  explicit_matches_context:
    type: boolean
    description: Whether explicit params matched the context projection.
    writer_ids: [deterministic_tool]
    source:
      type: state
      default: null
  mutation_outcomes:
    type: object
    description: Outcomes of attempted projection mutations.
    writer_ids: [deterministic_tool]
    source:
      type: state
      default: null
"""


def _write_workflow(root: Path) -> None:
    workflow_dir = root / WORKFLOW
    (workflow_dir / "tools").mkdir(parents=True, exist_ok=True)
    (workflow_dir / "orchestrator.yaml").write_text(
        f"schema_version: mozaiks.orchestrator.v1\nworkflow_name: {WORKFLOW}\n"
        "workflow_startup_mode: BackendOnly\n"
        "initial_agent: ContextOnlyAgent\n"
        "max_turns: 1\n",
        encoding="utf-8",
    )
    (workflow_dir / "agents.yaml").write_text(
        "agents:\n"
        + _AGENT_TEMPLATE.format(name="ContextOnlyAgent")
        + _AGENT_TEMPLATE.format(name="ExplicitAgent")
        + _AGENT_TEMPLATE.format(name="MutatorAgent")
        + _AGENT_TEMPLATE.format(name="PairMutateFirstAgent")
        + _AGENT_TEMPLATE.format(name="PairReadFirstAgent"),
        encoding="utf-8",
    )
    (workflow_dir / "structured_outputs.yaml").write_text(_STRUCTURED_OUTPUTS, encoding="utf-8")
    (workflow_dir / "context_variables.yaml").write_text(_CONTEXT_VARIABLES, encoding="utf-8")
    (workflow_dir / "tools.yaml").write_text(_TOOLS_YAML, encoding="utf-8")
    (workflow_dir / "tools" / "probe_tools.py").write_text(_TOOL_SOURCE, encoding="utf-8")


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


class _PatternContext:
    """Minimal live pattern context stand-in (AG2 WorkflowState surface)."""

    def __init__(self, initial: dict | None = None) -> None:
        self.data = dict(initial or {})

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value

    def remove(self, key):
        return self.data.pop(key, None) is not None

    def contains(self, key):
        return key in self.data

    def keys(self):
        return self.data.keys()

    def snapshot(self):
        return dict(self.data)

    def to_dict(self):
        return self.snapshot()


@pytest.fixture
def persisted_snapshots(monkeypatch):
    captured: list[dict] = []

    class _FakePersistenceManager:
        async def persist_context_variables(self, **kwargs):
            captured.append(dict(kwargs.get("variables") or {}))

    monkeypatch.setattr(_auto_tool_mod, "AG2PersistenceManager", _FakePersistenceManager)
    return captured


@pytest.fixture
def quiet_transport(monkeypatch):
    async def _no_transport():
        return None

    monkeypatch.setattr(_auto_tool_mod, "_get_simple_transport", _no_transport)


def _event(
    agent: str,
    *,
    structured_data: dict,
    turn: int = 1,
    pattern_context=None,
    context_variables: dict | None = None,
):
    context = {
        "chat_id": "chat-1",
        "app_id": "app-1",
        "workflow_name": WORKFLOW,
    }
    if context_variables is not None:
        context["context_variables"] = context_variables
    return build_runtime_agent_output_validated_event(
        agent=agent,
        model_name="ProbeOutput",
        structured_data=structured_data,
        auto_tool_call=True,
        context=context,
        turn_idempotency_key=build_turn_idempotency_key("chat-1", turn),
        pattern_context_ref=pattern_context,
        validation_passed=True,
    )


PAYLOAD = {"title": "Exact Title", "status": "ready"}


# ---------------------------------------------------------------------------
# Overlay unit contract
# ---------------------------------------------------------------------------


def test_overlay_reads_exact_payload_and_delegates_other_keys():
    base = create_context_container({"declared_key": "value"})
    overlay = StructuredOutputOverlay(base, PAYLOAD)
    assert dict(overlay.get(STRUCTURED_OUTPUT_KEY)) == PAYLOAD
    assert isinstance(overlay.get(STRUCTURED_OUTPUT_KEY), MappingProxyType)
    assert overlay.get("declared_key") == "value"
    assert overlay.contains(STRUCTURED_OUTPUT_KEY)
    assert STRUCTURED_OUTPUT_KEY in overlay
    assert STRUCTURED_OUTPUT_KEY not in list(overlay.keys())
    overlay.set("another_key", 5)
    assert base.get("another_key") == 5


def test_overlay_fails_every_projection_mutation_closed():
    overlay = StructuredOutputOverlay(create_context_container({}), PAYLOAD)
    with pytest.raises(StructuredOutputWriteError):
        overlay.set(STRUCTURED_OUTPUT_KEY, {"forged": True})
    with pytest.raises(StructuredOutputWriteError):
        overlay.remove(STRUCTURED_OUTPUT_KEY)
    with pytest.raises(StructuredOutputWriteError):
        overlay[STRUCTURED_OUTPUT_KEY] = {"forged": True}
    with pytest.raises(StructuredOutputWriteError):
        del overlay[STRUCTURED_OUTPUT_KEY]
    with pytest.raises(TypeError):
        overlay.get(STRUCTURED_OUTPUT_KEY)["title"] = "mutated"
    assert dict(overlay.get(STRUCTURED_OUTPUT_KEY)) == PAYLOAD


def test_overlay_snapshot_and_to_dict_never_expose_the_projection():
    base = create_context_container({"declared_key": "value"})
    overlay = StructuredOutputOverlay(base, PAYLOAD)
    overlay.set("written_by_tool", True)
    for snap in (overlay.snapshot(), overlay.to_dict()):
        assert STRUCTURED_OUTPUT_KEY not in snap
        assert snap["declared_key"] == "value"
        assert snap["written_by_tool"] is True
    with pytest.raises(AttributeError):
        _ = overlay.data


def test_overlay_shadows_caller_seeded_projection_without_erasing_base():
    """A stale/caller-planted/replayed base structured_output key is shadowed
    for reads and excluded from every enumeration-derived durable view."""
    base = _PatternContext(
        {STRUCTURED_OUTPUT_KEY: {"planted": "by-caller"}, "declared_key": "value"}
    )
    overlay = StructuredOutputOverlay(base, PAYLOAD)
    # get() serves the runtime transient projection, never the planted value.
    assert dict(overlay.get(STRUCTURED_OUTPUT_KEY)) == PAYLOAD
    # keys(), snapshot(), and to_dict() never expose the colliding base key.
    assert STRUCTURED_OUTPUT_KEY not in list(overlay.keys())
    assert "declared_key" in list(overlay.keys())
    assert STRUCTURED_OUTPUT_KEY not in overlay.snapshot()
    assert STRUCTURED_OUTPUT_KEY not in overlay.to_dict()
    assert overlay.snapshot()["declared_key"] == "value"
    # The base is not silently erased; only the turn-local view is shadowed.
    assert base.data[STRUCTURED_OUTPUT_KEY] == {"planted": "by-caller"}
    assert base.data["declared_key"] == "value"


# ---------------------------------------------------------------------------
# Handler contract through real bindings (temp workflow on disk)
# ---------------------------------------------------------------------------


async def test_context_only_auto_tool_sees_structured_output_and_succeeds(
    probe_manager, persisted_snapshots, quiet_transport
):
    handler = AutoToolEventHandler()
    pattern = _PatternContext({"declared_key": "value"})
    await handler.handle_tool_dispatch(
        _event("ContextOnlyAgent", structured_data=PAYLOAD, pattern_context=pattern)
    )
    assert pattern.data["observed_structured_output"] == PAYLOAD
    assert pattern.data["context_only_runs"] == 1
    # structured_output never became pattern/workflow state.
    assert STRUCTURED_OUTPUT_KEY not in pattern.data
    # Persistence captured the tool's declared writes, never the projection.
    assert persisted_snapshots, "context persistence did not run"
    for snapshot in persisted_snapshots:
        assert STRUCTURED_OUTPUT_KEY not in snapshot
    assert persisted_snapshots[-1]["observed_structured_output"] == PAYLOAD


async def test_explicit_param_auto_tool_receives_matching_fields(
    probe_manager, persisted_snapshots, quiet_transport
):
    handler = AutoToolEventHandler()
    pattern = _PatternContext()
    await handler.handle_tool_dispatch(
        _event("ExplicitAgent", structured_data=PAYLOAD, pattern_context=pattern)
    )
    assert pattern.data["explicit_matches_context"] is True
    assert STRUCTURED_OUTPUT_KEY not in pattern.data


async def test_auto_tool_cannot_mutate_or_replace_the_projection(
    probe_manager, persisted_snapshots, quiet_transport
):
    handler = AutoToolEventHandler()
    pattern = _PatternContext()
    await handler.handle_tool_dispatch(
        _event("MutatorAgent", structured_data=PAYLOAD, pattern_context=pattern)
    )
    outcomes = pattern.data["mutation_outcomes"]
    assert outcomes["set"] == "StructuredOutputWriteError"
    assert outcomes["remove"] == "StructuredOutputWriteError"
    assert outcomes["item_assignment"] == "TypeError"
    assert outcomes["value_after_attempts"] == PAYLOAD
    assert STRUCTURED_OUTPUT_KEY not in pattern.data


async def test_caller_cannot_seed_or_replace_the_transient_projection(
    probe_manager, persisted_snapshots, quiet_transport
):
    """A planted structured_output in the event context snapshot (no live
    pattern ref) must not reach the tool or persistence — the runtime payload
    is the only source."""
    handler = AutoToolEventHandler()
    await handler.handle_tool_dispatch(
        _event(
            "ContextOnlyAgent",
            structured_data=PAYLOAD,
            pattern_context=None,
            context_variables={STRUCTURED_OUTPUT_KEY: {"planted": "by-caller"}},
        )
    )
    assert persisted_snapshots, "context persistence did not run"
    last = persisted_snapshots[-1]
    assert last["observed_structured_output"] == PAYLOAD
    assert STRUCTURED_OUTPUT_KEY not in last


async def test_duplicate_runtime_event_cannot_duplicate_tool_execution(
    probe_manager, persisted_snapshots, quiet_transport
):
    handler = AutoToolEventHandler()
    pattern = _PatternContext()
    event = _event("ContextOnlyAgent", structured_data=PAYLOAD, pattern_context=pattern, turn=7)
    await handler.handle_tool_dispatch(event)
    await handler.handle_tool_dispatch(event)
    assert pattern.data["context_only_runs"] == 1


async def test_extra_field_in_event_payload_triggers_no_tool_ui_or_persistence(
    probe_manager, persisted_snapshots, monkeypatch
):
    """The handler re-validates against the same exact model: a payload with
    an undeclared field cannot reach the tool, UI emission, or persistence."""
    handler = AutoToolEventHandler()
    ui_calls: list = []

    async def _record_tool_call(*args, **kwargs):
        ui_calls.append(("tool_call", args))

    async def _record_tool_result(*args, **kwargs):
        ui_calls.append(("tool_result", args))

    monkeypatch.setattr(handler, "_emit_tool_call", _record_tool_call)
    monkeypatch.setattr(handler, "_emit_tool_result", _record_tool_result)
    pattern = _PatternContext()
    await handler.handle_tool_dispatch(
        _event(
            "ContextOnlyAgent",
            structured_data={**PAYLOAD, "undeclared_extra": 1},
            pattern_context=pattern,
            turn=9,
        )
    )
    assert ui_calls == []
    assert pattern.data == {}
    assert persisted_snapshots == []


async def test_hitl_style_resequencing_keeps_distinct_turns_independent(
    probe_manager, persisted_snapshots, quiet_transport
):
    """Distinct turn keys still dispatch independently (pause/resume safety),
    while each turn stays exactly-once."""
    handler = AutoToolEventHandler()
    pattern = _PatternContext()
    await handler.handle_tool_dispatch(
        _event("ContextOnlyAgent", structured_data=PAYLOAD, pattern_context=pattern, turn=1)
    )
    await handler.handle_tool_dispatch(
        _event("ContextOnlyAgent", structured_data=PAYLOAD, pattern_context=pattern, turn=2)
    )
    assert pattern.data["context_only_runs"] == 2


# ---------------------------------------------------------------------------
# Cross-binding payload isolation: one validated output, multiple bindings
# ---------------------------------------------------------------------------

NESTED_PAYLOAD = {"title": "Nested Probe", "meta": {"label": "ORIGINAL"}}


def _nested_event(agent: str, pattern, *, turn: int = 21):
    return build_runtime_agent_output_validated_event(
        agent=agent,
        model_name="NestedOutput",
        structured_data={
            "title": NESTED_PAYLOAD["title"],
            "meta": dict(NESTED_PAYLOAD["meta"]),
        },
        auto_tool_call=True,
        context={"chat_id": "chat-1", "app_id": "app-1", "workflow_name": WORKFLOW},
        turn_idempotency_key=build_turn_idempotency_key("chat-1", turn),
        pattern_context_ref=pattern,
        validation_passed=True,
    )


@pytest.mark.parametrize(
    "agent", ["PairMutateFirstAgent", "PairReadFirstAgent"], ids=["mutate-first", "read-first"]
)
async def test_explicit_mutation_cannot_contaminate_other_bindings(
    probe_manager, persisted_snapshots, quiet_transport, agent
):
    """One exact validated payload, two bindings, both orders: the mutator's
    explicit nested-argument mutation never reaches the canonical payload,
    the other binding's structured_output overlay, or the event audit data."""
    handler = AutoToolEventHandler()
    pattern = _PatternContext()
    event = _nested_event(agent, pattern)
    await handler.handle_tool_dispatch(event)

    # The context-only binding saw the ORIGINAL accepted nested object.
    assert pattern.data["reader_saw_meta"] == {"label": "ORIGINAL"}
    # Same-binding consistency: the mutator's own overlay projection is
    # unaffected by its explicit-argument mutation.
    assert pattern.data["mutator_overlay_after_own_mutation"] == {"label": "ORIGINAL"}
    # Event audit data keeps the exact accepted content — no shared alias.
    assert event["structured_data"] == NESTED_PAYLOAD
    assert "injected_temp" not in event["structured_data"]["meta"]
    assert STRUCTURED_OUTPUT_KEY not in pattern.data


# ---------------------------------------------------------------------------
# Failed exact validation upstream cannot emit the runtime event at all
# ---------------------------------------------------------------------------


async def test_failed_exact_validation_emits_no_runtime_event(probe_manager, monkeypatch):
    from mozaiksai.core.workflow.outputs.runtime_events import emit_validated_agent_output

    emitted: list = []

    class _RecordingDispatcher:
        async def emit(self, event_type, payload):
            emitted.append((event_type, payload))

    monkeypatch.setattr(
        "mozaiksai.core.events.unified_event_dispatcher.get_event_dispatcher",
        lambda: _RecordingDispatcher(),
    )
    _, registry = _so.load_workflow_structured_outputs(WORKFLOW)

    class _Logger:
        def warning(self, *args, **kwargs):
            return None

        def debug(self, *args, **kwargs):
            return None

    result = await emit_validated_agent_output(
        current_agent_name="ContextOnlyAgent",
        last_reply={**PAYLOAD, "undeclared_extra": 1},
        workflow_name=WORKFLOW,
        chat_id="chat-1",
        app_id="app-1",
        user_id=None,
        turn_sequence=1,
        context_vars_dict={},
        context_bridge=None,
        structured_registry=registry,
        auto_tool_agents={"ContextOnlyAgent"},
        wf_logger=_Logger(),
    )
    assert result is None
    assert emitted == []


async def test_valid_payload_emits_the_runtime_event_exactly_once(probe_manager, monkeypatch):
    from mozaiksai.core.workflow.outputs.runtime_events import emit_validated_agent_output

    emitted: list = []

    class _RecordingDispatcher:
        async def emit(self, event_type, payload):
            emitted.append((event_type, payload))

    monkeypatch.setattr(
        "mozaiksai.core.events.unified_event_dispatcher.get_event_dispatcher",
        lambda: _RecordingDispatcher(),
    )
    _, registry = _so.load_workflow_structured_outputs(WORKFLOW)

    class _Logger:
        def warning(self, *args, **kwargs):
            return None

        def debug(self, *args, **kwargs):
            return None

    result = await emit_validated_agent_output(
        current_agent_name="ContextOnlyAgent",
        last_reply=dict(PAYLOAD),
        workflow_name=WORKFLOW,
        chat_id="chat-1",
        app_id="app-1",
        user_id=None,
        turn_sequence=1,
        context_vars_dict={"declared_key": "value"},
        context_bridge=None,
        structured_registry=registry,
        auto_tool_agents={"ContextOnlyAgent"},
        wf_logger=_Logger(),
    )
    assert result == PAYLOAD
    assert len(emitted) == 1
    event_type, payload = emitted[0]
    assert event_type == "runtime.agent_output_validated"
    assert payload["structured_data"] == PAYLOAD
    assert payload["auto_tool_call"] is True
    # The event's context snapshot carries declared context only.
    assert STRUCTURED_OUTPUT_KEY not in payload["context"].get("context_variables", {})
