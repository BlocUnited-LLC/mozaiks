from __future__ import annotations

from pathlib import Path

from tests.import_utils import import_module_directly

_workflow_manager_mod = import_module_directly("mozaiksai.core.workflow.workflow_manager")


def _mozaiks_workflow_manager():
    workflows_root = Path(__file__).resolve().parents[1] / "factory_app" / "app" / "workflows"
    _workflow_manager_mod.UnifiedWorkflowManager._instance = None
    return _workflow_manager_mod.UnifiedWorkflowManager(workflows_base_path=str(workflows_root))


def test_mozaiks_app_generator_is_agent_driven() -> None:
    config = _mozaiks_workflow_manager().get_config("AppGenerator")
    assert config.get("workflow_startup_mode") == "AgentDriven"
    assert config.get("initial_agent") == "InterviewAgent"


def test_existing_app_discovery_is_agent_driven() -> None:
    config = _mozaiks_workflow_manager().get_config("ExistingAppDiscovery")
    assert config.get("workflow_startup_mode") == "AgentDriven"
    assert config.get("initial_agent") == "DiscoveryHostAgent"
