from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace


def _workspace() -> Path:
    return Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (_workspace() / relative_path).read_text(encoding="utf-8")


def _load_create_launcher_tool_module():
    tool_path = (
        _workspace()
        / "factory_app"
        / "app"
        / "workflows"
        / "CreateLauncher"
        / "tools"
        / "launch_shared_create.py"
    )
    spec = importlib.util.spec_from_file_location("tests.create_launcher_tool", tool_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_create_launcher_workflow_files_exist() -> None:
    workflow_root = _workspace() / "factory_app" / "app" / "workflows" / "CreateLauncher"
    assert (workflow_root / "orchestrator.yaml").exists()
    assert (workflow_root / "agents.yaml").exists()
    assert (workflow_root / "context_variables.yaml").exists()
    assert (workflow_root / "handoffs.yaml").exists()
    assert (workflow_root / "structured_outputs.yaml").exists()
    assert (workflow_root / "tools.yaml").exists()
    assert (workflow_root / "tools" / "launch_shared_create.py").exists()


def test_create_launcher_tool_contract_references_shared_create_transition() -> None:
    source = _read("factory_app/app/workflows/CreateLauncher/tools/launch_shared_create.py")
    agents = _read("factory_app/app/workflows/CreateLauncher/agents.yaml")
    tools_yaml = _read("factory_app/app/workflows/CreateLauncher/tools.yaml")

    assert 'transition_id="app_type_selector"' in source
    assert 'emit_workflow_launch_navigation' in source
    assert 'launch_shared_create' in tools_yaml
    assert 'shared create journey' in agents
    assert 'continue there' in agents
    assert 'duplicate create guidance' in agents


def test_create_launcher_overlay_registers_product_workflow() -> None:
    from conftest import active_app_root
    app_root = active_app_root()
    overlay_path = app_root / "workflows" / "extended_orchestration" / "extension_registry.json"
    overlay = json.loads(overlay_path.read_text(encoding="utf-8"))
    workflow_ids = {item["id"] for item in overlay["workflows"]}

    assert "CreateLauncher" in workflow_ids


def test_launch_shared_create_uses_session_launcher(monkeypatch) -> None:
    module = _load_create_launcher_tool_module()

    async def _fake_launch_transition(**kwargs):
        assert kwargs["transition_id"] == "app_type_selector"
        assert kwargs["option_id"] == "existing_app"
        return SimpleNamespace(
            resolution_type="workflow",
            transition_id="app_type_selector",
            option_id="existing_app",
            workflow_launch=SimpleNamespace(
                workflow_id="ValueEngine",
                requested_workflow_id="ValueEngine",
                chat_id="chat_123",
                websocket_url="/ws/ValueEngine/app_1/chat_123/user_1",
                trigger_source="transition",
                routing_explanation="ok",
                rerouted_by_dependency=False,
            ),
        )

    monkeypatch.setattr(module, "launch_transition", _fake_launch_transition)
    navigation_calls = []

    async def _fake_emit_navigation(**kwargs):
        navigation_calls.append(kwargs)
        return True

    monkeypatch.setattr(module, "emit_workflow_launch_navigation", _fake_emit_navigation)
    context_variables = {"app_id": "app_1", "user_id": "user_1"}

    result = asyncio.run(
        module.launch_shared_create(
            "existing_app",
            context_variables=context_variables,
            chat_id="chat_source",
        )
    )

    assert result["success"] is True
    assert result["workflow_id"] == "ValueEngine"
    assert result["chat_id"] == "chat_123"
    assert result["navigation_requested"] is True
    assert context_variables["create_launch_result"]["chat_id"] == "chat_123"
    assert navigation_calls == [
        {
            "source_chat_id": "chat_source",
            "workflow_launch": navigation_calls[0]["workflow_launch"],
        }
    ]
    assert navigation_calls[0]["workflow_launch"].chat_id == "chat_123"