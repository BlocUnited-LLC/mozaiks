"""
Context graph contract role and AST pure helper unit tests.

Covers:
  _module_file_contract_role:
    - module.yaml → "module_contract"
    - backend/handler.py → "module_handler"
    - backend/service.py → "module_service"
    - backend/repo.py → "module_repo"
    - backend/policy.py → "module_policy"
    - backend/schemas.py → "module_schemas"
    - backend/settings.py → "module_settings"
    - backend/admin.py → "module_admin"
    - path containing /contracts/ → "module_companion_contract"
    - runtime_extensions.yaml → "module_runtime_extensions"
    - other file → "module_support"

  _workflow_file_contract_role:
    - orchestrator.yaml → "workflow_orchestrator_contract"
    - agents.yaml → "workflow_agent_contract"
    - transition_graph.yaml → "workflow_transition_graph_contract"
    - structured_outputs.yaml → "workflow_output_contract"
    - tools.yaml → "workflow_tool_contract"
    - middleware.yaml → "workflow_middleware_contract"
    - context_variables.yaml → "workflow_context_contract"
    - path containing /tools/ → "workflow_tool_code"
    - path containing /ui/ → "workflow_ui_code"
    - other → "workflow_support"

  _python_call_target:
    - ast.Name → id
    - ast.Attribute → "parent.attr"
    - ast.Call wrapping ast.Name → id
    - ast.Call wrapping ast.Attribute → dotted name
    - None-like node → None
    - chained attributes → fully qualified

  _python_decorator_names:
    - empty list → []
    - single Name decorator → [id]
    - multiple decorators → deduplicated list
    - Call decorator → name resolved

  _resolve_import_path:
    - non-relative target → None
    - ./sibling.py in file_map → resolved path
    - ../parent_module in file_map → resolved path
    - target not in file_map → None
    - .module (Python dot notation) → resolved as /
"""
from __future__ import annotations

import ast

from mozaiksai.core.app_context.context_graph import (
    _module_file_contract_role,
    _python_call_target,
    _python_decorator_names,
    _resolve_import_path,
    _workflow_file_contract_role,
)

# ---------------------------------------------------------------------------
# 1. _module_file_contract_role
# ---------------------------------------------------------------------------

class TestModuleFileContractRole:
    def test_module_yaml(self):
        assert _module_file_contract_role("modules/tasks/module.yaml") == "module_contract"

    def test_backend_handler(self):
        assert _module_file_contract_role("modules/tasks/backend/handler.py") == "module_handler"

    def test_backend_service(self):
        assert _module_file_contract_role("modules/tasks/backend/service.py") == "module_service"

    def test_backend_repo(self):
        assert _module_file_contract_role("modules/tasks/backend/repo.py") == "module_repo"

    def test_backend_policy(self):
        assert _module_file_contract_role("modules/tasks/backend/policy.py") == "module_policy"

    def test_backend_schemas(self):
        assert _module_file_contract_role("modules/tasks/backend/schemas.py") == "module_schemas"

    def test_backend_settings(self):
        assert _module_file_contract_role("modules/tasks/backend/settings.py") == "module_settings"

    def test_backend_admin(self):
        assert _module_file_contract_role("modules/tasks/backend/admin.py") == "module_admin"

    def test_contracts_directory(self):
        result = _module_file_contract_role("modules/tasks/contracts/events.yaml")
        assert result == "module_companion_contract"

    def test_runtime_extensions_yaml(self):
        result = _module_file_contract_role("modules/tasks/runtime_extensions.yaml")
        assert result == "module_runtime_extensions"

    def test_other_file_is_module_support(self):
        result = _module_file_contract_role("modules/tasks/some_helper.py")
        assert result == "module_support"

    def test_nested_module_yaml_only_by_filename(self):
        # File named module.yaml regardless of depth
        assert _module_file_contract_role("deep/nested/path/module.yaml") == "module_contract"


# ---------------------------------------------------------------------------
# 2. _workflow_file_contract_role
# ---------------------------------------------------------------------------

class TestWorkflowFileContractRole:
    def test_orchestrator_yaml(self):
        assert _workflow_file_contract_role("workflows/AppGenerator/orchestrator.yaml") == "workflow_orchestrator_contract"

    def test_agents_yaml(self):
        assert _workflow_file_contract_role("workflows/AppGenerator/agents.yaml") == "workflow_agent_contract"

    def test_transition_graph_yaml(self):
        result = _workflow_file_contract_role("workflows/AppGenerator/transition_graph.yaml")
        assert result == "workflow_transition_graph_contract"

    def test_structured_outputs_yaml(self):
        result = _workflow_file_contract_role("workflows/AppGenerator/structured_outputs.yaml")
        assert result == "workflow_output_contract"

    def test_tools_yaml(self):
        assert _workflow_file_contract_role("workflows/AppGenerator/tools.yaml") == "workflow_tool_contract"

    def test_middleware_yaml(self):
        assert _workflow_file_contract_role("workflows/AppGenerator/middleware.yaml") == "workflow_middleware_contract"

    def test_context_variables_yaml(self):
        result = _workflow_file_contract_role("workflows/AppGenerator/context_variables.yaml")
        assert result == "workflow_context_contract"

    def test_tools_directory(self):
        result = _workflow_file_contract_role("workflows/AppGenerator/tools/my_tool.py")
        assert result == "workflow_tool_code"

    def test_ui_directory(self):
        result = _workflow_file_contract_role("workflows/AppGenerator/ui/AppGenerator/Component.jsx")
        assert result == "workflow_ui_code"

    def test_other_file_is_workflow_support(self):
        result = _workflow_file_contract_role("workflows/AppGenerator/utils.py")
        assert result == "workflow_support"


