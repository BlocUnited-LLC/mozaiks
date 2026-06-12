"""
Pure helper unit tests for:
  factory_app/workflows/AppGenerator/tools/save_app_schema.py

Covers helpers not tested in test_save_app_schema_helpers.py:

  _normalize_action_data:
    - non-dict passes through as-is
    - context_variables list → converted to dict
    - payload list → converted to dict
    - None values stripped from action
    - plain dict preserved

  _normalize_config_actions:
    - action field normalized
    - submit_action field normalized
    - cancel_action field normalized
    - actions list items normalized
    - empty.action normalized

  _normalize_blank_optional_strings:
    - blank string for optional key → None (then stripped)
    - non-blank string for optional key → preserved
    - non-optional key with blank string → preserved as-is
    - nested dicts traversed
    - lists traversed
    - non-optional key blank string untouched

  _normalize_page_section:
    - non-dict passes through
    - dict with no config → passes through
    - section with blank optional config field → cleared
    - children recursively normalized

  _normalize_page_schema:
    - non-dict passes through
    - page with sections → each section normalized
    - None values stripped from result

  _normalize_custom_route_bundle:
    - non-dict passes through
    - bundle with route_manifest → entries normalized
    - bundle with page_files → entries normalized
    - None values stripped

  _normalize_shell_config:
    - None stripped
    - dict values preserved
    - non-dict passed through
"""
from __future__ import annotations

from factory_app.workflows.AppGenerator.tools.save_app_schema import (
    _normalize_action_data,
    _normalize_blank_optional_strings,
    _normalize_config_actions,
    _normalize_custom_route_bundle,
    _normalize_page_schema,
    _normalize_page_section,
    _normalize_shell_config,
)

# ---------------------------------------------------------------------------
# 1. _normalize_action_data
# ---------------------------------------------------------------------------

class TestNormalizeActionData:
    def test_non_dict_returned_as_is(self):
        assert _normalize_action_data("submit") == "submit"

    def test_none_returned_as_none(self):
        # _strip_none on None returns None, _to_plain(None) is None
        assert _normalize_action_data(None) is None

    def test_plain_dict_preserved(self):
        action = {"id": "create_item", "action_type": "submit"}
        result = _normalize_action_data(action)
        assert result["id"] == "create_item"
        assert result["action_type"] == "submit"

    def test_none_values_stripped(self):
        action = {"id": "create_item", "label": None}
        result = _normalize_action_data(action)
        assert "label" not in result

    def test_context_variables_list_converted_to_dict(self):
        action = {
            "id": "create_item",
            "context_variables": [{"key": "app_id", "value": "abc"}],
        }
        result = _normalize_action_data(action)
        assert isinstance(result["context_variables"], dict)
        assert result["context_variables"]["app_id"] == "abc"

    def test_payload_list_converted_to_dict(self):
        action = {
            "id": "create_item",
            "payload": [{"key": "name", "value": "test"}],
        }
        result = _normalize_action_data(action)
        assert isinstance(result["payload"], dict)
        assert result["payload"]["name"] == "test"

    def test_context_variables_dict_preserved(self):
        action = {"id": "create", "context_variables": {"key1": "val1"}}
        result = _normalize_action_data(action)
        assert result["context_variables"] == {"key1": "val1"}

    def test_empty_list_context_variables(self):
        action = {"id": "create", "context_variables": []}
        result = _normalize_action_data(action)
        # Empty list → empty dict via _key_value_entries_to_dict
        assert isinstance(result.get("context_variables"), (dict, type(None)))


# ---------------------------------------------------------------------------
# 2. _normalize_config_actions
# ---------------------------------------------------------------------------

class TestNormalizeConfigActions:
    def test_action_field_normalized(self):
        config = {"action": {"id": "submit", "label": None}}
        result = _normalize_config_actions(config)
        assert "label" not in result["action"]

    def test_submit_action_field_normalized(self):
        config = {"submit_action": {"id": "submit", "label": None}}
        result = _normalize_config_actions(config)
        assert "label" not in result["submit_action"]

    def test_cancel_action_field_normalized(self):
        config = {"cancel_action": {"id": "cancel", "label": None}}
        result = _normalize_config_actions(config)
        assert "label" not in result["cancel_action"]

    def test_actions_list_items_normalized(self):
        config = {"actions": [{"id": "a1", "label": None}, {"id": "a2"}]}
        result = _normalize_config_actions(config)
        assert len(result["actions"]) == 2
        # None label stripped from first item
        assert "label" not in result["actions"][0]

    def test_empty_action_in_empty_dict(self):
        config = {"empty": {"action": {"id": "reload", "label": None}}}
        result = _normalize_config_actions(config)
        assert "label" not in result["empty"]["action"]

    def test_no_action_fields_passes_through(self):
        config = {"title": "My Page", "description": "desc"}
        result = _normalize_config_actions(config)
        assert result["title"] == "My Page"

    def test_actions_non_list_not_processed(self):
        config = {"actions": "not-a-list"}
        result = _normalize_config_actions(config)
        assert result["actions"] == "not-a-list"


# ---------------------------------------------------------------------------
# 3. _normalize_blank_optional_strings
# ---------------------------------------------------------------------------

