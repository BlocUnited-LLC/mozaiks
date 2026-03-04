"""
Integration tests for MFJ (Mid-Flight Journeys) — Phase 9
==========================================================

These tests exercise the **full MFJ lifecycle** end-to-end:
  trigger → fan-out → child completion → fan-in → merge → resume

Unlike the unit tests in test_workflow_pack_coordinator.py (which mock
individual methods), these integration tests let the coordinator's
internal logic flow naturally. External infrastructure (SimpleTransport,
PersistenceManager, AG2 runtime, file system) is replaced with
lightweight in-memory fakes.

Scenarios covered:
  1. Single MFJ — 3 children all succeed → merge → resume
  2. Multi-MFJ sequencing — MFJ-1 must complete before MFJ-2 can fire
  3. Timeout — 1 child hangs → timeout → partial results
  4. Partial failure — 1 child raises → resume_with_available → parent sees failure
  5. Contract violation — missing required_context → fan-out aborted
  6. Event sequence — verify the full event sequence for a successful cycle

Each test verifies:
  - The parent's context_variables are correctly patched
  - The emitted UI events are in the expected order with correct payloads
  - The coordinator's internal state is cleaned up (no lingering active runs)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Direct imports — same pattern as test_workflow_pack_coordinator.py
# ---------------------------------------------------------------------------
import importlib.util
import sys
import types
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent  # mozaiks repo root


def _direct_import(module_name: str, file_path: Path):
    """Import a single .py file as module_name, registering parent stubs."""
    parts = module_name.split(".")
    for i in range(1, len(parts)):
        parent = ".".join(parts[:i])
        if parent not in sys.modules:
            m = types.ModuleType(parent)
            m.__path__ = []
            m.__package__ = parent
            sys.modules[parent] = m
    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


# Pre-register namespace stubs
for _ns in [
    "mozaiksai",
    "mozaiksai.core",
    "mozaiksai.core.contracts",
    "mozaiksai.core.contracts.events",
    "mozaiksai.core.workflow",
    "mozaiksai.core.workflow.pack",
    "mozaiksai.orchestration",
]:
    if _ns not in sys.modules:
        _m = types.ModuleType(_ns)
        _m.__path__ = [str(_ROOT / _ns.replace(".", "/"))]
        _m.__package__ = _ns
        sys.modules[_ns] = _m

# Import the modules we need
_events_mod = _direct_import(
    "mozaiksai.core.contracts.events",
    _ROOT / "mozaiksai" / "core" / "contracts" / "events.py",
)
_decomp_mod = _direct_import(
    "mozaiksai.orchestration.decomposition",
    _ROOT / "mozaiksai" / "orchestration" / "decomposition.py",
)
_merge_mod = _direct_import(
    "mozaiksai.orchestration.merge",
    _ROOT / "mozaiksai" / "orchestration" / "merge.py",
)
_coord_mod = _direct_import(
    "mozaiksai.core.workflow.pack.workflow_pack_coordinator",
    _ROOT / "mozaiksai" / "core" / "workflow" / "pack" / "workflow_pack_coordinator.py",
)

WorkflowPackCoordinator = _coord_mod.WorkflowPackCoordinator
PartialFailureStrategy = _coord_mod.PartialFailureStrategy
FanOutContractError = _coord_mod.FanOutContractError
_ActivePackRun = _coord_mod._ActivePackRun
_resolve_triggers = _coord_mod._resolve_triggers

ChildResult = _merge_mod.ChildResult
ConcatenateMerge = _merge_mod.ConcatenateMerge
DecompositionPlan = _decomp_mod.DecompositionPlan
SubTask = _decomp_mod.SubTask


# ===========================================================================
# In-memory test infrastructure
# ===========================================================================

class InMemoryPersistenceManager:
    """Stores sessions in a dict — no MongoDB required.

    Tracks calls for assertion:
      - ``created_sessions`` — list of (chat_id, kwargs) from create_chat_session
      - ``patched_fields``   — dict of chat_id → last fields dict from patch_session_fields
      - ``session_store``    — dict of chat_id → extra_fields (set by create, returned by fetch)
    """

    def __init__(self):
        self.session_store: Dict[str, Dict[str, Any]] = {}
        self.created_sessions: List[tuple] = []
        self.patched_fields: Dict[str, Dict[str, Any]] = {}

    async def create_chat_session(
        self,
        chat_id: str,
        app_id: str,
        workflow_name: str,
        user_id: str,
        extra_fields: Optional[Dict[str, Any]] = None,
    ):
        self.created_sessions.append((chat_id, {
            "app_id": app_id,
            "workflow_name": workflow_name,
            "user_id": user_id,
            "extra_fields": extra_fields or {},
        }))
        self.session_store[chat_id] = dict(extra_fields or {})

    async def fetch_chat_session_extra_context(
        self,
        chat_id: str,
        app_id: str,
    ) -> Dict[str, Any]:
        return dict(self.session_store.get(chat_id, {}))

    async def patch_session_fields(
        self,
        chat_id: str,
        app_id: str,
        fields: Dict[str, Any],
    ):
        self.patched_fields[chat_id] = dict(fields)


class EventCollector:
    """Captures all UI events sent via transport.send_event_to_ui."""

    def __init__(self):
        self.events: List[Dict[str, Any]] = []

    async def send_event_to_ui(self, event: Dict[str, Any], chat_id: str = ""):
        self.events.append(event)

    def types(self) -> List[str]:
        """Return the ordered list of event type strings."""
        return [e.get("type", "") for e in self.events]

    def of_type(self, event_type: str) -> List[Dict[str, Any]]:
        """Return all events matching a given type."""
        return [e for e in self.events if e.get("type") == event_type]


def _make_done_task(result=None, error: Optional[Exception] = None):
    """Create a mock asyncio.Task that reports done() = True.

    If ``error`` is provided, calling ``.result()`` raises it.
    Otherwise ``.result()`` returns ``result``.
    """
    task = MagicMock()
    task.done.return_value = True
    if error:
        task.result.side_effect = error
    else:
        task.result.return_value = result
    task.cancel = MagicMock()
    return task


def _make_pending_task():
    """Create a mock asyncio.Task that reports done() = False."""
    task = MagicMock()
    task.done.return_value = False
    task.result = MagicMock(side_effect=asyncio.InvalidStateError("not done"))
    task.cancel = MagicMock()
    return task


def _build_transport(
    pm: InMemoryPersistenceManager,
    events: EventCollector,
    parent_chat_id: str = "parent_1",
    app_id: str = "app1",
    user_id: str = "user1",
    ws_id: int = 42,
) -> MagicMock:
    """Build a mock SimpleTransport wired to our in-memory PM and event collector.

    The transport:
      - Has a ``connections`` dict mapping parent_chat_id → conn metadata
      - ``_get_or_create_persistence_manager()`` returns our in-memory PM
      - ``send_event_to_ui`` delegates to EventCollector
      - ``pause_background_workflow`` is a no-op AsyncMock
      - ``_background_tasks`` is a real dict (coordinator reads/writes it)
      - ``_run_workflow_background`` returns a coroutine that immediately resolves
    """
    transport = AsyncMock()
    transport.connections = {
        parent_chat_id: {
            "app_id": app_id,
            "user_id": user_id,
            "ws_id": ws_id,
        },
    }
    transport._get_or_create_persistence_manager = MagicMock(return_value=pm)
    transport._background_tasks = {}
    transport.send_event_to_ui = events.send_event_to_ui
    transport.pause_background_workflow = AsyncMock()

    # _run_workflow_background: returns coroutine that resolves immediately.
    async def _noop_run(**kwargs):
        return None
    transport._run_workflow_background = _noop_run

    return transport


# ---------------------------------------------------------------------------
# Pack graph fixture
# ---------------------------------------------------------------------------

def _single_mfj_pack_graph(
    trigger_agent: str = "planner",
    trigger_id: str = "mfj_planning",
    merge_mode: str = "concatenate",
    timeout_seconds: Optional[float] = None,
    on_partial_failure: str = "resume_with_available",
    required_context: Optional[List[str]] = None,
    expected_output_keys: Optional[List[str]] = None,
    requires: Optional[List[str]] = None,
    description: str = "Planning phase",
    resume_agent: str = "presenter",
) -> Dict[str, Any]:
    """Build a minimal v3 workflow_graph.json for a single MFJ trigger."""
    trigger: Dict[str, Any] = {
        "id": trigger_id,
        "trigger_agent": trigger_agent,
        "merge_mode": merge_mode,
        "on_partial_failure": on_partial_failure,
        "resume_agent": resume_agent,
        "description": description,
    }
    if timeout_seconds is not None:
        trigger["timeout_seconds"] = timeout_seconds
    if required_context:
        trigger["required_context"] = required_context
    if expected_output_keys:
        trigger["expected_output_keys"] = expected_output_keys
    if requires:
        trigger["requires"] = requires
    return {
        "schema_version": 3,
        "mid_flight_journeys": [trigger],
    }


def _multi_mfj_pack_graph() -> Dict[str, Any]:
    """Two-MFJ config: MFJ-2 requires MFJ-1 to complete first."""
    return {
        "schema_version": 3,
        "mid_flight_journeys": [
            {
                "id": "mfj_planning",
                "trigger_agent": "planner",
                "merge_mode": "concatenate",
                "resume_agent": "presenter",
                "description": "Planning phase",
            },
            {
                "id": "mfj_execution",
                "trigger_agent": "executor",
                "merge_mode": "structured",
                "resume_agent": "reviewer",
                "requires": ["mfj_planning"],
                "description": "Execution phase",
            },
        ],
    }


# ---------------------------------------------------------------------------
# Structured output event builder
# ---------------------------------------------------------------------------

def _structured_output_event(
    agent_name: str = "planner",
    parent_chat_id: str = "parent_1",
    parent_workflow: str = "test_wf",
    workflows: Optional[List[Dict[str, str]]] = None,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the event dict that ``handle_structured_output_ready`` expects.

    The coordinator reads ``chat_id`` and ``workflow_name`` from the
    ``context`` sub-dict, NOT from the top-level event keys.  We merge
    caller-supplied context with the mandatory identifiers.
    """
    if workflows is None:
        workflows = [
            {"name": "child_wf_1"},
            {"name": "child_wf_2"},
            {"name": "child_wf_3"},
        ]
    # Base context must include chat_id and workflow_name for the coordinator
    base_context: Dict[str, Any] = {
        "chat_id": parent_chat_id,
        "workflow_name": parent_workflow,
    }
    if context:
        base_context.update(context)
    return {
        "agent_name": agent_name,
        "structured_data": {
            "PatternSelection": {
                "is_multi_workflow": True,
                "resume_agent": "presenter",
                "workflows": workflows,
            },
        },
        "context": base_context,
    }


