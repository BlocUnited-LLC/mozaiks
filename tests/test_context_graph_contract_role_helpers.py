"""
Context graph contract role pure helper unit tests.

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



"""
from __future__ import annotations

from mozaiksai.core.app_context.context_graph import (
    _module_file_contract_role,
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
