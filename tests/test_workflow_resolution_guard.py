from __future__ import annotations

from pathlib import Path

from mozaiksai.hosts import platform as platform_app
from mozaiksai.core.workflow.startup_messages import (
    resolve_workflow_launch_behavior,
    should_autostart_empty_workflow,
)


ROOT = Path(__file__).resolve().parents[1]


def test_non_runnable_workflow_id_is_rejected() -> None:
    assert platform_app._is_runnable_workflow_name(
        "extended_orchestration",
        ["ValueEngine", "AppGenerator"],
    ) is False


def test_resolve_requested_workflow_prefers_entry_point_for_non_runnable(monkeypatch) -> None:
    monkeypatch.setattr(
        platform_app,
        "_get_ordered_workflow_names",
        lambda: ["ValueEngine", "AppGenerator"],
    )
    monkeypatch.setattr(platform_app, "_get_configured_entry_point", lambda: "AppGenerator")

    assert platform_app._resolve_requested_workflow_name("extended_orchestration") == "AppGenerator"


def test_resolve_requested_workflow_uses_loaded_name_when_known(monkeypatch) -> None:
    monkeypatch.setattr(
        platform_app,
        "_get_ordered_workflow_names",
        lambda: ["ValueEngine", "AppGenerator"],
    )
    monkeypatch.setattr(platform_app, "_get_configured_entry_point", lambda: "AppGenerator")

    assert platform_app._resolve_requested_workflow_name("valueengine") == "ValueEngine"


def test_empty_workflow_autostart_modes_include_userdriven() -> None:
    assert should_autostart_empty_workflow("AgentDriven") is True
    assert should_autostart_empty_workflow("UserDriven") is True
    assert should_autostart_empty_workflow("userdriven") is True
    assert should_autostart_empty_workflow("UserDriven", launch_behavior="auto_start") is True
    assert should_autostart_empty_workflow("UserDriven", launch_behavior="wait_for_user") is False
    assert should_autostart_empty_workflow("BackendOnly") is False
    assert should_autostart_empty_workflow("Manual") is False
    assert should_autostart_empty_workflow(None) is False


def test_launch_behavior_defaults_are_first_class_taxonomy() -> None:
    assert resolve_workflow_launch_behavior("AgentDriven") == "auto_start"
    assert resolve_workflow_launch_behavior("UserDriven") == "auto_start"
    assert resolve_workflow_launch_behavior("BackendOnly") == "none"
    assert resolve_workflow_launch_behavior("UserDriven", launch_behavior="wait_for_user") == "wait_for_user"


def test_platform_websocket_uses_shared_empty_workflow_autostart_contract() -> None:
    source = (ROOT / "mozaiksai" / "hosts" / "platform.py").read_text(encoding="utf-8")
    assert "resolve_workflow_launch_taxonomy" in source
    assert "launch_behavior=launch_taxonomy.get(\"launch_behavior\")" in source
    assert "runtime_app.simple_transport._background_tasks[active_chat_id] = _task" in source
    assert 'startup_mode != "AgentDriven"' not in source

