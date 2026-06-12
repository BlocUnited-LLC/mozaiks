"""
Pure helper unit tests for:
  factory_app/workflows/AppGenerator/tools/app_validation.py

Covers the AST-related pure helpers (not covered by test_app_validation_helpers.py):

  _iter_module_yamls:
    - matching path modules/{name}/module.yaml → yielded
    - extra nesting (too deep/shallow) → not yielded
    - non-module.yaml filename at depth 3 → not yielded
    - wrong top-level dir → not yielded

  _iter_backend_python:
    - matching path modules/{name}/backend/handler.py → yielded
    - deeply nested backend path → yielded (len >= 4)
    - path without "backend" segment → not yielded
    - non-.py file in backend → not yielded
    - wrong top-level dir → not yielded

  _parse_python:
    - valid Python → (ast.Module, None)
    - syntax error → (None, error_dict)
    - error dict contains "test", "path", "error", "fix_suggestion"

  _defined_module_names:
    - function def → name added
    - async function def → name added
    - class def → name added
    - import (plain) → top-level module name added
    - import with alias → alias added
    - import from → imported name added
    - import from with alias → alias added
    - import * → skipped
    - assignment target → name added
    - annotated assignment → name added
    - builtins included

  _module_level_name_warnings:
    - class with resolved base → no warning
    - class with unresolved base → warning dict returned
    - warning dict has "test" == "backend_python_unresolved_class_base"
    - class with multiple bases — unresolved one flagged

  _backend_pass_statement_failures:
    - function with pass → failure dict returned
    - failure dict has "test" == "backend_python_pass_statement"
    - async function with pass → failure returned
    - non-function pass (class body class-level) → not reported
    - function without pass → no failure

  _class_method_nodes:
    - class with methods → method names in dict
    - class not found → None
    - only methods included (not class variables)
"""
from __future__ import annotations

import ast

from factory_app.workflows.AppGenerator.tools.app_validation import (
    _backend_pass_statement_failures,
    _class_method_nodes,
    _defined_module_names,
    _iter_backend_python,
    _iter_module_yamls,
    _module_level_name_warnings,
    _parse_python,
)

# ---------------------------------------------------------------------------
# 1. _iter_module_yamls
# ---------------------------------------------------------------------------

class TestIterModuleYamls:
    def test_matching_path_yielded(self):
        files = {"modules/billing/module.yaml": "id: billing"}
        results = list(_iter_module_yamls(files))
        assert len(results) == 1
        assert results[0][0] == "modules/billing/module.yaml"

    def test_content_preserved(self):
        files = {"modules/billing/module.yaml": "id: billing\nversion: 1"}
        results = list(_iter_module_yamls(files))
        assert results[0][1] == "id: billing\nversion: 1"

    def test_wrong_top_level_dir_skipped(self):
        files = {"app/billing/module.yaml": "id: billing"}
        assert list(_iter_module_yamls(files)) == []

    def test_too_shallow_path_skipped(self):
        files = {"modules/module.yaml": "id: billing"}
        assert list(_iter_module_yamls(files)) == []

    def test_too_deep_path_skipped(self):
        files = {"modules/billing/backend/module.yaml": "id: billing"}
        assert list(_iter_module_yamls(files)) == []

    def test_wrong_filename_skipped(self):
        files = {"modules/billing/handler.py": ""}
        assert list(_iter_module_yamls(files)) == []

    def test_multiple_modules(self):
        files = {
            "modules/billing/module.yaml": "id: billing",
            "modules/wallet/module.yaml": "id: wallet",
            "modules/billing/backend/handler.py": "",
        }
        results = list(_iter_module_yamls(files))
        paths = {r[0] for r in results}
        assert paths == {"modules/billing/module.yaml", "modules/wallet/module.yaml"}

    def test_sorted_output(self):
        files = {
            "modules/z_module/module.yaml": "z",
            "modules/a_module/module.yaml": "a",
        }
        results = list(_iter_module_yamls(files))
        assert results[0][0] == "modules/a_module/module.yaml"
        assert results[1][0] == "modules/z_module/module.yaml"


