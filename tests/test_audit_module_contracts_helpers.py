"""
audit_module_contracts.py pure helper unit tests.

These test private helpers not covered by test_module_contract_quality_gate.py.

  _parse_yaml_content:
    - valid YAML dict → dict returned
    - valid YAML list → None (must be dict)
    - malformed YAML → None
    - empty string → None
    - null YAML → None
    - plain scalar → None (not a dict)

  _audit_module_yaml:
    - correct schema_version, id, handler → no warnings
    - wrong schema_version → warning with "schema_version"
    - missing id → warning with "missing or empty 'id'"
    - empty id → warning with "missing or empty 'id'"
    - missing handler → warning with "missing 'handler'"
    - handler not starting with "backend.handler:" → warning
    - invalid type → warning with "type"
    - valid type → no type warning
    - actions not a list → warning
    - action missing id → warning
    - action missing handler_method → warning
    - valid action (id + handler_method) → no warning

  _audit_contract_yaml:
    - correct schema_version for events.yaml → no warnings
    - wrong schema_version for events.yaml → warning
    - correct for reactions.yaml → no warnings
    - basename not in expected versions → no warnings (unknown contract)

  _module_dir_of:
    - modules/{id}/module.yaml → modules/{id}
    - modules/{id}/contracts/events.yaml → modules/{id}
    - path without "modules" segment → parent dir
    - nested more deeply → still modules/{id}
"""
from __future__ import annotations

from factory_app.workflows.AppGenerator.tools.audit_module_contracts import (
    _audit_contract_yaml,
    _audit_module_yaml,
    _module_dir_of,
    _parse_yaml_content,
)

# ---------------------------------------------------------------------------
# 1. _parse_yaml_content
# ---------------------------------------------------------------------------

class TestParseYamlContent:
    def test_valid_dict_returned(self):
        result = _parse_yaml_content("id: tasks\nschema_version: mozaiks.module.v1")
        assert isinstance(result, dict)
        assert result["id"] == "tasks"

    def test_list_yaml_returns_none(self):
        result = _parse_yaml_content("- a\n- b")
        assert result is None

    def test_malformed_yaml_returns_none(self):
        result = _parse_yaml_content("{{{broken: yaml")
        assert result is None

    def test_empty_string_returns_none(self):
        result = _parse_yaml_content("")
        assert result is None

    def test_null_yaml_returns_none(self):
        result = _parse_yaml_content("null")
        assert result is None

    def test_plain_scalar_returns_none(self):
        result = _parse_yaml_content("just_a_string")
        assert result is None


# ---------------------------------------------------------------------------
# 2. _audit_module_yaml
# ---------------------------------------------------------------------------

def _valid_module_data():
    return {
        "schema_version": "mozaiks.module.v1",
        "id": "tasks",
        "handler": "backend.handler:TasksModule",
        "actions": [{"id": "create_task", "handler_method": "create_task"}],
    }


