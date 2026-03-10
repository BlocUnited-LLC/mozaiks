# ==============================================================================
# Tests for MFJ observability — MFJObserver + MFJObservationContext
# (mozaiksai.core.workflow.pack.mfj_observability)
# ==============================================================================

import time

import pytest

from tests.import_utils import import_module_directly

_obs_mod = import_module_directly("mozaiksai.core.workflow.pack.mfj_observability")
MFJObserver = _obs_mod.MFJObserver
MFJObservationContext = _obs_mod.MFJObservationContext
get_mfj_observer = _obs_mod.get_mfj_observer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def observer():
    return MFJObserver()


@pytest.fixture
def ctx(observer):
    return observer.start_cycle(
        trigger_id="trigger_abc",
        parent_chat_id="chat_parent_1",
        workflow_name="TestWorkflow",
    )


# ---------------------------------------------------------------------------
# MFJObserver.start_cycle
# ---------------------------------------------------------------------------


class TestStartCycle:
    def test_returns_observation_context(self, observer):
        ctx = observer.start_cycle(
            trigger_id="t1",
            parent_chat_id="chat_1",
            workflow_name="Wf",
        )
        assert isinstance(ctx, MFJObservationContext)

    def test_populates_fields(self, observer):
        ctx = observer.start_cycle(
            trigger_id="t_xyz",
            parent_chat_id="chat_abc",
            workflow_name="MyWorkflow",
        )
        assert ctx.trigger_id == "t_xyz"
        assert ctx.parent_chat_id == "chat_abc"
        assert ctx.workflow_name == "MyWorkflow"

    def test_assigns_unique_trace_ids(self, observer):
        ctx1 = observer.start_cycle(trigger_id="t1", parent_chat_id="p1", workflow_name="W")
        ctx2 = observer.start_cycle(trigger_id="t2", parent_chat_id="p2", workflow_name="W")
        assert ctx1.trace_id != ctx2.trace_id

    def test_started_at_is_recent(self, observer):
        before = time.perf_counter()
        ctx = observer.start_cycle(trigger_id="t", parent_chat_id="p", workflow_name="W")
        after = time.perf_counter()
        assert before <= ctx.started_at <= after

    def test_child_started_at_is_empty_initially(self, observer):
        ctx = observer.start_cycle(trigger_id="t", parent_chat_id="p", workflow_name="W")
        assert ctx.child_started_at == {}


# ---------------------------------------------------------------------------
# MFJObserver counter mechanics
# ---------------------------------------------------------------------------


class TestObserverCounters:
    def test_initial_counters_all_zero(self, observer):
        metrics = observer.snapshot_metrics()
        assert metrics["mfj.fan_out.total"] == 0
        assert metrics["mfj.fan_in.total"] == 0
        assert metrics["mfj.timeout.total"] == 0
        assert metrics["mfj.partial_failure.total"] == 0
        assert metrics["mfj.contract_violation.total"] == 0

    def test_on_fan_out_started_increments(self, observer, ctx):
        observer.on_fan_out_started(ctx, child_count=3, spawn_mode="workflow")
        assert observer.snapshot_metrics()["mfj.fan_out.total"] == 1

    def test_on_fan_in_started_increments(self, observer, ctx):
        observer.on_fan_in_started(ctx, child_count=3, reason="all_children_done")
        assert observer.snapshot_metrics()["mfj.fan_in.total"] == 1

    def test_on_timeout_increments(self, observer, ctx):
        observer.on_timeout(ctx, timeout_seconds=30)
        assert observer.snapshot_metrics()["mfj.timeout.total"] == 1

    def test_on_contract_violation_increments(self, observer):
        observer.on_contract_violation(
            parent_chat_id="chat_1",
            trigger_id="t1",
            missing=["key_a"],
            contract="input",
        )
        assert observer.snapshot_metrics()["mfj.contract_violation.total"] == 1

    def test_on_fan_in_completed_with_failures_increments_partial(self, observer, ctx):
        observer.on_fan_in_completed(
            ctx, succeeded_count=2, failed_count=1, strategy="collect_all"
        )
        assert observer.snapshot_metrics()["mfj.partial_failure.total"] == 1

    def test_on_fan_in_completed_all_success_no_partial_increment(self, observer, ctx):
        observer.on_fan_in_completed(
            ctx, succeeded_count=3, failed_count=0, strategy="collect_all"
        )
        assert observer.snapshot_metrics()["mfj.partial_failure.total"] == 0

    def test_multiple_cycles_accumulate_counters(self, observer):
        for i in range(5):
            c = observer.start_cycle(trigger_id=f"t{i}", parent_chat_id=f"p{i}", workflow_name="W")
            observer.on_fan_out_started(c, child_count=2, spawn_mode="workflow")
        assert observer.snapshot_metrics()["mfj.fan_out.total"] == 5

    def test_snapshot_is_a_copy(self, observer, ctx):
        snap1 = observer.snapshot_metrics()
        observer.on_fan_out_started(ctx, child_count=1, spawn_mode="workflow")
        snap2 = observer.snapshot_metrics()
        # Original snapshot must not be mutated
        assert snap1["mfj.fan_out.total"] == 0
        assert snap2["mfj.fan_out.total"] == 1


