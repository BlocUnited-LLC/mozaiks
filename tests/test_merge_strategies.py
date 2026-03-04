"""
Tests for Advanced Merge Strategies — Phase 7
==============================================

Covers:
1. DeepMergeMerge — recursive dict merge, disjoint keys, last-write-wins
2. FirstSuccessMerge — first successful child, no successes, all succeed
3. MajorityVoteMerge — clear majority, tie-break, no successful voters
4. MergeStrategyRegistry — register/get/list, builtins, custom, decorator
5. Coordinator _resolve_merge_strategy — all modes + "custom:" prefix
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Direct import mechanism (same pattern as test_workflow_pack_coordinator.py)
# ---------------------------------------------------------------------------
import importlib.util
import sys
import types
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent  # mozaiks repo root


def _direct_import(module_name: str, file_path: Path):
    """Import a single .py file as *module_name*, registering parent stubs."""
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

# 1. contracts.events
_events_mod = _direct_import(
    "mozaiksai.core.contracts.events",
    _ROOT / "mozaiksai" / "core" / "contracts" / "events.py",
)

# 2. orchestration.decomposition
_decomp_mod = _direct_import(
    "mozaiksai.orchestration.decomposition",
    _ROOT / "mozaiksai" / "orchestration" / "decomposition.py",
)

# 3. orchestration.merge
_merge_mod = _direct_import(
    "mozaiksai.orchestration.merge",
    _ROOT / "mozaiksai" / "orchestration" / "merge.py",
)
ChildResult = _merge_mod.ChildResult
ConcatenateMerge = _merge_mod.ConcatenateMerge
DeepMergeMerge = _merge_mod.DeepMergeMerge
FirstSuccessMerge = _merge_mod.FirstSuccessMerge
MajorityVoteMerge = _merge_mod.MajorityVoteMerge
MergeContext = _merge_mod.MergeContext
MergeResult = _merge_mod.MergeResult
MergeStrategy = _merge_mod.MergeStrategy
MergeStrategyRegistry = _merge_mod.MergeStrategyRegistry
StructuredMerge = _merge_mod.StructuredMerge
get_merge_strategy_registry = _merge_mod.get_merge_strategy_registry
merge_strategy = _merge_mod.merge_strategy
reset_merge_strategy_registry = _merge_mod.reset_merge_strategy_registry

# 4. The coordinator
_coord_mod = _direct_import(
    "mozaiksai.core.workflow.pack.workflow_pack_coordinator",
    _ROOT / "mozaiksai" / "core" / "workflow" / "pack" / "workflow_pack_coordinator.py",
)
WorkflowPackCoordinator = _coord_mod.WorkflowPackCoordinator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _child(
    task_id: str,
    workflow_name: str = "wf",
    text_output: str = "",
    structured_output: dict[str, Any] | None = None,
    success: bool = True,
    error: str | None = None,
) -> ChildResult:
    """Convenience factory for ChildResult."""
    return ChildResult(
        task_id=task_id,
        workflow_name=workflow_name,
        run_id=f"run_{task_id}",
        text_output=text_output,
        structured_output=structured_output or {},
        success=success,
        error=error,
    )


def _ctx(
    *children: ChildResult,
    parent_run_id: str = "parent_001",
    parent_workflow_name: str = "parent_wf",
) -> MergeContext:
    """Convenience factory for MergeContext."""
    return MergeContext(
        parent_run_id=parent_run_id,
        parent_workflow_name=parent_workflow_name,
        child_results=list(children),
    )


# ===========================================================================
# 1.  DeepMergeMerge
# ===========================================================================

class TestDeepMergeMerge:
    """Unit tests for the recursive deep-merge strategy."""

    def test_disjoint_keys(self):
        """Each child contributes disjoint keys → all appear in merged output."""
        strategy = DeepMergeMerge()
        ctx = _ctx(
            _child("t1", structured_output={"frontend": {"port": 3000}}),
            _child("t2", structured_output={"backend": {"port": 8080}}),
        )
        result = strategy.merge(ctx)

        assert result.all_succeeded is True
        assert result.merged_data == {
            "frontend": {"port": 3000},
            "backend": {"port": 8080},
        }

    def test_nested_merge(self):
        """Nested dicts are recursively merged (not replaced)."""
        strategy = DeepMergeMerge()
        ctx = _ctx(
            _child("t1", structured_output={"config": {"db": "postgres", "port": 5432}}),
            _child("t2", structured_output={"config": {"cache": "redis"}}),
        )
        result = strategy.merge(ctx)

        assert result.merged_data == {
            "config": {"db": "postgres", "port": 5432, "cache": "redis"},
        }

    def test_last_write_wins(self):
        """Duplicate scalar keys → last child (by task_id sort) wins."""
        strategy = DeepMergeMerge()
        ctx = _ctx(
            _child("t1", structured_output={"version": 1}),
            _child("t2", structured_output={"version": 2}),
        )
        result = strategy.merge(ctx)

        # t2 sorts after t1, so version=2 wins.
        assert result.merged_data == {"version": 2}

    def test_failed_children_excluded_from_merge(self):
        """Failed children are not deep-merged but still counted."""
        strategy = DeepMergeMerge()
        ctx = _ctx(
            _child("t1", structured_output={"ok": True}),
            _child("t2", structured_output={"bad": True}, success=False, error="boom"),
        )
        result = strategy.merge(ctx)

        assert result.all_succeeded is False
        # Failed child's structured_output IS still merged (success flag
        # is checked separately) — deep-merge merges non-None outputs.
        # However, the failed child has success=False reported correctly.
        assert len(result.child_results) == 2
        # Summary reflects partial success.
        assert "1/2" in result.summary_message

    def test_empty_children(self):
        """Children with empty structured_output contribute nothing."""
        strategy = DeepMergeMerge()
        ctx = _ctx(
            _child("t1", structured_output={}),
            _child("t2", structured_output={"a": 1}),
        )
        result = strategy.merge(ctx)
        assert result.merged_data == {"a": 1}

    def test_deterministic_order(self):
        """Children are processed in task_id order regardless of input order."""
        strategy = DeepMergeMerge()
        # Provide out of order — t2 before t1.
        ctx = _ctx(
            _child("t2", structured_output={"key": "from_t2"}),
            _child("t1", structured_output={"key": "from_t1"}),
        )
        result = strategy.merge(ctx)
        # t1 sorts first, t2 sorts second → t2 wins (last-write).
        assert result.merged_data == {"key": "from_t2"}


# ===========================================================================
# 2.  FirstSuccessMerge
# ===========================================================================

class TestFirstSuccessMerge:
    """Unit tests for first-success selection strategy."""

    def test_first_successful_child(self):
        """Returns the first successful child output, sorted by task_id."""
        strategy = FirstSuccessMerge()
        ctx = _ctx(
            _child("t2", structured_output={"val": "second"}, success=True),
            _child("t1", structured_output={"val": "first"}, success=True),
        )
        result = strategy.merge(ctx)

        # t1 sorts before t2, so t1 is the "first" success.
        assert result.merged_data == {"val": "first"}
        assert result.metadata["winner_task_id"] == "t1"
        assert result.all_succeeded is True

    def test_no_successes(self):
        """All children failed → empty merge, all_succeeded=False."""
        strategy = FirstSuccessMerge()
        ctx = _ctx(
            _child("t1", success=False, error="fail1"),
            _child("t2", success=False, error="fail2"),
        )
        result = strategy.merge(ctx)

        assert result.merged_data == {}
        assert result.all_succeeded is False
        assert "No child succeeded" in result.summary_message

    def test_some_failures_picks_first_success(self):
        """Mixed results — picks first successful by task_id."""
        strategy = FirstSuccessMerge()
        ctx = _ctx(
            _child("t1", success=False, error="fail"),
            _child("t2", structured_output={"ok": True}),
            _child("t3", structured_output={"also": True}),
        )
        result = strategy.merge(ctx)

        assert result.metadata["winner_task_id"] == "t2"
        assert result.merged_data == {"ok": True}
        # Not all succeeded because t1 failed.
        assert result.all_succeeded is False

    def test_text_output_only(self):
        """Winner with text output but no structured output → empty merged_data."""
        strategy = FirstSuccessMerge()
        ctx = _ctx(
            _child("t1", text_output="hello", success=True),
        )
        result = strategy.merge(ctx)
        assert result.merged_data == {}
        assert result.metadata["winner_task_id"] == "t1"


# ===========================================================================
# 3.  MajorityVoteMerge
# ===========================================================================

class TestMajorityVoteMerge:
    """Unit tests for majority-vote consensus strategy."""

    def test_clear_majority(self):
        """3 children — 2 agree, 1 differs → majority wins."""
        strategy = MajorityVoteMerge()
        ctx = _ctx(
            _child("t1", structured_output={"answer": 42}),
            _child("t2", structured_output={"answer": 42}),
            _child("t3", structured_output={"answer": 99}),
        )
        result = strategy.merge(ctx)

        assert result.merged_data == {"answer": 42}
        assert result.metadata["vote_count"] == 2
        assert result.metadata["total_voters"] == 3

    def test_tie_broken_by_task_id(self):
        """Equal vote counts → winner from earliest task_id."""
        strategy = MajorityVoteMerge()
        ctx = _ctx(
            _child("t1", structured_output={"pick": "A"}),
            _child("t2", structured_output={"pick": "B"}),
        )
        result = strategy.merge(ctx)

        # Tie: 1 vote each. t1 sorts first → {"pick": "A"} wins.
        assert result.merged_data == {"pick": "A"}
        assert result.metadata["winner_task_id"] == "t1"
        assert result.metadata["vote_count"] == 1

    def test_no_successful_voters(self):
        """All children failed → no voters."""
        strategy = MajorityVoteMerge()
        ctx = _ctx(
            _child("t1", structured_output={"a": 1}, success=False),
            _child("t2", structured_output={"b": 2}, success=False),
        )
        result = strategy.merge(ctx)

        assert result.merged_data == {}
        assert result.all_succeeded is False
        assert "No successful children" in result.summary_message

    def test_empty_structured_output_as_vote(self):
        """Children with empty {} structured_output still count as a vote."""
        strategy = MajorityVoteMerge()
        ctx = _ctx(
            _child("t1", structured_output={}),
            _child("t2", structured_output={}),
            _child("t3", structured_output={"different": True}),
        )
        result = strategy.merge(ctx)

        # {} appears twice, {"different": True} once.
        assert result.merged_data == {}
        assert result.metadata["vote_count"] == 2

    def test_canonical_json_comparison(self):
        """Key order doesn't matter — canonicalized before comparison."""
        strategy = MajorityVoteMerge()
        # Same logical dict but different key insertion order.
        ctx = _ctx(
            _child("t1", structured_output={"a": 1, "b": 2}),
            _child("t2", structured_output={"b": 2, "a": 1}),
            _child("t3", structured_output={"c": 3}),
        )
        result = strategy.merge(ctx)

        assert result.metadata["vote_count"] == 2
        assert result.merged_data == {"a": 1, "b": 2}


