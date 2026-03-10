# ==============================================================================
# Tests for fan-in merge strategies (mozaiksai.core.workflow.pack.merge)
# ==============================================================================

import pytest

from tests.import_utils import import_module_directly

_merge = import_module_directly("mozaiksai.core.workflow.pack.merge")
ChildResult = _merge.ChildResult
MergeResult = _merge.MergeResult
MergeStrategy = _merge.MergeStrategy
CollectAllMerge = _merge.CollectAllMerge
ConcatenateMerge = _merge.ConcatenateMerge
MergeBundlesMerge = _merge.MergeBundlesMerge
FirstSuccessMerge = _merge.FirstSuccessMerge
MajorityVoteMerge = _merge.MajorityVoteMerge
get_merge_strategy = _merge.get_merge_strategy
register_merge_strategy = _merge.register_merge_strategy


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def children():
    """Standard 3-child fixture: 2 success, 1 failed."""
    return [
        ChildResult(
            child_chat_id="c1",
            workflow_name="Facts",
            context={"facts": ["fact1", "fact2"], "source": "wiki"},
            success=True,
        ),
        ChildResult(
            child_chat_id="c2",
            workflow_name="Translations",
            context={"greetings": {"es": "Hola", "fr": "Bonjour"}},
            success=True,
        ),
        ChildResult(
            child_chat_id="c3",
            workflow_name="Broken",
            context={},
            success=False,
            error="timeout",
        ),
    ]


@pytest.fixture
def all_success():
    return [
        ChildResult(child_chat_id="a", workflow_name="A", context={"x": 1}, success=True),
        ChildResult(child_chat_id="b", workflow_name="B", context={"y": 2}, success=True),
    ]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_builtin_strategies(self):
        for name in ("collect_all", "concatenate", "merge_bundles", "first_success", "majority_vote"):
            s = get_merge_strategy(name)
            assert isinstance(s, MergeStrategy)

    def test_unknown_fallback(self):
        with pytest.raises(ValueError):
            get_merge_strategy("nonexistent_xyz")

    def test_register_custom(self):
        class CustomMerge(MergeStrategy):
            @property
            def name(self) -> str:
                return "custom_test"

            def merge(self, children):
                return MergeResult(
                    merged={"custom": True},
                        strategy_used="custom_test",
                        child_count=len(children),
                        failed_count=0,
                )

        register_merge_strategy(CustomMerge(), replace=True)
        s = get_merge_strategy("custom:custom_test")
        assert isinstance(s, CustomMerge)
        r = s.merge([])
        assert r.merged == {"custom": True}


# ---------------------------------------------------------------------------
# CollectAllMerge
# ---------------------------------------------------------------------------


class TestCollectAllMerge:
    def test_basic(self, children):
        r = CollectAllMerge().merge(children)
        assert r.strategy_used == "collect_all"
        assert r.child_count == 3
        assert r.failed_count == 1
        assert "Facts" in r.merged
        assert "Translations" in r.merged
        assert r.merged["Facts"] == {"facts": ["fact1", "fact2"], "source": "wiki"}
        assert r.merged["Translations"] == {"greetings": {"es": "Hola", "fr": "Bonjour"}}

    def test_empty(self):
        r = CollectAllMerge().merge([])
        assert r.merged == {}
        assert r.child_count == 0
        assert r.failed_count == 0

    def test_all_failed(self):
        children = [
            ChildResult(child_chat_id="x", workflow_name="X", success=False, error="e"),
        ]
        r = CollectAllMerge().merge(children)
        # CollectAllMerge includes a _failed key listing failed workflow names
        assert "_failed" in r.merged
        assert r.failed_count == 1


# ---------------------------------------------------------------------------
# ConcatenateMerge
# ---------------------------------------------------------------------------


class TestConcatenateMerge:
    def test_basic(self, children):
        r = ConcatenateMerge().merge(children)
        assert r.strategy_used == "concatenate"
        assert "facts" in r.merged
        assert "source" in r.merged
        assert "greetings" in r.merged
        # Last write wins for overlapping keys (none here)
        assert r.failed_count == 1

    def test_overlapping_keys(self):
        children = [
            ChildResult(child_chat_id="a", workflow_name="A", context={"x": 1}, success=True),
            ChildResult(child_chat_id="b", workflow_name="B", context={"x": 2}, success=True),
        ]
        r = ConcatenateMerge().merge(children)
        assert r.merged["x"] == 2  # last write wins


# ---------------------------------------------------------------------------
# MergeBundlesMerge
# ---------------------------------------------------------------------------


class TestMergeBundlesMerge:
    def test_basic(self, all_success):
        r = MergeBundlesMerge().merge(all_success)
        assert r.strategy_used == "merge_bundles"
        assert r.merged == {"x": 1, "y": 2}

    def test_deep_merge(self):
        children = [
            ChildResult(
                child_chat_id="a",
                workflow_name="A",
                context={"nested": {"a": 1, "b": 2}},
                success=True,
            ),
            ChildResult(
                child_chat_id="b",
                workflow_name="B",
                context={"nested": {"b": 99, "c": 3}},
                success=True,
            ),
        ]
        r = MergeBundlesMerge().merge(children)
        assert r.merged["nested"]["a"] == 1
        assert r.merged["nested"]["b"] == 99  # last write wins
        assert r.merged["nested"]["c"] == 3


# ---------------------------------------------------------------------------
# FirstSuccessMerge
# ---------------------------------------------------------------------------


class TestFirstSuccessMerge:
    def test_basic(self, children):
        r = FirstSuccessMerge().merge(children)
        assert r.strategy_used == "first_success"
        assert r.merged == {"facts": ["fact1", "fact2"], "source": "wiki"}

    def test_all_failed(self):
        children = [
            ChildResult(child_chat_id="x", workflow_name="X", success=False, error="e1"),
            ChildResult(child_chat_id="y", workflow_name="Y", success=False, error="e2"),
        ]
        r = FirstSuccessMerge().merge(children)
        assert r.merged == {}
        assert r.failed_count == 2


class TestMajorityVoteMerge:
    def test_majority(self):
        children = [
            ChildResult(child_chat_id="a", workflow_name="A", context={"x": 1}, success=True),
            ChildResult(child_chat_id="b", workflow_name="B", context={"x": 1}, success=True),
            ChildResult(child_chat_id="c", workflow_name="C", context={"x": 2}, success=True),
        ]
        r = MajorityVoteMerge().merge(children)
        assert r.strategy_used == "majority_vote"
        assert r.merged == {"x": 1}

# ---------------------------------------------------------------------------
# MergeResult / ChildResult data classes
# ---------------------------------------------------------------------------


class TestDataClasses:
    def test_child_result_defaults(self):
        c = ChildResult(child_chat_id="c", workflow_name="W")
        assert c.context == {}
        assert c.success is True
        assert c.error is None

    def test_merge_result_defaults(self):
        r = MergeResult(
            merged={"k": "v"},
            strategy_used="test",
            child_count=1,
            failed_count=0,
        )
        assert r.failed_count == 0
        assert r.merged == {"k": "v"}
