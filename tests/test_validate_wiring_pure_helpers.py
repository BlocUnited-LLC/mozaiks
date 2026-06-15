"""
Pure helper unit tests for:
  factory_app/workflows/AppGenerator/tools/validate_wiring.py

Covers:
  _context_get:
    - None context → None
    - dict context → value returned
    - dict context missing key → None
    - object with .get() method → value returned
    - object with .data dict → value returned
    - object with broken .get() and .data dict → falls back to .data
    - plain dict (no .get() method) → value returned

  _generated_files_from_context:
    - no generated_files key → {}
    - generated_files not a dict → {}
    - backslashes normalized to forward slashes
    - leading slash paths rejected
    - traversal paths with ".." rejected
    - empty path keys rejected
    - valid paths preserved with string content

  _pages_from_generated_files:
    - non-ui/pages path skipped
    - non-.yaml path skipped
    - valid YAML dict page → appended
    - invalid YAML → skipped
    - non-dict parsed YAML → skipped

  _extract_endpoint_refs:
    - empty pages → []
    - page with no sections → []
    - section with api_endpoint → triple extracted
    - section with href starting /api/ → triple extracted
    - nested children endpoint → extracted
    - submit_action endpoint → extracted
    - actions list endpoint → extracted
    - non-dict section skipped

  _endpoint_to_action_key:
    - absolute URL → (None, error)
    - path with query string → (None, error)
    - path with fragment → (None, error)
    - /api/modules/orders/list_orders → ("orders/list_orders", None)
    - /api/modules with wrong parts count → (None, error)
    - non-module api path → path returned as key
    - bare action path → path lstripped returned

  _actions_from_capability_packs:
    - empty list → empty set
    - pack with module_id and string actions → both "module/action" and "action" in set
    - pack with id field and string actions → uses id as module
    - pack with dict actions having id field → action id extracted
    - pack with dict actions having name field → action name extracted
    - pack without module_id skipped
    - non-dict pack skipped
    - non-list action_list skipped
    - action_ids and action_names also accepted
    - empty action string skipped

  _actions_from_generated_module_files:
    - non-module path skipped
    - non-module.yaml path skipped
    - module with id and actions → both "module/action" and "action" in set
    - module with nested "module" block → id extracted from block
    - invalid YAML skipped
    - non-dict parsed YAML skipped
    - empty module id → skipped
"""
from __future__ import annotations

from typing import Any

import yaml

from factory_app.workflows.AppGenerator.tools.validate_wiring import (
    _actions_from_capability_packs,
    _actions_from_generated_module_files,
    _context_get,
    _endpoint_to_action_key,
    _extract_endpoint_refs,
    _generated_files_from_context,
    _pages_from_generated_files,
)

# ---------------------------------------------------------------------------
# 1. _context_get
# ---------------------------------------------------------------------------

class TestContextGet:
    def test_none_context_returns_none(self):
        assert _context_get(None, "app_id") is None

    def test_dict_context_returns_value(self):
        assert _context_get({"app_id": "test"}, "app_id") == "test"

    def test_dict_context_missing_key_returns_none(self):
        assert _context_get({}, "missing") is None

    def test_object_with_get_method_used(self):
        class FakeCtx:
            def get(self, key: str) -> Any:
                if key == "app_id":
                    return "obj-id"
                return None

        assert _context_get(FakeCtx(), "app_id") == "obj-id"

    def test_object_with_get_returning_none_falls_back(self):
        class FakeCtx:
            data = {"app_id": "data-id"}

            def get(self, key: str) -> Any:
                return None

        # .get() returns None → falls back to .data
        assert _context_get(FakeCtx(), "app_id") == "data-id"

    def test_object_with_data_dict_used(self):
        class FakeCtx:
            data = {"app_id": "data-abc"}

        assert _context_get(FakeCtx(), "app_id") == "data-abc"

    def test_object_with_data_dict_missing_key_returns_none(self):
        class FakeCtx:
            data: dict = {}

        assert _context_get(FakeCtx(), "missing") is None

    def test_non_none_value_from_get_returned_directly(self):
        ctx = {"key": "value", "other": "x"}
        assert _context_get(ctx, "key") == "value"


