"""
Pure helper unit tests for:
  mozaiksai/core/workflow/agents/a2a.py
  mozaiksai/core/workflow/context/schema.py
  mozaiksai/core/workflow/context/projection.py

Covers (a2a.py):
  _as_bool:
    - True/False pass through
    - non-bool → default

  _as_float:
    - numeric string → float
    - int → float
    - None → default
    - invalid string → default

  _as_int:
    - int passthrough
    - numeric string → int
    - float → int
    - None → default
    - invalid string → default

  _sanitize_string_list:
    - non-list → []
    - empty list → []
    - whitespace-only strings excluded
    - non-string items excluded
    - strips whitespace from items

  _build_client_config_kwargs:
    - "streaming" key → bool coerced
    - "polling" key → bool coerced
    - "use_client_preference" key → bool coerced
    - "accepted_output_modes" key → sanitized list
    - "extensions" key → sanitized list
    - "supported_transports" key → sanitized list
    - "push_notification_configs" list key → passed through
    - "push_notification_configs" non-list → not included
    - keys not in allowlist not included
    - empty mapping → {}

Covers (context/schema.py):
  _required_text:
    - non-empty string → lowercased/stripped
    - None → ValueError
    - empty string → ValueError
    - whitespace only → ValueError

  _optional_text:
    - None → None
    - empty string → None
    - whitespace only → None
    - non-empty string → stripped

  _normalize_string_list (schema.py):
    - empty list → []
    - duplicates removed
    - whitespace strings excluded
    - None coerced and excluded
    - order preserved

Covers (context/projection.py):
  _compose_prompt_sections:
    - empty list → ""
    - section with heading and content
    - section with content only
    - non-dict sections skipped
    - multiple sections joined with double newline

  _read_dotted:
    - simple key lookup
    - dotted path lookup
    - missing key → None
    - non-mapping mid-path → None
    - empty path → None
    - None value → None

  _string_list:
    - non-list → []
    - items stripped and empty excluded
    - valid items returned

  _record_label:
    - record with id and label → "id. label"
    - record with id only
    - record with no fields → "record"
    - key not in heading → appended in parentheses

  _projection_targets_agent:
    - "*" in recipients → True for any agent
    - "all" in recipients → True
    - specific agent name → True
    - different agent name → False
    - empty recipients → False
    - string recipient (not list) → treated as single target

  _asset_projections:
    - "projections" list → list of dicts
    - "projection" single mapping → wrapped in list
    - neither → []
    - non-mapping items in list → skipped
"""
from __future__ import annotations

import pytest

from mozaiksai.core.workflow.agents.a2a import (
    _as_bool,
    _as_float,
    _as_int,
    _build_client_config_kwargs,
    _sanitize_string_list,
)
from mozaiksai.core.workflow.context.projection import (
    _asset_projections,
    _compose_prompt_sections,
    _projection_targets_agent,
    _read_dotted,
    _record_label,
    _string_list,
)
from mozaiksai.core.workflow.context.schema import (
    _normalize_string_list,
    _optional_text,
    _required_text,
)

# ---------------------------------------------------------------------------
# 1. _as_bool
# ---------------------------------------------------------------------------

class TestAsBool:
    def test_true_passthrough(self):
        assert _as_bool(True, default=False) is True

    def test_false_passthrough(self):
        assert _as_bool(False, default=True) is False

    def test_string_returns_default(self):
        assert _as_bool("true", default=True) is True

    def test_int_one_returns_default(self):
        # int is not bool (well, technically in Python bool is a subclass of int...)
        # but we rely on isinstance(value, bool), so 1 → default
        result = _as_bool(1, default=False)
        # 1 is not isinstance(1, bool) True because bool is subclass of int
        # Actually bool IS a subclass of int, so isinstance(True, int) is True
        # But isinstance(1, bool) is False. Let's check:
        assert result is False  # 1 is not bool, returns default

    def test_none_returns_default(self):
        assert _as_bool(None, default=True) is True

    def test_default_false_returned(self):
        assert _as_bool("anything", default=False) is False


# ---------------------------------------------------------------------------
# 2. _as_float
# ---------------------------------------------------------------------------