# ---------------------------------------------------------------------------
# 2. _iter_backend_python
# ---------------------------------------------------------------------------

class TestIterBackendPython:
    def test_matching_path_yielded(self):
        files = {"modules/billing/backend/handler.py": "# handler"}
        results = list(_iter_backend_python(files))
        assert len(results) == 1
        assert results[0][0] == "modules/billing/backend/handler.py"

    def test_content_preserved(self):
        files = {"modules/billing/backend/service.py": "def fn(): pass"}
        results = list(_iter_backend_python(files))
        assert results[0][1] == "def fn(): pass"

    def test_wrong_top_level_dir_skipped(self):
        files = {"app/billing/backend/handler.py": ""}
        assert list(_iter_backend_python(files)) == []

    def test_missing_backend_segment_skipped(self):
        files = {"modules/billing/handler.py": ""}
        assert list(_iter_backend_python(files)) == []

    def test_non_python_file_skipped(self):
        files = {"modules/billing/backend/handler.yaml": ""}
        assert list(_iter_backend_python(files)) == []

    def test_deeply_nested_backend_file_included(self):
        files = {"modules/billing/backend/subdir/helper.py": ""}
        results = list(_iter_backend_python(files))
        assert len(results) == 1

    def test_multiple_backend_files(self):
        files = {
            "modules/billing/backend/handler.py": "",
            "modules/billing/backend/service.py": "",
            "modules/billing/module.yaml": "",
        }
        results = list(_iter_backend_python(files))
        assert len(results) == 2

    def test_sorted_output(self):
        files = {
            "modules/billing/backend/service.py": "",
            "modules/billing/backend/handler.py": "",
        }
        results = list(_iter_backend_python(files))
        assert results[0][0] == "modules/billing/backend/handler.py"


# ---------------------------------------------------------------------------
# 3. _parse_python
# ---------------------------------------------------------------------------

class TestParsePython:
    def test_valid_python_returns_ast(self):
        tree, error = _parse_python("handler.py", "def fn(): pass")
        assert isinstance(tree, ast.Module)
        assert error is None

    def test_valid_empty_module_returns_ast(self):
        tree, error = _parse_python("handler.py", "")
        assert isinstance(tree, ast.Module)
        assert error is None

    def test_syntax_error_returns_none_tree(self):
        tree, error = _parse_python("handler.py", "def fn(: pass")
        assert tree is None

    def test_syntax_error_returns_error_dict(self):
        tree, error = _parse_python("handler.py", "def fn(: pass")
        assert error is not None
        assert isinstance(error, dict)

    def test_error_dict_has_test_field(self):
        _, error = _parse_python("handler.py", "def fn(: pass")
        assert error["test"] == "backend_python_syntax"

    def test_error_dict_has_path_field(self):
        _, error = _parse_python("modules/billing/backend/handler.py", "def fn(: pass")
        assert error["path"] == "modules/billing/backend/handler.py"

    def test_error_dict_has_error_field(self):
        _, error = _parse_python("handler.py", "def fn(: pass")
        assert "error" in error

    def test_error_dict_has_fix_suggestion(self):
        _, error = _parse_python("handler.py", "def fn(: pass")
        assert "fix_suggestion" in error


# ---------------------------------------------------------------------------
# 4. _defined_module_names
# ---------------------------------------------------------------------------