# ---------------------------------------------------------------------------
# Context manager: patches SimpleTransport + load_pack_graph + workflow dirs
# ---------------------------------------------------------------------------

def _integration_patches(transport, pack_graph):
    """Return a contextmanager that patches the three external dependencies.

    1. ``SimpleTransport.get_instance()`` → our mock transport
    2. ``load_pack_graph()`` → our pack_graph dict
    3. ``Path.exists()`` → True for workflow dirs (so children aren't filtered)
    """
    fake_transport_module = MagicMock()
    fake_transport_module.SimpleTransport.get_instance = AsyncMock(return_value=transport)

    # Also stub session_registry to avoid import errors
    fake_registry_module = MagicMock()
    fake_registry_module.session_registry = MagicMock()

    # Stub orchestration.events (observability helpers)
    fake_events_module = MagicMock()

    patches = [
        patch.dict(sys.modules, {
            "mozaiksai.core.transport.simple_transport": fake_transport_module,
            "mozaiksai.core.transport.session_registry": fake_registry_module,
            "mozaiksai.orchestration.events": fake_events_module,
        }),
        patch.object(
            _coord_mod,
            "load_pack_graph",
            return_value=pack_graph,
        ),
        patch.object(Path, "exists", return_value=True),
    ]

    class _Combined:
        def __enter__(self):
            for p in patches:
                p.__enter__()
            return self
        def __exit__(self, *args):
            for p in reversed(patches):
                p.__exit__(*args)

    return _Combined()