class TestAsFloat:
    def test_float_passthrough(self):
        assert _as_float(3.14, default=0.0) == 3.14

    def test_int_to_float(self):
        assert _as_float(5, default=0.0) == 5.0

    def test_string_to_float(self):
        assert _as_float("2.5", default=0.0) == 2.5

    def test_invalid_string_returns_default(self):
        assert _as_float("abc", default=1.0) == 1.0

    def test_none_returns_default(self):
        assert _as_float(None, default=0.5) == 0.5

    def test_zero_passthrough(self):
        assert _as_float(0, default=1.0) == 0.0

    def test_negative_float(self):
        assert _as_float(-1.5, default=0.0) == -1.5


# ---------------------------------------------------------------------------
# 3. _as_int
# ---------------------------------------------------------------------------

class TestAsInt:
    def test_int_passthrough(self):
        assert _as_int(42, default=0) == 42

    def test_string_to_int(self):
        assert _as_int("10", default=0) == 10

    def test_float_truncated(self):
        assert _as_int(3.9, default=0) == 3

    def test_invalid_string_returns_default(self):
        assert _as_int("abc", default=5) == 5

    def test_none_returns_default(self):
        assert _as_int(None, default=3) == 3

    def test_negative_int(self):
        assert _as_int(-5, default=0) == -5

    def test_zero_passthrough(self):
        assert _as_int(0, default=1) == 0


# ---------------------------------------------------------------------------
# 4. _sanitize_string_list
# ---------------------------------------------------------------------------

class TestSanitizeStringList:
    def test_non_list_returns_empty(self):
        assert _sanitize_string_list("not a list") == []

    def test_none_returns_empty(self):
        assert _sanitize_string_list(None) == []

    def test_empty_list_returns_empty(self):
        assert _sanitize_string_list([]) == []

    def test_valid_strings_returned(self):
        result = _sanitize_string_list(["a", "b", "c"])
        assert result == ["a", "b", "c"]

    def test_whitespace_only_excluded(self):
        result = _sanitize_string_list(["  ", "valid"])
        assert "  " not in result
        assert "valid" in result

    def test_non_string_items_excluded(self):
        result = _sanitize_string_list([42, "valid", None])
        assert 42 not in result
        assert "valid" in result

    def test_strips_whitespace_from_items(self):
        result = _sanitize_string_list(["  hello  ", "world"])
        assert "hello" in result
        assert "world" in result
        assert "  hello  " not in result

    def test_preserves_order(self):
        result = _sanitize_string_list(["b", "a", "c"])
        assert result == ["b", "a", "c"]


# ---------------------------------------------------------------------------
# 5. _build_client_config_kwargs
# ---------------------------------------------------------------------------

class TestBuildClientConfigKwargs:
    def test_empty_mapping_returns_empty(self):
        assert _build_client_config_kwargs({}) == {}

    def test_streaming_coerced_to_bool(self):
        result = _build_client_config_kwargs({"streaming": True})
        assert result["streaming"] is True

    def test_polling_coerced_to_bool(self):
        result = _build_client_config_kwargs({"polling": False})
        assert result["polling"] is False

    def test_use_client_preference_coerced(self):
        result = _build_client_config_kwargs({"use_client_preference": True})
        assert result["use_client_preference"] is True

    def test_accepted_output_modes_sanitized(self):
        result = _build_client_config_kwargs({"accepted_output_modes": ["text", "json"]})
        assert result["accepted_output_modes"] == ["text", "json"]

    def test_extensions_sanitized(self):
        result = _build_client_config_kwargs({"extensions": ["ext1", "  ", "ext2"]})
        assert result["extensions"] == ["ext1", "ext2"]

    def test_supported_transports_sanitized(self):
        result = _build_client_config_kwargs({"supported_transports": ["http"]})
        assert result["supported_transports"] == ["http"]

    def test_push_notification_configs_list_passthrough(self):
        configs = [{"url": "https://example.com"}]
        result = _build_client_config_kwargs({"push_notification_configs": configs})
        assert result["push_notification_configs"] == configs

    def test_push_notification_configs_non_list_excluded(self):
        result = _build_client_config_kwargs({"push_notification_configs": "not-a-list"})
        assert "push_notification_configs" not in result

    def test_unknown_key_not_included(self):
        result = _build_client_config_kwargs({"unknown_key": "value"})
        assert "unknown_key" not in result

    def test_streaming_missing_not_added(self):
        result = _build_client_config_kwargs({"polling": False})
        assert "streaming" not in result


# ---------------------------------------------------------------------------
# 6. _required_text (schema.py)
# ---------------------------------------------------------------------------

