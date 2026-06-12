"""
assemble_app_tasks.py pure helper unit tests.

Covers:
  _is_truthy:
    - bool True → True
    - bool False → False
    - string "true" / "1" / "yes" / "passed" / "ready" → True
    - string "false" / "no" / "0" / other → False
    - whitespace around truthy string → True
    - non-empty list → True
    - empty list → False
    - None → False
    - 0 → False
    - 1 → True

  _handler_methods:
    - valid class with methods → set of method names
    - class not found → empty set
    - syntax error source → empty set
    - empty source → empty set
    - async methods included
    - nested classes not matched by name

  _apply_module_handler_method_alignment:
    - no module.yaml files → unchanged
    - module.yaml without handler → unchanged
    - handler method matches actual method name → unchanged
    - handler method wrong, action_id matches → corrected
    - handler method not matching and action_id also not in methods → unchanged
    - action without id → skipped
    - non-module.yaml path skipped (e.g. deeper nesting)
"""
from __future__ import annotations

import yaml

from factory_app.workflows.AppGenerator.tools.assemble_app_tasks import (
    _apply_module_handler_method_alignment,
    _handler_methods,
    _is_truthy,
)

# ---------------------------------------------------------------------------
# 1. _is_truthy
# ---------------------------------------------------------------------------

class TestIsTruthy:
    def test_bool_true(self):
        assert _is_truthy(True) is True

    def test_bool_false(self):
        assert _is_truthy(False) is False

    def test_string_true(self):
        assert _is_truthy("true") is True

    def test_string_True_case_insensitive(self):
        assert _is_truthy("TRUE") is True

    def test_string_1(self):
        assert _is_truthy("1") is True

    def test_string_yes(self):
        assert _is_truthy("yes") is True

    def test_string_passed(self):
        assert _is_truthy("passed") is True

    def test_string_ready(self):
        assert _is_truthy("ready") is True

    def test_string_false(self):
        assert _is_truthy("false") is False

    def test_string_no(self):
        assert _is_truthy("no") is False

    def test_string_zero(self):
        assert _is_truthy("0") is False

    def test_string_other(self):
        assert _is_truthy("maybe") is False

    def test_whitespace_around_truthy_string(self):
        assert _is_truthy("  true  ") is True

    def test_whitespace_around_falsy_string(self):
        assert _is_truthy("  false  ") is False

    def test_non_empty_list(self):
        assert _is_truthy([1, 2, 3]) is True

    def test_empty_list(self):
        assert _is_truthy([]) is False

    def test_none(self):
        assert _is_truthy(None) is False

    def test_zero_int(self):
        assert _is_truthy(0) is False

    def test_one_int(self):
        assert _is_truthy(1) is True


# ---------------------------------------------------------------------------
# 2. _handler_methods
# ---------------------------------------------------------------------------

_HANDLER_SOURCE = '''
class TasksModule:
    def list_tasks(self):
        pass

    async def create_task(self, data):
        pass

    def delete_task(self, task_id):
        pass
'''


class TestHandlerMethods:
    def test_valid_class_returns_method_names(self):
        result = _handler_methods(_HANDLER_SOURCE, "TasksModule")
        assert "list_tasks" in result
        assert "create_task" in result
        assert "delete_task" in result

    def test_async_methods_included(self):
        result = _handler_methods(_HANDLER_SOURCE, "TasksModule")
        assert "create_task" in result

    def test_class_not_found_returns_empty(self):
        result = _handler_methods(_HANDLER_SOURCE, "NonExistentModule")
        assert result == set()

    def test_syntax_error_returns_empty(self):
        result = _handler_methods("def (broken:", "MyClass")
        assert result == set()

    def test_empty_source_returns_empty(self):
        result = _handler_methods("", "MyClass")
        assert result == set()

    def test_class_without_methods_returns_empty_set(self):
        source = "class EmptyModule:\n    pass\n"
        result = _handler_methods(source, "EmptyModule")
        assert result == set()

    def test_nested_class_not_matched_by_outer_name(self):
        source = '''
class Outer:
    class Inner:
        def inner_method(self):
            pass
    def outer_method(self):
        pass
'''
        result = _handler_methods(source, "Outer")
        assert "outer_method" in result
        # Inner is a class in the body, not a function — so it won't appear in methods
        assert "inner_method" not in result


# ---------------------------------------------------------------------------
# 3. _apply_module_handler_method_alignment
# ---------------------------------------------------------------------------

def _make_files(files: dict[str, str]) -> list[dict[str, str]]:
    return [{"filename": k, "content": v} for k, v in files.items()]


def _files_to_map(files: list[dict[str, str]]) -> dict[str, str]:
    return {f["filename"]: f["content"] for f in files}


