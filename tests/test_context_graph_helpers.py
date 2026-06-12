"""
mozaiksai.core.app_context.context_graph pure helper unit tests.

Covers helpers not yet tested by test_context_graph_contract_role_helpers.py
or test_context_graph_extraction_helpers.py:

  _stable_token:
    - ascii alphanumeric → lowercased
    - spaces/specials replaced with underscore
    - leading/trailing underscores stripped
    - empty string → sha256[:24]
    - whitespace-only → sha256[:24] of original raw
    - very long clean token (>96) → truncated[:83] + _ + sha256[:12]
    - deterministic for same input

  _stable_id:
    - format is "prefix:token"
    - deterministic

  _language_for_path:
    - .py → python
    - .js / .jsx → javascript
    - .ts / .tsx → typescript
    - .yaml / .yml → yaml
    - .json → json
    - .css → css
    - .md → markdown
    - unknown suffix / no suffix → text
    - uppercase suffix normalised

  _contract_path:
    - "app/" prefix stripped
    - non-"app/" prefix unchanged
    - empty string unchanged
    - "application/..." not stripped (wrong prefix)

  _safe_relpath:
    - valid relative path returned normalised
    - backslashes converted to forward slashes
    - absolute path → None
    - path with ".." segment → None
    - non-string → None
    - empty string → None
    - leading slash stripped, not absolute → safe

  _safe_file_map:
    - unsafe paths filtered out
    - safe paths retained with content as str
    - None file_map treated as empty

  _first_text:
    - returns first key with non-empty string value
    - empty string skipped
    - non-string value skipped
    - returns None when nothing matches
    - strips whitespace from value

  _dedupe:
    - preserves insertion order
    - removes duplicates
    - skips empty strings
    - strips whitespace from entries

  _clean_metadata:
    - removes None values
    - removes empty string values
    - removes empty list values
    - keeps 0, False, non-empty lists
    - None input → {}

  _sha256_json:
    - deterministic for same input
    - different input → different hash
    - keys sorted (dict ordering irrelevant)
    - returns 64-char hex string

  _file_map_checksum:
    - deterministic for same file_map
    - starts with "sha256:"
    - different content → different checksum
    - key order in input irrelevant (sorted internally)

  _short_text:
    - None → None
    - empty string → None
    - whitespace-only → None
    - normalises internal whitespace
    - truncated at max_length

  _string_list:
    - non-list → []
    - empty list → []
    - deduplicates
    - capped at max_items
    - None items skipped
    - whitespace collapsed in items

  _annotation_node_id:
    - dict with node_id → stripped string
    - dict without node_id → None
    - empty node_id → None
    - non-dict → None

  _normalize_semantic_annotation:
    - valid risk_level preserved
    - invalid risk_level → "unknown"
    - confidence clamped 0.0–1.0
    - non-numeric confidence → 0.0
    - schema_version and source always present
    - empty purpose/lists filtered by _clean_metadata

  _dedupe_symbols:
    - duplicate (name, kind, line) removed
    - first occurrence preserved
    - symbols with different line kept
    - empty list → []

  _semantic_candidate_sort_key:
    - module_action_handler → priority 0
    - module_service_symbol / module_repo_symbol → priority 1
    - page_component / ui_component → priority 2
    - other non-empty role → priority 5
    - no role → priority 20
    - returns tuple (priority, path, qualified_name_or_label)
"""
from __future__ import annotations

import hashlib
import json

from mozaiksai.core.app_context.context_graph import (
    ExtractedSymbol,
    _annotation_node_id,
    _clean_metadata,
    _contract_path,
    _dedupe,
    _dedupe_symbols,
    _file_map_checksum,
    _first_text,
    _language_for_path,
    _normalize_semantic_annotation,
    _safe_file_map,
    _safe_relpath,
    _semantic_candidate_sort_key,
    _sha256_json,
    _short_text,
    _stable_id,
    _stable_token,
    _string_list,
)
from mozaiksai.core.app_context.models import AppContextGraphNode, GraphNodeType

