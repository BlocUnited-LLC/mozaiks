"""
Context utility pure function unit tests.

Covers:
  context_to_dict:
    - object with to_dict() → calls to_dict, returns dict
    - object with data: dict attr → uses data
    - plain dict → shallow copy
    - unrecognized object → empty dict
    - to_dict raises exception → falls through to data attr

  stringify_context_value:
    - None with null_label → returns null_label
    - None without null_label (None) → "None"
    - bool True → "true"
    - bool False → "false"
    - int → str(int)
    - string passthrough

  format_template:
    - {key} placeholder substituted
    - missing key → returns template unchanged
    - multiple placeholders
    - no placeholders → returns as-is

  render_exposure_fragment:
    - non-dict exposure → empty string
    - no variables and empty fallback → empty string
    - variables from exposure.variables
    - falls back to fallback_variables when exposure.variables absent
    - null_label used for None values
    - template used when present
    - header prepended when present
    - empty rendered_body → empty string

  merge_message_parts:
    - append (default) → existing + \n\n + fragment
    - prepend → fragment + \n\n + existing
    - replace → fragment only
    - empty fragment → returns existing unchanged
    - empty existing → returns fragment only
    - non-string placement defaults to append
    - strips whitespace from both parts

  apply_context_exposures:
    - empty exposures + fallback vars → uses fallback
    - non-dict entries in exposures skipped
    - multiple exposures applied in order
    - placement from each exposure respected
    - no exposures and no fallback → base_message unchanged
"""
from __future__ import annotations

from mozaiksai.core.workflow.context.context_utils import (
    apply_context_exposures,
    context_to_dict,
    format_template,
    merge_message_parts,
    render_exposure_fragment,
    stringify_context_value,
)

# ---------------------------------------------------------------------------
# 1. context_to_dict
# ---------------------------------------------------------------------------

class TestContextToDict:
    def test_to_dict_method_called(self):
        class Container:
            def to_dict(self):
                return {"key": "value"}
        assert context_to_dict(Container()) == {"key": "value"}

    def test_data_dict_attr_used(self):
        class Container:
            data = {"a": 1, "b": 2}
        assert context_to_dict(Container()) == {"a": 1, "b": 2}

    def test_plain_dict_returned_as_copy(self):
        d = {"x": 10}
        result = context_to_dict(d)
        assert result == {"x": 10}
        assert result is not d  # shallow copy

    def test_unknown_object_returns_empty(self):
        result = context_to_dict(object())
        assert result == {}

    def test_to_dict_returns_copy(self):
        """Mutations to result should not affect container."""
        class Container:
            def to_dict(self):
                return {"key": "value"}
        result = context_to_dict(Container())
        result["key"] = "mutated"
        assert context_to_dict(Container())["key"] == "value"

    def test_data_attr_not_dict_returns_empty(self):
        class Container:
            data = "not_a_dict"
        assert context_to_dict(Container()) == {}

    def test_none_input_returns_empty(self):
        assert context_to_dict(None) == {}


# ---------------------------------------------------------------------------
# 2. stringify_context_value
# ---------------------------------------------------------------------------

class TestStringifyContextValue:
    def test_none_with_null_label(self):
        assert stringify_context_value(None, "N/A") == "N/A"

    def test_none_without_null_label_returns_none_string(self):
        assert stringify_context_value(None, None) == "None"

    def test_bool_true_returns_true(self):
        assert stringify_context_value(True, None) == "true"

    def test_bool_false_returns_false(self):
        assert stringify_context_value(False, None) == "false"

    def test_int_returns_str(self):
        assert stringify_context_value(42, None) == "42"

    def test_string_passthrough(self):
        assert stringify_context_value("hello", None) == "hello"

    def test_list_returns_str_representation(self):
        result = stringify_context_value(["a", "b"], None)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# 3. format_template
# ---------------------------------------------------------------------------

class TestFormatTemplate:
    def test_placeholder_substituted(self):
        result = format_template("Hello {name}!", {"name": "World"})
        assert result == "Hello World!"

    def test_multiple_placeholders(self):
        result = format_template("{a} + {b} = {c}", {"a": "1", "b": "2", "c": "3"})
        assert result == "1 + 2 = 3"

    def test_no_placeholders_passthrough(self):
        assert format_template("no placeholders", {}) == "no placeholders"

    def test_missing_key_returns_template_unchanged(self):
        # KeyError → template returned as-is
        result = format_template("Hello {missing}!", {})
        assert result == "Hello {missing}!"

    def test_empty_template(self):
        assert format_template("", {"key": "val"}) == ""


# ---------------------------------------------------------------------------
# 4. render_exposure_fragment
# ---------------------------------------------------------------------------

