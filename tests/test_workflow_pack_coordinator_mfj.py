# ==============================================================================
# Tests for WorkflowPackCoordinator MFJ fan-out/fan-in logic
# (mozaiksai.core.workflow.pack.workflow_pack_coordinator)
# ==============================================================================

import pytest

from tests.import_utils import import_module_directly

_coord_mod = import_module_directly("mozaiksai.core.workflow.pack.workflow_pack_coordinator")
_ag2_orch_mod = import_module_directly("mozaiksai.core.adapters.ag2_orchestration")
_merge_mod = import_module_directly("mozaiksai.core.workflow.pack.merge")
_schema_mod = import_module_directly("mozaiksai.core.workflow.pack.schema")
_obs_mod = import_module_directly("mozaiksai.core.workflow.pack.mfj_observability")

WorkflowPackCoordinator = _coord_mod.WorkflowPackCoordinator
_ChildRunState = _coord_mod._ChildRunState
_ActivePackRun = _coord_mod._ActivePackRun
_is_terminal_child_status = _coord_mod._is_terminal_child_status

ChildResult = _merge_mod.ChildResult
CollectAllMerge = _merge_mod.CollectAllMerge
ConcatenateMerge = _merge_mod.ConcatenateMerge
FirstSuccessMerge = _merge_mod.FirstSuccessMerge
MajorityVoteMerge = _merge_mod.MajorityVoteMerge

MFJObserver = _obs_mod.MFJObserver


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_coordinator(**kwargs):
    return WorkflowPackCoordinator(**kwargs)


def _make_children(n=3, all_success=True):
    children = []
    for i in range(n):
        children.append(
            ChildResult(
                child_chat_id=f"chat_child_{i}",
                workflow_name=f"Workflow{i}",
                context={"result": f"output_{i}", "score": i},
                success=all_success or (i < n - 1),
                error=None if (all_success or i < n - 1) else "timeout",
            )
        )
    return children


# ---------------------------------------------------------------------------
# Constructor / initial state
# ---------------------------------------------------------------------------


class TestCoordinatorInit:
    def test_creates_with_defaults(self):
        coord = _make_coordinator()
        assert coord is not None

    def test_active_by_parent_empty(self):
        coord = _make_coordinator()
        assert len(coord._active_by_parent) == 0

    def test_active_by_child_empty(self):
        coord = _make_coordinator()
        assert len(coord._active_by_child) == 0

    def test_accepts_custom_observer(self):
        obs = MFJObserver()
        coord = _make_coordinator(observer=obs)
        assert coord._observer is obs

    def test_accepts_max_retry_rounds(self):
        coord = _make_coordinator(max_retry_rounds=3)
        assert coord._max_retry_rounds == 3


class TestChildCompletionStatus:
    def test_completed_status_is_terminal(self):
        assert _is_terminal_child_status(1) is True
        assert _is_terminal_child_status("completed") is True

    def test_paused_status_is_not_terminal(self):
        assert _is_terminal_child_status(0) is False
        assert _is_terminal_child_status("paused") is False
        assert _is_terminal_child_status("in_progress") is False


# ---------------------------------------------------------------------------
# _extract_child_specs — static method
# ---------------------------------------------------------------------------


class TestExtractChildSpecs:
    def test_list_of_strings(self):
        data = {"workflows": ["WorkflowA", "WorkflowB"]}
        specs = WorkflowPackCoordinator._extract_child_specs(data)
        assert len(specs) == 2
        assert specs[0]["name"] == "WorkflowA"
        assert specs[1]["name"] == "WorkflowB"

    def test_list_of_dicts(self):
        data = {
            "workflows": [
                {"name": "BillingWorkflow", "description": "Handle billing"},
                {"name": "AuditWorkflow"},
            ]
        }
        specs = WorkflowPackCoordinator._extract_child_specs(data)
        assert len(specs) == 2
        assert specs[0]["name"] == "BillingWorkflow"
        assert specs[0]["description"] == "Handle billing"

    def test_task_index_assigned(self):
        data = {"workflows": ["A", "B", "C"]}
        specs = WorkflowPackCoordinator._extract_child_specs(data)
        indices = [s["task_index"] for s in specs]
        assert indices == [0, 1, 2]

    def test_empty_workflows_list(self):
        specs = WorkflowPackCoordinator._extract_child_specs({"workflows": []})
        assert specs == []

    def test_missing_workflows_key(self):
        specs = WorkflowPackCoordinator._extract_child_specs({})
        assert specs == []

    def test_mixed_types_normalised(self):
        data = {"workflows": ["PlainName", {"name": "DictName", "initial_message": "Hi"}]}
        specs = WorkflowPackCoordinator._extract_child_specs(data)
        assert specs[0]["name"] == "PlainName"
        assert specs[1]["name"] == "DictName"
        assert specs[1]["initial_message"] == "Hi"


