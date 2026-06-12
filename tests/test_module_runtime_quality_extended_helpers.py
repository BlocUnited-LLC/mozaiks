"""
Pure helper unit tests for:
  factory_app/workflows/AppGenerator/tools/module_runtime_quality.py

Covers helpers NOT already tested in test_module_runtime_quality_pure_helpers.py:

  _context_get:
    - None context → default returned
    - dict context with key → value returned
    - dict context missing key → default returned
    - object with .get → value returned
    - object with .data attribute → value from data
    - None stored value → returns default not None

  _context_set:
    - None context → no-op
    - dict context → key set in place
    - object with .data attribute → key set in data
    - object with .set method → .set called

  _as_int:
    - valid int input → same int returned
    - valid string number → converted
    - invalid string → default returned
    - None → default returned
    - float → truncated to int
    - custom default used when conversion fails

  _iter_backend_python_files:
    - non-list input → yields nothing
    - non-dict item → skipped
    - item without filename → skipped
    - item with content=None → skipped
    - item with matching backend python path → yielded
    - item with non-backend path → not yielded
    - content stringified
"""
from __future__ import annotations

from factory_app.workflows.AppGenerator.tools.module_runtime_quality import (
    _as_int,
    _context_get,
    _context_set,
    _iter_backend_python_files,
)

# ---------------------------------------------------------------------------
# 1. _context_get
# ---------------------------------------------------------------------------

class TestContextGet:
    def test_none_context_returns_default(self):
        assert _context_get(None, "key") is None
        assert _context_get(None, "key", "fallback") == "fallback"

    def test_dict_context_key_found(self):
        assert _context_get({"my_key": "val"}, "my_key") == "val"

    def test_dict_context_missing_key_returns_default(self):
        assert _context_get({}, "missing", "default") == "default"

    def test_none_stored_value_returns_default(self):
        # The implementation returns default when value is None
        assert _context_get({"key": None}, "key", "default") == "default"

    def test_object_with_data_attribute(self):
        class FakeCtx:
            data = {"x": 42}
        # FakeCtx has no .get, so falls through to .data
        ctx = FakeCtx()
        # Actually FakeCtx has no .get, so will use .data
        result = _context_get(ctx, "x", 0)
        assert result == 42

    def test_integer_context_returns_default(self):
        assert _context_get(42, "key", "fallback") == "fallback"


# ---------------------------------------------------------------------------
# 2. _context_set
# ---------------------------------------------------------------------------

class TestContextSet:
    def test_none_context_is_noop(self):
        # Should not raise
        _context_set(None, "key", "value")

    def test_dict_context_sets_key(self):
        ctx = {}
        _context_set(ctx, "mykey", "myval")
        assert ctx["mykey"] == "myval"

    def test_dict_context_overwrites_key(self):
        ctx = {"x": "old"}
        _context_set(ctx, "x", "new")
        assert ctx["x"] == "new"

    def test_object_with_data_attribute(self):
        class FakeCtx:
            data = {}
        ctx = FakeCtx()
        _context_set(ctx, "foo", "bar")
        assert ctx.data["foo"] == "bar"

    def test_object_with_set_method(self):
        calls = {}

        class FakeCtx:
            def set(self, key, value):
                calls[key] = value

        ctx = FakeCtx()
        _context_set(ctx, "key", "val")
        assert calls["key"] == "val"


# ---------------------------------------------------------------------------
# 3. _as_int
# ---------------------------------------------------------------------------

class TestAsInt:
    def test_int_input_returned(self):
        assert _as_int(42) == 42

    def test_string_number_converted(self):
        assert _as_int("5") == 5

    def test_invalid_string_returns_default(self):
        assert _as_int("abc") == 0

    def test_none_returns_default(self):
        assert _as_int(None) == 0

    def test_float_truncated(self):
        assert _as_int(3.9) == 3

    def test_custom_default_used(self):
        assert _as_int("bad", default=-1) == -1

    def test_zero_returned(self):
        assert _as_int(0) == 0

    def test_negative_int(self):
        assert _as_int(-5) == -5


# ---------------------------------------------------------------------------
# 4. _iter_backend_python_files
# ---------------------------------------------------------------------------

class TestIterBackendPythonFiles:
    def test_non_list_yields_nothing(self):
        result = list(_iter_backend_python_files("not-a-list"))
        assert result == []

    def test_none_yields_nothing(self):
        result = list(_iter_backend_python_files(None))
        assert result == []

    def test_non_dict_item_skipped(self):
        result = list(_iter_backend_python_files(["not-a-dict"]))
        assert result == []

    def test_item_without_filename_skipped(self):
        item = {"content": "x = 1"}
        result = list(_iter_backend_python_files([item]))
        assert result == []

    def test_item_with_none_content_skipped(self):
        item = {"filename": "modules/orders/backend/service.py", "content": None}
        result = list(_iter_backend_python_files([item]))
        assert result == []

    def test_valid_backend_python_yielded(self):
        item = {
            "filename": "modules/orders/backend/service.py",
            "content": "def get_orders(): pass",
        }
        result = list(_iter_backend_python_files([item]))
        assert len(result) == 1
        assert result[0][0] == "modules/orders/backend/service.py"

    def test_non_backend_path_skipped(self):
        item = {
            "filename": "modules/orders/module.yaml",
            "content": "id: orders",
        }
        result = list(_iter_backend_python_files([item]))
        assert result == []

    def test_content_stringified(self):
        item = {
            "filename": "modules/orders/backend/handler.py",
            "content": "class Handler: pass",
        }
        result = list(_iter_backend_python_files([item]))
        assert result[0][1] == "class Handler: pass"

    def test_path_key_also_works(self):
        item = {
            "path": "modules/orders/backend/handler.py",
            "content": "x = 1",
        }
        result = list(_iter_backend_python_files([item]))
        assert len(result) == 1

    def test_multiple_valid_items(self):
        items = [
            {"filename": "modules/orders/backend/service.py", "content": "a"},
            {"filename": "modules/payments/backend/service.py", "content": "b"},
        ]
        result = list(_iter_backend_python_files(items))
        assert len(result) == 2
