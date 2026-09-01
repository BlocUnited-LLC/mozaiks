from __future__ import annotations

import types

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
    manager._config_cache = {}

    def fail_reload(_module):
        raise AssertionError("importlib.reload should not be called for evicted workflow modules")

    monkeypatch.setattr(workflow_manager_module.importlib, "reload", fail_reload)
    monkeypatch.setattr(manager, "resolve_workflow_path", lambda _workflow_name: tmp_path)
    monkeypatch.setattr(manager, "_load_workflow_tools", lambda _workflow_path: None)
    monkeypatch.setattr(
        manager,
        "_load_single_workflow",
        lambda workflow_name: WorkflowInfo(name=workflow_name, config={"loaded": True}, path=str(tmp_path)),
    )

    result = manager.reload_workflow("ValueEngine")

    assert result["name"] == "ValueEngine"
    assert result["config"] == {"loaded": True}