class TestWorkflowExists:
    def test_shared_generator_workflow_exists_under_platform_workflows(self):
        # AgentGenerator is a real shared workflow under factory_app/workflows/ —
        # verifies that _workflow_exists resolves against the repo tree.
        assert WorkflowPackCoordinator._workflow_exists("AgentGenerator") is True


# ---------------------------------------------------------------------------
# _validate_input_contract — static method
# ---------------------------------------------------------------------------


class TestValidateInputContract:
    """WorkflowPackCoordinator._validate_input_contract(parent_context, contract)"""

    def _make_contract(self, required, optional=None):
        # The contract is an MFJContract-like object with .required and .optional
        class _C:
            pass

        c = _C()
        c.required = required
        c.optional = optional or []
        return c

    def test_all_required_present(self):
        context = {"user_id": "u1", "data": "hello"}
        contract = self._make_contract(required=["user_id", "data"])
        missing = WorkflowPackCoordinator._validate_input_contract(context, contract)
        assert missing == []

    def test_missing_required_reported(self):
        context = {"user_id": "u1"}
        contract = self._make_contract(required=["user_id", "data", "prompt"])
        missing = WorkflowPackCoordinator._validate_input_contract(context, contract)
        assert set(missing) == {"data", "prompt"}

    def test_empty_required_always_passes(self):
        context = {}
        contract = self._make_contract(required=[])
        missing = WorkflowPackCoordinator._validate_input_contract(context, contract)
        assert missing == []

    def test_optional_keys_not_checked(self):
        context = {}
        contract = self._make_contract(required=[], optional=["maybe_key"])
        missing = WorkflowPackCoordinator._validate_input_contract(context, contract)
        assert missing == []

    def test_context_with_extra_keys_does_not_affect_result(self):
        context = {"a": 1, "b": 2, "c": 3}
        contract = self._make_contract(required=["a"])
        missing = WorkflowPackCoordinator._validate_input_contract(context, contract)
        assert missing == []


# ---------------------------------------------------------------------------
# _validate_output_contract — static method
# ---------------------------------------------------------------------------


class TestValidateOutputContract:
    """WorkflowPackCoordinator._validate_output_contract(child_results, contract)"""

    def _make_contract(self, required):
        class _C:
            pass

        c = _C()
        c.required = required
        c.optional = []
        return c

    def test_all_children_satisfy_contract(self):
        children = _make_children(2)
        contract = self._make_contract(required=["result"])
        violations = WorkflowPackCoordinator._validate_output_contract(children, contract)
        assert violations == []

    def test_failed_children_skipped(self):
        children = _make_children(2, all_success=False)
        # Last child has success=False — only children 0..n-2 are checked
        contract = self._make_contract(required=["result"])
        violations = WorkflowPackCoordinator._validate_output_contract(children, contract)
        assert violations == []

    def test_missing_output_key_reported(self):
        children = [
            ChildResult(
                child_chat_id="c1",
                workflow_name="WfA",
                context={"result": "ok"},
                success=True,
            ),
            ChildResult(
                child_chat_id="c2",
                workflow_name="WfB",
                context={},  # missing "result"
                success=True,
            ),
        ]
        contract = self._make_contract(required=["result"])
        violations = WorkflowPackCoordinator._validate_output_contract(children, contract)
        assert any("WfB" in v or "c2" in v or "result" in v for v in violations)

    def test_no_required_keys_no_violations(self):
        children = _make_children(3)
        contract = self._make_contract(required=[])
        violations = WorkflowPackCoordinator._validate_output_contract(children, contract)
        assert violations == []


# ---------------------------------------------------------------------------
# _find_trigger — static method
# ---------------------------------------------------------------------------