# ---------------------------------------------------------------------------
# 1. _stable_token
# ---------------------------------------------------------------------------

class TestStableToken:
    def test_lowercase_ascii_preserved(self):
        assert _stable_token("hello") == "hello"

    def test_uppercase_lowercased(self):
        assert _stable_token("HELLO") == "hello"

    def test_spaces_replaced_with_underscore(self):
        assert _stable_token("hello world") == "hello_world"

    def test_specials_replaced_with_underscore(self):
        result = _stable_token("hello-world.test")
        assert result == "hello_world_test"

    def test_leading_trailing_underscores_stripped(self):
        result = _stable_token("__hello__")
        assert result == "hello"

    def test_mixed_case_and_specials(self):
        result = _stable_token("My Module: v2!")
        assert "_" in result
        assert result == result.lower()

    def test_empty_string_returns_sha256(self):
        result = _stable_token("")
        expected = hashlib.sha256(b"").hexdigest()[:24]
        assert result == expected

    def test_whitespace_only_returns_sha256_of_original(self):
        raw = "   "
        result = _stable_token(raw)
        expected = hashlib.sha256(raw.encode()).hexdigest()[:24]
        assert result == expected

    def test_long_token_truncated_with_digest(self):
        # 100 a's → clean token = "a" * 100 > 96
        raw = "a" * 100
        result = _stable_token(raw)
        digest = hashlib.sha256(raw.encode()).hexdigest()[:12]
        clean = "a" * 100
        assert result == f"{clean[:83]}_{digest}"
        # total length: 83 + 1 + 12 = 96
        assert len(result) == 96

    def test_exactly_96_chars_not_truncated(self):
        raw = "a" * 96
        result = _stable_token(raw)
        assert result == "a" * 96

    def test_deterministic(self):
        assert _stable_token("my.module.path") == _stable_token("my.module.path")

    def test_numeric_preserved(self):
        assert _stable_token("item123") == "item123"

    def test_underscores_in_middle_preserved(self):
        assert _stable_token("my_func_name") == "my_func_name"


# ---------------------------------------------------------------------------
# 2. _stable_id
# ---------------------------------------------------------------------------

class TestStableId:
    def test_format_is_prefix_colon_token(self):
        result = _stable_id("file", "hello.py")
        assert result.startswith("file:")

    def test_deterministic(self):
        assert _stable_id("symbol", "my_func") == _stable_id("symbol", "my_func")

    def test_different_prefix_different_id(self):
        assert _stable_id("file", "foo") != _stable_id("symbol", "foo")

    def test_different_raw_different_id(self):
        assert _stable_id("file", "a.py") != _stable_id("file", "b.py")

    def test_token_embedded_in_result(self):
        token = _stable_token("hello")
        result = _stable_id("file", "hello")
        assert f"file:{token}" == result


# ---------------------------------------------------------------------------
# 3. _language_for_path
# ---------------------------------------------------------------------------

class TestLanguageForPath:
    def test_py_is_python(self):
        assert _language_for_path("hello.py") == "python"

    def test_js_is_javascript(self):
        assert _language_for_path("component.js") == "javascript"

    def test_jsx_is_javascript(self):
        assert _language_for_path("component.jsx") == "javascript"

    def test_ts_is_typescript(self):
        assert _language_for_path("module.ts") == "typescript"

    def test_tsx_is_typescript(self):
        assert _language_for_path("component.tsx") == "typescript"

    def test_yaml_is_yaml(self):
        assert _language_for_path("config.yaml") == "yaml"

    def test_yml_is_yaml(self):
        assert _language_for_path("config.yml") == "yaml"

    def test_json_is_json(self):
        assert _language_for_path("data.json") == "json"

    def test_css_is_css(self):
        assert _language_for_path("styles.css") == "css"

    def test_md_is_markdown(self):
        assert _language_for_path("README.md") == "markdown"

    def test_unknown_suffix_is_text(self):
        assert _language_for_path("file.txt") == "text"

    def test_no_suffix_is_text(self):
        assert _language_for_path("Makefile") == "text"

    def test_uppercase_suffix_normalised(self):
        assert _language_for_path("script.PY") == "python"

    def test_nested_path_uses_final_suffix(self):
        assert _language_for_path("modules/tasks/backend/service.py") == "python"