# ---------------------------------------------------------------------------
# 2. _generated_files_from_context
# ---------------------------------------------------------------------------

class TestGeneratedFilesFromContext:
    def test_no_generated_files_key_returns_empty(self):
        assert _generated_files_from_context({}) == {}

    def test_none_context_returns_empty(self):
        assert _generated_files_from_context(None) == {}

    def test_generated_files_not_dict_returns_empty(self):
        assert _generated_files_from_context({"generated_files": "not-a-dict"}) == {}

    def test_valid_path_preserved(self):
        ctx = {"generated_files": {"ui/pages/home.yaml": "route: /home"}}
        result = _generated_files_from_context(ctx)
        assert "ui/pages/home.yaml" in result

    def test_backslashes_normalized(self):
        ctx = {"generated_files": {"ui\\pages\\home.yaml": "route: /home"}}
        result = _generated_files_from_context(ctx)
        assert "ui/pages/home.yaml" in result

    def test_leading_slash_path_rejected(self):
        ctx = {"generated_files": {"/absolute/path.yaml": "content"}}
        result = _generated_files_from_context(ctx)
        assert result == {}

    def test_traversal_path_rejected(self):
        ctx = {"generated_files": {"../../etc/passwd": "content"}}
        result = _generated_files_from_context(ctx)
        assert result == {}

    def test_empty_path_rejected(self):
        ctx = {"generated_files": {"   ": "content"}}
        result = _generated_files_from_context(ctx)
        assert result == {}

    def test_non_string_path_key_skipped(self):
        ctx = {"generated_files": {42: "content"}}
        result = _generated_files_from_context(ctx)
        assert result == {}

    def test_content_coerced_to_string(self):
        ctx = {"generated_files": {"ui/pages/home.yaml": 123}}
        result = _generated_files_from_context(ctx)
        assert result["ui/pages/home.yaml"] == "123"


# ---------------------------------------------------------------------------
# 3. _pages_from_generated_files
# ---------------------------------------------------------------------------

class TestPagesFromGeneratedFiles:
    def test_non_ui_pages_path_skipped(self):
        files = {"modules/orders/module.yaml": "id: orders"}
        assert _pages_from_generated_files(files) == []

    def test_non_yaml_extension_skipped(self):
        files = {"ui/pages/home.jsx": "export default function Home() {}"}
        assert _pages_from_generated_files(files) == []

    def test_valid_yaml_page_appended(self):
        page_yaml = "name: home\nroute: /home\nsections: []"
        files = {"ui/pages/home.yaml": page_yaml}
        result = _pages_from_generated_files(files)
        assert len(result) == 1
        assert result[0]["name"] == "home"

    def test_invalid_yaml_skipped(self):
        files = {"ui/pages/home.yaml": "{invalid yaml ["}
        result = _pages_from_generated_files(files)
        assert result == []

    def test_non_dict_yaml_skipped(self):
        files = {"ui/pages/home.yaml": "- item1\n- item2"}
        result = _pages_from_generated_files(files)
        assert result == []

    def test_yml_extension_accepted(self):
        files = {"ui/pages/home.yml": "name: home\nroute: /home"}
        result = _pages_from_generated_files(files)
        assert len(result) == 1

    def test_multiple_pages_returned(self):
        files = {
            "ui/pages/a.yaml": "name: a\nroute: /a",
            "ui/pages/b.yaml": "name: b\nroute: /b",
        }
        result = _pages_from_generated_files(files)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# 4. _extract_endpoint_refs
# ---------------------------------------------------------------------------