class TestFindTrigger:
    def _make_mfj(self, decomposition_agent, trigger_id="t1", trigger_on="decomposition_event"):
        class _FanOut:
            spawn_mode = "workflow"
            max_children = 10

        class _FanIn:
            resume_agent = "ResumeAgent"
            aggregation_strategy = "collect_all"

        class _MFJ:
            id = trigger_id
            description = ""
            requires = []
            fan_out = _FanOut()
            fan_in = _FanIn()

        m = _MFJ()
        m.decomposition_agent = decomposition_agent
        m.trigger_on = trigger_on
        return m

    def test_returns_matching_trigger(self):
        mfjs = [self._make_mfj("PlannerAgent", "t1")]
        result = WorkflowPackCoordinator._find_trigger(mfjs, "PlannerAgent")
        assert result is not None
        assert result.id == "t1"

    def test_no_match_returns_none(self):
        mfjs = [self._make_mfj("PlannerAgent", "t1")]
        result = WorkflowPackCoordinator._find_trigger(mfjs, "OtherAgent")
        assert result is None

    def test_wrong_trigger_on_returns_none(self):
        mfj = self._make_mfj("PlannerAgent")
        mfj.trigger_on = "message"  # not decomposition_event
        result = WorkflowPackCoordinator._find_trigger([mfj], "PlannerAgent")
        assert result is None

    def test_empty_list_returns_none(self):
        result = WorkflowPackCoordinator._find_trigger([], "PlannerAgent")
        assert result is None

    def test_first_matching_trigger_returned(self):
        mfjs = [
            self._make_mfj("PlannerAgent", "t_first"),
            self._make_mfj("PlannerAgent", "t_second"),
        ]
        result = WorkflowPackCoordinator._find_trigger(mfjs, "PlannerAgent")
        assert result.id == "t_first"


# ---------------------------------------------------------------------------
# _check_requires — in-memory cache behaviour
# ---------------------------------------------------------------------------


class TestCheckRequires:
    @pytest.mark.asyncio
    async def test_empty_requires_always_true(self):
        coord = _make_coordinator()
        result = await coord._check_requires("app1", "parent1", [])
        assert result is True

    @pytest.mark.asyncio
    async def test_requires_met_from_memory_cache(self):
        coord = _make_coordinator()
        # Pre-populate in-memory cache
        coord._completed_mfjs["parent1"] = {"trigger_a", "trigger_b"}
        result = await coord._check_requires("app1", "parent1", ["trigger_a", "trigger_b"])
        assert result is True

    @pytest.mark.asyncio
    async def test_requires_not_met_from_memory_cache(self):
        coord = _make_coordinator()
        coord._completed_mfjs["parent1"] = {"trigger_a"}
        result = await coord._check_requires("app1", "parent1", ["trigger_a", "trigger_b"])
        assert result is False


class TestResumeParent:
    @pytest.mark.asyncio
    async def test_resume_parent_runs_background_with_resume_agent(self, monkeypatch):
        coord = _make_coordinator()

        class _FanOut:
            timeout_seconds = 30

        class _FanIn:
            timeout_seconds = 30
            resume_agent = "PresenterAgent"
            resume_entry_agent = "ResumeRouterAgent"
            inject_as = "mfj_results"

        class _Contract:
            required = []
            optional = []

        class _Trigger:
            id = "trigger-1"
            fan_out = _FanOut()
            fan_in = _FanIn()
            output_contract = _Contract()

        active = _ActivePackRun(
            parent_chat_id="parent-chat",
            parent_workflow_name="SmokeParent",
            app_id="app-1",
            user_id="user-1",
            ws_id=None,
            trigger=_Trigger(),
            decomposition_agent="PlannerAgent",
            merge_strategy=CollectAllMerge(),
            on_partial_failure="continue",
            max_retry_rounds=0,
            mfj_cycle=1,
            parent_context_snapshot={},
            structured_data_snapshot={},
        )

        persisted_messages = []
        background_call = {}

        class _PM:
            async def persist_initial_messages(self, **kwargs):
                persisted_messages.append(kwargs)

        class _Transport:
            def __init__(self):
                self._background_tasks = {}
                self.pm = _PM()

            def _get_or_create_persistence_manager(self):
                return self.pm

            async def _run_workflow_background(self, **kwargs):
                background_call.update(kwargs)

            async def send_event_to_ui(self, event, chat_id):
                return None

        transport = _Transport()

        class _Adapter:
            async def resume(self, request):
                background_call.update(
                    {
                        "workflow_name": request.workflow_name,
                        "app_id": request.app_id,
                        "chat_id": request.chat_id,
                        "user_id": request.user_id,
                        "resume_agent": request.resume_agent,
                    }
                )

        monkeypatch.setattr(_ag2_orch_mod, "get_ag2_adapter", lambda: _Adapter())

        await coord._resume_parent(
            transport=transport,
            active=active,
            merged_payload={"result": "ok"},
            succeeded_count=1,
            failed_count=0,
            resume_nonce="nonce-1",
        )
        await _coord_mod.asyncio.sleep(0)

        assert persisted_messages[0]["messages"][0]["name"] == "ResumeRouterAgent"
        assert background_call["resume_agent"] == "PresenterAgent"

    @pytest.mark.asyncio
    async def test_no_cache_entry_falls_back_to_store(self, monkeypatch):
        # Patch completion_store.load_completed_trigger_ids to return a known set.
        # Must be an async coroutine because _check_requires awaits the call.
        coord = _make_coordinator()

        async def _stub_store(app_id, parent_chat_id):
            return {"trigger_x"}

        monkeypatch.setattr(
            coord._completion_store,
            "load_completed_trigger_ids",
            _stub_store,
        )
        result = await coord._check_requires("app1", "parent_new", ["trigger_x"])
        assert result is True

    @pytest.mark.asyncio
    async def test_store_miss_returns_false(self, monkeypatch):
        coord = _make_coordinator()

        async def _stub_store(app_id, parent_chat_id):
            return set()

        monkeypatch.setattr(
            coord._completion_store,
            "load_completed_trigger_ids",
            _stub_store,
        )
        result = await coord._check_requires("app1", "parent_new", ["trigger_x"])
        assert result is False