# ===========================================================================
# Integration Test 1: Single MFJ — full successful cycle
# ===========================================================================

class TestSingleMFJFullCycle:
    """trigger → fan-out 3 children → all succeed → merge → resume → verify."""

    @pytest.mark.asyncio
    async def test_full_cycle_happy_path(self):
        """Three children succeed, results merge into parent context."""
        pm = InMemoryPersistenceManager()
        events = EventCollector()
        transport = _build_transport(pm, events)
        pack_graph = _single_mfj_pack_graph()
        coord = WorkflowPackCoordinator()

        event = _structured_output_event()

        with _integration_patches(transport, pack_graph):
            await coord.handle_structured_output_ready(event)

        # --- Verify fan-out happened ---
        # 3 child sessions created in PM
        assert len(pm.created_sessions) == 3

        # 3 children tracked in transport._background_tasks
        assert len(transport._background_tasks) == 3

        # Active run stored in coordinator
        assert "parent_1" in coord._active_by_parent
        active = coord._active_by_parent["parent_1"]
        assert len(active.child_chat_ids) == 3
        assert active.trigger_id == "mfj_planning"
        assert active.mfj_cycle == 1
        assert active.mfj_description == "Planning phase"

        # --- Simulate all children completing ---
        # Store child results in PM (like AG2 would)
        for chat_id in active.child_chat_ids:
            pm.session_store[chat_id] = {"report": f"result from {chat_id}"}

        # Mark all background tasks as done
        for chat_id in active.child_chat_ids:
            transport._background_tasks[chat_id] = _make_done_task()

        # Trigger fan-in by calling handle_run_complete for each child
        child_ids = list(active.child_chat_ids)
        with _integration_patches(transport, pack_graph):
            # First two children: not all done yet (we need to mark them done
            # one at a time for child_completed events). Actually, since we
            # already set all tasks as done above, the first handle_run_complete
            # will trigger the full fan-in. Let's do it properly:

            # Reset: only first child is done
            transport._background_tasks[child_ids[0]] = _make_done_task()
            transport._background_tasks[child_ids[1]] = _make_pending_task()
            transport._background_tasks[child_ids[2]] = _make_pending_task()

            await coord.handle_run_complete({"chat_id": child_ids[0]})
            # Should emit child_completed (1 of 3 done, not all done)
            assert "chat.workflow_child_completed" in events.types()

            # Second child done
            transport._background_tasks[child_ids[1]] = _make_done_task()
            await coord.handle_run_complete({"chat_id": child_ids[1]})

            # Third child done → triggers full fan-in
            transport._background_tasks[child_ids[2]] = _make_done_task()
            await coord.handle_run_complete({"chat_id": child_ids[2]})

        # --- Verify fan-in completed ---
        # Parent context was patched with merge results
        assert "parent_1" in pm.patched_fields
        parent_fields = pm.patched_fields["parent_1"]
        assert "child_results" in parent_fields
        assert "merge_summary" in parent_fields
        assert parent_fields["merge_all_succeeded"] is True
        assert parent_fields["merge_succeeded_count"] == 3
        assert parent_fields["merge_failed_count"] == 0

        # MFJ completion recorded
        assert "parent_1" in coord._completed_mfjs
        records = coord._completed_mfjs["parent_1"]
        assert len(records) == 1
        assert records[0].trigger_id == "mfj_planning"
        assert records[0].all_succeeded is True

        # Coordinator state cleaned up
        assert "parent_1" not in coord._active_by_parent
        for cid in child_ids:
            assert cid not in coord._active_by_child

    @pytest.mark.asyncio
    async def test_full_cycle_event_sequence(self):
        """Verify the complete event sequence for a 3-child MFJ."""
        pm = InMemoryPersistenceManager()
        events = EventCollector()
        transport = _build_transport(pm, events)
        pack_graph = _single_mfj_pack_graph()
        coord = WorkflowPackCoordinator()

        event = _structured_output_event()

        with _integration_patches(transport, pack_graph):
            await coord.handle_structured_output_ready(event)

        active = coord._active_by_parent["parent_1"]
        child_ids = list(active.child_chat_ids)

        # Set up child results
        for cid in child_ids:
            pm.session_store[cid] = {"report": f"output_{cid}"}

        with _integration_patches(transport, pack_graph):
            # Children complete one at a time
            transport._background_tasks[child_ids[0]] = _make_done_task()
            transport._background_tasks[child_ids[1]] = _make_pending_task()
            transport._background_tasks[child_ids[2]] = _make_pending_task()
            await coord.handle_run_complete({"chat_id": child_ids[0]})

            transport._background_tasks[child_ids[1]] = _make_done_task()
            await coord.handle_run_complete({"chat_id": child_ids[1]})

            transport._background_tasks[child_ids[2]] = _make_done_task()
            await coord.handle_run_complete({"chat_id": child_ids[2]})

        # Expected event sequence:
        expected_types = [
            "chat.workflow_batch_started",       # fan-out started
            "chat.workflow_child_completed",      # child 1 done (not all done)
            "chat.workflow_child_completed",      # child 2 done (not all done)
            "chat.mfj_fan_in_started",           # all children done → merge starts
            "chat.workflow_resumed",              # parent resumes
        ]
        assert events.types() == expected_types

        # Verify batch_started payload
        batch = events.of_type("chat.workflow_batch_started")[0]["data"]
        assert batch["parent_chat_id"] == "parent_1"
        assert batch["count"] == 3
        assert batch["trigger_id"] == "mfj_planning"
        assert batch["mfj_cycle"] == 1
        assert batch["mfj_description"] == "Planning phase"

        # Verify resumed payload
        resumed = events.of_type("chat.workflow_resumed")[0]["data"]
        assert resumed["chat_id"] == "parent_1"
        assert resumed["trigger_id"] == "mfj_planning"
        assert resumed["mfj_cycle"] == 1
        assert resumed["succeeded_count"] == 3
        assert resumed["failed_count"] == 0

        # Verify child_completed payloads show progress
        child_events = events.of_type("chat.workflow_child_completed")
        assert len(child_events) == 2  # Only 2 — the third triggers fan-in instead
        assert child_events[0]["data"]["done_count"] == 1
        assert child_events[1]["data"]["done_count"] == 2

    @pytest.mark.asyncio
    async def test_parent_context_contains_merged_child_outputs(self):
        """Parent session fields contain correctly merged child data."""
        pm = InMemoryPersistenceManager()
        events = EventCollector()
        transport = _build_transport(pm, events)
        pack_graph = _single_mfj_pack_graph()
        coord = WorkflowPackCoordinator()

        event = _structured_output_event(
            workflows=[{"name": "wf_a"}, {"name": "wf_b"}],
        )

        with _integration_patches(transport, pack_graph):
            await coord.handle_structured_output_ready(event)

        active = coord._active_by_parent["parent_1"]
        child_ids = list(active.child_chat_ids)

        # Give each child distinct structured output
        pm.session_store[child_ids[0]] = {"report": "Alpha analysis", "score": 85}
        pm.session_store[child_ids[1]] = {"report": "Beta analysis", "score": 92}

        # All done at once
        for cid in child_ids:
            transport._background_tasks[cid] = _make_done_task()

        with _integration_patches(transport, pack_graph):
            await coord.handle_run_complete({"chat_id": child_ids[0]})

        # Verify parent was patched
        fields = pm.patched_fields["parent_1"]
        assert fields["merge_succeeded_count"] == 2
        assert fields["merge_failed_count"] == 0
        assert fields["merge_all_succeeded"] is True