class TestRequiredText:
    def test_non_empty_string_returned(self):
        assert _required_text("hello", field_name="name") == "hello"

    def test_strips_whitespace(self):
        assert _required_text("  hello  ", field_name="name") == "hello"

    def test_none_raises_value_error(self):
        with pytest.raises(ValueError):
            _required_text(None, field_name="name")

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            _required_text("", field_name="name")

    def test_whitespace_only_raises_value_error(self):
        with pytest.raises(ValueError):
            _required_text("   ", field_name="name")

    def test_non_string_coerced(self):
        # str(42) = "42"
        result = _required_text(42, field_name="value")
        assert result == "42"


# ---------------------------------------------------------------------------
# 7. _optional_text (schema.py)
# ---------------------------------------------------------------------------

class TestOptionalText:
    def test_none_returns_none(self):
        assert _optional_text(None) is None

    def test_empty_string_returns_none(self):
        assert _optional_text("") is None

    def test_whitespace_only_returns_none(self):
        assert _optional_text("   ") is None

    def test_non_empty_string_returned(self):
        assert _optional_text("hello") == "hello"

    def test_strips_whitespace(self):
        assert _optional_text("  hello  ") == "hello"

    def test_int_coerced(self):
        assert _optional_text(42) == "42"


# ---------------------------------------------------------------------------
# 8. _normalize_string_list (schema.py)
# ---------------------------------------------------------------------------

class TestNormalizeStringListSchema:
    def test_empty_list_returns_empty(self):
        assert _normalize_string_list([]) == []

    def test_duplicates_removed(self):
        result = _normalize_string_list(["a", "b", "a"])
        assert result.count("a") == 1

    def test_whitespace_excluded(self):
        result = _normalize_string_list(["a", "  ", "b"])
        assert "  " not in result

    def test_none_coerced_and_excluded(self):
        result = _normalize_string_list([None, "valid"])  # type: ignore[list-item]
        assert None not in result
        assert "valid" in result

    def test_order_preserved(self):
        result = _normalize_string_list(["c", "a", "b"])
        assert result == ["c", "a", "b"]

    def test_strips_whitespace(self):
        result = _normalize_string_list(["  hello  "])
        assert result == ["hello"]


# ---------------------------------------------------------------------------
# 9. _compose_prompt_sections (projection.py)
# ---------------------------------------------------------------------------

class TestComposePromptSections:
    def test_empty_list_returns_empty(self):
        assert _compose_prompt_sections([]) == ""

    def test_none_sections_returns_empty(self):
        assert _compose_prompt_sections(None) == ""  # type: ignore[arg-type]

    def test_heading_and_content_joined(self):
        sections = [{"heading": "# Title", "content": "Body text"}]
        result = _compose_prompt_sections(sections)
        assert "# Title" in result
        assert "Body text" in result

    def test_content_only_section(self):
        sections = [{"content": "Only content"}]
        result = _compose_prompt_sections(sections)
        assert result == "Only content"

    def test_heading_only_section(self):
        sections = [{"heading": "Only heading"}]
        result = _compose_prompt_sections(sections)
        assert result == "Only heading"

    def test_non_dict_sections_skipped(self):
        sections = ["not a dict", {"content": "valid"}]
        result = _compose_prompt_sections(sections)
        assert result == "valid"

    def test_multiple_sections_joined_with_double_newline(self):
        sections = [
            {"content": "First"},
            {"content": "Second"},
        ]
        result = _compose_prompt_sections(sections)
        assert "First" in result
        assert "Second" in result
        assert "\n\n" in result

    def test_empty_section_skipped(self):
        sections = [{"heading": "", "content": ""}, {"content": "valid"}]
        result = _compose_prompt_sections(sections)
        assert result == "valid"


# ---------------------------------------------------------------------------
# 10. _read_dotted (projection.py)
# ---------------------------------------------------------------------------

class TestReadDotted:
    def test_simple_key(self):
        assert _read_dotted({"key": "value"}, "key") == "value"

    def test_dotted_path(self):
        assert _read_dotted({"a": {"b": "deep"}}, "a.b") == "deep"

    def test_deeply_nested(self):
        assert _read_dotted({"a": {"b": {"c": 42}}}, "a.b.c") == 42

    def test_missing_key_returns_none(self):
        assert _read_dotted({"a": 1}, "b") is None

    def test_missing_nested_key_returns_none(self):
        assert _read_dotted({"a": {"b": 1}}, "a.c") is None

    def test_non_mapping_mid_path_returns_none(self):
        assert _read_dotted({"a": "not_a_dict"}, "a.b") is None

    def test_empty_path_returns_none(self):
        assert _read_dotted({"a": 1}, "") is None

    def test_none_value_returned(self):
        assert _read_dotted({"key": None}, "key") is None