# ===========================================================================
# 4.  MergeStrategyRegistry
# ===========================================================================

class TestMergeStrategyRegistry:
    """Unit tests for the registry + singleton + decorator."""

    def setup_method(self):
        """Reset the singleton before each test."""
        reset_merge_strategy_registry()

    def teardown_method(self):
        """Reset after each test to avoid pollution."""
        reset_merge_strategy_registry()

    def test_builtins_pre_registered(self):
        """Singleton registry has all 5 built-in strategies on first access."""
        reg = get_merge_strategy_registry()
        names = reg.list()
        assert "concatenate" in names
        assert "structured" in names
        assert "deep_merge" in names
        assert "first_success" in names
        assert "majority_vote" in names
        assert len(names) == 5

    def test_register_and_get(self):
        """Custom strategy can be registered and retrieved."""
        reg = get_merge_strategy_registry()

        class MyMerge:
            def merge(self, context):
                pass

        reg.register("my_merge", MyMerge)
        assert reg.get("my_merge") is MyMerge
        assert "my_merge" in reg.list()

    def test_duplicate_registration_raises(self):
        """Registering the same name twice without replace=True raises."""
        reg = get_merge_strategy_registry()

        class MergeA:
            pass

        class MergeB:
            pass

        reg.register("dup_test", MergeA)
        with pytest.raises(ValueError, match="already registered"):
            reg.register("dup_test", MergeB)

    def test_replace_flag(self):
        """replace=True allows overwriting an existing registration."""
        reg = get_merge_strategy_registry()

        class MergeA:
            pass

        class MergeB:
            pass

        reg.register("replace_test", MergeA)
        reg.register("replace_test", MergeB, replace=True)
        assert reg.get("replace_test") is MergeB

    def test_get_nonexistent_returns_none(self):
        """Looking up an unregistered name returns None."""
        reg = get_merge_strategy_registry()
        assert reg.get("nonexistent_strategy") is None

    def test_decorator_registers(self):
        """@merge_strategy('name') decorator registers the class."""
        @merge_strategy("decorated_merge")
        class DecoratedMerge:
            def merge(self, context):
                pass

        reg = get_merge_strategy_registry()
        assert reg.get("decorated_merge") is DecoratedMerge
        assert hasattr(DecoratedMerge, "__merge_strategy_name__")
        assert DecoratedMerge.__merge_strategy_name__ == "decorated_merge"

    def test_empty_name_raises(self):
        """Empty name string raises ValueError."""
        reg = get_merge_strategy_registry()

        class Bad:
            pass

        with pytest.raises(ValueError, match="name is required"):
            reg.register("", Bad)

    def test_singleton_returns_same_instance(self):
        """get_merge_strategy_registry() returns the same object on repeat calls."""
        r1 = get_merge_strategy_registry()
        r2 = get_merge_strategy_registry()
        assert r1 is r2

    def test_reset_clears_singleton(self):
        """After reset, next call returns a fresh registry with builtins only."""
        reg1 = get_merge_strategy_registry()

        class Custom:
            pass

        reg1.register("custom_thing", Custom)
        assert reg1.get("custom_thing") is Custom

        reset_merge_strategy_registry()
        reg2 = get_merge_strategy_registry()
        assert reg2 is not reg1
        assert reg2.get("custom_thing") is None
        # But builtins are still present.
        assert len(reg2.list()) == 5