class TestNormalizeBlankOptionalStrings:
    def test_blank_optional_key_cleared(self):
        # "title" is in _OPTIONAL_STRING_KEYS
        result = _normalize_blank_optional_strings({"title": "  "})
        # Blank string → None, then stripped → key absent
        assert "title" not in result

    def test_non_blank_optional_key_preserved(self):
        result = _normalize_blank_optional_strings({"title": "My App"})
        assert result["title"] == "My App"

    def test_non_optional_key_blank_string_preserved(self):
        # "custom_key" is NOT in _OPTIONAL_STRING_KEYS
        result = _normalize_blank_optional_strings({"custom_key": "  "})
        assert result["custom_key"] == "  "

    def test_nested_dict_traversed(self):
        value = {"config": {"title": "  ", "custom": "ok"}}
        result = _normalize_blank_optional_strings(value)
        # Nested "title" should be cleared
        assert "title" not in result["config"]
        assert result["config"]["custom"] == "ok"

    def test_list_traversed(self):
        value = [{"title": "  "}, {"title": "Real"}]
        result = _normalize_blank_optional_strings(value)
        assert "title" not in result[0]
        assert result[1]["title"] == "Real"

    def test_non_dict_non_list_returned_as_is(self):
        assert _normalize_blank_optional_strings(42) == 42
        assert _normalize_blank_optional_strings("text") == "text"

    def test_href_blank_cleared(self):
        result = _normalize_blank_optional_strings({"href": ""})
        assert "href" not in result

    def test_icon_blank_cleared(self):
        result = _normalize_blank_optional_strings({"icon": "   "})
        assert "icon" not in result


# ---------------------------------------------------------------------------
# 4. _normalize_page_section
# ---------------------------------------------------------------------------

class TestNormalizePageSection:
    def test_non_dict_returned_as_is(self):
        assert _normalize_page_section("not-a-dict") == "not-a-dict"

    def test_none_returned_as_none(self):
        # _strip_none(_to_plain(None)) → None
        assert _normalize_page_section(None) is None

    def test_section_without_config_passes_through(self):
        section = {"primitive": "Text", "id": "greeting"}
        result = _normalize_page_section(section)
        assert result["primitive"] == "Text"

    def test_blank_optional_config_field_cleared(self):
        section = {"primitive": "Text", "config": {"title": "  "}}
        result = _normalize_page_section(section)
        assert "title" not in result.get("config", {})

    def test_none_values_stripped_from_section(self):
        section = {"primitive": "Text", "label": None}
        result = _normalize_page_section(section)
        assert "label" not in result

    def test_children_recursively_normalized(self):
        section = {
            "primitive": "Container",
            "config": {
                "children": [
                    {"primitive": "Text", "config": {"title": "  "}}
                ]
            },
        }
        result = _normalize_page_section(section)
        child = result["config"]["children"][0]
        assert "title" not in child.get("config", {})


# ---------------------------------------------------------------------------
# 5. _normalize_page_schema
# ---------------------------------------------------------------------------

class TestNormalizePageSchema:
    def test_non_dict_returned_as_is(self):
        assert _normalize_page_schema("bad") == "bad"

    def test_none_returned_as_none(self):
        assert _normalize_page_schema(None) is None

    def test_page_sections_normalized(self):
        page = {
            "route": "/dashboard",
            "sections": [{"primitive": "Text", "config": {"title": "  "}}],
        }
        result = _normalize_page_schema(page)
        assert result["route"] == "/dashboard"
        # blank title in section config should be cleared
        section = result["sections"][0]
        assert "title" not in section.get("config", {})

    def test_none_values_stripped_from_page(self):
        page = {"route": "/dashboard", "label": None}
        result = _normalize_page_schema(page)
        assert "label" not in result

    def test_no_sections_passes_through(self):
        page = {"route": "/about"}
        result = _normalize_page_schema(page)
        assert result["route"] == "/about"


# ---------------------------------------------------------------------------
# 6. _normalize_custom_route_bundle
# ---------------------------------------------------------------------------

class TestNormalizeCustomRouteBundle:
    def test_non_dict_passed_through(self):
        assert _normalize_custom_route_bundle("not-a-dict") == "not-a-dict"

    def test_none_returned_as_none(self):
        assert _normalize_custom_route_bundle(None) is None

    def test_route_manifest_entries_normalized(self):
        bundle = {
            "route_manifest": [{"path": "/custom", "label": None}]
        }
        result = _normalize_custom_route_bundle(bundle)
        entry = result["route_manifest"][0]
        assert entry["path"] == "/custom"
        assert "label" not in entry

    def test_page_files_entries_normalized(self):
        bundle = {
            "page_files": [{"filename": "CustomPage.jsx", "label": None}]
        }
        result = _normalize_custom_route_bundle(bundle)
        entry = result["page_files"][0]
        assert entry["filename"] == "CustomPage.jsx"
        assert "label" not in entry

    def test_none_values_stripped_from_bundle(self):
        bundle = {"route_manifest": [], "label": None}
        result = _normalize_custom_route_bundle(bundle)
        assert "label" not in result


# ---------------------------------------------------------------------------
# 7. _normalize_shell_config
# ---------------------------------------------------------------------------

class TestNormalizeShellConfig:
    def test_none_returns_none(self):
        assert _normalize_shell_config(None) is None

    def test_dict_with_none_value_stripped(self):
        result = _normalize_shell_config({"key": "value", "empty": None})
        assert result["key"] == "value"
        assert "empty" not in result

    def test_plain_dict_preserved(self):
        config = {"nav_type": "sidebar", "brand": "MyApp"}
        result = _normalize_shell_config(config)
        assert result == {"nav_type": "sidebar", "brand": "MyApp"}

    def test_nested_none_stripped(self):
        config = {"nav": {"type": "sidebar", "icon": None}}
        result = _normalize_shell_config(config)
        assert "icon" not in result["nav"]

    def test_non_dict_passed_through(self):
        assert _normalize_shell_config("raw_string") == "raw_string"