class TestDefinedModuleNames:
    def _parse(self, src: str) -> ast.Module:
        return ast.parse(src)

    def test_includes_builtins(self):
        tree = self._parse("")
        names = _defined_module_names(tree)
        assert "int" in names
        assert "str" in names
        assert "len" in names

    def test_function_def_added(self):
        tree = self._parse("def my_function(): pass")
        assert "my_function" in _defined_module_names(tree)

    def test_async_function_def_added(self):
        tree = self._parse("async def my_handler(): pass")
        assert "my_handler" in _defined_module_names(tree)

    def test_class_def_added(self):
        tree = self._parse("class MyClass: pass")
        assert "MyClass" in _defined_module_names(tree)

    def test_plain_import_top_level_module_added(self):
        tree = self._parse("import os")
        assert "os" in _defined_module_names(tree)

    def test_import_dotted_top_level_only(self):
        tree = self._parse("import os.path")
        names = _defined_module_names(tree)
        assert "os" in names
        assert "os.path" not in names

    def test_import_with_alias(self):
        tree = self._parse("import numpy as np")
        names = _defined_module_names(tree)
        assert "np" in names
        assert "numpy" not in names

    def test_import_from_adds_name(self):
        tree = self._parse("from typing import Optional")
        assert "Optional" in _defined_module_names(tree)

    def test_import_from_with_alias(self):
        tree = self._parse("from typing import Optional as Opt")
        names = _defined_module_names(tree)
        assert "Opt" in names
        assert "Optional" not in names

    def test_import_star_skipped(self):
        # star import shouldn't add "*" to names
        tree = self._parse("from module import *")
        names = _defined_module_names(tree)
        assert "*" not in names

    def test_assignment_target_added(self):
        tree = self._parse("MY_CONST = 42")
        assert "MY_CONST" in _defined_module_names(tree)

    def test_annotated_assignment_added(self):
        tree = self._parse("MY_VAR: int = 10")
        assert "MY_VAR" in _defined_module_names(tree)

    def test_multiple_names_all_added(self):
        src = "import os\ndef fn(): pass\nX = 1"
        names = _defined_module_names(ast.parse(src))
        assert "os" in names
        assert "fn" in names
        assert "X" in names


# ---------------------------------------------------------------------------
# 5. _module_level_name_warnings
# ---------------------------------------------------------------------------

class TestModuleLevelNameWarnings:
    def test_resolved_base_no_warning(self):
        src = "class Base: pass\nclass Child(Base): pass"
        tree = ast.parse(src)
        warnings = _module_level_name_warnings("handler.py", tree)
        assert warnings == []

    def test_builtin_base_no_warning(self):
        src = "class MyError(Exception): pass"
        tree = ast.parse(src)
        warnings = _module_level_name_warnings("handler.py", tree)
        assert warnings == []

    def test_unresolved_base_returns_warning(self):
        src = "class Child(UnknownBase): pass"
        tree = ast.parse(src)
        warnings = _module_level_name_warnings("handler.py", tree)
        assert len(warnings) == 1

    def test_warning_has_correct_test_field(self):
        src = "class Child(UnknownBase): pass"
        tree = ast.parse(src)
        warnings = _module_level_name_warnings("handler.py", tree)
        assert warnings[0]["test"] == "backend_python_unresolved_class_base"

    def test_warning_has_path_field(self):
        src = "class Child(UnknownBase): pass"
        tree = ast.parse(src)
        warnings = _module_level_name_warnings("modules/billing/backend/handler.py", tree)
        assert warnings[0]["path"] == "modules/billing/backend/handler.py"

    def test_warning_has_error_and_fix_suggestion(self):
        src = "class Child(UnknownBase): pass"
        tree = ast.parse(src)
        warnings = _module_level_name_warnings("handler.py", tree)
        assert "error" in warnings[0]
        assert "fix_suggestion" in warnings[0]

    def test_imported_base_no_warning(self):
        src = "from mymodule import MyBase\nclass Child(MyBase): pass"
        tree = ast.parse(src)
        warnings = _module_level_name_warnings("handler.py", tree)
        assert warnings == []

    def test_multiple_bases_only_unresolved_flagged(self):
        src = "class Base: pass\nclass Child(Base, UnknownMixin): pass"
        tree = ast.parse(src)
        warnings = _module_level_name_warnings("handler.py", tree)
        assert len(warnings) == 1
        assert "UnknownMixin" in warnings[0]["error"]