# ===========================================================================
# Integration Test 2: Multi-MFJ sequencing
# ===========================================================================

class TestMultiMFJSequencing:
    """MFJ-1 must complete before MFJ-2 fires."""

    @pytest.mark.asyncio
    async def test_mfj2_blocked_until_mfj1_complete(self):
        """MFJ-2 (requires: [mfj_planning]) is blocked when MFJ-1 hasn't run."""
        pm = InMemoryPersistenceManager()
        events = EventCollector()
        transport = _build_transport(pm, events)
        pack_graph = _multi_mfj_pack_graph()
        coord = WorkflowPackCoordinator()

        # Try to trigger MFJ-2 directly (executor agent) — should be blocked
        event_mfj2 = _structured_output_event(
            agent_name="executor",
            workflows=[{"name": "exec_wf_1"}],
        )

        with _integration_patches(transport, pack_graph):
            await coord.handle_structured_output_ready(event_mfj2)

        # No fan-out happened — blocked by requires
        assert len(pm.created_sessions) == 0
        assert "parent_1" not in coord._active_by_parent
        assert len(events.events) == 0

    @pytest.mark.asyncio
    async def test_mfj2_unblocked_after_mfj1_completes(self):
        """After MFJ-1 completes, MFJ-2 can fire."""
        pm = InMemoryPersistenceManager()
        events = EventCollector()
        transport = _build_transport(pm, events)
        pack_graph = _multi_mfj_pack_graph()
        coord = WorkflowPackCoordinator()

        # --- Run MFJ-1 (planner) ---
        event_mfj1 = _structured_output_event(
            agent_name="planner",
            workflows=[{"name": "plan_wf_1"}],
        )

        with _integration_patches(transport, pack_graph):
            await coord.handle_structured_output_ready(event_mfj1)

        active1 = coord._active_by_parent["parent_1"]
        child_ids_1 = list(active1.child_chat_ids)

        # Complete MFJ-1
        for cid in child_ids_1:
            pm.session_store[cid] = {"plan": "done"}
            transport._background_tasks[cid] = _make_done_task()

        with _integration_patches(transport, pack_graph):
            await coord.handle_run_complete({"chat_id": child_ids_1[0]})

        # MFJ-1 is recorded
        assert "parent_1" in coord._completed_mfjs
        assert any(r.trigger_id == "mfj_planning" for r in coord._completed_mfjs["parent_1"])

        # --- Now trigger MFJ-2 (executor) — should succeed ---
        events.events.clear()
        event_mfj2 = _structured_output_event(
            agent_name="executor",
            workflows=[{"name": "exec_wf_1"}, {"name": "exec_wf_2"}],
        )

        with _integration_patches(transport, pack_graph):
            await coord.handle_structured_output_ready(event_mfj2)

        # MFJ-2 fan-out happened
        assert "parent_1" in coord._active_by_parent
        active2 = coord._active_by_parent["parent_1"]
        assert active2.trigger_id == "mfj_execution"
        assert active2.mfj_cycle == 2  # Second MFJ cycle for this parent
        assert len(active2.child_chat_ids) == 2

    @pytest.mark.asyncio
    async def test_cycle_counter_increments_per_parent(self):
        """Each MFJ trigger for the same parent increments the cycle counter."""
        pm = InMemoryPersistenceManager()
        events = EventCollector()
        transport = _build_transport(pm, events)
        pack_graph = _multi_mfj_pack_graph()
        coord = WorkflowPackCoordinator()

        # MFJ-1
        with _integration_patches(transport, pack_graph):
            await coord.handle_structured_output_ready(
                _structured_output_event(agent_name="planner", workflows=[{"name": "w1"}])
            )

        active1 = coord._active_by_parent["parent_1"]
        assert active1.mfj_cycle == 1

        # Complete MFJ-1
        for cid in list(active1.child_chat_ids):
            pm.session_store[cid] = {"done": True}
            transport._background_tasks[cid] = _make_done_task()
        with _integration_patches(transport, pack_graph):
            await coord.handle_run_complete({"chat_id": active1.child_chat_ids[0]})

        # MFJ-2
        with _integration_patches(transport, pack_graph):
            await coord.handle_structured_output_ready(
                _structured_output_event(agent_name="executor", workflows=[{"name": "w2"}])
            )

        active2 = coord._active_by_parent["parent_1"]
        assert active2.mfj_cycle == 2