# ---------------------------------------------------------------------------
# 11. _string_list (projection.py)
# ---------------------------------------------------------------------------

class TestStringList:
    def test_non_list_returns_empty(self):
        assert _string_list("not a list") == []

    def test_none_returns_empty(self):
        assert _string_list(None) == []

    def test_empty_list_returns_empty(self):
        assert _string_list([]) == []

    def test_valid_items_returned(self):
        result = _string_list(["a", "b", "c"])
        assert result == ["a", "b", "c"]

    def test_whitespace_items_excluded(self):
        result = _string_list(["a", "  ", "c"])
        assert "  " not in result
        assert "a" in result
        assert "c" in result

    def test_items_stripped(self):
        result = _string_list(["  hello  "])
        assert result == ["hello"]

    def test_non_string_coerced(self):
        result = _string_list([42, 3.14])
        assert "42" in result
        assert "3.14" in result


# ---------------------------------------------------------------------------
# 12. _record_label (projection.py)
# ---------------------------------------------------------------------------

class TestRecordLabel:
    def test_id_and_label(self):
        result = _record_label({"id": "rec-1", "label": "My Record"})
        assert "rec-1" in result
        assert "My Record" in result

    def test_id_only(self):
        result = _record_label({"id": "rec-1"})
        assert "rec-1" in result

    def test_no_fields_returns_record(self):
        assert _record_label({}) == "record"

    def test_key_appended_when_not_in_heading(self):
        result = _record_label({"id": "rec-1", "key": "billing"})
        assert "billing" in result

    def test_name_used_when_no_label(self):
        result = _record_label({"id": "1", "name": "My Name"})
        assert "My Name" in result

    def test_key_used_when_no_id_or_label(self):
        result = _record_label({"key": "my-key"})
        assert "my-key" in result


# ---------------------------------------------------------------------------
# 13. _projection_targets_agent (projection.py)
# ---------------------------------------------------------------------------

class TestProjectionTargetsAgent:
    def test_wildcard_targets_any_agent(self):
        assert _projection_targets_agent({"recipients": ["*"]}, "MyAgent") is True

    def test_all_targets_any_agent(self):
        assert _projection_targets_agent({"recipients": ["all"]}, "MyAgent") is True

    def test_specific_agent_name_true(self):
        assert _projection_targets_agent({"recipients": ["MyAgent"]}, "MyAgent") is True

    def test_different_agent_name_false(self):
        assert _projection_targets_agent({"recipients": ["OtherAgent"]}, "MyAgent") is False

    def test_empty_recipients_false(self):
        assert _projection_targets_agent({"recipients": []}, "MyAgent") is False

    def test_no_recipients_key_false(self):
        assert _projection_targets_agent({}, "MyAgent") is False

    def test_string_recipient_treated_as_single(self):
        assert _projection_targets_agent({"recipients": "MyAgent"}, "MyAgent") is True

    def test_whitespace_stripped_from_recipients(self):
        assert _projection_targets_agent({"recipients": ["  MyAgent  "]}, "MyAgent") is True

    def test_non_list_non_string_recipients_false(self):
        assert _projection_targets_agent({"recipients": 42}, "MyAgent") is False


# ---------------------------------------------------------------------------
# 14. _asset_projections (projection.py)
# ---------------------------------------------------------------------------

class TestAssetProjections:
    def test_projections_list_returned(self):
        asset = {"projections": [{"recipients": ["MyAgent"]}]}
        result = _asset_projections(asset)
        assert len(result) == 1
        assert result[0]["recipients"] == ["MyAgent"]

    def test_single_projection_mapping_wrapped(self):
        asset = {"projection": {"recipients": ["MyAgent"]}}
        result = _asset_projections(asset)
        assert len(result) == 1

    def test_neither_returns_empty(self):
        assert _asset_projections({}) == []

    def test_non_mapping_items_skipped(self):
        asset = {"projections": ["not a dict", {"recipients": ["MyAgent"]}]}
        result = _asset_projections(asset)
        assert len(result) == 1

    def test_empty_projections_list(self):
        assert _asset_projections({"projections": []}) == []

    def test_projection_key_only_if_projections_absent(self):
        # "projections" key present and is None → falls through to "projection"
        asset = {"projections": None, "projection": {"recipients": ["MyAgent"]}}
        result = _asset_projections(asset)
        assert len(result) == 1