# ---------------------------------------------------------------------------
# 4. _contract_path
# ---------------------------------------------------------------------------

class TestContractPath:
    def test_app_prefix_stripped(self):
        assert _contract_path("app/modules/tasks/module.yaml") == "modules/tasks/module.yaml"

    def test_non_app_prefix_unchanged(self):
        assert _contract_path("modules/tasks/module.yaml") == "modules/tasks/module.yaml"

    def test_workflows_prefix_unchanged(self):
        assert _contract_path("workflows/MyFlow/orchestrator.yaml") == "workflows/MyFlow/orchestrator.yaml"

    def test_empty_string_unchanged(self):
        assert _contract_path("") == ""

    def test_application_prefix_not_stripped(self):
        # "application/..." does NOT start with "app/"
        assert _contract_path("application/test.py") == "application/test.py"

    def test_app_slash_stripped_only_once(self):
        # "app/app/foo.py" → strips first "app/" → "app/foo.py"
        assert _contract_path("app/app/foo.py") == "app/foo.py"

    def test_nested_app_path(self):
        assert _contract_path("app/ui/pages/home.yaml") == "ui/pages/home.yaml"


# ---------------------------------------------------------------------------
# 5. _safe_relpath
# ---------------------------------------------------------------------------

class TestSafeRelpath:
    def test_valid_relative_path_returned(self):
        assert _safe_relpath("modules/tasks/module.yaml") == "modules/tasks/module.yaml"

    def test_backslashes_converted(self):
        assert _safe_relpath("modules\\tasks\\module.yaml") == "modules/tasks/module.yaml"

    def test_leading_slash_stripped(self):
        # after strip("/"), becomes relative → safe
        assert _safe_relpath("/modules/tasks.py") == "modules/tasks.py"

    def test_parent_traversal_returns_none(self):
        assert _safe_relpath("../secret.py") is None

    def test_embedded_parent_traversal_returns_none(self):
        assert _safe_relpath("modules/../secret.py") is None

    def test_non_string_returns_none(self):
        assert _safe_relpath(None) is None  # type: ignore[arg-type]
        assert _safe_relpath(42) is None  # type: ignore[arg-type]

    def test_empty_string_returns_none(self):
        assert _safe_relpath("") is None

    def test_whitespace_only_returns_none(self):
        assert _safe_relpath("   ") is None

    def test_whitespace_stripped_from_path(self):
        result = _safe_relpath("  modules/tasks.py  ")
        assert result == "modules/tasks.py"

    def test_simple_filename_safe(self):
        assert _safe_relpath("module.yaml") == "module.yaml"


# ---------------------------------------------------------------------------
# 6. _safe_file_map
# ---------------------------------------------------------------------------

class TestSafeFileMap:
    def test_valid_paths_retained(self):
        fm = {"modules/tasks/module.yaml": "id: tasks", "app/ui/page.yaml": "id: home"}
        result = _safe_file_map(fm)
        assert "modules/tasks/module.yaml" in result
        assert "app/ui/page.yaml" in result

    def test_unsafe_paths_filtered(self):
        fm = {"../secret.py": "content", "valid.py": "ok"}
        result = _safe_file_map(fm)
        assert "../secret.py" not in result
        assert "valid.py" in result

    def test_backslash_paths_normalised(self):
        fm = {"modules\\tasks\\module.yaml": "content"}
        result = _safe_file_map(fm)
        assert "modules/tasks/module.yaml" in result

    def test_none_file_map_returns_empty(self):
        result = _safe_file_map(None)  # type: ignore[arg-type]
        assert result == {}

    def test_content_cast_to_str(self):
        fm = {"valid.yaml": 123}  # type: ignore[dict-item]
        result = _safe_file_map(fm)
        assert result["valid.yaml"] == "123"

    def test_empty_file_map_returns_empty(self):
        assert _safe_file_map({}) == {}


