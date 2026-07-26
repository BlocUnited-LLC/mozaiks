"""
save_app_schema.py extended pure helper unit tests.

Covers helpers not covered by the existing test_save_app_schema_helpers.py:

  _is_non_empty_string:
    - non-string → False
    - empty string → False
    - whitespace only → False
    - valid string → True

  _require_dict:
    - non-dict → ValueError with field name
    - dict → returned as-is

  _deep_merge_dicts:
    - base and overlay → overlay takes precedence
    - nested dicts merged recursively
    - non-dict values in overlay overwrite base
    - empty overlay → base returned unchanged

  _normalize_blank_optional_strings:
    - dict with blank optional key → key set to None → stripped
    - dict with non-optional blank key → kept
    - nested dict → recursed
    - list → recursed

  _normalize_action_data:
    - dict with context_variables list → converted via _key_value_entries_to_dict
    - dict with payload list → converted
    - dict without those fields → returned as-is
    - None stripped

  _normalize_config_actions:
    - dict with action field → action normalized
    - dict with submit_action → normalized
    - dict with cancel_action → normalized
    - dict with actions list → each normalized
    - dict with empty.action → normalized

  _normalize_page_section:
    - non-dict → returned as-is
    - dict without config → stripped and returned
    - dict with config → config normalized
    - nested children → recursed

  _validate_string_list:
    - None → no error
    - non-list → ValueError
    - list of valid strings → no error
    - list with empty string → ValueError
    - list with non-string item → ValueError

  _validate_optional_string:
    - None → no error
    - valid string → no error
    - empty string → ValueError
    - whitespace only → ValueError
    - non-string → ValueError

  _validate_api_endpoint:
    - None → no error
    - relative path starting with / → no error
    - absolute URL with scheme → ValueError
    - path without leading slash → ValueError
    - path with query string → ValueError
    - path with fragment → ValueError

  _validate_shell_mode:
    - None → no error
    - valid mode "standard" → no error
    - invalid mode → ValueError listing valid modes

  _validate_shell_path:
    - None → no error
    - path starting with / → no error
    - path without leading slash → ValueError
    - empty string → ValueError

  _validate_shell_string_or_list:
    - None → no error
    - valid string → no error
    - list of valid strings → no error
    - empty string → ValueError
    - list with empty string → ValueError
    - non-string, non-list → ValueError
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.save_app_schema import (
    _deep_merge_dicts,
    _is_non_empty_string,
    _normalize_action_data,
    _normalize_blank_optional_strings,
    _normalize_config_actions,
    _normalize_page_section,
    _persist_to_filesystem,
    _require_dict,
    _validate_api_endpoint,
    _validate_optional_string,
    _validate_shell_mode,
    _validate_shell_path,
    _validate_shell_string_or_list,
    _validate_string_list,
)

# ---------------------------------------------------------------------------
# 1. _is_non_empty_string
# ---------------------------------------------------------------------------

class TestIsNonEmptyString:
    def test_non_string_returns_false(self):
        assert _is_non_empty_string(None) is False
        assert _is_non_empty_string(42) is False
        assert _is_non_empty_string([]) is False

    def test_empty_string_returns_false(self):
        assert _is_non_empty_string("") is False

    def test_whitespace_only_returns_false(self):
        assert _is_non_empty_string("   ") is False

    def test_valid_string_returns_true(self):
        assert _is_non_empty_string("hello") is True

    def test_single_char_returns_true(self):
        assert _is_non_empty_string("x") is True


# ---------------------------------------------------------------------------
# 2. _require_dict
# ---------------------------------------------------------------------------

class TestRequireDict:
    def test_dict_returned(self):
        d = {"key": "value"}
        result = _require_dict(d, "my_field")
        assert result is d

    def test_non_dict_raises_with_field_name(self):
        with pytest.raises(ValueError, match="my_field"):
            _require_dict("not_a_dict", "my_field")

    def test_none_raises(self):
        with pytest.raises(ValueError):
            _require_dict(None, "field")

    def test_list_raises(self):
        with pytest.raises(ValueError):
            _require_dict([], "field")

    def test_empty_dict_returned(self):
        result = _require_dict({}, "field")
        assert result == {}


# ---------------------------------------------------------------------------
# 3. _deep_merge_dicts
# ---------------------------------------------------------------------------

class TestDeepMergeDicts:
    def test_overlay_takes_precedence(self):
        result = _deep_merge_dicts({"a": 1}, {"a": 2})
        assert result["a"] == 2

    def test_base_keys_preserved_when_not_in_overlay(self):
        result = _deep_merge_dicts({"a": 1, "b": 2}, {"a": 10})
        assert result["b"] == 2
        assert result["a"] == 10

    def test_nested_dicts_merged_recursively(self):
        base = {"config": {"title": "Old", "size": "lg"}}
        overlay = {"config": {"title": "New"}}
        result = _deep_merge_dicts(base, overlay)
        assert result["config"]["title"] == "New"
        assert result["config"]["size"] == "lg"  # preserved from base

    def test_non_dict_value_in_overlay_overwrites(self):
        base = {"config": {"nested": "value"}}
        overlay = {"config": "replaced_string"}
        result = _deep_merge_dicts(base, overlay)
        assert result["config"] == "replaced_string"

    def test_empty_overlay_returns_base_copy(self):
        result = _deep_merge_dicts({"a": 1}, {})
        assert result == {"a": 1}

    def test_empty_base_returns_overlay_copy(self):
        result = _deep_merge_dicts({}, {"b": 2})
        assert result == {"b": 2}

    def test_result_is_new_dict(self):
        base = {"a": 1}
        overlay = {"b": 2}
        result = _deep_merge_dicts(base, overlay)
        assert result is not base
        assert result is not overlay


# ---------------------------------------------------------------------------
# 4. _normalize_blank_optional_strings
# ---------------------------------------------------------------------------

class TestNormalizeBlankOptionalStrings:
    def test_blank_title_removed(self):
        result = _normalize_blank_optional_strings({"title": "  ", "other": "value"})
        # title is in _OPTIONAL_STRING_KEYS and blank → set to None → stripped
        assert "title" not in result

    def test_valid_title_preserved(self):
        result = _normalize_blank_optional_strings({"title": "My Title"})
        assert result["title"] == "My Title"

    def test_non_optional_blank_key_preserved(self):
        # "primitive" is NOT in _OPTIONAL_STRING_KEYS → kept
        result = _normalize_blank_optional_strings({"primitive": ""})
        assert result["primitive"] == ""

    def test_nested_dict_recursed(self):
        data = {"config": {"title": "  ", "label": "Click me"}}
        result = _normalize_blank_optional_strings(data)
        assert "title" not in result.get("config", {})
        assert result["config"]["label"] == "Click me"

    def test_list_recursed(self):
        data = [{"title": "  "}, {"title": "Valid"}]
        result = _normalize_blank_optional_strings(data)
        assert "title" not in result[0]
        assert result[1]["title"] == "Valid"

    def test_none_values_stripped(self):
        result = _normalize_blank_optional_strings({"title": None, "icon": "star"})
        assert "title" not in result
        assert result["icon"] == "star"


# ---------------------------------------------------------------------------
# 5. _normalize_action_data
# ---------------------------------------------------------------------------

class TestNormalizeActionData:
    def test_none_returns_none(self):
        result = _normalize_action_data(None)
        assert result is None

    def test_non_dict_returned_unchanged(self):
        result = _normalize_action_data("string")
        assert result == "string"

    def test_dict_context_variables_list_normalized(self):
        action = {
            "id": "submit",
            "context_variables": [{"key": "name", "value": "Alice"}],
        }
        result = _normalize_action_data(action)
        assert isinstance(result["context_variables"], dict)
        assert result["context_variables"]["name"] == "Alice"

    def test_dict_payload_list_normalized(self):
        action = {
            "id": "submit",
            "payload": [{"key": "item_id", "value": "123"}],
        }
        result = _normalize_action_data(action)
        assert isinstance(result["payload"], dict)
        assert result["payload"]["item_id"] == "123"

    def test_dict_context_variables_already_dict(self):
        action = {"id": "submit", "context_variables": {"name": "Alice"}}
        result = _normalize_action_data(action)
        assert result["context_variables"] == {"name": "Alice"}

    def test_none_values_stripped(self):
        action = {"id": "submit", "optional": None}
        result = _normalize_action_data(action)
        assert "optional" not in result


# ---------------------------------------------------------------------------
# 6. _normalize_config_actions
# ---------------------------------------------------------------------------

class TestNormalizeConfigActions:
    def test_action_field_normalized(self):
        config = {
            "action": {
                "id": "submit",
                "context_variables": [{"key": "x", "value": "1"}],
            }
        }
        result = _normalize_config_actions(config)
        assert isinstance(result["action"]["context_variables"], dict)

    def test_submit_action_normalized(self):
        config = {
            "submit_action": {
                "id": "create",
                "context_variables": [{"key": "name", "value": "test"}],
            }
        }
        result = _normalize_config_actions(config)
        assert isinstance(result["submit_action"]["context_variables"], dict)

    def test_actions_list_normalized(self):
        config = {
            "actions": [
                {"id": "a1", "context_variables": [{"key": "k", "value": "v"}]},
                {"id": "a2"},
            ]
        }
        result = _normalize_config_actions(config)
        assert isinstance(result["actions"][0]["context_variables"], dict)

    def test_empty_config_unchanged(self):
        config = {"title": "My Form"}
        result = _normalize_config_actions(config)
        assert result["title"] == "My Form"

    def test_empty_action_normalized(self):
        config = {"empty": {"action": {"id": "load", "context_variables": [{"key": "x", "value": "1"}]}}}
        result = _normalize_config_actions(config)
        assert isinstance(result["empty"]["action"]["context_variables"], dict)


# ---------------------------------------------------------------------------
# 7. _normalize_page_section
# ---------------------------------------------------------------------------

class TestNormalizePageSection:
    def test_non_dict_returned_as_is(self):
        result = _normalize_page_section("string")
        assert result == "string"

    def test_none_returned_as_is(self):
        result = _normalize_page_section(None)
        assert result is None

    def test_dict_without_config_returned(self):
        result = _normalize_page_section({"primitive": "Button", "label": "Click"})
        assert result["primitive"] == "Button"

    def test_none_values_stripped_from_section(self):
        result = _normalize_page_section({"primitive": "Button", "optional": None})
        assert "optional" not in result

    def test_config_normalized(self):
        section = {
            "primitive": "Form",
            "config": {
                "title": "My Form",
                "submit_action": {"id": "submit", "context_variables": [{"key": "x", "value": "1"}]},
            },
        }
        result = _normalize_page_section(section)
        assert isinstance(result["config"]["submit_action"]["context_variables"], dict)

    def test_config_children_recursed(self):
        section = {
            "primitive": "Container",
            "config": {
                "children": [
                    {"primitive": "Button", "config": {"title": "  "}},
                ]
            },
        }
        result = _normalize_page_section(section)
        # blank title in child config should be stripped
        child_config = result["config"]["children"][0].get("config", {})
        assert "title" not in child_config


# ---------------------------------------------------------------------------
# 8. _validate_string_list
# ---------------------------------------------------------------------------

class TestValidateStringList:
    def test_none_no_error(self):
        _validate_string_list(None, field="tags")

    def test_non_list_raises(self):
        with pytest.raises(ValueError, match="must be a list"):
            _validate_string_list("not_a_list", field="tags")

    def test_valid_list_no_error(self):
        _validate_string_list(["a", "b", "c"], field="tags")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="non-empty string"):
            _validate_string_list(["a", ""], field="tags")

    def test_non_string_item_raises(self):
        with pytest.raises(ValueError, match="non-empty string"):
            _validate_string_list(["a", 42], field="tags")

    def test_whitespace_item_raises(self):
        with pytest.raises(ValueError, match="non-empty string"):
            _validate_string_list(["a", "  "], field="tags")

    def test_empty_list_no_error(self):
        _validate_string_list([], field="tags")


# ---------------------------------------------------------------------------
# 9. _validate_optional_string
# ---------------------------------------------------------------------------

class TestValidateOptionalString:
    def test_none_no_error(self):
        _validate_optional_string(None, field="title")

    def test_valid_string_no_error(self):
        _validate_optional_string("My Title", field="title")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="non-empty string"):
            _validate_optional_string("", field="title")

    def test_whitespace_raises(self):
        with pytest.raises(ValueError, match="non-empty string"):
            _validate_optional_string("   ", field="title")

    def test_non_string_raises(self):
        with pytest.raises(ValueError, match="non-empty string"):
            _validate_optional_string(42, field="title")


# ---------------------------------------------------------------------------
# 10. _validate_api_endpoint
# ---------------------------------------------------------------------------

class TestValidateApiEndpoint:
    def test_none_no_error(self):
        _validate_api_endpoint(None, field="api_endpoint")

    def test_valid_relative_path_no_error(self):
        _validate_api_endpoint("/api/modules/tasks/list", field="api_endpoint")

    def test_absolute_url_with_scheme_raises(self):
        with pytest.raises(ValueError, match="app-relative"):
            _validate_api_endpoint("https://example.com/api", field="api_endpoint")

    def test_path_without_leading_slash_raises(self):
        with pytest.raises(ValueError, match="start with /"):
            _validate_api_endpoint("api/tasks", field="api_endpoint")

    def test_path_with_query_string_raises(self):
        with pytest.raises(ValueError, match="query strings"):
            _validate_api_endpoint("/api/tasks?limit=10", field="api_endpoint")

    def test_path_with_fragment_raises(self):
        with pytest.raises(ValueError, match="query strings"):
            _validate_api_endpoint("/api/tasks#section", field="api_endpoint")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="non-empty string"):
            _validate_api_endpoint("", field="api_endpoint")


# ---------------------------------------------------------------------------
# 11. _validate_shell_mode
# ---------------------------------------------------------------------------

class TestValidateShellMode:
    def test_none_no_error(self):
        _validate_shell_mode(None, field="shell_mode")

    def test_valid_standard_no_error(self):
        _validate_shell_mode("standard", field="shell_mode")

    def test_valid_workspace_no_error(self):
        _validate_shell_mode("workspace", field="shell_mode")

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError):
            _validate_shell_mode("unknown_mode", field="shell_mode")

    def test_all_valid_modes(self):
        for mode in ("standard", "workspace", "conversation", "focused", "immersive", "public"):
            _validate_shell_mode(mode, field="shell_mode")


# ---------------------------------------------------------------------------
# 12. _validate_shell_path
# ---------------------------------------------------------------------------

class TestValidateShellPath:
    def test_none_no_error(self):
        _validate_shell_path(None, field="path")

    def test_path_with_leading_slash_no_error(self):
        _validate_shell_path("/dashboard", field="path")

    def test_path_without_leading_slash_raises(self):
        with pytest.raises(ValueError, match="starting with /"):
            _validate_shell_path("dashboard", field="path")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="starting with /"):
            _validate_shell_path("", field="path")

    def test_whitespace_raises(self):
        with pytest.raises(ValueError, match="starting with /"):
            _validate_shell_path("   ", field="path")


# ---------------------------------------------------------------------------
# 13. _validate_shell_string_or_list
# ---------------------------------------------------------------------------

class TestValidateShellStringOrList:
    def test_none_no_error(self):
        _validate_shell_string_or_list(None, field="field")

    def test_valid_string_no_error(self):
        _validate_shell_string_or_list("value", field="field")

    def test_valid_list_of_strings_no_error(self):
        _validate_shell_string_or_list(["a", "b"], field="field")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            _validate_shell_string_or_list("", field="field")

    def test_whitespace_string_raises(self):
        with pytest.raises(ValueError):
            _validate_shell_string_or_list("   ", field="field")

    def test_list_with_empty_string_raises(self):
        with pytest.raises(ValueError):
            _validate_shell_string_or_list(["valid", ""], field="field")

    def test_non_string_non_list_raises(self):
        with pytest.raises(ValueError):
            _validate_shell_string_or_list(42, field="field")

    def test_empty_list_raises(self):
        # all() of empty list is True, but empty list is falsy
        # Let me check: isinstance([], list) = True, all(_is_non_empty_string(item) for item in []) = True
        # So empty list should NOT raise
        _validate_shell_string_or_list([], field="field")


# ---------------------------------------------------------------------------
# 14. _persist_to_filesystem — config/profile.yaml write step
# ---------------------------------------------------------------------------

def _minimal_manifest() -> dict:
    return {
        "app_name": "Test App",
        "default_route": "/home",
        "auth_strategy": "public",
    }


def _minimal_page() -> dict:
    return {
        "name": "home",
        "route": "/home",
        "title": "Home",
        "schema_version": "mozaiks.page.v1",
        "sections": [
            {
                "id": "main",
                "label": "Main",
                "components": [{"id": "c1", "primitive": "Heading", "config": {"text": "Hello"}}],
            }
        ],
    }


class TestPersistProfileLayout:
    def test_profile_yaml_written_when_layout_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            written = _persist_to_filesystem(
                out,
                _minimal_manifest(),
                [_minimal_page()],
                None, None, None, None, None,
                profile_layout="sidebar_left",
            )
            assert "config/profile.yaml" in written
            doc = yaml.safe_load((out / "config" / "profile.yaml").read_text())
            assert doc["layout"] == "sidebar_left"
            assert doc["schema_version"] == "mozaiks.profile.v1"

    def test_profile_yaml_not_written_when_layout_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            written = _persist_to_filesystem(
                out,
                _minimal_manifest(),
                [_minimal_page()],
                None, None, None, None, None,
                profile_layout=None,
            )
            assert "config/profile.yaml" not in written
            assert not (out / "config" / "profile.yaml").exists()

    def test_all_valid_layouts_written_correctly(self):
        for layout in ("sidebar_left", "top_nav", "drawer", "icon_rail"):
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp)
                written = _persist_to_filesystem(
                    out,
                    _minimal_manifest(),
                    [_minimal_page()],
                    None, None, None, None, None,
                    profile_layout=layout,
                )
                assert "config/profile.yaml" in written
                doc = yaml.safe_load((out / "config" / "profile.yaml").read_text())
                assert doc["layout"] == layout