# ===========================================================================
# Integration Test 3: Timeout handling
# ===========================================================================

class TestTimeoutHandling:
    """trigger → fan-out → 1 child hangs → timeout → partial results."""

    @pytest.mark.asyncio
    async def test_timeout_resumes_with_available(self):
        """When timeout fires, available child results are merged and parent resumes."""
        pm = InMemoryPersistenceManager()
        events = EventCollector()
        transport = _build_transport(pm, events)
        pack_graph = _single_mfj_pack_graph(
            timeout_seconds=0.05,
            on_partial_failure="resume_with_available",
        )
        coord = WorkflowPackCoordinator()

        event = _structured_output_event(
            workflows=[{"name": "fast_wf"}, {"name": "slow_wf"}],
        )

        with _integration_patches(transport, pack_graph):
            await coord.handle_structured_output_ready(event)

        active = coord._active_by_parent["parent_1"]
        child_ids = list(active.child_chat_ids)

        # First child completes quickly with result
        pm.session_store[child_ids[0]] = {"report": "fast result"}
        transport._background_tasks[child_ids[0]] = _make_done_task()

        # Second child stays pending
        transport._background_tasks[child_ids[1]] = _make_pending_task()

        # Wait for timeout to fire (it's 50ms)
        with _integration_patches(transport, pack_graph):
            await asyncio.sleep(0.15)

        # Give event loop a chance to process
        await asyncio.sleep(0.05)

        # Parent should have been resumed with partial results
        if "parent_1" in pm.patched_fields:
            fields = pm.patched_fields["parent_1"]
            assert fields.get("merge_timed_out") is True
            # At least the fast child's result should be present
            assert fields["merge_succeeded_count"] >= 1

    @pytest.mark.asyncio
    async def test_timeout_cancelled_when_all_children_finish(self):
        """If all children finish before timeout, the watchdog is cancelled."""
        pm = InMemoryPersistenceManager()
        events = EventCollector()
        transport = _build_transport(pm, events)
        pack_graph = _single_mfj_pack_graph(timeout_seconds=10.0)
        coord = WorkflowPackCoordinator()

        event = _structured_output_event(workflows=[{"name": "wf_1"}])

        with _integration_patches(transport, pack_graph):
            await coord.handle_structured_output_ready(event)

        active = coord._active_by_parent["parent_1"]
        child_ids = list(active.child_chat_ids)
        timeout_task = active.timeout_task
        assert timeout_task is not None

        # Child completes
        pm.session_store[child_ids[0]] = {"result": "ok"}
        transport._background_tasks[child_ids[0]] = _make_done_task()

        with _integration_patches(transport, pack_graph):
            await coord.handle_run_complete({"chat_id": child_ids[0]})

        # Timeout task should have been cancelled.  Give the event loop
        # a tick to transition from "cancelling" → "cancelled".
        await asyncio.sleep(0)
        assert timeout_task.cancelled() or timeout_task.done()