# ---------------------------------------------------------------------------
# 7. _first_text
# ---------------------------------------------------------------------------

class TestFirstText:
    def test_returns_first_matching_key(self):
        result = _first_text({"id": "tasks", "name": "Tasks"}, "id", "name")
        assert result == "tasks"

    def test_skips_empty_string(self):
        result = _first_text({"id": "", "name": "Tasks"}, "id", "name")
        assert result == "Tasks"

    def test_skips_non_string_value(self):
        result = _first_text({"id": 42, "name": "Tasks"}, "id", "name")
        assert result == "Tasks"

    def test_returns_none_when_nothing_matches(self):
        result = _first_text({"id": "", "name": None}, "id", "name")
        assert result is None

    def test_strips_whitespace(self):
        result = _first_text({"title": "  My Page  "}, "title")
        assert result == "My Page"

    def test_returns_none_for_empty_dict(self):
        assert _first_text({}, "id", "name") is None


# ---------------------------------------------------------------------------
# 8. _dedupe
# ---------------------------------------------------------------------------

class TestDedupe:
    def test_preserves_order(self):
        assert _dedupe(["c", "a", "b"]) == ["c", "a", "b"]

    def test_removes_duplicates(self):
        assert _dedupe(["a", "b", "a"]) == ["a", "b"]

    def test_skips_empty_strings(self):
        assert _dedupe(["a", "", "b"]) == ["a", "b"]

    def test_strips_whitespace(self):
        result = _dedupe(["  a  ", "b"])
        assert result == ["a", "b"]

    def test_empty_input_returns_empty(self):
        assert _dedupe([]) == []

    def test_whitespace_only_items_skipped(self):
        assert _dedupe(["   ", "a"]) == ["a"]


# ---------------------------------------------------------------------------
# 9. _clean_metadata
# ---------------------------------------------------------------------------

class TestCleanMetadata:
    def test_removes_none_values(self):
        result = _clean_metadata({"a": None, "b": "val"})
        assert "a" not in result
        assert result["b"] == "val"

    def test_removes_empty_string_values(self):
        result = _clean_metadata({"a": "", "b": "val"})
        assert "a" not in result

    def test_removes_empty_list_values(self):
        result = _clean_metadata({"a": [], "b": [1, 2]})
        assert "a" not in result
        assert result["b"] == [1, 2]

    def test_keeps_zero(self):
        result = _clean_metadata({"count": 0})
        assert result["count"] == 0

    def test_keeps_false(self):
        result = _clean_metadata({"flag": False})
        assert result["flag"] is False

    def test_keeps_non_empty_dict(self):
        result = _clean_metadata({"meta": {"key": "val"}})
        assert result["meta"] == {"key": "val"}

    def test_none_input_returns_empty_dict(self):
        assert _clean_metadata(None) == {}

    def test_empty_dict_returns_empty_dict(self):
        assert _clean_metadata({}) == {}


# ---------------------------------------------------------------------------
# 10. _sha256_json
# ---------------------------------------------------------------------------

