"""
Task batches pure helper unit tests.

Covers:
  resolve_path_value:
    - empty path → returns payload as-is (stops immediately)
    - simple key → dict.get(key)
    - nested dot path → traverses nested dicts
    - missing key at any level → None
    - list integer index → list[i]
    - list integer index out of range → None
    - non-integer key on list → None
    - path through non-dict/non-list → None
    - BaseModel payload traversal

  _build_scoped_worker_prompt:
    - returns string containing original prompt
    - contains [TASK BATCH CONTEXT] marker
    - includes current_task_id from task_context
    - includes current_task from task_context
    - includes dependency_task_outputs (defaults to {})
    - missing keys produce None in envelope (not KeyError)

  _reject_task_output_identity_drift:
    - matching task_id and kind → no error
    - mismatched task_id → ValueError
    - mismatched kind → ValueError
    - empty task_id → no check (no error even if output differs)
    - empty output task_id → no check

  _stamp_task_output_identity:
    - output has no task_id → stamped from task
    - output already has task_id → not overwritten
    - output has no kind → stamped from task
    - output already has kind → not overwritten
    - empty task task_id → output not stamped

  _planned_pages:
    - no app_build_plan key → checks "pages"
    - app_build_plan with pages list → returns pages
    - app_build_plan.pages not a list → falls through to "pages"
    - top-level pages list → returns pages
    - non-dict page items filtered
    - empty list → []
    - no matching key → []
"""
from __future__ import annotations

import pytest
from pydantic import BaseModel

from mozaiksai.core.workflow.task_batches import (
    _build_scoped_worker_prompt,
    _planned_pages,
    _reject_task_output_identity_drift,
    _stamp_task_output_identity,
    resolve_path_value,
)

# ---------------------------------------------------------------------------
# 1. resolve_path_value
# ---------------------------------------------------------------------------

class TestResolvePathValue:
    def test_empty_path_returns_payload(self):
        result = resolve_path_value({"key": "val"}, "")
        assert result == {"key": "val"}

    def test_simple_key(self):
        assert resolve_path_value({"name": "test"}, "name") == "test"

    def test_nested_dot_path(self):
        payload = {"a": {"b": {"c": 42}}}
        assert resolve_path_value(payload, "a.b.c") == 42

    def test_missing_key_returns_none(self):
        assert resolve_path_value({"a": 1}, "b") is None

    def test_missing_nested_key_returns_none(self):
        assert resolve_path_value({"a": {"b": 1}}, "a.c") is None

    def test_list_integer_index(self):
        payload = {"items": ["first", "second", "third"]}
        assert resolve_path_value(payload, "items.1") == "second"

    def test_list_out_of_range_returns_none(self):
        payload = {"items": ["a", "b"]}
        assert resolve_path_value(payload, "items.5") is None

    def test_non_integer_key_on_list_returns_none(self):
        payload = {"items": ["a", "b"]}
        assert resolve_path_value(payload, "items.name") is None

    def test_path_through_scalar_returns_none(self):
        payload = {"a": 42}
        assert resolve_path_value(payload, "a.b") is None

    def test_none_payload_returns_none(self):
        assert resolve_path_value(None, "key") is None

    def test_empty_path_segments_skipped(self):
        # "a..b" has empty segment — skipped
        payload = {"a": {"b": 99}}
        result = resolve_path_value(payload, "a..b")
        assert result == 99

    def test_pydantic_model_traversal(self):
        class Inner(BaseModel):
            value: int = 10

        class Outer(BaseModel):
            inner: Inner = Inner()

        outer = Outer()
        result = resolve_path_value(outer, "inner.value")
        assert result == 10


# ---------------------------------------------------------------------------
# 2. _build_scoped_worker_prompt
# ---------------------------------------------------------------------------

class TestBuildScopedWorkerPrompt:
    def _ctx(self, **kwargs) -> dict:
        return {
            "current_task_batch_id": "batch-1",
            "current_task_id": "task-1",
            "current_task": {"kind": "generate_module"},
            "dependency_task_outputs": {},
            **kwargs,
        }

    def test_contains_original_prompt(self):
        result = _build_scoped_worker_prompt("Do the thing.", self._ctx())
        assert "Do the thing." in result

    def test_contains_task_batch_context_marker(self):
        result = _build_scoped_worker_prompt("prompt", self._ctx())
        assert "[TASK BATCH CONTEXT]" in result

    def test_contains_current_task_id(self):
        ctx = self._ctx(current_task_id="my-task-42")
        result = _build_scoped_worker_prompt("prompt", ctx)
        assert "my-task-42" in result

    def test_contains_current_task(self):
        ctx = self._ctx(current_task={"kind": "build_page"})
        result = _build_scoped_worker_prompt("prompt", ctx)
        assert "build_page" in result

    def test_dependency_outputs_defaulted_empty(self):
        ctx = {"current_task_batch_id": None, "current_task_id": "t", "current_task": None}
        result = _build_scoped_worker_prompt("prompt", ctx)
        # dependency_task_outputs defaults to {} → serialized as "{}"
        assert '"dependency_task_outputs": {}' in result

    def test_missing_context_keys_produce_none_not_error(self):
        # Should not raise even if keys are absent
        result = _build_scoped_worker_prompt("prompt", {})
        assert "[TASK BATCH CONTEXT]" in result


