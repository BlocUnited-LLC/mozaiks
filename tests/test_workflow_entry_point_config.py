from __future__ import annotations

import json
from pathlib import Path

from tests.import_utils import import_module_directly

_workflow_manager_mod = import_module_directly("mozaiksai.core.workflow.workflow_manager")


def _mozaiks_workflow_manager():
    workflows_root = Path(__file__).resolve().parents[1] / "factory_app" / "workflows"
    _workflow_manager_mod.UnifiedWorkflowManager._instance = None
    return _workflow_manager_mod.UnifiedWorkflowManager(workflows_base_path=str(workflows_root))


def test_factory_app_entry_point_uses_value_engine() -> None:
    ai_path = Path(__file__).resolve().parents[1] / "factory_app" / "app" / "config" / "ai.json"
    data = json.loads(ai_path.read_text(encoding="utf-8"))

    assert data["workflows"]["entry_point"] == "ValueEngine"


def test_create_launcher_workflow_is_removed() -> None:
    workflow_root = Path(__file__).resolve().parents[1] / "factory_app" / "workflows" / "CreateLauncher"

    assert not workflow_root.exists()


def test_mozaiks_app_generator_is_agent_driven() -> None:
    config = _mozaiks_workflow_manager().get_config("AppGenerator")
    assert config.get("workflow_startup_mode") == "AgentDriven"
    assert config.get("initial_agent") == "InterviewAgent"


def test_existing_app_discovery_is_agent_driven() -> None:
    config = _mozaiks_workflow_manager().get_config("ExistingAppDiscovery")
    assert config.get("workflow_startup_mode") == "AgentDriven"
    assert config.get("initial_agent") == "DiscoveryHostAgent"