class TestSha256Json:
    def test_returns_64_char_hex(self):
        result = _sha256_json({"key": "val"})
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_deterministic(self):
        payload = {"b": 2, "a": 1}
        assert _sha256_json(payload) == _sha256_json(payload)

    def test_key_order_irrelevant(self):
        assert _sha256_json({"a": 1, "b": 2}) == _sha256_json({"b": 2, "a": 1})

    def test_different_payload_different_hash(self):
        assert _sha256_json({"key": "val1"}) != _sha256_json({"key": "val2"})

    def test_empty_dict_deterministic(self):
        r1 = _sha256_json({})
        r2 = _sha256_json({})
        assert r1 == r2

    def test_matches_manual_computation(self):
        payload = {"hello": "world"}
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
        expected = hashlib.sha256(raw).hexdigest()
        assert _sha256_json(payload) == expected


# ---------------------------------------------------------------------------
# 11. _file_map_checksum
# ---------------------------------------------------------------------------

class TestFileMapChecksum:
    def test_starts_with_sha256_prefix(self):
        result = _file_map_checksum({"a.py": "content"})
        assert result.startswith("sha256:")

    def test_deterministic_for_same_map(self):
        fm = {"a.py": "hello", "b.py": "world"}
        assert _file_map_checksum(fm) == _file_map_checksum(fm)

    def test_key_order_irrelevant(self):
        fm1 = {"a.py": "hello", "b.py": "world"}
        fm2 = {"b.py": "world", "a.py": "hello"}
        assert _file_map_checksum(fm1) == _file_map_checksum(fm2)

    def test_different_content_different_checksum(self):
        fm1 = {"a.py": "hello"}
        fm2 = {"a.py": "goodbye"}
        assert _file_map_checksum(fm1) != _file_map_checksum(fm2)

    def test_empty_file_map_deterministic(self):
        assert _file_map_checksum({}) == _file_map_checksum({})


# ---------------------------------------------------------------------------
# 12. _short_text
# ---------------------------------------------------------------------------

class TestShortText:
    def test_none_returns_none(self):
        assert _short_text(None, max_length=100) is None

    def test_empty_string_returns_none(self):
        assert _short_text("", max_length=100) is None

    def test_whitespace_only_returns_none(self):
        assert _short_text("   ", max_length=100) is None

    def test_normalises_internal_whitespace(self):
        result = _short_text("hello\n  world", max_length=100)
        assert result == "hello world"

    def test_truncated_at_max_length(self):
        result = _short_text("abcde", max_length=3)
        assert result == "abc"

    def test_within_max_length_returned_whole(self):
        result = _short_text("hello", max_length=100)
        assert result == "hello"

    def test_non_string_converted(self):
        result = _short_text(42, max_length=100)
        assert result == "42"

    def test_tab_normalised(self):
        result = _short_text("a\tb", max_length=100)
        assert result == "a b"


# ---------------------------------------------------------------------------
# 13. _string_list
# ---------------------------------------------------------------------------

class TestStringList:
    def test_non_list_returns_empty(self):
        assert _string_list("not_a_list", max_items=5) == []
        assert _string_list(None, max_items=5) == []
        assert _string_list({}, max_items=5) == []

    def test_empty_list_returns_empty(self):
        assert _string_list([], max_items=5) == []

    def test_strings_returned(self):
        result = _string_list(["a", "b", "c"], max_items=5)
        assert result == ["a", "b", "c"]

    def test_duplicates_removed(self):
        result = _string_list(["a", "b", "a"], max_items=5)
        assert result == ["a", "b"]

    def test_capped_at_max_items(self):
        result = _string_list(["a", "b", "c", "d"], max_items=2)
        assert result == ["a", "b"]

    def test_none_items_skipped(self):
        result = _string_list([None, "a", None, "b"], max_items=5)
        assert result == ["a", "b"]

    def test_whitespace_collapsed(self):
        result = _string_list(["hello  world"], max_items=5)
        assert result == ["hello world"]


# ---------------------------------------------------------------------------
# 14. _annotation_node_id
# ---------------------------------------------------------------------------

