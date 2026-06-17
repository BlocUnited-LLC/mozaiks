"""Tests for hook_managed_capabilities_context.py."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

_APPGEN_DIR = (
    Path(__file__).parent.parent
    / "factory_app"
    / "workflows"
    / "AppGenerator"
)
_HOOKS_YAML = _APPGEN_DIR / "middleware.yaml"
_TOOLS_DIR = _APPGEN_DIR / "tools"
_CONTEXT_VARS_PATH = _APPGEN_DIR / "context_variables.yaml"
_STRUCTURED_OUTPUTS_PATH = _APPGEN_DIR / "structured_outputs.yaml"


class _FakeAgent:
    def __init__(self, name: str, context_variables: dict[str, Any] | None = None):
        self.name = name
        self.system_message = ""
        self.context_variables = context_variables or {}

    def update_system_message(self, message: str) -> None:
        self.system_message = message


@pytest.fixture(autouse=True)
def _import_hook():
    tools_path = str(_TOOLS_DIR)
    if tools_path not in sys.path:
        sys.path.insert(0, tools_path)
    if "hook_managed_capabilities_context" in sys.modules:
        del sys.modules["hook_managed_capabilities_context"]
    import hook_managed_capabilities_context as m
    return m


@pytest.fixture()
def hook(_import_hook):
    return _import_hook


# ---------------------------------------------------------------------------
# OSS no-op behaviour
# ---------------------------------------------------------------------------

class TestOSSNoOp:
    def test_noop_when_null(self, hook):
        agent = _FakeAgent("AppPlanAgent", context_variables={
            "capability_packs": None,
        })
        hook.inject_managed_capabilities_context(agent, [])
        assert agent.system_message == ""

    def test_noop_when_empty_list(self, hook):
        agent = _FakeAgent("AppPlanAgent", context_variables={
            "capability_packs": [],
        })
        hook.inject_managed_capabilities_context(agent, [])
        assert agent.system_message == ""

    def test_noop_when_context_vars_absent(self, hook):
        agent = _FakeAgent("AppPlanAgent", context_variables={})
        hook.inject_managed_capabilities_context(agent, [])
        assert agent.system_message == ""

    def test_noop_for_wrong_agent(self, hook):
        agent = _FakeAgent("InterviewAgent", context_variables={
            "capability_packs": [{"id": "wallet"}],
        })
        hook.inject_managed_capabilities_context(agent, [])
        assert agent.system_message == ""

    def test_oss_default_context_vars_are_empty(self):
        data = yaml.safe_load(_CONTEXT_VARS_PATH.read_text(encoding="utf-8"))
        defs = data["definitions"]
        assert "capability_packs" in defs, "Missing definition: capability_packs"
        default = defs["capability_packs"].get("source", {}).get("default")
        assert default in (None, []), (
            f"capability_packs default should be null or [], got {default!r}"
        )

    def test_oss_default_context_does_not_mention_mozaikspay_or_managed_capabilities(self):
        # When running in OSS mode the injected context must be empty — no
        # mention of proprietary managed capabilities.
        agent = _FakeAgent("AppPlanAgent", context_variables={})
        import hook_managed_capabilities_context as m
        m.inject_managed_capabilities_context(agent, [])
        msg = agent.system_message.lower()
        assert "mozaikspay" not in msg
        assert "managed_entitlements" not in msg
        assert "wallet" not in msg
        assert "investor_marketplace" not in msg


# ---------------------------------------------------------------------------
# Hosted mode injection
# ---------------------------------------------------------------------------

class TestHostedInjection:
    def test_injects_header_when_capability_packs_present(self, hook):
        agent = _FakeAgent("AppPlanAgent", context_variables={
            "capability_packs": [
                {"id": "managed_wallet", "display_name": "Managed Wallet"},
            ],
        })
        hook.inject_managed_capabilities_context(agent, [])
        assert "[MANAGED CAPABILITIES CONTEXT]" in agent.system_message
        assert "managed_wallet" in agent.system_message

    def test_injects_packs_as_string_list(self, hook):
        agent = _FakeAgent("AppPlanAgent", context_variables={
            "capability_packs": ["wallet", "investor_marketplace"],
        })
        hook.inject_managed_capabilities_context(agent, [])
        msg = agent.system_message
        assert "[MANAGED CAPABILITIES CONTEXT]" in msg
        assert "wallet" in msg
        assert "investor_marketplace" in msg

    def test_injects_packs_as_dict_list(self, hook):
        agent = _FakeAgent("AppPlanAgent", context_variables={
            "capability_packs": [
                {
                    "id": "wallet",
                    "display_name": "Wallet",
                    "description": "Hosted wallet for payouts.",
                    "capabilities": [
                        {"capability_id": "wallet.view"},
                        {"capability_id": "wallet.payout"},
                    ],
                },
                {
                    "id": "investor_marketplace",
                    "display_name": "Investor Marketplace",
                },
            ],
        })
        hook.inject_managed_capabilities_context(agent, [])
        msg = agent.system_message
        assert "wallet" in msg
        assert "investor_marketplace" in msg
        assert "wallet.view" in msg

    def test_pack_source_path_does_not_appear_in_injected_context(self, hook):
        """pack_source_path is an internal resolver field — not exposed to the agent."""
        agent = _FakeAgent("AppPlanAgent", context_variables={
            "capability_packs": [
                {
                    "id": "wallet",
                    "display_name": "Wallet",
                    "capability_source": "managed_capability",
                    "pack_source_path": "/build_context/wallet",
                }
            ],
        })
        hook.inject_managed_capabilities_context(agent, [])
        msg = agent.system_message
        assert "[MANAGED CAPABILITIES CONTEXT]" in msg
        assert "wallet" in msg
        # Internal resolver path is not surfaced in the agent prompt
        assert "/build_context/packs" not in msg

    def test_taxonomy_guidance_always_present_when_injected(self, hook):
        agent = _FakeAgent("AppPlanAgent", context_variables={
            "capability_packs": [{"id": "module_execution"}],
        })
        hook.inject_managed_capabilities_context(agent, [])
        msg = agent.system_message
        assert "host_universal" in msg
        assert "framework_pack" in msg
        assert "managed_capability" in msg
        assert "generated_module" in msg
        assert "external_adapter" in msg

    def test_managed_capability_planning_rule_injected(self, hook):
        agent = _FakeAgent("AppPlanAgent", context_variables={
            "capability_packs": [{"id": "wallet"}],
        })
        hook.inject_managed_capabilities_context(agent, [])
        msg = agent.system_message
        assert "module_contract" in msg
        assert "external_integration" in msg

    def test_is_idempotent(self, hook):
        agent = _FakeAgent("AppPlanAgent", context_variables={
            "capability_packs": [{"id": "managed_wallet"}],
        })
        hook.inject_managed_capabilities_context(agent, [])
        hook.inject_managed_capabilities_context(agent, [])
        assert agent.system_message.count("[MANAGED CAPABILITIES CONTEXT]") == 1


# ---------------------------------------------------------------------------
# middleware.yaml registration
# ---------------------------------------------------------------------------

class TestHooksYamlRegistration:
    def test_hook_registered_for_appplanagent(self):
        data = yaml.safe_load(_HOOKS_YAML.read_text(encoding="utf-8"))
        hooks = data["prompt_middleware"]
        match = [
            h for h in hooks
            if h.get("agent") == "AppPlanAgent"
            and h.get("filename") == "hook_managed_capabilities_context.py"
            and h.get("function") == "inject_managed_capabilities_context"
        ]
        assert len(match) == 1, "hook_managed_capabilities_context not registered for AppPlanAgent"

    def test_hook_not_registered_for_other_agents(self):
        data = yaml.safe_load(_HOOKS_YAML.read_text(encoding="utf-8"))
        hooks = data["prompt_middleware"]
        others = [
            h for h in hooks
            if h.get("filename") == "hook_managed_capabilities_context.py"
            and h.get("agent") != "AppPlanAgent"
        ]
        assert others == [], f"Hook should only fire for AppPlanAgent, found: {others}"


# ---------------------------------------------------------------------------
# Structured output contract
# ---------------------------------------------------------------------------

class TestStructuredOutputContract:
    def test_appcapabilitypack_has_capability_source_field(self):
        data = yaml.safe_load(_STRUCTURED_OUTPUTS_PATH.read_text(encoding="utf-8"))
        models = data.get("models", {})
        pack_model = models.get("AppCapabilityPack", {})
        fields = pack_model.get("fields", {})
        assert "capability_source" in fields, "AppCapabilityPack missing capability_source field"

    def test_capability_source_is_optional(self):
        data = yaml.safe_load(_STRUCTURED_OUTPUTS_PATH.read_text(encoding="utf-8"))
        models = data.get("models", {})
        pack_model = models.get("AppCapabilityPack", {})
        fields = pack_model.get("fields", {})
        cs = fields.get("capability_source", {})
        assert cs.get("type") in ("optional_str", "str", "optional"), \
            f"capability_source should be optional, got type={cs.get('type')!r}"

    def test_appplanagent_variables_include_capability_packs(self):
        data = yaml.safe_load(_CONTEXT_VARS_PATH.read_text(encoding="utf-8"))
        agents = data.get("agents", {})
        plan_vars = agents.get("AppPlanAgent", {}).get("variables", [])
        assert "capability_packs" in plan_vars, "AppPlanAgent missing variable: capability_packs"