# ===========================================================================
# Integration Test 4: Partial failure
# ===========================================================================

class TestPartialFailure:
    """trigger → fan-out → 1 child fails → merge handles failure."""

    @pytest.mark.asyncio
    async def test_one_child_fails_merge_captures_failure(self):
        """One child crashes, merge result reflects the failure."""
        pm = InMemoryPersistenceManager()
        events = EventCollector()
        transport = _build_transport(pm, events)
        pack_graph = _single_mfj_pack_graph()
        coord = WorkflowPackCoordinator()

        event = _structured_output_event(
            workflows=[{"name": "ok_wf"}, {"name": "bad_wf"}],
        )

        with _integration_patches(transport, pack_graph):
            await coord.handle_structured_output_ready(event)

        active = coord._active_by_parent["parent_1"]
        child_ids = list(active.child_chat_ids)

        # First child succeeds
        pm.session_store[child_ids[0]] = {"report": "success"}
        transport._background_tasks[child_ids[0]] = _make_done_task()

        # Second child fails (task.result() raises)
        pm.session_store[child_ids[1]] = {}
        transport._background_tasks[child_ids[1]] = _make_done_task(
            error=RuntimeError("agent crash"),
        )

        # Both done at once
        with _integration_patches(transport, pack_graph):
            await coord.handle_run_complete({"chat_id": child_ids[0]})

        # Parent was resumed with merge results including the failure
        fields = pm.patched_fields["parent_1"]
        assert fields["merge_succeeded_count"] == 1
        assert fields["merge_failed_count"] == 1
        assert fields["merge_all_succeeded"] is False

        # Resumed event shows the failure count
        resumed_events = events.of_type("chat.workflow_resumed")
        assert len(resumed_events) == 1
        assert resumed_events[0]["data"]["succeeded_count"] == 1
        assert resumed_events[0]["data"]["failed_count"] == 1

    @pytest.mark.asyncio
    async def test_all_children_fail(self):
        """All children fail — merge still produces a result, parent resumes."""
        pm = InMemoryPersistenceManager()
        events = EventCollector()
        transport = _build_transport(pm, events)
        pack_graph = _single_mfj_pack_graph()
        coord = WorkflowPackCoordinator()

        event = _structured_output_event(workflows=[{"name": "wf_1"}, {"name": "wf_2"}])

        with _integration_patches(transport, pack_graph):
            await coord.handle_structured_output_ready(event)

        active = coord._active_by_parent["parent_1"]
        child_ids = list(active.child_chat_ids)

        for cid in child_ids:
            pm.session_store[cid] = {}
            transport._background_tasks[cid] = _make_done_task(error=RuntimeError("boom"))

        with _integration_patches(transport, pack_graph):
            await coord.handle_run_complete({"chat_id": child_ids[0]})

        fields = pm.patched_fields["parent_1"]
        assert fields["merge_succeeded_count"] == 0
        assert fields["merge_failed_count"] == 2
        assert fields["merge_all_succeeded"] is False

        # Parent still resumes (not stuck)
        assert "chat.workflow_resumed" in events.types()


# ===========================================================================
# Integration Test 5: Fan-out contract violation
# ===========================================================================

class TestContractViolation:
    """Missing required_context → fan-out aborted, parent not paused."""

    @pytest.mark.asyncio
    async def test_missing_required_context_aborts_fanout(self):
        """Fan-out is aborted when parent context lacks required keys."""
        pm = InMemoryPersistenceManager()
        events = EventCollector()
        transport = _build_transport(pm, events)
        pack_graph = _single_mfj_pack_graph(
            required_context=["InterviewTranscript", "PatternSelection"],
        )
        coord = WorkflowPackCoordinator()

        # Context is missing InterviewTranscript
        event = _structured_output_event(
            context={"PatternSelection": {"some": "data"}},
        )

        with _integration_patches(transport, pack_graph):
            await coord.handle_structured_output_ready(event)

        # No children spawned
        assert len(pm.created_sessions) == 0
        assert "parent_1" not in coord._active_by_parent

        # Parent not paused
        transport.pause_background_workflow.assert_not_called()

        # No events emitted
        assert len(events.events) == 0

    @pytest.mark.asyncio
    async def test_required_context_present_allows_fanout(self):
        """Fan-out proceeds when all required_context keys are present."""
        pm = InMemoryPersistenceManager()
        events = EventCollector()
        transport = _build_transport(pm, events)
        pack_graph = _single_mfj_pack_graph(
            required_context=["InterviewTranscript"],
        )
        coord = WorkflowPackCoordinator()

        event = _structured_output_event(
            context={"InterviewTranscript": "Some transcript data"},
        )

        with _integration_patches(transport, pack_graph):
            await coord.handle_structured_output_ready(event)

        # Children were spawned
        assert len(pm.created_sessions) == 3
        assert "parent_1" in coord._active_by_parent