class TestExtractEndpointRefs:
    def test_empty_pages_returns_empty(self):
        assert _extract_endpoint_refs([]) == []

    def test_none_pages_returns_empty(self):
        assert _extract_endpoint_refs(None) == []

    def test_page_with_no_sections_returns_empty(self):
        assert _extract_endpoint_refs([{"name": "home", "sections": []}]) == []

    def test_section_with_api_endpoint_extracted(self):
        pages = [
            {
                "name": "orders",
                "sections": [
                    {"id": "list", "config": {"api_endpoint": "/api/modules/orders/list"}},
                ],
            }
        ]
        results = _extract_endpoint_refs(pages)
        assert len(results) == 1
        assert results[0] == ("orders", "list", "/api/modules/orders/list")

    def test_section_with_href_starting_api_extracted(self):
        pages = [
            {
                "name": "home",
                "sections": [{"id": "link", "config": {"href": "/api/modules/nav/go"}}],
            }
        ]
        results = _extract_endpoint_refs(pages)
        assert len(results) == 1
        assert results[0][2] == "/api/modules/nav/go"

    def test_non_api_href_not_extracted(self):
        pages = [
            {
                "name": "home",
                "sections": [{"id": "link", "config": {"href": "/dashboard"}}],
            }
        ]
        assert _extract_endpoint_refs(pages) == []

    def test_non_dict_section_skipped(self):
        pages = [{"name": "home", "sections": ["not-a-dict"]}]
        assert _extract_endpoint_refs(pages) == []

    def test_submit_action_endpoint_extracted(self):
        pages = [
            {
                "name": "form",
                "sections": [
                    {
                        "id": "form",
                        "config": {
                            "submit_action": {"api_endpoint": "/api/modules/orders/create"}
                        },
                    }
                ],
            }
        ]
        results = _extract_endpoint_refs(pages)
        assert any(r[2] == "/api/modules/orders/create" for r in results)

    def test_actions_list_endpoint_extracted(self):
        pages = [
            {
                "name": "list",
                "sections": [
                    {
                        "id": "actions",
                        "config": {
                            "actions": [{"id": "delete", "api_endpoint": "/api/modules/orders/delete"}]
                        },
                    }
                ],
            }
        ]
        results = _extract_endpoint_refs(pages)
        assert any(r[2] == "/api/modules/orders/delete" for r in results)


# ---------------------------------------------------------------------------
# 5. _endpoint_to_action_key
# ---------------------------------------------------------------------------

class TestEndpointToActionKey:
    def test_absolute_url_returns_error(self):
        key, error = _endpoint_to_action_key("https://api.example.com/orders")
        assert key is None
        assert error is not None

    def test_query_string_returns_error(self):
        key, error = _endpoint_to_action_key("/api/modules/orders/list?page=1")
        assert key is None
        assert error is not None

    def test_fragment_returns_error(self):
        key, error = _endpoint_to_action_key("/api/modules/orders/list#top")
        assert key is None
        assert error is not None

    def test_valid_module_endpoint_returns_key(self):
        key, error = _endpoint_to_action_key("/api/modules/orders/list_orders")
        assert key == "orders/list_orders"
        assert error is None

    def test_platform_account_usage_endpoint_returns_no_action_key(self):
        key, error = _endpoint_to_action_key("/api/me/usage")
        assert key is None
        assert error is None

    def test_module_endpoint_too_many_parts(self):
        key, error = _endpoint_to_action_key("/api/modules/orders/sub/list")
        assert key is None
        assert error is not None

    def test_module_endpoint_empty_module_id(self):
        key, error = _endpoint_to_action_key("/api/modules//list_orders")
        assert key is None
        assert error is not None

    def test_non_module_api_path_returned(self):
        key, error = _endpoint_to_action_key("/api/me/profile")
        assert key == "api/me/profile"
        assert error is None

    def test_leading_slash_stripped_from_non_module_path(self):
        key, error = _endpoint_to_action_key("/dashboard")
        assert not key.startswith("/")
        assert error is None

    def test_whitespace_stripped_from_endpoint(self):
        key, error = _endpoint_to_action_key("  /api/modules/orders/list  ")
        assert key == "orders/list"
        assert error is None


# ---------------------------------------------------------------------------
# 6. _actions_from_capability_packs
# ---------------------------------------------------------------------------

