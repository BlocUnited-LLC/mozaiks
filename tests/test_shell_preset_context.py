from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

import yaml

from factory_app.workflows.AppGenerator.tools.hook_shell_preset_context import (
    _load_shell_presets,
    inject_shell_preset_context,
)


_APPGEN_DIR = (
    Path(__file__).parent.parent
    / "factory_app"
    / "workflows"
    / "AppGenerator"
)
_HOOKS_YAML = _APPGEN_DIR / "hooks.yaml"
_SHELL_PRESETS_YAML = _APPGEN_DIR / "tools" / "shell_presets.yaml"


class _FakeAgent:
    def __init__(self, name: str) -> None:
        self.name = name
        self.system_message = ""

    def update_system_message(self, message: str) -> None:
        self.system_message = message


def _run_hook(agent: _FakeAgent, messages: List[Dict[str, Any]] | None = None) -> None:
    inject_shell_preset_context(agent, messages or [])


def test_shell_presets_catalog_exists_and_has_required_presets() -> None:
    assert _SHELL_PRESETS_YAML.exists()

    data = yaml.safe_load(_SHELL_PRESETS_YAML.read_text(encoding="utf-8"))
    presets = data["presets"]

    for preset_id in (
        "product_app",
        "workspace_studio",
        "public_plus_app",
        "conversation_app",
        "flow_app",
    ):
        assert preset_id in presets
        assert presets[preset_id]["select_when"]
        assert presets[preset_id]["chrome_default"]

    assert "Prompt-time" in data["description"] or "prompt-time" in data["description"]


def test_hooks_yaml_registers_shell_preset_hook_for_planning_and_schema_agents() -> None:
    data = yaml.safe_load(_HOOKS_YAML.read_text(encoding="utf-8"))
    hooks = data.get("hooks") or []
    targets = {
        entry.get("hook_agent")
        for entry in hooks
        if entry.get("filename") == "hook_shell_preset_context.py"
        and entry.get("function") == "inject_shell_preset_context"
    }

    assert targets == {"AppPlanAgent", "AppSchemaAgent"}


def test_shell_preset_hook_injects_prompt_time_guidance() -> None:
    _load_shell_presets.cache_clear()

    for agent_name in ("AppPlanAgent", "AppSchemaAgent"):
        agent = _FakeAgent(agent_name)
        _run_hook(agent)

        assert "[SHELL PRESET CONTEXT]" in agent.system_message
        assert "prompt-time guidance only" in agent.system_message
        assert "not runtime artifacts" in agent.system_message
        assert "shell_preset_hint" in agent.system_message
        assert "workspace_studio" in agent.system_message
        assert "AppPageSchema.navigation" in agent.system_message
        assert "Do not emit preset ids into generated app files" in agent.system_message


def test_shell_preset_hook_is_noop_for_unrelated_agents() -> None:
    for agent_name in ("InterviewAgent", "ConfigMiddlewareAgent", "ServiceAgent", ""):
        agent = _FakeAgent(agent_name)
        _run_hook(agent)
        assert agent.system_message == ""


def test_shell_preset_hook_replaces_section_without_dropping_trailing_content() -> None:
    _load_shell_presets.cache_clear()
    agent = _FakeAgent("AppPlanAgent")
    agent.system_message = "[SHELL PRESET CONTEXT]\nold body\n\n[OTHER SECTION]\nother content"

    _run_hook(agent)

    assert agent.system_message.count("[SHELL PRESET CONTEXT]") == 1
    assert "old body" not in agent.system_message
    assert "[OTHER SECTION]" in agent.system_message
    assert "other content" in agent.system_message


def test_shell_preset_hook_injects_warning_when_catalog_missing() -> None:
    agent = _FakeAgent("AppSchemaAgent")

    with patch(
        "factory_app.workflows.AppGenerator.tools.hook_shell_preset_context._load_shell_presets",
        return_value=None,
    ):
        _run_hook(agent)

    assert "[SHELL PRESET CONTEXT]" in agent.system_message
    assert "WARNING: Shell preset catalog could not be loaded" in agent.system_message
    assert "Do not emit shell_preset_hint" in agent.system_message