# ---------------------------------------------------------------------------
# MFJ cycle counter mechanics
# ---------------------------------------------------------------------------


class TestMfjCycleCounter:
    def test_initial_cycle_counter_zero(self):
        coord = _make_coordinator()
        assert coord._mfj_cycle_counter == {}

    def test_cycle_counter_incremented_per_parent(self):
        coord = _make_coordinator()
        coord._mfj_cycle_counter["parent1"] = coord._mfj_cycle_counter.get("parent1", 0) + 1
        coord._mfj_cycle_counter["parent1"] = coord._mfj_cycle_counter.get("parent1", 0) + 1
        coord._mfj_cycle_counter["parent2"] = coord._mfj_cycle_counter.get("parent2", 0) + 1
        assert coord._mfj_cycle_counter["parent1"] == 2
        assert coord._mfj_cycle_counter["parent2"] == 1

    def test_completed_mfjs_tracks_trigger_ids(self):
        coord = _make_coordinator()
        coord._completed_mfjs.setdefault("parent1", set()).add("trigger_a")
        coord._completed_mfjs.setdefault("parent1", set()).add("trigger_b")
        assert "trigger_a" in coord._completed_mfjs["parent1"]
        assert "trigger_b" in coord._completed_mfjs["parent1"]


# ---------------------------------------------------------------------------
# Merge strategy integration with coordinator results
# ---------------------------------------------------------------------------


class TestMergeStrategyIntegration:
    """Coordinator is responsible for choosing + applying merge strategies.
    These tests verify the strategies produce correct shapes that the coordinator
    would inject into parent context."""

    def test_collect_all_merge_preserves_per_workflow_context(self):
        children = _make_children(3)
        result = CollectAllMerge().merge(children)
        assert result.strategy_used == "collect_all"
        assert result.child_count == 3
        # Each child's context accessible by workflow name
        for child in children:
            assert child.workflow_name in result.merged

    def test_first_success_merge_returns_first_result(self):
        children = [
            ChildResult("c1", "Wf0", {"key": "first"}, success=True),
            ChildResult("c2", "Wf1", {"key": "second"}, success=True),
        ]
        result = FirstSuccessMerge().merge(children)
        assert result.merged.get("key") == "first"

    def test_majority_vote_all_same_context(self):
        context = {"answer": "Paris"}
        children = [
            ChildResult(f"c{i}", f"Wf{i}", context.copy(), success=True)
            for i in range(3)
        ]
        result = MajorityVoteMerge().merge(children)
        assert result.merged.get("answer") == "Paris"
        assert result.strategy_used == "majority_vote"

    def test_merge_with_all_failed_children(self):
        children = [
            ChildResult("c1", "Wf0", {}, success=False, error="crashed"),
            ChildResult("c2", "Wf1", {}, success=False, error="timeout"),
        ]
        result = CollectAllMerge().merge(children)
        assert result.child_count == 2
        assert result.failed_count == 2

    def test_merge_partial_failure_count(self):
        children = _make_children(3, all_success=False)
        result = CollectAllMerge().merge(children)
        # _make_children with all_success=False marks last child as failed
        assert result.failed_count == 1

    def test_concatenate_merge_flat_dict(self):
        children = [
            ChildResult("c1", "Wf0", {"a": 1, "b": 2}, success=True),
            ChildResult("c2", "Wf1", {"c": 3}, success=True),
        ]
        result = ConcatenateMerge().merge(children)
        assert result.merged.get("a") == 1
        assert result.merged.get("c") == 3