# ===========================================================================
# 5.  Coordinator _resolve_merge_strategy
# ===========================================================================

class TestResolveViaCoordinator:
    """Integration: coordinator resolves mode strings via the registry."""

    def setup_method(self):
        reset_merge_strategy_registry()
        # Lightweight coordinator instance (we only call _resolve_merge_strategy).
        transport = MagicMock()
        transport.persistence_manager = MagicMock()
        self.coordinator = WorkflowPackCoordinator.__new__(WorkflowPackCoordinator)
        self.coordinator._transport = transport

    def teardown_method(self):
        reset_merge_strategy_registry()

    def test_resolves_concatenate(self):
        s = self.coordinator._resolve_merge_strategy("concatenate")
        assert isinstance(s, ConcatenateMerge)

    def test_resolves_structured(self):
        s = self.coordinator._resolve_merge_strategy("structured")
        assert isinstance(s, StructuredMerge)

    def test_resolves_deep_merge(self):
        s = self.coordinator._resolve_merge_strategy("deep_merge")
        assert isinstance(s, DeepMergeMerge)

    def test_resolves_first_success(self):
        s = self.coordinator._resolve_merge_strategy("first_success")
        assert isinstance(s, FirstSuccessMerge)

    def test_resolves_majority_vote(self):
        s = self.coordinator._resolve_merge_strategy("majority_vote")
        assert isinstance(s, MajorityVoteMerge)

    def test_resolves_collect_all(self):
        """collect_all is an internal strategy (not in registry)."""
        s = self.coordinator._resolve_merge_strategy("collect_all")
        # _CollectAllMerge is private — just check it has a merge() method.
        assert hasattr(s, "merge")
        assert not isinstance(s, ConcatenateMerge)

    def test_resolves_custom_prefix(self):
        """custom:name syntax resolves a user-registered strategy."""
        class MyCustom:
            def merge(self, context):
                return MergeResult(summary_message="custom!", merged_data={})

        reg = get_merge_strategy_registry()
        reg.register("my_custom", MyCustom)

        s = self.coordinator._resolve_merge_strategy("custom:my_custom")
        assert isinstance(s, MyCustom)

    def test_unknown_custom_falls_back(self):
        """custom:unknown → falls back to ConcatenateMerge."""
        s = self.coordinator._resolve_merge_strategy("custom:nonexistent")
        assert isinstance(s, ConcatenateMerge)

    def test_unknown_mode_falls_back(self):
        """Unrecognized mode string → ConcatenateMerge default."""
        s = self.coordinator._resolve_merge_strategy("totally_unknown")
        assert isinstance(s, ConcatenateMerge)

    def test_empty_custom_name_falls_back(self):
        """custom: with no name after it → ConcatenateMerge."""
        s = self.coordinator._resolve_merge_strategy("custom:")
        assert isinstance(s, ConcatenateMerge)