# ---------------------------------------------------------------------------
# 3. _python_call_target
# ---------------------------------------------------------------------------

def _name(id: str) -> ast.Name:
    return ast.Name(id=id)


def _attr(parent: ast.expr, attr: str) -> ast.Attribute:
    return ast.Attribute(value=parent, attr=attr)


def _call(func: ast.expr) -> ast.Call:
    return ast.Call(func=func, args=[], keywords=[])


class TestPythonCallTarget:
    def test_name_node_returns_id(self):
        assert _python_call_target(_name("my_func")) == "my_func"

    def test_attribute_node_returns_dotted(self):
        node = _attr(_name("module"), "function")
        assert _python_call_target(node) == "module.function"

    def test_call_wrapping_name_returns_id(self):
        assert _python_call_target(_call(_name("decorator"))) == "decorator"

    def test_call_wrapping_attribute_returns_dotted(self):
        node = _call(_attr(_name("some_module"), "my_decorator"))
        assert _python_call_target(node) == "some_module.my_decorator"

    def test_unknown_node_returns_none(self):
        assert _python_call_target(ast.Constant(value=42)) is None

    def test_chained_attributes(self):
        node = _attr(_attr(_name("a"), "b"), "c")
        assert _python_call_target(node) == "a.b.c"


# ---------------------------------------------------------------------------
# 4. _python_decorator_names
# ---------------------------------------------------------------------------

class TestPythonDecoratorNames:
    def test_empty_list_returns_empty(self):
        assert _python_decorator_names([]) == []

    def test_single_name_decorator(self):
        result = _python_decorator_names([_name("staticmethod")])
        assert result == ["staticmethod"]

    def test_call_decorator_resolved(self):
        result = _python_decorator_names([_call(_name("pytest.mark.skip"))])
        # _python_call_target on ast.Call(func=ast.Name(id="pytest.mark.skip")) → "pytest.mark.skip"
        assert result == ["pytest.mark.skip"]

    def test_duplicates_deduplicated(self):
        result = _python_decorator_names([_name("classmethod"), _name("classmethod")])
        assert result == ["classmethod"]

    def test_multiple_different_decorators(self):
        result = _python_decorator_names([_name("classmethod"), _name("property")])
        assert result == ["classmethod", "property"]

    def test_attribute_decorator(self):
        result = _python_decorator_names([_attr(_name("pytest"), "fixture")])
        assert result == ["pytest.fixture"]

    def test_unknown_node_skipped(self):
        result = _python_decorator_names([ast.Constant(value="not_a_decorator"), _name("valid")])
        assert result == ["valid"]


# ---------------------------------------------------------------------------
# 5. _resolve_import_path
# ---------------------------------------------------------------------------

class TestResolveImportPath:
    def test_non_relative_target_returns_none(self):
        result = _resolve_import_path(
            path="modules/tasks/backend/service.py",
            import_target="os",
            file_map={"os.py": ""},
        )
        assert result is None

    def test_relative_sibling_py_resolved(self):
        file_map = {"modules/tasks/backend/schemas.py": "# schemas"}
        result = _resolve_import_path(
            path="modules/tasks/backend/service.py",
            import_target="./schemas",
            file_map=file_map,
        )
        assert result == "modules/tasks/backend/schemas.py"

    def test_relative_parent_py_resolved(self):
        file_map = {"modules/tasks/utils.py": "# utils"}
        result = _resolve_import_path(
            path="modules/tasks/backend/service.py",
            import_target="../utils",
            file_map=file_map,
        )
        assert result == "modules/tasks/utils.py"

    def test_target_not_in_file_map_returns_none(self):
        result = _resolve_import_path(
            path="modules/tasks/backend/service.py",
            import_target="./nonexistent",
            file_map={},
        )
        assert result is None

    def test_dot_module_notation_resolved(self):
        # ".schemas" in Python relative import = same directory
        file_map = {"modules/tasks/backend/schemas.py": ""}
        result = _resolve_import_path(
            path="modules/tasks/backend/service.py",
            import_target=".schemas",
            file_map=file_map,
        )
        assert result == "modules/tasks/backend/schemas.py"

    def test_empty_target_returns_none(self):
        result = _resolve_import_path(
            path="modules/tasks/backend/service.py",
            import_target="",
            file_map={},
        )
        assert result is None