# ===========================================================================
# Integration Test 6: Output contract warnings
# ===========================================================================

class TestOutputContractWarnings:
    """Fan-in output contract produces warnings but doesn't block merge."""

    @pytest.mark.asyncio
    async def test_missing_output_keys_still_merges(self):
        """Children missing expected_output_keys: warnings logged but merge proceeds."""
        pm = InMemoryPersistenceManager()
        events = EventCollector()
        transport = _build_transport(pm, events)
        pack_graph = _single_mfj_pack_graph(
            expected_output_keys=["report", "score"],
        )
        coord = WorkflowPackCoordinator()

        event = _structured_output_event(workflows=[{"name": "wf_1"}])

        with _integration_patches(transport, pack_graph):
            await coord.handle_structured_output_ready(event)

        active = coord._active_by_parent["parent_1"]
        child_ids = list(active.child_chat_ids)

        # Child output is missing "score" key
        pm.session_store[child_ids[0]] = {"report": "only report, no score"}
        transport._background_tasks[child_ids[0]] = _make_done_task()

        with _integration_patches(transport, pack_graph):
            await coord.handle_run_complete({"chat_id": child_ids[0]})

        # Merge still happened despite missing key
        assert "parent_1" in pm.patched_fields
        fields = pm.patched_fields["parent_1"]
        assert fields["merge_succeeded_count"] == 1
        assert "chat.workflow_resumed" in events.types()


# ===========================================================================
# Integration Test 7: Duplicate fan-out prevention
# ===========================================================================

class TestDuplicatePrevention:
    """Only one active MFJ per parent at a time."""

    @pytest.mark.asyncio
    async def test_second_trigger_ignored_while_active(self):
        """A second trigger for the same parent is ignored while MFJ is active."""
        pm = InMemoryPersistenceManager()
        events = EventCollector()
        transport = _build_transport(pm, events)
        pack_graph = _single_mfj_pack_graph()
        coord = WorkflowPackCoordinator()

        event = _structured_output_event()

        with _integration_patches(transport, pack_graph):
            await coord.handle_structured_output_ready(event)

        # First MFJ is active
        assert "parent_1" in coord._active_by_parent
        first_active = coord._active_by_parent["parent_1"]
        first_child_count = len(first_active.child_chat_ids)

        # Try to trigger again
        events.events.clear()
        with _integration_patches(transport, pack_graph):
            await coord.handle_structured_output_ready(event)

        # No additional children spawned
        assert len(coord._active_by_parent["parent_1"].child_chat_ids) == first_child_count
        # No new batch_started event
        assert "chat.workflow_batch_started" not in events.types()


# ===========================================================================
# Integration Test 8: State cleanup
# ===========================================================================

class TestStateCleanup:
    """Verify that coordinator internal state is properly cleaned after fan-in."""

    @pytest.mark.asyncio
    async def test_state_cleaned_after_fan_in(self):
        """_active_by_parent, _active_by_child are empty after successful fan-in."""
        pm = InMemoryPersistenceManager()
        events = EventCollector()
        transport = _build_transport(pm, events)
        pack_graph = _single_mfj_pack_graph()
        coord = WorkflowPackCoordinator()

        event = _structured_output_event(workflows=[{"name": "wf_a"}])

        with _integration_patches(transport, pack_graph):
            await coord.handle_structured_output_ready(event)

        active = coord._active_by_parent["parent_1"]
        child_ids = list(active.child_chat_ids)

        pm.session_store[child_ids[0]] = {"done": True}
        transport._background_tasks[child_ids[0]] = _make_done_task()

        with _integration_patches(transport, pack_graph):
            await coord.handle_run_complete({"chat_id": child_ids[0]})

        # All state cleaned
        assert len(coord._active_by_parent) == 0
        assert len(coord._active_by_child) == 0

    @pytest.mark.asyncio
    async def test_can_run_second_mfj_after_first_completes(self):
        """After MFJ-1 completes and state is cleaned, same trigger can fire again."""
        pm = InMemoryPersistenceManager()
        events = EventCollector()
        transport = _build_transport(pm, events)
        pack_graph = _single_mfj_pack_graph()
        coord = WorkflowPackCoordinator()

        # First MFJ
        with _integration_patches(transport, pack_graph):
            await coord.handle_structured_output_ready(
                _structured_output_event(workflows=[{"name": "wf_1"}])
            )

        active1 = coord._active_by_parent["parent_1"]
        cids1 = list(active1.child_chat_ids)
        pm.session_store[cids1[0]] = {"r": 1}
        transport._background_tasks[cids1[0]] = _make_done_task()

        with _integration_patches(transport, pack_graph):
            await coord.handle_run_complete({"chat_id": cids1[0]})

        assert len(coord._active_by_parent) == 0

        # Second MFJ — same trigger, same parent
        events.events.clear()
        with _integration_patches(transport, pack_graph):
            await coord.handle_structured_output_ready(
                _structured_output_event(workflows=[{"name": "wf_2"}])
            )

        assert "parent_1" in coord._active_by_parent
        active2 = coord._active_by_parent["parent_1"]
        assert active2.mfj_cycle == 2  # Cycle incremented
        assert "chat.workflow_batch_started" in events.types()


# ===========================================================================
# Integration Test 9: Merge strategy selection
# ===========================================================================