# ---------------------------------------------------------------------------
# 6. _backend_pass_statement_failures
# ---------------------------------------------------------------------------

class TestBackendPassStatementFailures:
    def test_function_with_pass_returns_failure(self):
        src = "def my_fn(): pass"
        tree = ast.parse(src)
        failures = _backend_pass_statement_failures("handler.py", tree)
        assert len(failures) == 1

    def test_failure_has_correct_test_field(self):
        src = "def my_fn(): pass"
        tree = ast.parse(src)
        failures = _backend_pass_statement_failures("handler.py", tree)
        assert failures[0]["test"] == "backend_python_pass_statement"

    def test_failure_has_path_field(self):
        src = "def my_fn(): pass"
        tree = ast.parse(src)
        failures = _backend_pass_statement_failures("modules/billing/backend/handler.py", tree)
        assert failures[0]["path"] == "modules/billing/backend/handler.py"

    def test_failure_has_error_and_fix_suggestion(self):
        src = "def my_fn(): pass"
        tree = ast.parse(src)
        failures = _backend_pass_statement_failures("handler.py", tree)
        assert "error" in failures[0]
        assert "fix_suggestion" in failures[0]

    def test_async_function_with_pass_returns_failure(self):
        src = "async def my_fn(): pass"
        tree = ast.parse(src)
        failures = _backend_pass_statement_failures("handler.py", tree)
        assert len(failures) == 1

    def test_function_without_pass_no_failure(self):
        src = "def my_fn():\n    return 42"
        tree = ast.parse(src)
        assert _backend_pass_statement_failures("handler.py", tree) == []

    def test_module_with_no_functions_no_failure(self):
        src = "x = 1\ny = 2"
        tree = ast.parse(src)
        assert _backend_pass_statement_failures("handler.py", tree) == []

    def test_multiple_functions_with_pass(self):
        src = "def fn1(): pass\ndef fn2(): pass"
        tree = ast.parse(src)
        failures = _backend_pass_statement_failures("handler.py", tree)
        assert len(failures) == 2


# ---------------------------------------------------------------------------
# 7. _class_method_nodes
# ---------------------------------------------------------------------------

class TestClassMethodNodes:
    def test_class_not_found_returns_none(self):
        tree = ast.parse("class OtherClass: pass")
        result = _class_method_nodes(tree, "MyHandler")
        assert result is None

    def test_class_with_methods_returns_dict(self):
        src = "class MyHandler:\n    def handle(self): pass\n    def dispatch(self): pass"
        tree = ast.parse(src)
        result = _class_method_nodes(tree, "MyHandler")
        assert result is not None
        assert "handle" in result
        assert "dispatch" in result

    def test_method_nodes_are_function_defs(self):
        src = "class MyHandler:\n    def handle(self): pass"
        tree = ast.parse(src)
        result = _class_method_nodes(tree, "MyHandler")
        assert isinstance(result["handle"], (ast.FunctionDef, ast.AsyncFunctionDef))

    def test_async_methods_included(self):
        src = "class MyHandler:\n    async def async_handle(self): pass"
        tree = ast.parse(src)
        result = _class_method_nodes(tree, "MyHandler")
        assert "async_handle" in result

    def test_class_variables_not_included(self):
        src = "class MyHandler:\n    x = 1\n    def handle(self): pass"
        tree = ast.parse(src)
        result = _class_method_nodes(tree, "MyHandler")
        assert "x" not in result
        assert "handle" in result

    def test_empty_class_returns_empty_dict(self):
        src = "class MyHandler: pass"
        tree = ast.parse(src)
        result = _class_method_nodes(tree, "MyHandler")
        assert result == {}

    def test_correct_class_selected_among_multiple(self):
        src = (
            "class Handler:\n    def handle(self): pass\n"
            "class Service:\n    def execute(self): pass\n"
        )
        tree = ast.parse(src)
        result = _class_method_nodes(tree, "Service")
        assert "execute" in result
        assert "handle" not in result
