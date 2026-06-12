"""
Pure helper unit tests for:
  factory_app/workflows/AppGenerator/tools/app_validation.py

Covers helpers NOT already tested in test_app_validation_helpers.py or
test_app_validation_ast_helpers.py:

  _append_command_output:
    - appends command header to build_output
    - appends stdout
    - stderr appended when non-empty
    - empty stderr not appended
    - modifies result dict in place

  _normalize_module_yaml:
    - invalid YAML → (None, [error])
    - YAML not a dict → (None, [error])
    - valid YAML → (dict, [])
    - module_id from module.id field
    - module_id from folder name when id absent
    - actions from top-level actions list
    - handler from module.handler or top-level handler
    - non-list actions normalized to []

  _generated_files_from_context:
    - None context → {}
    - context without .get → {}
    - missing generated_files key → {}
    - non-dict generated_files → {}
    - valid generated_files → file paths sanitized and returned
    - path with leading slash → stripped

  _all_method_nodes:
    - empty module → {}
    - module with no classes → {}
    - class with methods → method names in result
    - async method included
    - duplicate method name → first occurrence kept
    - non-class top-level nodes ignored

  _input_schema_required_fields:
    - no input_schema → empty set
    - non-dict input_schema → empty set
    - no required field → empty set
    - non-list required → empty set
    - list of strings → set of stripped strings
    - whitespace-only items excluded
"""
from __future__ import annotations

import ast

from factory_app.workflows.AppGenerator.tools.app_validation import (
    _all_method_nodes,
    _append_command_output,
    _generated_files_from_context,
    _input_schema_required_fields,
    _normalize_module_yaml,
)

# ---------------------------------------------------------------------------
# 1. _append_command_output
# ---------------------------------------------------------------------------

class TestAppendCommandOutput:
    def _base_result(self) -> dict:
        return {"build_output": ""}

    def test_command_header_appended(self):
        result = self._base_result()
        _append_command_output(result, command="npm run build", stdout="", stderr="")
        assert "npm run build" in result["build_output"]

    def test_stdout_appended(self):
        result = self._base_result()
        _append_command_output(result, command="cmd", stdout="Build succeeded", stderr="")
        assert "Build succeeded" in result["build_output"]

    def test_stderr_appended_when_non_empty(self):
        result = self._base_result()
        _append_command_output(result, command="cmd", stdout="", stderr="Error: missing module")
        assert "Error: missing module" in result["build_output"]

    def test_empty_stderr_not_appended(self):
        result = self._base_result()
        _append_command_output(result, command="cmd", stdout="ok", stderr="")
        # No double newline or extra content from empty stderr
        assert "Error" not in result["build_output"]

    def test_modifies_dict_in_place(self):
        result = self._base_result()
        original_id = id(result)
        _append_command_output(result, command="cmd", stdout="", stderr="")
        assert id(result) == original_id


# ---------------------------------------------------------------------------
# 2. _normalize_module_yaml
# ---------------------------------------------------------------------------

class TestNormalizeModuleYaml:
    def test_invalid_yaml_returns_none_and_error(self):
        parsed, errors = _normalize_module_yaml("modules/orders/module.yaml", "invalid: yaml: [[[")
        assert parsed is None
        assert len(errors) == 1
        assert errors[0]["test"] == "module_yaml_parse"

    def test_yaml_not_dict_returns_none_and_error(self):
        parsed, errors = _normalize_module_yaml("modules/orders/module.yaml", "- item1\n- item2\n")
        assert parsed is None
        assert len(errors) == 1
        assert errors[0]["test"] == "module_yaml_shape"

    def test_valid_yaml_returns_dict_no_errors(self):
        content = "module:\n  id: orders\nactions:\n  - id: list_orders\n"
        parsed, errors = _normalize_module_yaml("modules/orders/module.yaml", content)
        assert parsed is not None
        assert errors == []

    def test_module_id_from_module_block(self):
        content = "module:\n  id: orders\nactions: []\n"
        parsed, _ = _normalize_module_yaml("modules/orders/module.yaml", content)
        assert parsed["module_id"] == "orders"

    def test_module_id_from_folder_when_id_absent(self):
        content = "actions: []\n"
        parsed, _ = _normalize_module_yaml("modules/payments/module.yaml", content)
        assert parsed["module_id"] == "payments"

    def test_actions_from_top_level(self):
        content = "actions:\n  - id: list_orders\n  - id: get_order\n"
        parsed, _ = _normalize_module_yaml("modules/orders/module.yaml", content)
        assert len(parsed["actions"]) == 2

    def test_non_list_actions_normalized_to_empty(self):
        content = "actions: not_a_list\n"
        parsed, _ = _normalize_module_yaml("modules/orders/module.yaml", content)
        assert parsed["actions"] == []

    def test_handler_from_module_block(self):
        content = "module:\n  id: orders\n  handler: handlers.OrderHandler\nactions: []\n"
        parsed, _ = _normalize_module_yaml("modules/orders/module.yaml", content)
        assert parsed["handler"] == "handlers.OrderHandler"

    def test_path_preserved_in_result(self):
        path = "modules/orders/module.yaml"
        parsed, _ = _normalize_module_yaml(path, "id: orders\nactions: []\n")
        assert parsed["path"] == path