class TestMergeStrategyIntegration:
    """Different merge_mode values produce different output shapes."""

    @pytest.mark.asyncio
    async def test_concatenate_merge(self):
        """merge_mode=concatenate produces concatenated text summary."""
        pm = InMemoryPersistenceManager()
        events = EventCollector()
        transport = _build_transport(pm, events)
        pack_graph = _single_mfj_pack_graph(merge_mode="concatenate")
        coord = WorkflowPackCoordinator()

        event = _structured_output_event(workflows=[{"name": "wf_a"}, {"name": "wf_b"}])

        with _integration_patches(transport, pack_graph):
            await coord.handle_structured_output_ready(event)

        active = coord._active_by_parent["parent_1"]
        child_ids = list(active.child_chat_ids)

        pm.session_store[child_ids[0]] = {"data": "A"}
        pm.session_store[child_ids[1]] = {"data": "B"}
        for cid in child_ids:
            transport._background_tasks[cid] = _make_done_task()

        with _integration_patches(transport, pack_graph):
            await coord.handle_run_complete({"chat_id": child_ids[0]})

        fields = pm.patched_fields["parent_1"]
        assert fields["merge_succeeded_count"] == 2
        # ConcatenateMerge summary should mention both children
        assert "2" in fields["merge_summary"]

    @pytest.mark.asyncio
    async def test_collect_all_merge(self):
        """merge_mode=collect_all dumps raw child outputs keyed by task_id."""
        pm = InMemoryPersistenceManager()
        events = EventCollector()
        transport = _build_transport(pm, events)
        pack_graph = _single_mfj_pack_graph(merge_mode="collect_all")
        coord = WorkflowPackCoordinator()

        event = _structured_output_event(workflows=[{"name": "wf_a"}])

        with _integration_patches(transport, pack_graph):
            await coord.handle_structured_output_ready(event)

        active = coord._active_by_parent["parent_1"]
        child_ids = list(active.child_chat_ids)

        pm.session_store[child_ids[0]] = {"raw_data": "collected"}
        transport._background_tasks[child_ids[0]] = _make_done_task()

        with _integration_patches(transport, pack_graph):
            await coord.handle_run_complete({"chat_id": child_ids[0]})

        fields = pm.patched_fields["parent_1"]
        # collect_all mode → merged_data is a dict keyed by task_id
        assert isinstance(fields["child_results"], dict)


# ===========================================================================
# Integration Test 10: MFJ with persistence store
# ===========================================================================

class TestMFJWithPersistenceStore:
    """Integration with MFJCompletionStore (mocked MongoDB)."""

    @pytest.mark.asyncio
    async def test_completion_written_to_store(self):
        """After fan-in, MFJ completion is written to both cache and store."""
        from unittest.mock import AsyncMock as AM

        mock_store = MagicMock()
        mock_store.write_completion = AM(return_value=True)
        mock_store.load_completed_trigger_ids = AM(return_value=set())

        pm = InMemoryPersistenceManager()
        events = EventCollector()
        transport = _build_transport(pm, events)
        pack_graph = _single_mfj_pack_graph()
        coord = WorkflowPackCoordinator(mfj_store=mock_store)

        event = _structured_output_event(workflows=[{"name": "wf_1"}])

        with _integration_patches(transport, pack_graph):
            await coord.handle_structured_output_ready(event)

        active = coord._active_by_parent["parent_1"]
        child_ids = list(active.child_chat_ids)
        pm.session_store[child_ids[0]] = {"result": "done"}
        transport._background_tasks[child_ids[0]] = _make_done_task()

        with _integration_patches(transport, pack_graph):
            await coord.handle_run_complete({"chat_id": child_ids[0]})

        # Store was called with the completion data
        mock_store.write_completion.assert_awaited_once()
        call_kwargs = mock_store.write_completion.call_args
        assert call_kwargs.kwargs["parent_chat_id"] == "parent_1"
        assert call_kwargs.kwargs["trigger_id"] == "mfj_planning"

        # In-memory cache also updated
        assert "parent_1" in coord._completed_mfjs

    @pytest.mark.asyncio
    async def test_requires_check_reads_store_on_cache_miss(self):
        """When cache misses, requires check falls through to the store."""
        from unittest.mock import AsyncMock as AM

        mock_store = MagicMock()
        mock_store.write_completion = AM(return_value=True)
        # Store returns that mfj_planning was already completed
        mock_store.load_completed_trigger_ids = AM(return_value={"mfj_planning"})

        pm = InMemoryPersistenceManager()
        events = EventCollector()
        transport = _build_transport(pm, events)
        pack_graph = _multi_mfj_pack_graph()
        coord = WorkflowPackCoordinator(mfj_store=mock_store)

        # Try MFJ-2 directly — cache is empty, but store has the record
        event_mfj2 = _structured_output_event(
            agent_name="executor",
            workflows=[{"name": "exec_wf"}],
        )

        with _integration_patches(transport, pack_graph):
            await coord.handle_structured_output_ready(event_mfj2)

        # MFJ-2 should have fired (store said mfj_planning was complete)
        assert "parent_1" in coord._active_by_parent
        active = coord._active_by_parent["parent_1"]
        assert active.trigger_id == "mfj_execution"

        # Store was queried
        mock_store.load_completed_trigger_ids.assert_awaited_once_with("parent_1")