class TestAnnotationNodeId:
    def test_dict_with_node_id_returned(self):
        assert _annotation_node_id({"node_id": "abc"}) == "abc"

    def test_strips_whitespace(self):
        assert _annotation_node_id({"node_id": "  abc  "}) == "abc"

    def test_missing_node_id_returns_none(self):
        assert _annotation_node_id({}) is None

    def test_empty_node_id_returns_none(self):
        assert _annotation_node_id({"node_id": ""}) is None

    def test_none_node_id_returns_none(self):
        assert _annotation_node_id({"node_id": None}) is None

    def test_non_dict_returns_none(self):
        assert _annotation_node_id("not_a_dict") is None  # type: ignore[arg-type]
        assert _annotation_node_id(None) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 15. _normalize_semantic_annotation
# ---------------------------------------------------------------------------

class TestNormalizeSemanticAnnotation:
    def _item(self, **kwargs):
        return {
            "risk_level": "low",
            "confidence": 0.8,
            **kwargs,
        }

    def test_valid_risk_level_preserved(self):
        for level in ("low", "medium", "high", "unknown"):
            result = _normalize_semantic_annotation(self._item(risk_level=level), annotated_at="2026-01-01")
            assert result["risk_level"] == level

    def test_invalid_risk_level_becomes_unknown(self):
        result = _normalize_semantic_annotation(self._item(risk_level="critical"), annotated_at="2026-01-01")
        assert result["risk_level"] == "unknown"

    def test_missing_risk_level_becomes_unknown(self):
        item = {"confidence": 0.5}
        result = _normalize_semantic_annotation(item, annotated_at="2026-01-01")
        assert result["risk_level"] == "unknown"

    def test_confidence_preserved_within_bounds(self):
        result = _normalize_semantic_annotation(self._item(confidence=0.5), annotated_at="2026-01-01")
        assert result["confidence"] == 0.5

    def test_confidence_clamped_above_one(self):
        result = _normalize_semantic_annotation(self._item(confidence=2.0), annotated_at="2026-01-01")
        assert result["confidence"] == 1.0

    def test_confidence_clamped_below_zero(self):
        result = _normalize_semantic_annotation(self._item(confidence=-0.5), annotated_at="2026-01-01")
        assert result["confidence"] == 0.0

    def test_non_numeric_confidence_becomes_zero(self):
        result = _normalize_semantic_annotation(self._item(confidence="invalid"), annotated_at="2026-01-01")
        assert result["confidence"] == 0.0

    def test_schema_version_always_present(self):
        result = _normalize_semantic_annotation({}, annotated_at="2026-01-01")
        assert "schema_version" in result
        assert result["schema_version"].startswith("mozaiks")

    def test_source_is_llm_advisory(self):
        result = _normalize_semantic_annotation({}, annotated_at="2026-01-01")
        assert result["source"] == "llm_advisory"

    def test_annotated_at_preserved(self):
        result = _normalize_semantic_annotation({}, annotated_at="2026-06-12T00:00:00Z")
        assert result["annotated_at"] == "2026-06-12T00:00:00Z"

    def test_empty_purpose_filtered(self):
        result = _normalize_semantic_annotation(self._item(purpose=""), annotated_at="2026-01-01")
        assert "purpose" not in result

    def test_purpose_included_when_present(self):
        result = _normalize_semantic_annotation(self._item(purpose="Handles domain events"), annotated_at="2026-01-01")
        assert result["purpose"] == "Handles domain events"

    def test_empty_domain_concepts_filtered(self):
        result = _normalize_semantic_annotation(self._item(domain_concepts=[]), annotated_at="2026-01-01")
        assert "domain_concepts" not in result

    def test_domain_concepts_included_when_present(self):
        result = _normalize_semantic_annotation(
            self._item(domain_concepts=["user", "event"]), annotated_at="2026-01-01"
        )
        assert result["domain_concepts"] == ["user", "event"]


# ---------------------------------------------------------------------------
# 16. _dedupe_symbols
# ---------------------------------------------------------------------------

