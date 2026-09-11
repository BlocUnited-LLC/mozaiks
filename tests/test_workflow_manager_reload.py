from __future__ import annotations

import types
from pathlib import Path

import pytest

from mozaiksai.core.workflow import workflow_manager as workflow_manager_module
from mozaiksai.core.workflow.workflow_manager import UnifiedWorkflowManager, WorkflowInfo


def test_reload_workflow_skips_module_reload_when_module_was_evicted(monkeypatch, tmp_path) -> None:
    manager = object.__new__(UnifiedWorkflowManager)
    evicted_module = types.ModuleType("workflows.ValueEngine")
    manager._workflows = {
        "valueengine": WorkflowInfo(
            name="ValueEngine",
            config={},
            path=str(tmp_path),
            module=evicted_module,
        )
    }
    manager._ui_registry = {}
    manager._ui_tool_path_cache = {}
    manager._ui_loaded_workflows = set()
    manager._config_cache = {}

    def fail_reload(_module):
        raise AssertionError("importlib.reload should not be called for evicted workflow modules")

    monkeypatch.setattr(workflow_manager_module.importlib, "reload", fail_reload)
    monkeypatch.setattr(manager, "resolve_workflow_path", lambda _workflow_name: tmp_path)
    monkeypatch.setattr(
        manager,
        "_load_single_workflow",
        lambda workflow_name: WorkflowInfo(name=workflow_name, config={"loaded": True}, path=str(tmp_path)),
    )

    result = manager.reload_workflow("ValueEngine")

    assert result["name"] == "ValueEngine"
    assert result["config"] == {"loaded": True}


@pytest.fixture
def factory_manager():
    manager = object.__new__(UnifiedWorkflowManager)
    manager.__init__(str(Path(__file__).resolve().parents[1] / "factory_app/workflows"))
    return manager


def test_value_engine_review_component_survives_repeated_reload(factory_manager):
    manager = factory_manager
    original = manager.get_ui_tool_record("save_value_manifest")
    assert original["component"] == "ConceptBlueprint"
    for _ in range(3):
        result = manager.reload_workflow("ValueEngine")
        assert result["status"] == "loaded"
        assert manager.get_ui_tool_record("save_value_manifest") == original
        assert manager.get_ui_tool_record(original["path"]) == original


def test_unload_evicts_only_own_ui_metadata(factory_manager):
    manager = factory_manager
    original = manager.get_ui_tool_record("save_value_manifest")
    other_tools = [item for item in manager.iter_ui_tools() if item["workflow_name"] != "ValueEngine"]
    assert other_tools
    manager.unload_workflow("valueengine")
    assert manager.get_ui_tool_record("save_value_manifest") is None
    assert manager.get_ui_tool_record(original["path"]) is None
    assert "ValueEngine" not in manager._ui_loaded_workflows
    assert manager.iter_ui_tools() == other_tools
    assert manager.reload_workflow("ValueEngine")["status"] == "loaded"
    assert manager.get_ui_tool_record("save_value_manifest") == original


def test_invalid_reload_does_not_retain_ui_metadata(factory_manager, monkeypatch):
    manager = factory_manager
    original = manager.get_ui_tool_record("save_value_manifest")

    def invalid_config(_path):
        raise ValueError("Invalid replacement workflow")

    monkeypatch.setattr(manager, "_load_modular_workflow_config", invalid_config)
    result = manager.reload_workflow("ValueEngine")
    assert "Invalid replacement workflow" in result["error"]
    assert manager.get_ui_tool_record("save_value_manifest") is None
    assert manager.get_ui_tool_record(original["path"]) is None
