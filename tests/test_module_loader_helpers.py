"""
mozaiksai/core/runtime/app/module_loader.py pure helper unit tests.

Covers:
  _required_text:
    - valid string → stripped string returned
    - empty string → ValueError
    - whitespace only → ValueError
    - None → ValueError
    - non-zero int → stringified and returned

  _optional_text:
    - None → None
    - empty string → None
    - whitespace only → None
    - valid string → stripped
    - 0 int → "0" returned

  _string_list:
    - None → []
    - non-list → ValueError
    - list of strings → stripped, deduplicated
    - empty string items excluded
    - whitespace-only items excluded
    - None items treated as empty strings → excluded

  _validate_entrypoint:
    - no colon → ValueError
    - empty module path → ValueError
    - empty class name → ValueError
    - _shared in path → ValueError
    - backslash in path → ValueError
    - absolute path → ValueError
    - ".." in path → ValueError
    - valid entrypoint → (rel_path, class_name) tuple
    - class name with underscore accepted
    - non-identifier class name → ValueError

Pydantic validators via model construction:
  ModuleIdentity:
    - empty id → ValidationError
    - invalid handler (no colon) → ValidationError
    - valid handler → accepted

  ActionDef:
    - empty id → ValidationError
    - empty description → ValidationError
    - empty handler_method → ValidationError
    - valid → accepted

  ModulePermission:
    - empty id → ValidationError
    - empty description → ValidationError
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from mozaiksai.core.runtime.app.module_loader import (
    ActionDef,
    ModuleIdentity,
    ModulePermission,
    _optional_text,
    _required_text,
    _string_list,
    _validate_entrypoint,
)

# ---------------------------------------------------------------------------
# 1. _required_text
# ---------------------------------------------------------------------------

class TestRequiredText:
    def test_valid_string_returned(self):
        assert _required_text("hello", field_name="name") == "hello"

    def test_string_stripped(self):
        assert _required_text("  hello  ", field_name="name") == "hello"

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            _required_text("", field_name="name")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            _required_text("   ", field_name="name")

    def test_none_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            _required_text(None, field_name="name")

    def test_zero_raises(self):
        # str(0 or "") = str("") = "" → raises
        with pytest.raises(ValueError, match="non-empty"):
            _required_text(0, field_name="count")

    def test_non_zero_int_stringified(self):
        result = _required_text(42, field_name="version")
        assert result == "42"

    def test_field_name_in_message(self):
        with pytest.raises(ValueError, match="my_field"):
            _required_text("", field_name="my_field")


# ---------------------------------------------------------------------------
# 2. _optional_text
# ---------------------------------------------------------------------------

class TestOptionalText:
    def test_none_returns_none(self):
        assert _optional_text(None) is None

    def test_empty_string_returns_none(self):
        assert _optional_text("") is None

    def test_whitespace_returns_none(self):
        assert _optional_text("   ") is None

    def test_valid_string_stripped(self):
        assert _optional_text("  hello  ") == "hello"

    def test_zero_int_stringified(self):
        assert _optional_text(0) == "0"

    def test_non_string_truthy_stringified(self):
        assert _optional_text(42) == "42"


# ---------------------------------------------------------------------------
# 3. _string_list
# ---------------------------------------------------------------------------

class TestStringList:
    def test_none_returns_empty(self):
        assert _string_list(None) == []

    def test_non_list_raises(self):
        with pytest.raises(ValueError, match="must be a list"):
            _string_list("not_a_list")

    def test_non_list_dict_raises(self):
        with pytest.raises(ValueError, match="must be a list"):
            _string_list({"key": "val"})

    def test_empty_list_returns_empty(self):
        assert _string_list([]) == []

    def test_strings_stripped(self):
        result = _string_list(["  a  ", "  b  "])
        assert result == ["a", "b"]

    def test_empty_string_items_excluded(self):
        result = _string_list(["a", "", "b"])
        assert result == ["a", "b"]

    def test_whitespace_only_items_excluded(self):
        result = _string_list(["a", "   ", "b"])
        assert result == ["a", "b"]

    def test_duplicates_removed(self):
        result = _string_list(["a", "b", "a", "c"])
        assert result == ["a", "b", "c"]

    def test_none_items_excluded(self):
        # str(None or "").strip() = "" → excluded
        result = _string_list([None, "valid"])
        assert result == ["valid"]

    def test_order_preserved(self):
        result = _string_list(["c", "a", "b"])
        assert result == ["c", "a", "b"]


# ---------------------------------------------------------------------------
# 4. _validate_entrypoint
# ---------------------------------------------------------------------------

class TestValidateEntrypoint:
    def test_no_colon_raises(self):
        with pytest.raises(ValueError, match="module.path:ClassName"):
            _validate_entrypoint("modules.tasks.backend.handler")

    def test_empty_module_path_raises(self):
        with pytest.raises(ValueError, match="both module path"):
            _validate_entrypoint(":ClassName")

    def test_empty_class_name_raises(self):
        with pytest.raises(ValueError, match="both module path"):
            _validate_entrypoint("modules.tasks.backend.handler:")

    def test_shared_in_path_raises(self):
        with pytest.raises(ValueError, match="module-local"):
            _validate_entrypoint("_shared.utils:Handler")

    def test_backslash_in_path_raises(self):
        with pytest.raises(ValueError, match="module-local"):
            _validate_entrypoint("modules\\tasks\\handler:Handler")

    def test_dotdot_in_path_raises(self):
        with pytest.raises(ValueError, match="safe module-local"):
            _validate_entrypoint("../modules.tasks:Handler")

    def test_valid_entrypoint_returns_tuple(self):
        rel_path, class_name = _validate_entrypoint("modules.tasks.backend.handler:TaskHandler")
        assert rel_path.endswith(".py")
        assert "tasks" in rel_path
        assert class_name == "TaskHandler"

    def test_class_name_with_underscore_accepted(self):
        rel_path, class_name = _validate_entrypoint("modules.tasks.backend.handler:Task_Handler")
        assert class_name == "Task_Handler"

    def test_non_identifier_class_name_raises(self):
        with pytest.raises(ValueError, match="valid identifier"):
            _validate_entrypoint("modules.tasks.handler:Task-Handler")

    def test_returns_relative_path(self):
        rel_path, _ = _validate_entrypoint("modules.tasks.backend.handler:Handler")
        assert rel_path == "modules/tasks/backend/handler.py"


# ---------------------------------------------------------------------------
# 5. ModuleIdentity validators via model construction
# ---------------------------------------------------------------------------

class TestModuleIdentityValidators:
    def _valid_identity(self, **kwargs):
        defaults = {
            "id": "tasks",
            "handler": "modules.tasks.backend.handler:TaskHandler",
        }
        defaults.update(kwargs)
        return ModuleIdentity(**defaults)

    def test_valid_identity_accepted(self):
        identity = self._valid_identity()
        assert identity.id == "tasks"

    def test_empty_id_raises(self):
        with pytest.raises(ValidationError):
            self._valid_identity(id="")

    def test_whitespace_id_raises(self):
        with pytest.raises(ValidationError):
            self._valid_identity(id="   ")

    def test_invalid_handler_no_colon_raises(self):
        with pytest.raises(ValidationError):
            self._valid_identity(handler="no_colon_here")

    def test_handler_stored_as_is(self):
        identity = self._valid_identity()
        assert ":" in identity.handler


# ---------------------------------------------------------------------------
# 6. ActionDef validators via model construction
# ---------------------------------------------------------------------------

class TestActionDefValidators:
    def _valid_action(self, **kwargs):
        defaults = {
            "id": "create_task",
            "description": "Create a new task",
            "handler_method": "create_task",
        }
        defaults.update(kwargs)
        return ActionDef(**defaults)

    def test_valid_action_accepted(self):
        action = self._valid_action()
        assert action.id == "create_task"

    def test_empty_id_raises(self):
        with pytest.raises(ValidationError):
            self._valid_action(id="")

    def test_empty_description_raises(self):
        with pytest.raises(ValidationError):
            self._valid_action(description="")

    def test_empty_handler_method_raises(self):
        with pytest.raises(ValidationError):
            self._valid_action(handler_method="")

    def test_permissions_list_normalized(self):
        action = self._valid_action(permissions=["  read  ", "write", "read"])
        assert "read" in action.permissions
        assert "write" in action.permissions
        assert action.permissions.count("read") == 1

    def test_none_permissions_returns_empty(self):
        action = self._valid_action(permissions=None)
        assert action.permissions == []


# ---------------------------------------------------------------------------
# 7. ModulePermission validators
# ---------------------------------------------------------------------------

class TestModulePermissionValidators:
    def test_valid_permission_accepted(self):
        perm = ModulePermission(id="manage_tasks", description="Allows managing tasks")
        assert perm.id == "manage_tasks"

    def test_empty_id_raises(self):
        with pytest.raises(ValidationError):
            ModulePermission(id="", description="desc")

    def test_empty_description_raises(self):
        with pytest.raises(ValidationError):
            ModulePermission(id="perm_id", description="")

    def test_whitespace_id_raises(self):
        with pytest.raises(ValidationError):
            ModulePermission(id="   ", description="desc")