class TestActionsFromCapabilityPacks:
    def test_empty_list_returns_empty_set(self):
        assert _actions_from_capability_packs([]) == set()

    def test_none_returns_empty_set(self):
        assert _actions_from_capability_packs(None) == set()

    def test_string_actions_with_module_id(self):
        packs = [{"module_id": "orders", "actions": ["list_orders", "create_order"]}]
        result = _actions_from_capability_packs(packs)
        assert "orders/list_orders" in result
        assert "list_orders" in result
        assert "orders/create_order" in result

    def test_id_field_used_as_module(self):
        packs = [{"id": "billing", "actions": ["get_invoice"]}]
        result = _actions_from_capability_packs(packs)
        assert "billing/get_invoice" in result

    def test_dict_actions_with_id_field(self):
        packs = [{"module_id": "orders", "actions": [{"id": "list_orders"}]}]
        result = _actions_from_capability_packs(packs)
        assert "orders/list_orders" in result

    def test_dict_actions_with_name_field(self):
        packs = [{"module_id": "orders", "actions": [{"name": "list_orders"}]}]
        result = _actions_from_capability_packs(packs)
        assert "orders/list_orders" in result

    def test_pack_without_module_id_skipped(self):
        packs = [{"actions": ["list_orders"]}]
        result = _actions_from_capability_packs(packs)
        assert result == set()

    def test_non_dict_pack_skipped(self):
        packs = ["not-a-dict"]
        result = _actions_from_capability_packs(packs)
        assert result == set()

    def test_non_list_action_list_skipped(self):
        packs = [{"module_id": "orders", "actions": "not-a-list"}]
        result = _actions_from_capability_packs(packs)
        assert result == set()

    def test_action_ids_field_accepted(self):
        packs = [{"module_id": "orders", "action_ids": ["list_orders"]}]
        result = _actions_from_capability_packs(packs)
        assert "orders/list_orders" in result

    def test_action_names_field_accepted(self):
        packs = [{"module_id": "orders", "action_names": ["list_orders"]}]
        result = _actions_from_capability_packs(packs)
        assert "orders/list_orders" in result

    def test_empty_action_string_skipped(self):
        packs = [{"module_id": "orders", "actions": ["", "  "]}]
        result = _actions_from_capability_packs(packs)
        assert result == set()


# ---------------------------------------------------------------------------
# 7. _actions_from_generated_module_files
# ---------------------------------------------------------------------------

class TestActionsFromGeneratedModuleFiles:
    def test_non_module_path_skipped(self):
        files = {"ui/pages/home.yaml": "name: home"}
        result = _actions_from_generated_module_files(files)
        assert result == set()

    def test_non_module_yaml_name_skipped(self):
        files = {"modules/orders/handler.py": "def list_orders(): pass"}
        result = _actions_from_generated_module_files(files)
        assert result == set()

    def test_valid_module_yaml_returns_actions(self):
        module_yaml = yaml.dump({"id": "orders", "actions": [{"id": "list_orders"}]})
        files = {"modules/orders/module.yaml": module_yaml}
        result = _actions_from_generated_module_files(files)
        assert "orders/list_orders" in result
        assert "list_orders" in result

    def test_bare_action_string_in_actions(self):
        module_yaml = yaml.dump({"id": "orders", "actions": ["list_orders", "create_order"]})
        files = {"modules/orders/module.yaml": module_yaml}
        result = _actions_from_generated_module_files(files)
        assert "orders/list_orders" in result
        assert "orders/create_order" in result

    def test_invalid_yaml_skipped(self):
        files = {"modules/orders/module.yaml": "{invalid [yaml"}
        result = _actions_from_generated_module_files(files)
        assert result == set()

    def test_non_dict_yaml_skipped(self):
        files = {"modules/orders/module.yaml": "- item1\n- item2"}
        result = _actions_from_generated_module_files(files)
        assert result == set()

    def test_empty_module_id_falls_back_to_folder_name(self):
        # When declared id is empty, folder name "orders" from path is used
        module_yaml = yaml.dump({"id": "", "actions": [{"id": "list_orders"}]})
        files = {"modules/orders/module.yaml": module_yaml}
        result = _actions_from_generated_module_files(files)
        assert "orders/list_orders" in result

    def test_nested_module_block_id_extracted(self):
        # id is in nested "module" block; actions must still be at top level
        # (code reads data.get("actions") for the action list)
        module_yaml = yaml.dump({
            "module": {"id": "orders"},
            "actions": [{"id": "list_orders"}],
        })
        files = {"modules/orders/module.yaml": module_yaml}
        result = _actions_from_generated_module_files(files)
        assert "orders/list_orders" in result
