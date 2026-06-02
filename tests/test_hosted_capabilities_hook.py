"""Tests for hook_hosted_capabilities_context.py."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

import pytest
import yaml

_APPGEN_DIR = (
    Path(__file__).parent.parent
    / "factory_app"
    / "workflows"
    / "AppGenerator"
)
_HOOKS_YAML = _APPGEN_DIR / "hooks.yaml"
_TOOLS_DIR = _APPGEN_DIR / "tools"
_CONTEXT_VARS_PATH = _APPGEN_DIR / "context_variables.yaml"
_STRUCTURED_OUTPUTS_PATH = _APPGEN_DIR / "structured_outputs.yaml"


class _FakeAgent:
    def __init__(self, name: str, context_variables: Dict[str, Any] | None = None):
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
    if "hook_hosted_capabilities_context" in sys.modules:
        del sys.modules["hook_hosted_capabilities_context"]
    import hook_hosted_capabilities_context as m
    return m


@pytest.fixture()
def hook(_import_hook):
    return _import_hook


# ---------------------------------------------------------------------------
# OSS no-op behaviour
# ---------------------------------------------------------------------------

class TestOSSNoOp:
    def test_noop_when_all_null(self, hook):
        agent = _FakeAgent("AppPlanAgent", context_variables={
            "runtime_capabilities": None,
            "available_hosted_packs": None,
            "hosted_capability_selection": None,
            "pack_sources": None,
        })
        hook.inject_hosted_capabilities_context(agent, [])
        assert agent.system_message == ""

    def test_noop_when_all_empty_lists(self, hook):
        agent = _FakeAgent("AppPlanAgent", context_variables={
            "runtime_capabilities": [],
            "available_hosted_packs": [],
            "hosted_capability_selection": {},
            "pack_sources": [],
        })
        hook.inject_hosted_capabilities_context(agent, [])
        assert agent.system_message == ""

    def test_noop_when_context_vars_absent(self, hook):
        agent = _FakeAgent("AppPlanAgent", context_variables={})
        hook.inject_hosted_capabilities_context(agent, [])
        assert agent.system_message == ""

    def test_noop_for_wrong_agent(self, hook):
        agent = _FakeAgent("InterviewAgent", context_variables={
            "runtime_capabilities": ["wallet", "investor_marketplace"],
        })
        hook.inject_hosted_capabilities_context(agent, [])
        assert agent.system_message == ""

    def test_oss_default_context_vars_are_null(self):
        data = yaml.safe_load(_CONTEXT_VARS_PATH.read_text(encoding="utf-8"))
        defs = data["definitions"]
        for key in [
            "runtime_capabilities",
            "available_hosted_packs",
            "hosted_capability_selection",
            "pack_sources",
        ]:
            assert key in defs, f"Missing definition: {key}"
            default = defs[key].get("source", {}).get("default")
            assert default is None, f"{key} default should be null, got {default!r}"

    def test_oss_default_context_does_not_mention_mozaikspay_or_hosted_packs(self):
        # When running in OSS mode the injected context must be empty — no
        # mention of proprietary hosted packs.
        agent = _FakeAgent("AppPlanAgent", context_variables={})
        import hook_hosted_capabilities_context as m
        m.inject_hosted_capabilities_context(agent, [])
        msg = agent.system_message.lower()
        assert "mozaikspay" not in msg
        assert "hosted_entitlements" not in msg
        assert "wallet" not in msg
        assert "investor_marketplace" not in msg


# ---------------------------------------------------------------------------
# Hosted mode injection
# ---------------------------------------------------------------------------

class TestHostedInjection:
    def test_injects_header_when_runtime_capabilities_present(self, hook):
        agent = _FakeAgent("AppPlanAgent", context_variables={
            "runtime_capabilities": ["module_execution", "hosted_wallet"],
            "available_hosted_packs": None,
            "pack_sources": None,
        })
        hook.inject_hosted_capabilities_context(agent, [])
        assert "[HOSTED CAPABILITIES CONTEXT]" in agent.system_message
        assert "hosted_wallet" in agent.system_message

    def test_injects_hosted_packs_as_string_list(self, hook):
        agent = _FakeAgent("AppPlanAgent", context_variables={
            "runtime_capabilities": None,
            "available_hosted_packs": ["wallet", "investor_marketplace"],
            "pack_sources": None,
        })
        hook.inject_hosted_capabilities_context(agent, [])
        msg = agent.system_message
        assert "[HOSTED CAPABILITIES CONTEXT]" in msg
        assert "wallet" in msg
        assert "investor_marketplace" in msg

    def test_injects_hosted_packs_as_dict_list(self, hook):
        agent = _FakeAgent("AppPlanAgent", context_variables={
            "runtime_capabilities": None,
            "available_hosted_packs": [
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
            "pack_sources": None,
        })
        hook.inject_hosted_capabilities_context(agent, [])
        msg = agent.system_message
        assert "wallet" in msg
        assert "investor_marketplace" in msg
        assert "wallet.view" in msg

    def test_injects_pack_sources(self, hook):
        agent = _FakeAgent("AppPlanAgent", context_variables={
            "runtime_capabilities": None,
            "available_hosted_packs": None,
            "pack_sources": [
                {
                    "id": "mozaiks_app_hosted",
                    "kind": "filesystem",
                    "path": "app_generator/capability_packs",
                    "capability_source": "hosted_pack",
                }
            ],
        })
        hook.inject_hosted_capabilities_context(agent, [])
        msg = agent.system_message
        assert "mozaiks_app_hosted" in msg
        assert "hosted_pack" in msg
        assert "planning context only" in msg.lower()

    def test_injects_host_selected_capability_intent(self, hook):
        agent = _FakeAgent("AppPlanAgent", context_variables={
            "runtime_capabilities": None,
            "available_hosted_packs": None,
            "hosted_capability_selection": {
                "intent_id": "subscription_revenue",
                "pack_id": "hosted_checkout",
                "surfaces": ["checkout", "billing"],
                "source": "builder",
            },
            "pack_sources": None,
        })
        hook.inject_hosted_capabilities_context(agent, [])
        msg = agent.system_message
        assert "[HOSTED CAPABILITIES CONTEXT]" in msg
        assert "Host-selected capability intent" in msg
        assert "subscription_revenue" in msg
        assert "hosted_checkout" in msg
        assert "checkout, billing" in msg

    def test_taxonomy_guidance_always_present_when_injected(self, hook):
        agent = _FakeAgent("AppPlanAgent", context_variables={
            "runtime_capabilities": ["module_execution"],
        })
        hook.inject_hosted_capabilities_context(agent, [])
        msg = agent.system_message
        assert "host_universal" in msg
        assert "framework_pack" in msg
        assert "hosted_pack" in msg
        assert "generated_module" in msg
        assert "external_adapter" in msg

    def test_hosted_pack_planning_rule_injected(self, hook):
        agent = _FakeAgent("AppPlanAgent", context_variables={
            "available_hosted_packs": ["wallet"],
        })
        hook.inject_hosted_capabilities_context(agent, [])
        msg = agent.system_message
        assert "module_contract" in msg
        assert "external_integration" in msg

    def test_is_idempotent(self, hook):
        agent = _FakeAgent("AppPlanAgent", context_variables={
            "runtime_capabilities": ["hosted_wallet"],
        })
        hook.inject_hosted_capabilities_context(agent, [])
        hook.inject_hosted_capabilities_context(agent, [])
        assert agent.system_message.count("[HOSTED CAPABILITIES CONTEXT]") == 1


# ---------------------------------------------------------------------------
# hooks.yaml registration
# ---------------------------------------------------------------------------

class TestHooksYamlRegistration:
    def test_hook_registered_for_appplanagent(self):
        data = yaml.safe_load(_HOOKS_YAML.read_text(encoding="utf-8"))
        hooks = data["hooks"]
        match = [
            h for h in hooks
            if h.get("hook_agent") == "AppPlanAgent"
            and h.get("filename") == "hook_hosted_capabilities_context.py"
            and h.get("function") == "inject_hosted_capabilities_context"
        ]
        assert len(match) == 1, "hook_hosted_capabilities_context not registered for AppPlanAgent"

    def test_hook_not_registered_for_other_agents(self):
        data = yaml.safe_load(_HOOKS_YAML.read_text(encoding="utf-8"))
        hooks = data["hooks"]
        others = [
            h for h in hooks
            if h.get("filename") == "hook_hosted_capabilities_context.py"
            and h.get("hook_agent") != "AppPlanAgent"
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

    def test_appplanagent_variables_include_hosted_context_keys(self):
        data = yaml.safe_load(_CONTEXT_VARS_PATH.read_text(encoding="utf-8"))
        agents = data.get("agents", {})
        plan_vars = agents.get("AppPlanAgent", {}).get("variables", [])
        for key in [
            "runtime_capabilities",
            "available_hosted_packs",
            "hosted_capability_selection",
            "pack_sources",
        ]:
            assert key in plan_vars, f"AppPlanAgent missing variable: {key}"