class TestDedupeSymbols:
    def _sym(self, name: str, kind: str = "function", line: int | None = 1) -> ExtractedSymbol:
        return ExtractedSymbol(name=name, kind=kind, line=line)

    def test_empty_list_returns_empty(self):
        assert _dedupe_symbols([]) == []

    def test_single_symbol_returned(self):
        sym = self._sym("foo")
        assert _dedupe_symbols([sym]) == [sym]

    def test_duplicate_name_kind_line_removed(self):
        sym1 = self._sym("foo", "function", 1)
        sym2 = self._sym("foo", "function", 1)
        result = _dedupe_symbols([sym1, sym2])
        assert len(result) == 1
        assert result[0] is sym1

    def test_different_line_kept(self):
        sym1 = self._sym("foo", "function", 1)
        sym2 = self._sym("foo", "function", 2)
        result = _dedupe_symbols([sym1, sym2])
        assert len(result) == 2

    def test_different_kind_kept(self):
        sym1 = self._sym("Foo", "function", 1)
        sym2 = self._sym("Foo", "class", 1)
        result = _dedupe_symbols([sym1, sym2])
        assert len(result) == 2

    def test_preserves_first_occurrence(self):
        sym1 = self._sym("foo", "function", 1)
        sym2 = self._sym("foo", "function", 1)
        result = _dedupe_symbols([sym1, sym2])
        assert result[0] is sym1

    def test_none_line_deduped_correctly(self):
        sym1 = self._sym("foo", "function", None)
        sym2 = self._sym("foo", "function", None)
        result = _dedupe_symbols([sym1, sym2])
        assert len(result) == 1


# ---------------------------------------------------------------------------
# 17. _semantic_candidate_sort_key
# ---------------------------------------------------------------------------

class TestSemanticCandidateSortKey:
    def _node(self, contract_role: str = "", path: str = "a.py", label: str = "fn") -> AppContextGraphNode:
        return AppContextGraphNode(
            node_id="test-node",
            node_type=GraphNodeType.SYMBOL,
            label=label,
            metadata={"contract_role": contract_role, "path": path, "qualified_name": label},
        )

    def test_module_action_handler_priority_zero(self):
        key = _semantic_candidate_sort_key(self._node("module_action_handler"))
        assert key[0] == 0

    def test_module_service_symbol_priority_one(self):
        key = _semantic_candidate_sort_key(self._node("module_service_symbol"))
        assert key[0] == 1

    def test_module_repo_symbol_priority_one(self):
        key = _semantic_candidate_sort_key(self._node("module_repo_symbol"))
        assert key[0] == 1

    def test_page_component_priority_two(self):
        key = _semantic_candidate_sort_key(self._node("page_component"))
        assert key[0] == 2

    def test_ui_component_priority_two(self):
        key = _semantic_candidate_sort_key(self._node("ui_component"))
        assert key[0] == 2

    def test_other_nonempty_role_priority_five(self):
        key = _semantic_candidate_sort_key(self._node("module_symbol"))
        assert key[0] == 5

    def test_no_role_priority_twenty(self):
        key = _semantic_candidate_sort_key(self._node(""))
        assert key[0] == 20

    def test_returns_tuple_with_three_elements(self):
        key = _semantic_candidate_sort_key(self._node("module_action_handler"))
        assert isinstance(key, tuple)
        assert len(key) == 3

    def test_path_in_second_element(self):
        key = _semantic_candidate_sort_key(self._node("module_action_handler", path="modules/foo/handler.py"))
        assert key[1] == "modules/foo/handler.py"

    def test_label_in_third_element_when_no_qualified_name(self):
        node = AppContextGraphNode(
            node_id="n",
            node_type=GraphNodeType.SYMBOL,
            label="my_func",
            metadata={"contract_role": "module_action_handler", "path": ""},
        )
        key = _semantic_candidate_sort_key(node)
        assert key[2] == "my_func"