class TestAuditModuleYaml:
    def test_valid_module_no_warnings(self):
        warnings = _audit_module_yaml("modules/tasks/module.yaml", _valid_module_data())
        assert warnings == []

    def test_wrong_schema_version_warning(self):
        data = {**_valid_module_data(), "schema_version": "wrong.version"}
        warnings = _audit_module_yaml("modules/tasks/module.yaml", data)
        assert any("schema_version" in w for w in warnings)

    def test_missing_id_warning(self):
        data = {**_valid_module_data()}
        del data["id"]
        warnings = _audit_module_yaml("modules/tasks/module.yaml", data)
        assert any("missing or empty 'id'" in w for w in warnings)

    def test_empty_id_warning(self):
        data = {**_valid_module_data(), "id": ""}
        warnings = _audit_module_yaml("modules/tasks/module.yaml", data)
        assert any("missing or empty 'id'" in w for w in warnings)

    def test_missing_handler_warning(self):
        data = {**_valid_module_data()}
        del data["handler"]
        warnings = _audit_module_yaml("modules/tasks/module.yaml", data)
        assert any("missing 'handler'" in w for w in warnings)

    def test_handler_wrong_prefix_warning(self):
        data = {**_valid_module_data(), "handler": "handler:TasksModule"}
        warnings = _audit_module_yaml("modules/tasks/module.yaml", data)
        assert any("backend.handler:" in w for w in warnings)

    def test_invalid_type_warning(self):
        data = {**_valid_module_data(), "type": "unknown_type"}
        warnings = _audit_module_yaml("modules/tasks/module.yaml", data)
        assert any("type" in w and "unknown_type" in w for w in warnings)

    def test_valid_type_no_warning(self):
        data = {**_valid_module_data(), "type": "standard"}
        warnings = _audit_module_yaml("modules/tasks/module.yaml", data)
        assert not any("type" in w for w in warnings)

    def test_actions_not_list_warning(self):
        data = {**_valid_module_data(), "actions": "not_a_list"}
        warnings = _audit_module_yaml("modules/tasks/module.yaml", data)
        assert any("'actions' must be a list" in w for w in warnings)

    def test_action_missing_id_warning(self):
        data = {**_valid_module_data(), "actions": [{"handler_method": "do_it"}]}
        warnings = _audit_module_yaml("modules/tasks/module.yaml", data)
        assert any("missing 'id'" in w for w in warnings)

    def test_action_missing_handler_method_warning(self):
        data = {**_valid_module_data(), "actions": [{"id": "create_task"}]}
        warnings = _audit_module_yaml("modules/tasks/module.yaml", data)
        assert any("missing 'handler_method'" in w for w in warnings)

    def test_non_dict_action_warning(self):
        data = {**_valid_module_data(), "actions": ["string_action"]}
        warnings = _audit_module_yaml("modules/tasks/module.yaml", data)
        assert any("not a mapping" in w for w in warnings)


# ---------------------------------------------------------------------------
# 3. _audit_contract_yaml
# ---------------------------------------------------------------------------

class TestAuditContractYaml:
    def test_correct_schema_version_events_yaml_no_warning(self):
        warnings = _audit_contract_yaml(
            "modules/tasks/contracts/events.yaml",
            {"schema_version": "mozaiks.events.v1"},
        )
        assert warnings == []

    def test_wrong_schema_version_events_yaml_warning(self):
        warnings = _audit_contract_yaml(
            "modules/tasks/contracts/events.yaml",
            {"schema_version": "wrong.version"},
        )
        assert any("schema_version" in w for w in warnings)

    def test_correct_reactions_yaml(self):
        warnings = _audit_contract_yaml(
            "modules/tasks/contracts/reactions.yaml",
            {"schema_version": "mozaiks.reactions.v1"},
        )
        assert warnings == []

    def test_unknown_contract_basename_no_warning(self):
        warnings = _audit_contract_yaml(
            "modules/tasks/contracts/custom.yaml",
            {"schema_version": "anything"},
        )
        assert warnings == []

    def test_admin_yaml_wrong_version_warning(self):
        warnings = _audit_contract_yaml(
            "modules/tasks/contracts/admin.yaml",
            {"schema_version": "mozaiks.admin.v1"},
        )
        assert any("schema_version" in w for w in warnings)


# ---------------------------------------------------------------------------
# 4. _module_dir_of
# ---------------------------------------------------------------------------

class TestModuleDirOf:
    def test_module_yaml_path(self):
        result = _module_dir_of("modules/tasks/module.yaml")
        assert result == "modules/tasks"

    def test_contracts_subdirectory_path(self):
        result = _module_dir_of("modules/tasks/contracts/events.yaml")
        assert result == "modules/tasks"

    def test_deeply_nested_path(self):
        result = _module_dir_of("modules/tasks/contracts/deep/nested.yaml")
        assert result == "modules/tasks"

    def test_path_without_modules_segment(self):
        result = _module_dir_of("services/config.py")
        assert result == "services"

    def test_path_with_app_prefix(self):
        # If path is like "app/modules/tasks/module.yaml" it still finds "modules"
        result = _module_dir_of("app/modules/tasks/module.yaml")
        assert result == "app/modules/tasks"
