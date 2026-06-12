from __future__ import annotations

import json
from pathlib import Path

import yaml

from mozaiks_cli.commands import init_command
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


def test_factory_app_ai_config_keeps_runtime_startup_sections() -> None:
    ai_path = Path(__file__).resolve().parents[1] / "factory_app" / "app" / "config" / "ai.json"
    data = json.loads(ai_path.read_text(encoding="utf-8"))

    assert sorted(data.keys()) == ["ask", "chat", "workflows"]


def test_factory_app_control_plane_defaults_are_declared() -> None:
    runtime_path = Path(__file__).resolve().parents[1] / "factory_app" / "control_plane" / "config" / "runtime.yaml"
    data = yaml.safe_load(runtime_path.read_text(encoding="utf-8"))

    assert data["enabled"] is True
    assert sorted(data["llm_profiles"]) == [
        "architecture",
        "classifier",
        "codegen",
        "impact_analyzer",
        "planner_replanner",
        "reviewer_validator",
    ]
    assert data["classifier"]["enabled"] is True
    assert data["classifier"]["llm_profile"] == "classifier"
    assert data["coding"]["llm_profile"] == "codegen"
    assert data["llm_profiles"]["classifier"]["llm_config"]["model"] == "gpt-5-nano"


def test_generated_ai_config_uses_factory_control_plane_defaults() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    ai_path = repo_root / "factory_app" / "app" / "config" / "ai.json"
    runtime_path = repo_root / "factory_app" / "control_plane" / "config" / "runtime.yaml"
    factory_data = json.loads(ai_path.read_text(encoding="utf-8"))
    runtime_data = yaml.safe_load(runtime_path.read_text(encoding="utf-8"))
    generated_data = init_command.build_default_ai_config("Generated App")

    assert generated_data["ask"] == factory_data["ask"]
    assert generated_data["chat"] == factory_data["chat"]
    assert generated_data["workflows"] == factory_data["workflows"]
    assert runtime_data["classifier"]["llm_profile"] == "classifier"
    assert "app_context" not in generated_data


def test_workflow_manager_uses_control_plane_startup_entry_point() -> None:
    config = _mozaiks_workflow_manager().get_config("ValueEngine")

    assert config.get("entry_point") is True


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

