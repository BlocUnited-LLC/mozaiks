"""
Tests for hook_capability_routing_context.py

Verifies:
- middleware.yaml registers the hook on AppPlanAgent
- inject_capability_routing_context appends [CAPABILITY ROUTING CONTEXT] to the agent message
- The block surfaces all four routing layers
- The block lists known capability packs from capability_routing.yaml
- The hook is a no-op for non-AppPlanAgent agents
- The hook is a no-op when capability_routing.yaml cannot be loaded
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_APPGEN_DIR = (
    Path(__file__).parent.parent
    / "factory_app"
    / "workflows"
    / "AppGenerator"
)
_APPGEN_CATALOG_DIR = (
    Path(__file__).parent.parent
    / "factory_app"
    / "build_context"
    / "AppGenerator"
)
_HOOKS_YAML = _APPGEN_DIR / "middleware.yaml"
_ROUTING_YAML = _APPGEN_CATALOG_DIR / "capability_routing.yaml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeAgent:
    def __init__(self, name: str, context_variables: dict[str, Any] | None = None):
        self.name = name
        self.system_message = ""
        self.context_variables = context_variables or {}

    def update_system_message(self, message: str) -> None:
        self.system_message = message


def _run_hook(agent: _FakeAgent, messages: list[dict] | None = None) -> None:
    from factory_app.workflows.AppGenerator.tools.hook_capability_routing_context import (
        inject_capability_routing_context,
    )
    inject_capability_routing_context(agent, messages or [])


# ---------------------------------------------------------------------------
# middleware.yaml registration
# ---------------------------------------------------------------------------

class TestHooksYamlRegistration:
    def test_hooks_yaml_contains_capability_routing_hook(self) -> None:
        assert _HOOKS_YAML.exists()
        data = yaml.safe_load(_HOOKS_YAML.read_text(encoding="utf-8"))
        hooks = data.get("prompt_middleware") or []
        matching = [
            h for h in hooks
            if h.get("filename") == "hook_capability_routing_context.py"
            and h.get("function") == "inject_capability_routing_context"
        ]
        assert matching, (
            "middleware.yaml must register inject_capability_routing_context "
            "from hook_capability_routing_context.py"
        )

    def test_capability_routing_hook_targets_appplanagent(self) -> None:
        data = yaml.safe_load(_HOOKS_YAML.read_text(encoding="utf-8"))
        hooks = data.get("prompt_middleware") or []
        for h in hooks:
            if h.get("filename") == "hook_capability_routing_context.py":
                assert h.get("agent") == "AppPlanAgent", (
                    "inject_capability_routing_context must target AppPlanAgent"
                )
                break


# ---------------------------------------------------------------------------
# capability_routing.yaml existence and shape
# ---------------------------------------------------------------------------

class TestCapabilityRoutingYaml:
    def test_capability_routing_yaml_exists(self) -> None:
        assert _ROUTING_YAML.exists(), f"capability_routing.yaml not found at {_ROUTING_YAML}"

    def test_capability_routing_yaml_has_four_layers(self) -> None:
        data = yaml.safe_load(_ROUTING_YAML.read_text(encoding="utf-8"))
        layers = data.get("layers") or {}
        for expected in ("runtime_provided", "ai_workflow", "capability_pack", "custom_owned"):
            assert expected in layers, f"capability_routing.yaml missing layer: {expected}"

    def test_capability_routing_yaml_packs_have_required_fields(self) -> None:
        data = yaml.safe_load(_ROUTING_YAML.read_text(encoding="utf-8"))
        packs = (data.get("layers") or {}).get("capability_pack", {}).get("packs") or []
        assert packs, "capability_routing.yaml capability_pack layer must list at least one pack"
        for pack in packs:
            assert "id" in pack, f"pack missing 'id': {pack}"
            assert "capability_kind" in pack, f"pack {pack.get('id')} missing 'capability_kind'"

    def test_capability_routing_yaml_includes_operator_packs(self) -> None:
        data = yaml.safe_load(_ROUTING_YAML.read_text(encoding="utf-8"))
        packs = (data.get("layers") or {}).get("capability_pack", {}).get("packs") or []
        ids = [p.get("id") for p in packs if isinstance(p, dict)]
        assert "messaging" in ids
        assert "files" in ids


# ---------------------------------------------------------------------------
# Hook injection behaviour
# ---------------------------------------------------------------------------

class TestCapabilityRoutingHook:
    def test_injects_routing_header_into_appplanagent(self) -> None:
        agent = _FakeAgent("AppPlanAgent")
        _run_hook(agent)
        assert "[CAPABILITY ROUTING CONTEXT]" in agent.system_message

    def test_routing_block_contains_all_four_layers(self) -> None:
        agent = _FakeAgent("AppPlanAgent")
        _run_hook(agent)
        for layer in ("runtime_provided", "ai_workflow", "capability_pack", "custom_owned"):
            assert layer in agent.system_message, (
                f"[CAPABILITY ROUTING CONTEXT] must mention layer: {layer}"
            )

    def test_routing_block_lists_known_packs(self) -> None:
        agent = _FakeAgent("AppPlanAgent")
        _run_hook(agent)
        # At minimum messaging and files should appear.
        assert "files" in agent.system_message
        assert "messaging" in agent.system_message

    def test_routing_block_contains_decision_order(self) -> None:
        agent = _FakeAgent("AppPlanAgent")
        _run_hook(agent)
        assert "Decision order" in agent.system_message or "decision order" in agent.system_message.lower()

    def test_hook_is_noop_for_wrong_agent_name(self) -> None:
        for name in ("InterviewAgent", "ConfigMiddlewareAgent", "AppSchemaAgent", ""):
            agent = _FakeAgent(name)
            _run_hook(agent)
            assert agent.system_message == "", (
                f"Hook must not modify system_message for agent: {name!r}"
            )

    def test_hook_is_noop_when_yaml_missing(self) -> None:
        agent = _FakeAgent("AppPlanAgent")
        with patch(
            "factory_app.workflows.AppGenerator.tools.hook_capability_routing_context._load_routing",
            return_value=None,
        ):
            _run_hook(agent)
        assert agent.system_message == ""

    def test_repeated_call_replaces_section_not_appends(self) -> None:
        agent = _FakeAgent("AppPlanAgent")
        _run_hook(agent)
        _run_hook(agent)
        count = agent.system_message.count("[CAPABILITY ROUTING CONTEXT]")
        assert count == 1, (
            f"Repeated hook call must replace the section, not append. Got {count} occurrences."
        )

    def test_routing_block_includes_use_when_avoid_when_for_packs(self) -> None:
        agent = _FakeAgent("AppPlanAgent")
        _run_hook(agent)
        assert "use_when" in agent.system_message, (
            "Pack entries must include use_when guidance"
        )
        assert "avoid_when" in agent.system_message, (
            "Pack entries must include avoid_when guidance"
        )

    def test_routing_block_includes_operator_pack_note(self) -> None:
        agent = _FakeAgent("AppPlanAgent")
        _run_hook(agent)
        assert "Operator capability packs" in agent.system_message, (
            "Operator pack guidance must appear in the routing block"
        )

    def test_routing_block_preserves_trailing_sections(self) -> None:
        """Replacing the routing section must not drop content that follows it."""
        agent = _FakeAgent("AppPlanAgent")
        agent.system_message = "[CAPABILITY ROUTING CONTEXT]\nold body\n\n[OTHER SECTION]\nother content"
        _run_hook(agent)
        assert "[OTHER SECTION]" in agent.system_message, (
            "Replacing [CAPABILITY ROUTING CONTEXT] must preserve subsequent sections"
        )
        assert "other content" in agent.system_message

    def test_load_routing_is_cached(self) -> None:
        from factory_app.workflows.AppGenerator.tools.hook_capability_routing_context import (
            _load_routing,
        )
        _load_routing.cache_clear()
        result1 = _load_routing()
        result2 = _load_routing()
        assert result1 is result2, "_load_routing must return the same object on repeated calls (cached)"


