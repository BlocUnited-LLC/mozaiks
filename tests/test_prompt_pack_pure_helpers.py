"""
Pure helper unit tests for:
  factory_app/workflows/_shared/context_graph/prompt_pack.py

Covers:
  _path_hints:
    - empty input → []
    - dict items with "path" → path used
    - dict items with "label" fallback → label used as path
    - dict items with "node_id" fallback → node_id used as path
    - non-dict but truthy items → string-wrapped
    - None/falsy items skipped
    - limit respected
    - score, matched_terms, node_id, path_priority included when present

  _dedupe:
    - empty list → []
    - duplicates removed
    - order preserved
    - None values included only once
    - works with mixed types

  _scan_summary:
    - empty dict → None
    - None → None
    - valid health dict → fields extracted
    - parser_status/languages parsed
    - parser_active only includes entries with active_parser
"""
from __future__ import annotations

from factory_app.workflows._shared.context_graph.prompt_pack import (
    _dedupe,
    _path_hints,
    _scan_summary,
)

# ---------------------------------------------------------------------------
# 1. _path_hints
# ---------------------------------------------------------------------------

class TestPathHints:
    def test_empty_list_returns_empty(self):
        assert _path_hints([], limit=10) == []

    def test_none_returns_empty(self):
        assert _path_hints(None, limit=10) == []

    def test_dict_with_path_key(self):
        items = [{"path": "modules/billing/handler.py", "score": 85}]
        result = _path_hints(items, limit=10)
        assert len(result) == 1
        assert result[0]["path"] == "modules/billing/handler.py"
        assert result[0]["score"] == 85

    def test_dict_with_label_fallback(self):
        items = [{"label": "billing module", "score": 70}]
        result = _path_hints(items, limit=10)
        assert len(result) == 1
        assert result[0]["path"] == "billing module"

    def test_dict_with_node_id_fallback(self):
        items = [{"node_id": "node-001", "score": 50}]
        result = _path_hints(items, limit=10)
        assert len(result) == 1
        assert result[0]["path"] == "node-001"

    def test_dict_without_path_skipped(self):
        items = [{"score": 50}]
        result = _path_hints(items, limit=10)
        assert result == []

    def test_non_dict_truthy_item_string_wrapped(self):
        items = ["modules/billing/handler.py"]
        result = _path_hints(items, limit=10)
        assert len(result) == 1
        assert result[0]["path"] == "modules/billing/handler.py"

    def test_falsy_item_skipped(self):
        items = [None, "", 0, {"path": "valid.py"}]
        result = _path_hints(items, limit=10)
        assert len(result) == 1
        assert result[0]["path"] == "valid.py"

    def test_limit_respected(self):
        items = [{"path": f"file{i}.py"} for i in range(20)]
        result = _path_hints(items, limit=5)
        assert len(result) == 5

    def test_matched_terms_included(self):
        items = [{"path": "module.py", "matched_terms": ["billing", "payment"]}]
        result = _path_hints(items, limit=10)
        assert result[0]["matched_terms"] == ["billing", "payment"]

    def test_matched_terms_none_returns_empty_list(self):
        items = [{"path": "module.py"}]
        result = _path_hints(items, limit=10)
        assert result[0]["matched_terms"] == []

    def test_node_id_and_path_priority_included(self):
        items = [{"path": "module.py", "node_id": "n-001", "path_priority": 10}]
        result = _path_hints(items, limit=10)
        assert result[0]["node_id"] == "n-001"
        assert result[0]["path_priority"] == 10


# ---------------------------------------------------------------------------
# 2. _dedupe
# ---------------------------------------------------------------------------

class TestDedupe:
    def test_empty_returns_empty(self):
        assert _dedupe([]) == []

    def test_no_duplicates_unchanged(self):
        assert _dedupe([1, 2, 3]) == [1, 2, 3]

    def test_duplicates_removed(self):
        result = _dedupe([1, 2, 1, 3])
        assert result == [1, 2, 3]

    def test_order_preserved(self):
        result = _dedupe(["c", "a", "b", "a"])
        assert result == ["c", "a", "b"]

    def test_none_included_once(self):
        result = _dedupe([None, None, "x"])
        assert result.count(None) == 1

    def test_mixed_types(self):
        result = _dedupe([1, "a", 1, "a"])
        assert result == [1, "a"]

    def test_single_item(self):
        assert _dedupe(["x"]) == ["x"]


# ---------------------------------------------------------------------------
# 3. _scan_summary
# ---------------------------------------------------------------------------

class TestScanSummary:
    def test_empty_dict_returns_none(self):
        assert _scan_summary({}) is None

    def test_none_returns_none(self):
        assert _scan_summary(None) is None  # type: ignore[arg-type]

    def test_valid_health_dict_returns_summary(self):
        health = {
            "policy_id": "default",
            "selected_file_count": 42,
            "candidate_file_count": 100,
            "limit_reached": False,
        }
        result = _scan_summary(health)
        assert result is not None
        assert result["policy_id"] == "default"
        assert result["selected_file_count"] == 42

    def test_parser_active_extracted(self):
        health = {
            "policy_id": "default",
            "parser_status": {
                "languages": {
                    "python": {"active_parser": "tree-sitter"},
                    "javascript": {"active_parser": "regex"},
                }
            },
        }
        result = _scan_summary(health)
        assert result is not None
        assert result["parser_active"]["python"] == "tree-sitter"
        assert result["parser_active"]["javascript"] == "regex"

    def test_parser_active_excludes_missing_active_parser(self):
        health = {
            "policy_id": "default",
            "parser_status": {
                "languages": {
                    "python": {"active_parser": "tree-sitter"},
                    "css": {"other_key": "value"},
                }
            },
        }
        result = _scan_summary(health)
        assert result is not None
        assert "python" in result["parser_active"]
        assert "css" not in result["parser_active"]

    def test_non_dict_parser_status_tolerated(self):
        health = {
            "policy_id": "default",
            "parser_status": "invalid",
        }
        result = _scan_summary(health)
        assert result is not None
        assert result["parser_active"] == {}

    def test_selected_by_priority_included(self):
        health = {
            "policy_id": "default",
            "selected_by_priority": {"modules": 15, "other": 27},
        }
        result = _scan_summary(health)
        assert result is not None
        assert result["selected_by_priority"] == {"modules": 15, "other": 27}