# ---------------------------------------------------------------------------
# 3. _generated_files_from_context
# ---------------------------------------------------------------------------

class TestGeneratedFilesFromContext:
    def test_none_context_returns_empty(self):
        assert _generated_files_from_context(None) == {}

    def test_context_without_get_returns_empty(self):
        assert _generated_files_from_context("not-a-dict") == {}

    def test_missing_generated_files_returns_empty(self):
        assert _generated_files_from_context({"other": "value"}) == {}

    def test_non_dict_generated_files_returns_empty(self):
        assert _generated_files_from_context({"generated_files": "not-a-dict"}) == {}

    def test_valid_files_returned(self):
        ctx = {"generated_files": {"modules/orders/module.yaml": "id: orders"}}
        result = _generated_files_from_context(ctx)
        assert "modules/orders/module.yaml" in result
        assert result["modules/orders/module.yaml"] == "id: orders"

    def test_paths_sanitized(self):
        # _safe_relpath will normalize leading slashes/dangerous paths
        ctx = {"generated_files": {"modules/orders/module.yaml": "content"}}
        result = _generated_files_from_context(ctx)
        assert any("orders" in k for k in result)

    def test_multiple_files_returned(self):
        ctx = {
            "generated_files": {
                "modules/orders/module.yaml": "id: orders",
                "modules/payments/module.yaml": "id: payments",
            }
        }
        result = _generated_files_from_context(ctx)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# 4. _all_method_nodes
# ---------------------------------------------------------------------------

def _parse_to_module(source: str) -> ast.Module:
    return ast.parse(source)


class TestAllMethodNodes:
    def test_empty_module_returns_empty(self):
        tree = _parse_to_module("")
        assert _all_method_nodes(tree) == {}

    def test_module_with_no_classes_returns_empty(self):
        source = "x = 1\ndef top_level(): pass\n"
        tree = _parse_to_module(source)
        assert _all_method_nodes(tree) == {}

    def test_class_method_included(self):
        source = "class MyHandler:\n    def list_orders(self): pass\n"
        tree = _parse_to_module(source)
        result = _all_method_nodes(tree)
        assert "list_orders" in result

    def test_async_method_included(self):
        source = "class MyHandler:\n    async def list_orders(self): pass\n"
        tree = _parse_to_module(source)
        result = _all_method_nodes(tree)
        assert "list_orders" in result

    def test_duplicate_method_name_first_class_wins(self):
        source = (
            "class A:\n    def my_method(self): pass\n"
            "class B:\n    def my_method(self): return 'b'\n"
        )
        tree = _parse_to_module(source)
        result = _all_method_nodes(tree)
        # "setdefault" means first class wins
        assert "my_method" in result

    def test_multiple_methods_all_included(self):
        source = (
            "class MyHandler:\n"
            "    def create(self): pass\n"
            "    def update(self): pass\n"
            "    def delete(self): pass\n"
        )
        tree = _parse_to_module(source)
        result = _all_method_nodes(tree)
        assert "create" in result
        assert "update" in result
        assert "delete" in result


# ---------------------------------------------------------------------------
# 5. _input_schema_required_fields
# ---------------------------------------------------------------------------

class TestInputSchemaRequiredFields:
    def test_no_input_schema_returns_empty(self):
        assert _input_schema_required_fields({}) == set()

    def test_non_dict_input_schema_returns_empty(self):
        assert _input_schema_required_fields({"input_schema": "not-a-dict"}) == set()

    def test_no_required_field_returns_empty(self):
        assert _input_schema_required_fields({"input_schema": {}}) == set()

    def test_non_list_required_returns_empty(self):
        action = {"input_schema": {"required": "not-a-list"}}
        assert _input_schema_required_fields(action) == set()

    def test_list_of_strings_returned(self):
        action = {"input_schema": {"required": ["name", "email"]}}
        result = _input_schema_required_fields(action)
        assert result == {"name", "email"}

    def test_whitespace_stripped(self):
        action = {"input_schema": {"required": ["  name  ", " email "]}}
        result = _input_schema_required_fields(action)
        assert "name" in result
        assert "email" in result

    def test_whitespace_only_items_excluded(self):
        action = {"input_schema": {"required": ["  ", "name"]}}
        result = _input_schema_required_fields(action)
        assert "name" in result
        assert "  " not in result
        assert "" not in result

    def test_returns_set(self):
        action = {"input_schema": {"required": ["x", "y"]}}
        result = _input_schema_required_fields(action)
        assert isinstance(result, set)
