"""
OSS prompt hygiene tests.

Verifies that base OSS generator prompts are host-agnostic:

1. hook_universal_prompts.py has no proprietary product names (MozaiksPay, etc.).
2. AppGenerator agents.yaml does not use HOSTED_WALLET_URL as the primary generic example.
3. Generic hosted adapter acceptance criteria do not say "Stripe" in non-wallet-scoped templates.
4. OSS mode: hook_hosted_capabilities_context is a no-op when no hosted context is supplied.
5. When a hosted pack descriptor includes generation_rules, the hook renders them.
6. MozaiksPay does not appear anywhere in OSS default prompts or hooks.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any, Dict, List

import pytest
import yaml

_WORKSPACE = Path(__file__).resolve().parents[1]
_AGENTGEN_TOOLS = _WORKSPACE / "factory_app" / "workflows" / "AgentGenerator" / "tools"
_APPGEN_TOOLS = _WORKSPACE / "factory_app" / "workflows" / "AppGenerator" / "tools"
_APPGEN_DIR = _WORKSPACE / "factory_app" / "workflows" / "AppGenerator"
_UNIVERSAL_PROMPTS_PATH = _AGENTGEN_TOOLS / "hook_universal_prompts.py"
_HOSTED_CAPS_PATH = _APPGEN_TOOLS / "hook_hosted_capabilities_context.py"
_AGENTS_YAML_PATH = _APPGEN_DIR / "agents.yaml"

_PROPRIETARY_NAMES = [
    "MozaiksPay",
    "mozaikspay",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module spec from {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeAgent:
    def __init__(self, name: str, context_variables: Dict[str, Any] | None = None):
        self.name = name
        self.system_message = ""
        self.context_variables = context_variables or {}

    def update_system_message(self, msg: str) -> None:
        self.system_message = msg


# ---------------------------------------------------------------------------
# 1. hook_universal_prompts.py — no proprietary product names
# ---------------------------------------------------------------------------


class TestUniversalPromptsHostAgnostic:
    def _read_source(self) -> str:
        return _UNIVERSAL_PROMPTS_PATH.read_text(encoding="utf-8")

    def test_universal_prompts_file_exists(self) -> None:
        assert _UNIVERSAL_PROMPTS_PATH.exists(), (
            f"hook_universal_prompts.py not found at {_UNIVERSAL_PROMPTS_PATH}"
        )

    @pytest.mark.parametrize("name", _PROPRIETARY_NAMES)
    def test_no_proprietary_product_names_in_source(self, name: str) -> None:
        source = self._read_source()
        assert name not in source, (
            f"Proprietary product name {name!r} found in hook_universal_prompts.py. "
            "Universal prompts must be host-agnostic OSS defaults."
        )

    def test_runtime_context_uses_platform_runtime_label(self) -> None:
        """Token management section should reference 'Platform Runtime', not a product name."""
        source = self._read_source()
        assert "Platform Runtime" in source or "platform runtime" in source, (
            "RUNTIME_CONTEXT token management section should reference 'Platform Runtime' "
            "(generic) after MozaiksPay cleanup."
        )

    def test_runtime_context_injected_into_workflow_design_agent(self) -> None:
        """WorkflowStrategyAgent still receives RUNTIME_CONTEXT after cleanup."""
        mod = _load_module(_UNIVERSAL_PROMPTS_PATH, f"test_universal_prompts.{id(object())}")
        agent = _FakeAgent(
            name="WorkflowStrategyAgent",
            context_variables={},
        )
        mod.inject_universal_prompts(agent, [])
        assert "[MOZAIKS RUNTIME CONTEXT]" in agent.system_message, (
            "WorkflowStrategyAgent should still receive [MOZAIKS RUNTIME CONTEXT] "
            "after the proprietary-name cleanup."
        )

    def test_runtime_context_anti_patterns_still_present(self) -> None:
        """The 'TokenTracker / UsageMonitor' anti-pattern guidance must still exist."""
        mod = _load_module(_UNIVERSAL_PROMPTS_PATH, f"test_universal_prompts_anti.{id(object())}")
        agent = _FakeAgent(name="WorkflowStrategyAgent", context_variables={})
        mod.inject_universal_prompts(agent, [])
        msg = agent.system_message
        assert "TokenTracker" in msg or "UsageMonitor" in msg, (
            "Anti-pattern guidance for token trackers must still be present in RUNTIME_CONTEXT."
        )

    def test_runtime_context_no_mozaikspay_in_injected_message(self) -> None:
        """After injection, the system message must not contain MozaiksPay."""
        mod = _load_module(_UNIVERSAL_PROMPTS_PATH, f"test_universal_prompts_inj.{id(object())}")
        agent = _FakeAgent(name="WorkflowStrategyAgent", context_variables={})
        mod.inject_universal_prompts(agent, [])
        assert "MozaiksPay" not in agent.system_message, (
            "Injected RUNTIME_CONTEXT must not contain 'MozaiksPay'."
        )


# ---------------------------------------------------------------------------
# 2. AppGenerator agents.yaml — generic examples
# ---------------------------------------------------------------------------


class TestAppGeneratorAgentsYamlHostAgnostic:
    def _read_source(self) -> str:
        return _AGENTS_YAML_PATH.read_text(encoding="utf-8")

    def test_agents_yaml_exists(self) -> None:
        assert _AGENTS_YAML_PATH.exists(), f"agents.yaml not found at {_AGENTS_YAML_PATH}"

    def test_hosted_adapter_env_var_example_is_generic(self) -> None:
        """Generic hosted adapter instruction must not use HOSTED_WALLET_URL as primary example."""
        source = self._read_source()
        # Should use generic pattern like HOSTED_{PACK_ID}_URL, not the wallet-specific name
        assert "HOSTED_WALLET_URL" not in source, (
            "agents.yaml still contains HOSTED_WALLET_URL as a generic env var example. "
            "Replace with app_backend_url or HOSTED_{PACK_ID}_URL."
        )

    def test_hosted_adapter_env_var_uses_generic_pattern(self) -> None:
        """The hosted adapter lane guidance should use a generic env var placeholder."""
        source = self._read_source()
        assert "HOSTED_{PACK_ID}_URL" in source or "app_backend_url" in source, (
            "agents.yaml hosted adapter lane should use 'app_backend_url' or "
            "'HOSTED_{PACK_ID}_URL' as the generic env var example."
        )

    def test_generic_hosted_adapter_acceptance_criteria_no_stripe(self) -> None:
        """
        The generic hosted adapter example's acceptance_criteria must not say
        'Does not call Stripe directly'. That is a wallet-specific constraint.
        """
        source = self._read_source()
        # Find the acceptance_criteria block in the generic hosted adapter output example.
        # The wallet-specific example scoped around task_wallet_adapter is acceptable.
        # The generic ControllerAgent prompt template must not have Stripe-specific criteria.

        # Check the full source does not have the old wording in any non-wallet context.
        # We check the exact old text is gone.
        assert '"Does not call Stripe directly"' not in source, (
            'Generic acceptance criteria in agents.yaml still contain '
            '"Does not call Stripe directly". Replace with a generic constraint.'
        )

    def test_generic_acceptance_criteria_uses_provider_agnostic_wording(self) -> None:
        """Replacement acceptance criterion should be provider-agnostic."""
        source = self._read_source()
        assert (
            "Does not call the hosted provider" in source
            or "Does not call third-party" in source
        ), (
            "agents.yaml should contain a provider-agnostic acceptance criterion like "
            "'Does not call the hosted provider\\'s private APIs directly'."
        )


# ---------------------------------------------------------------------------
# 3. hook_hosted_capabilities_context.py — OSS no-op + generation_rules rendering
# ---------------------------------------------------------------------------


class TestHostedCapabilitiesHook:
    def _load_hook(self):
        return _load_module(_HOSTED_CAPS_PATH, f"test_hosted_caps.{id(object())}")

    def test_hook_is_noop_in_oss_mode(self) -> None:
        """When no hosted context is supplied, the hook must not modify system_message."""
        mod = self._load_hook()
        agent = _FakeAgent(name="AppPlanAgent", context_variables={})
        agent.system_message = "base prompt"
        mod.inject_hosted_capabilities_context(agent, [])
        assert agent.system_message == "base prompt", (
            "inject_hosted_capabilities_context must be a no-op in OSS mode "
            "(no runtime_capabilities, no available_hosted_packs, no pack_sources)."
        )

    def test_hook_injects_when_hosted_packs_present(self) -> None:
        """When hosted packs are supplied, the hook must inject [HOSTED CAPABILITIES CONTEXT]."""
        mod = self._load_hook()
        agent = _FakeAgent(
            name="AppPlanAgent",
            context_variables={
                "available_hosted_packs": [
                    {"id": "wallet", "label": "Wallet", "capability_source": "hosted_pack"}
                ]
            },
        )
        mod.inject_hosted_capabilities_context(agent, [])
        assert "[HOSTED CAPABILITIES CONTEXT]" in agent.system_message, (
            "inject_hosted_capabilities_context must inject the context block when "
            "available_hosted_packs is non-empty."
        )

    def test_generation_rules_rendered_when_supplied(self) -> None:
        """
        If a hosted pack descriptor includes generation_rules, the hook must render them
        into the injected context block.
        """
        mod = self._load_hook()
        agent = _FakeAgent(
            name="AppPlanAgent",
            context_variables={
                "available_hosted_packs": [
                    {
                        "id": "testpay",
                        "label": "TestPay",
                        "capability_source": "hosted_pack",
                        "generation_rules": [
                            "Do not generate token tracking modules.",
                            "Do not generate payment rails.",
                        ],
                    }
                ]
            },
        )
        mod.inject_hosted_capabilities_context(agent, [])
        msg = agent.system_message
        assert "Do not generate token tracking modules." in msg, (
            "generation_rules supplied by host pack descriptor must be rendered into "
            "the injected context block."
        )
        assert "Do not generate payment rails." in msg

    def test_generation_rules_absent_when_not_supplied(self) -> None:
        """When no pack supplies generation_rules, the rules block must not appear."""
        mod = self._load_hook()
        agent = _FakeAgent(
            name="AppPlanAgent",
            context_variables={
                "available_hosted_packs": [
                    {"id": "wallet", "label": "Wallet", "capability_source": "hosted_pack"}
                ]
            },
        )
        mod.inject_hosted_capabilities_context(agent, [])
        assert "Host-provided generation rules" not in agent.system_message, (
            "The generation rules block must not appear when no pack provides rules."
        )

    def test_generation_rules_only_from_host_not_oss_defaults(self) -> None:
        """
        The base hook code must not contain any hardcoded MozaiksPay generation rules.
        Rules are always supplied by the host at runtime via pack descriptors.
        """
        source = _HOSTED_CAPS_PATH.read_text(encoding="utf-8")
        for name in _PROPRIETARY_NAMES:
            assert name not in source, (
                f"Proprietary product name {name!r} found in hook_hosted_capabilities_context.py. "
                "Host-specific rules must be supplied at runtime via pack descriptors, "
                "not hardcoded in OSS base hooks."
            )

    def test_hook_ignores_non_appplanagent(self) -> None:
        """Hook must be a no-op for agents that are not AppPlanAgent."""
        mod = self._load_hook()
        agent = _FakeAgent(
            name="ConfigMiddlewareAgent",
            context_variables={
                "available_hosted_packs": [
                    {"id": "wallet", "label": "Wallet"}
                ]
            },
        )
        agent.system_message = "original"
        mod.inject_hosted_capabilities_context(agent, [])
        assert agent.system_message == "original", (
            "inject_hosted_capabilities_context must only modify AppPlanAgent."
        )


# ---------------------------------------------------------------------------
# 4. Cross-file: MozaiksPay not in any OSS default prompt file
# ---------------------------------------------------------------------------


class TestNoProprietaryNamesInOssDefaults:
    _OSS_PROMPT_FILES = [
        _UNIVERSAL_PROMPTS_PATH,
        _HOSTED_CAPS_PATH,
        _APPGEN_TOOLS / "hook_file_contract_context.py",
    ]

    @pytest.mark.parametrize("file_path", _OSS_PROMPT_FILES)
    def test_no_mozaikspay_in_file(self, file_path: Path) -> None:
        if not file_path.exists():
            pytest.skip(f"{file_path.name} not found")
        source = file_path.read_text(encoding="utf-8")
        for name in _PROPRIETARY_NAMES:
            assert name not in source, (
                f"Proprietary name {name!r} found in OSS default file {file_path.name}. "
                "OSS base prompts must be host-agnostic."
            )


# ---------------------------------------------------------------------------
# 5. Backend helper governance: ServiceAgent cannot invent helpers
# ---------------------------------------------------------------------------


class TestBackendHelperGovernanceInAgents:
    """Verify backend helper file governance is enforced in agents.yaml."""

    def _read_agents(self) -> str:
        return _AGENTS_YAML_PATH.read_text(encoding="utf-8")

    def test_service_agent_prohibits_inventing_helpers(self) -> None:
        """ServiceAgent guidance must explicitly prohibit inventing helper files."""
        source = self._read_agents()
        assert "Do not invent" in source or "do not invent" in source, (
            "ServiceAgent section must contain prohibition on inventing helper files."
        )

    def test_service_agent_requires_helper_declaration(self) -> None:
        """ServiceAgent must require helper files to be declared before generation."""
        source = self._read_agents()
        assert "helper" in source.lower()
        assert "declared" in source.lower() or "python_stubs" in source.lower(), (
            "ServiceAgent must require that helper files be declared in python_stubs."
        )

    def test_app_plan_agent_requires_helper_rationale(self) -> None:
        """AppPlanAgent must require that helper files include a rationale."""
        source = self._read_agents()
        assert "helper" in source.lower()
        assert "rationale" in source.lower() or "justified" in source.lower(), (
            "AppPlanAgent must require a rationale or justification for helper files."
        )

    def test_canonical_backend_layers_unchanged(self) -> None:
        """All five canonical backend layers must be mentioned in agents.yaml."""
        source = self._read_agents()
        layers = [
            "backend/handler.py",
            "backend/service.py",
            "backend/repo.py",
            "backend/policy.py",
            "backend/schemas.py",
        ]
        for layer in layers:
            assert layer in source, (
                f"agents.yaml must mention canonical layer: {layer}"
            )

    def test_handler_described_as_thin_dispatch(self) -> None:
        """handler.py must be described as thin dispatch, not a business logic layer."""
        source = self._read_agents()
        assert "thin" in source.lower(), (
            "agents.yaml must describe handler.py as 'thin' dispatch."
        )
        assert "dispatch" in source.lower() or "adapter" in source.lower(), (
            "agents.yaml must describe handler.py as dispatch or adapter layer."
        )

    def test_service_described_as_business_logic(self) -> None:
        """service.py must own all business logic."""
        source = self._read_agents()
        assert "business logic" in source.lower(), (
            "agents.yaml must assign business logic responsibility to service.py."
        )

    def test_repo_described_as_persistence_only(self) -> None:
        """repo.py must be described as persistence layer, not logic."""
        source = self._read_agents()
        assert "persistence" in source.lower() or "database" in source.lower(), (
            "agents.yaml must describe repo.py as persistence layer."
        )

    def test_policy_described_as_authorization_scoping(self) -> None:
        """policy.py must own authorization and scoping."""
        source = self._read_agents()
        assert (
            "authz" in source.lower()
            or "authorization" in source.lower()
            or "ownership" in source.lower()
        ), (
            "agents.yaml must assign authorization/ownership to policy.py."
        )


# ---------------------------------------------------------------------------
# 6. Provider-neutral helper examples
# ---------------------------------------------------------------------------


class TestProviderNeutralHelperExamples:
    """Verify helper file examples use provider-neutral naming."""

    def _read_agents(self) -> str:
        return _AGENTS_YAML_PATH.read_text(encoding="utf-8")

    def _read_file_contracts(self) -> dict:
        file_contracts_path = _APPGEN_TOOLS / "file_contracts.yaml"
        with file_contracts_path.open(encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def test_helper_examples_no_stripe_reference(self) -> None:
        """Helper file examples must not reference Stripe."""
        contracts = self._read_file_contracts()
        examples = contracts.get("backend_helper_files", {}).get("examples", [])
        examples_str = " ".join(str(e) for e in examples)
        assert "stripe" not in examples_str.lower(), (
            "Helper file examples must be provider-neutral. Found Stripe reference."
        )

    def test_helper_examples_use_generic_provider_client(self) -> None:
        """Helper examples should have a generic 'provider_client.py' example."""
        contracts = self._read_file_contracts()
        examples = contracts.get("backend_helper_files", {}).get("examples", [])
        examples_str = " ".join(str(e) for e in examples)
        assert "provider_client.py" in examples_str or "provider_client" in examples_str, (
            "Helper examples should include generic 'provider_client.py' pattern."
        )

    def test_routes_example_uses_generic_naming(self) -> None:
        """Routes helper should use 'routes_hooks.py', not 'routes_webhooks.py'."""
        contracts = self._read_file_contracts()
        examples = contracts.get("backend_helper_files", {}).get("examples", [])
        examples_str = " ".join(str(e) for e in examples)
        # Should have generic routes example
        assert "routes_" in examples_str, (
            "Helper examples should include a routes_* pattern."
        )
        # Should not be Stripe-specific
        assert "stripe_webhook" not in examples_str.lower(), (
            "Routes example must be provider-neutral, not Stripe-specific."
        )

    def test_worker_example_uses_generic_naming(self) -> None:
        """Worker/service helper should use generic naming like 'event_worker.py'."""
        contracts = self._read_file_contracts()
        examples = contracts.get("backend_helper_files", {}).get("examples", [])
        examples_str = " ".join(str(e) for e in examples)
        assert "worker" in examples_str.lower() or "service" in examples_str.lower(), (
            "Helper examples should include a generic worker or service pattern."
        )