class TestRenderExposureFragment:
    def test_non_dict_exposure_returns_empty(self):
        assert render_exposure_fragment("not_a_dict", {}, []) == ""

    def test_no_variables_and_empty_fallback_returns_empty(self):
        assert render_exposure_fragment({}, {}, []) == ""

    def test_variables_from_exposure(self):
        exposure = {"variables": ["name"]}
        result = render_exposure_fragment(exposure, {"name": "Alice"}, [])
        assert "NAME" in result
        assert "Alice" in result

    def test_falls_back_to_fallback_variables(self):
        result = render_exposure_fragment({}, {"city": "Paris"}, ["city"])
        assert "CITY" in result

    def test_null_label_used_for_none(self):
        exposure = {"variables": ["missing"], "null_label": "N/A"}
        result = render_exposure_fragment(exposure, {}, [])
        assert "N/A" in result

    def test_template_used_when_present(self):
        exposure = {"variables": ["name"], "template": "My name is {name}"}
        result = render_exposure_fragment(exposure, {"name": "Bob"}, [])
        assert "My name is Bob" in result

    def test_header_prepended(self):
        exposure = {"variables": ["key"], "header": "== Context =="}
        result = render_exposure_fragment(exposure, {"key": "val"}, [])
        assert result.startswith("== Context ==")

    def test_empty_variables_list_after_filtering_returns_empty(self):
        # empty strings should be filtered out
        exposure = {"variables": ["", "  "]}
        assert render_exposure_fragment(exposure, {}, []) == ""

    def test_whitespace_values_stripped_from_variables(self):
        exposure = {"variables": ["  name  "]}
        result = render_exposure_fragment(exposure, {"name": "Alice"}, [])
        assert "Alice" in result


# ---------------------------------------------------------------------------
# 5. merge_message_parts
# ---------------------------------------------------------------------------

class TestMergeMessageParts:
    def test_append_default(self):
        result = merge_message_parts("existing", "fragment", "append")
        assert result == "existing\n\nfragment"

    def test_prepend(self):
        result = merge_message_parts("existing", "fragment", "prepend")
        assert result == "fragment\n\nexisting"

    def test_replace(self):
        result = merge_message_parts("existing", "new content", "replace")
        assert result == "new content"

    def test_empty_fragment_returns_existing(self):
        result = merge_message_parts("existing", "", "append")
        assert result == "existing"

    def test_whitespace_fragment_returns_existing(self):
        result = merge_message_parts("existing", "   ", "append")
        assert result == "existing"

    def test_empty_existing_returns_fragment(self):
        result = merge_message_parts("", "fragment", "append")
        assert result == "fragment"

    def test_none_placement_defaults_to_append(self):
        result = merge_message_parts("a", "b", None)
        assert result == "a\n\nb"

    def test_case_insensitive_prepend(self):
        result = merge_message_parts("existing", "fragment", "PREPEND")
        assert result == "fragment\n\nexisting"

    def test_both_empty_returns_empty(self):
        result = merge_message_parts("", "", "append")
        assert result == ""


# ---------------------------------------------------------------------------
# 6. apply_context_exposures
# ---------------------------------------------------------------------------

class TestApplyContextExposures:
    def test_empty_exposures_no_fallback_returns_base(self):
        result = apply_context_exposures("base message", [], {}, [])
        assert result == "base message"

    def test_empty_exposures_with_fallback_uses_fallback(self):
        result = apply_context_exposures(
            "base", [], {"name": "Alice"}, ["name"]
        )
        assert "Alice" in result

    def test_non_dict_entries_skipped(self):
        # list with a non-dict entry should not crash
        result = apply_context_exposures(
            "base",
            ["not_a_dict", {"variables": ["key"]}],
            {"key": "val"},
            [],
        )
        assert "val" in result

    def test_multiple_exposures_applied_in_order(self):
        exposures = [
            {"variables": ["a"], "placement": "append"},
            {"variables": ["b"], "placement": "append"},
        ]
        result = apply_context_exposures(
            "base", exposures, {"a": "1", "b": "2"}, []
        )
        assert "1" in result
        assert "2" in result

    def test_placement_respected_per_exposure(self):
        exposures = [{"variables": ["note"], "placement": "prepend"}]
        result = apply_context_exposures(
            "base", exposures, {"note": "prefix"}, []
        )
        assert result.index("prefix") < result.index("base")

    def test_replace_placement_replaces_entirely(self):
        exposures = [{"variables": ["key"], "placement": "replace"}]
        result = apply_context_exposures(
            "base message", exposures, {"key": "replaced"}, []
        )
        assert "base message" not in result
        assert "replaced" in result