class TestApplyModuleHandlerMethodAlignment:
    def test_no_module_yaml_unchanged(self):
        files = _make_files({"modules/tasks/backend/handler.py": "class Foo: pass"})
        result = _apply_module_handler_method_alignment(files)
        result_map = _files_to_map(result)
        assert result_map["modules/tasks/backend/handler.py"] == "class Foo: pass"

    def test_module_yaml_without_handler_unchanged(self):
        module_yaml = yaml.safe_dump({
            "id": "tasks",
            "schema_version": "mozaiks.module.v1",
            "actions": [{"id": "list_tasks", "handler_method": "list_tasks"}],
        })
        files = _make_files({"modules/tasks/module.yaml": module_yaml})
        result = _apply_module_handler_method_alignment(files)
        result_map = _files_to_map(result)
        loaded = yaml.safe_load(result_map["modules/tasks/module.yaml"])
        assert loaded["actions"][0]["handler_method"] == "list_tasks"

    def test_handler_method_matches_method_name_unchanged(self):
        handler_py = "class TasksModule:\n    def list_tasks(self): pass\n"
        module_yaml = yaml.safe_dump({
            "handler": "backend.handler:TasksModule",
            "actions": [{"id": "list_tasks", "handler_method": "list_tasks"}],
        })
        files = _make_files({
            "modules/tasks/module.yaml": module_yaml,
            "modules/tasks/backend/handler.py": handler_py,
        })
        result = _apply_module_handler_method_alignment(files)
        result_map = _files_to_map(result)
        loaded = yaml.safe_load(result_map["modules/tasks/module.yaml"])
        assert loaded["actions"][0]["handler_method"] == "list_tasks"

    def test_wrong_handler_method_corrected_when_action_id_matches(self):
        handler_py = "class TasksModule:\n    def list_tasks(self): pass\n"
        module_yaml = yaml.safe_dump({
            "handler": "backend.handler:TasksModule",
            "actions": [{"id": "list_tasks", "handler_method": "ListTasks"}],
        })
        files = _make_files({
            "modules/tasks/module.yaml": module_yaml,
            "modules/tasks/backend/handler.py": handler_py,
        })
        result = _apply_module_handler_method_alignment(files)
        result_map = _files_to_map(result)
        loaded = yaml.safe_load(result_map["modules/tasks/module.yaml"])
        # handler_method corrected to match action_id
        assert loaded["actions"][0]["handler_method"] == "list_tasks"

    def test_handler_method_not_in_methods_and_action_id_also_missing_unchanged(self):
        handler_py = "class TasksModule:\n    def some_other_method(self): pass\n"
        module_yaml = yaml.safe_dump({
            "handler": "backend.handler:TasksModule",
            "actions": [{"id": "list_tasks", "handler_method": "wrong_method"}],
        })
        files = _make_files({
            "modules/tasks/module.yaml": module_yaml,
            "modules/tasks/backend/handler.py": handler_py,
        })
        result = _apply_module_handler_method_alignment(files)
        result_map = _files_to_map(result)
        loaded = yaml.safe_load(result_map["modules/tasks/module.yaml"])
        # Not corrected because action_id is also not in methods
        assert loaded["actions"][0]["handler_method"] == "wrong_method"

    def test_deeply_nested_module_yaml_skipped(self):
        # Only modules/{module_id}/module.yaml (3-part path) is processed
        module_yaml = yaml.safe_dump({
            "handler": "backend.handler:TasksModule",
            "actions": [{"id": "list_tasks", "handler_method": "WrongMethod"}],
        })
        handler_py = "class TasksModule:\n    def list_tasks(self): pass\n"
        files = _make_files({
            # 4-part path — should not be processed
            "app/modules/tasks/module.yaml": module_yaml,
            "app/modules/tasks/backend/handler.py": handler_py,
        })
        result = _apply_module_handler_method_alignment(files)
        result_map = _files_to_map(result)
        loaded = yaml.safe_load(result_map["app/modules/tasks/module.yaml"])
        # Not corrected because path has 4 parts
        assert loaded["actions"][0]["handler_method"] == "WrongMethod"

    def test_non_dict_action_skipped(self):
        handler_py = "class TasksModule:\n    def list_tasks(self): pass\n"
        module_yaml = yaml.safe_dump({
            "handler": "backend.handler:TasksModule",
            "actions": ["string_action"],
        })
        files = _make_files({
            "modules/tasks/module.yaml": module_yaml,
            "modules/tasks/backend/handler.py": handler_py,
        })
        # Should not raise
        result = _apply_module_handler_method_alignment(files)
        assert len(result) > 0