# ---------------------------------------------------------------------------
# MFJObserver child tracking
# ---------------------------------------------------------------------------


class TestChildTracking:
    def test_on_child_spawned_records_start_time(self, observer, ctx):
        before = time.perf_counter()
        observer.on_child_spawned(ctx, child_chat_id="child_1", task_key="Task_0")
        after = time.perf_counter()
        assert before <= ctx.child_started_at["child_1"] <= after

    def test_on_child_completed_clears_start_time(self, observer, ctx):
        observer.on_child_spawned(ctx, child_chat_id="child_1", task_key="Task_0")
        observer.on_child_completed(ctx, child_chat_id="child_1", success=True)
        assert "child_1" not in ctx.child_started_at

    def test_on_child_completed_missing_start_does_not_raise(self, observer, ctx):
        # Completing a child that was never spawned (e.g. re-entrant) must be safe
        observer.on_child_completed(ctx, child_chat_id="never_spawned", success=False)

    def test_multiple_children_tracked_independently(self, observer, ctx):
        observer.on_child_spawned(ctx, child_chat_id="c1", task_key="T0")
        observer.on_child_spawned(ctx, child_chat_id="c2", task_key="T1")
        observer.on_child_completed(ctx, child_chat_id="c1", success=True)
        assert "c1" not in ctx.child_started_at
        assert "c2" in ctx.child_started_at


# ---------------------------------------------------------------------------
# MFJObserver full cycle
# ---------------------------------------------------------------------------


class TestFullCycle:
    def test_full_cycle_does_not_raise(self, observer):
        ctx = observer.start_cycle(trigger_id="t1", parent_chat_id="p1", workflow_name="W")
        observer.on_fan_out_started(ctx, child_count=2, spawn_mode="workflow")
        observer.on_child_spawned(ctx, child_chat_id="c1", task_key="Task_0")
        observer.on_child_spawned(ctx, child_chat_id="c2", task_key="Task_1")
        observer.on_child_completed(ctx, child_chat_id="c1", success=True)
        observer.on_child_completed(ctx, child_chat_id="c2", success=False)
        observer.on_fan_in_started(ctx, child_count=2, reason="all_children_done")
        observer.on_fan_in_completed(ctx, succeeded_count=1, failed_count=1, strategy="collect_all")
        observer.on_cycle_completed(ctx)

    def test_full_cycle_increments_all_relevant_counters(self, observer):
        ctx = observer.start_cycle(trigger_id="t1", parent_chat_id="p1", workflow_name="W")
        observer.on_fan_out_started(ctx, child_count=2, spawn_mode="workflow")
        observer.on_fan_in_started(ctx, child_count=2, reason="all_children_done")
        observer.on_fan_in_completed(ctx, succeeded_count=1, failed_count=1, strategy="merge_bundles")
        observer.on_cycle_completed(ctx)
        m = observer.snapshot_metrics()
        assert m["mfj.fan_out.total"] == 1
        assert m["mfj.fan_in.total"] == 1
        assert m["mfj.partial_failure.total"] == 1

    def test_timeout_cycle(self, observer):
        ctx = observer.start_cycle(trigger_id="t2", parent_chat_id="p2", workflow_name="W")
        observer.on_fan_out_started(ctx, child_count=3, spawn_mode="generator_subrun")
        observer.on_timeout(ctx, timeout_seconds=600)
        m = observer.snapshot_metrics()
        assert m["mfj.fan_out.total"] == 1
        assert m["mfj.timeout.total"] == 1


# ---------------------------------------------------------------------------
# get_mfj_observer — module-level singleton
# ---------------------------------------------------------------------------


class TestGetMfjObserver:
    def test_returns_mfj_observer_instance(self):
        obs = get_mfj_observer()
        assert isinstance(obs, MFJObserver)

    def test_singleton_same_instance(self):
        obs1 = get_mfj_observer()
        obs2 = get_mfj_observer()
        assert obs1 is obs2