# ---------------------------------------------------------------------------
# 3. _reject_task_output_identity_drift
# ---------------------------------------------------------------------------

class TestRejectTaskOutputIdentityDrift:
    def test_matching_task_id_no_error(self):
        task = {"task_id": "t-1", "kind": "generate"}
        output = {"task_id": "t-1", "kind": "generate"}
        _reject_task_output_identity_drift(task, output)  # no exception

    def test_mismatched_task_id_raises(self):
        task = {"task_id": "t-1", "kind": "generate"}
        output = {"task_id": "t-2", "kind": "generate"}
        with pytest.raises(ValueError, match="t-1"):
            _reject_task_output_identity_drift(task, output)

    def test_mismatched_kind_raises(self):
        task = {"task_id": "t-1", "kind": "generate"}
        output = {"task_id": "t-1", "kind": "analyze"}
        with pytest.raises(ValueError, match="analyze"):
            _reject_task_output_identity_drift(task, output)

    def test_empty_task_id_skips_check(self):
        task = {"task_id": "", "kind": "generate"}
        output = {"task_id": "t-2", "kind": "generate"}
        _reject_task_output_identity_drift(task, output)  # no exception

    def test_empty_output_task_id_skips_check(self):
        task = {"task_id": "t-1", "kind": "generate"}
        output = {"task_id": "", "kind": "generate"}
        _reject_task_output_identity_drift(task, output)  # no exception

    def test_empty_kind_in_task_skips_kind_check(self):
        task = {"task_id": "t-1", "kind": ""}
        output = {"task_id": "t-1", "kind": "whatever"}
        _reject_task_output_identity_drift(task, output)  # no exception

    def test_empty_kind_in_output_skips_kind_check(self):
        task = {"task_id": "t-1", "kind": "generate"}
        output = {"task_id": "t-1", "kind": ""}
        _reject_task_output_identity_drift(task, output)  # no exception


# ---------------------------------------------------------------------------
# 4. _stamp_task_output_identity
# ---------------------------------------------------------------------------

class TestStampTaskOutputIdentity:
    def test_stamps_task_id_when_absent(self):
        task = {"task_id": "t-1", "kind": "generate"}
        output: dict = {}
        _stamp_task_output_identity(task, output)
        assert output["task_id"] == "t-1"

    def test_does_not_overwrite_existing_task_id(self):
        task = {"task_id": "t-1", "kind": "generate"}
        output = {"task_id": "existing", "kind": ""}
        _stamp_task_output_identity(task, output)
        assert output["task_id"] == "existing"

    def test_stamps_kind_when_absent(self):
        task = {"task_id": "t-1", "kind": "generate"}
        output: dict = {}
        _stamp_task_output_identity(task, output)
        assert output["kind"] == "generate"

    def test_does_not_overwrite_existing_kind(self):
        task = {"task_id": "t-1", "kind": "generate"}
        output = {"task_id": "", "kind": "analyze"}
        _stamp_task_output_identity(task, output)
        assert output["kind"] == "analyze"

    def test_empty_task_id_not_stamped(self):
        task = {"task_id": "", "kind": "generate"}
        output: dict = {}
        _stamp_task_output_identity(task, output)
        assert "task_id" not in output

    def test_empty_task_kind_not_stamped(self):
        task = {"task_id": "t-1", "kind": ""}
        output: dict = {}
        _stamp_task_output_identity(task, output)
        assert "kind" not in output


# ---------------------------------------------------------------------------
# 5. _planned_pages
# ---------------------------------------------------------------------------

class TestPlannedPages:
    def test_empty_context_returns_empty(self):
        assert _planned_pages({}) == []

    def test_app_build_plan_with_pages(self):
        ctx = {"app_build_plan": {"pages": [{"id": "home"}, {"id": "profile"}]}}
        result = _planned_pages(ctx)
        assert len(result) == 2
        assert result[0]["id"] == "home"

    def test_app_build_plan_pages_not_list_falls_through(self):
        ctx = {"app_build_plan": {"pages": "not-a-list"}, "pages": [{"id": "fallback"}]}
        result = _planned_pages(ctx)
        assert result[0]["id"] == "fallback"

    def test_top_level_pages_list_used_when_no_plan(self):
        ctx = {"pages": [{"id": "settings"}, {"id": "about"}]}
        result = _planned_pages(ctx)
        assert len(result) == 2
        assert result[0]["id"] == "settings"

    def test_non_dict_pages_filtered(self):
        ctx = {"app_build_plan": {"pages": [{"id": "home"}, "not-a-dict", None, {"id": "about"}]}}
        result = _planned_pages(ctx)
        assert len(result) == 2

    def test_returns_copy_not_original(self):
        page = {"id": "home"}
        ctx = {"app_build_plan": {"pages": [page]}}
        result = _planned_pages(ctx)
        result[0]["id"] = "mutated"
        assert page["id"] == "home"  # original not modified

    def test_app_build_plan_not_dict_falls_through(self):
        ctx = {"app_build_plan": "not-a-dict", "pages": [{"id": "from-top"}]}
        result = _planned_pages(ctx)
        assert result[0]["id"] == "from-top"
