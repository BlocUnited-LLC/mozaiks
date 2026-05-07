"""
Tests for hook_domain_catalog_context.py

Verifies:
- hooks.yaml loads and contains the two new hook entries
- inject_domain_catalog_context appends [DOMAIN CATALOG CONTEXT] to the agent system message
- inject_module_file_manifest_guard appends [MODULE FILE MANIFEST GUARD] for module_contract tasks
- Both hooks exit silently for wrong agent names
- inject_module_file_manifest_guard exits silently for non-module_contract task types
"""
from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest
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
_HOOKS_YAML = _APPGEN_DIR / "hooks.yaml"
_CATALOG_PATH = _APPGEN_DIR / "tools" / "domain_catalogs.yaml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeAgent:
    """Minimal agent stub matching the interface the hooks expect."""

    def __init__(self, name: str, context_variables: Dict[str, Any] | None = None):
        self.name = name
        self.system_message = ""
        self.context_variables = context_variables or {}
        self._update_calls: List[str] = []

    def update_system_message(self, message: str) -> None:
        self.system_message = message
        self._update_calls.append(message)


# ---------------------------------------------------------------------------
# hooks.yaml tests
# ---------------------------------------------------------------------------

class TestHooksYaml:
    def test_hooks_yaml_loads(self):
        assert _HOOKS_YAML.exists(), f"hooks.yaml not found at {_HOOKS_YAML}"
        with open(_HOOKS_YAML, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        assert isinstance(data, dict)
        assert "hooks" in data
        assert isinstance(data["hooks"], list)

    def test_domain_catalog_hook_for_app_plan_agent_present(self):
        with open(_HOOKS_YAML, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        hooks = data["hooks"]
        match = [
            h for h in hooks
            if h.get("hook_agent") == "AppPlanAgent"
            and h.get("filename") == "hook_domain_catalog_context.py"
            and h.get("function") == "inject_domain_catalog_context"
        ]
        assert match, "AppPlanAgent / inject_domain_catalog_context hook missing from hooks.yaml"

    def test_manifest_guard_hook_for_config_middleware_agent_present(self):
        with open(_HOOKS_YAML, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        hooks = data["hooks"]
        match = [
            h for h in hooks
            if h.get("hook_agent") == "ConfigMiddlewareAgent"
            and h.get("filename") == "hook_domain_catalog_context.py"
            and h.get("function") == "inject_module_file_manifest_guard"
        ]
        assert match, "ConfigMiddlewareAgent / inject_module_file_manifest_guard hook missing from hooks.yaml"

    def test_all_hooks_have_required_fields(self):
        with open(_HOOKS_YAML, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        for hook in data["hooks"]:
            for field in ("hook_type", "hook_agent", "filename", "function"):
                assert field in hook, f"Hook missing field '{field}': {hook}"


# ---------------------------------------------------------------------------
# Catalog YAML test
# ---------------------------------------------------------------------------

class TestDomainCatalogYaml:
    def test_catalog_yaml_valid(self):
        assert _CATALOG_PATH.exists(), f"domain_catalogs.yaml not found at {_CATALOG_PATH}"
        with open(_CATALOG_PATH, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        assert isinstance(data, dict)
        assert "domains" in data
        assert "global_base" in data

    def test_catalog_has_meaningful_domains(self):
        with open(_CATALOG_PATH, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        assert len(data["domains"]) >= 10

    def test_catalog_is_domain_prior_not_file_inventory(self):
        source = _CATALOG_PATH.read_text(encoding="utf-8")
        assert "recommended_module_type:" in source
        assert "yaml_files:" not in source
        assert "python_files:" not in source


# ---------------------------------------------------------------------------
# inject_domain_catalog_context tests
# ---------------------------------------------------------------------------

class TestInjectDomainCatalogContext:
    @pytest.fixture(autouse=True)
    def _import_hook(self):
        import sys
        tools_path = str(_APPGEN_DIR / "tools")
        if tools_path not in sys.path:
            sys.path.insert(0, tools_path)
        # Force fresh import so path changes take effect
        if "hook_domain_catalog_context" in sys.modules:
            del sys.modules["hook_domain_catalog_context"]
        import hook_domain_catalog_context as m
        self.mod = m

    def test_wrong_agent_name_is_noop(self):
        agent = _FakeAgent(name="ServiceAgent")
        self.mod.inject_domain_catalog_context(agent, [])
        assert agent.system_message == ""
        assert agent._update_calls == []

    def test_appends_header_to_system_message(self):
        agent = _FakeAgent(
            name="AppPlanAgent",
            context_variables={"concept_overview": "I want to build an ecommerce store"},
        )
        self.mod.inject_domain_catalog_context(agent, [])
        assert "[DOMAIN CATALOG CONTEXT]" in agent.system_message

    def test_includes_global_base_section(self):
        agent = _FakeAgent(
            name="AppPlanAgent",
            context_variables={"concept_overview": "social media platform"},
        )
        self.mod.inject_domain_catalog_context(agent, [])
        assert "Global base modules" in agent.system_message

    def test_includes_advisory_rules(self):
        agent = _FakeAgent(
            name="AppPlanAgent",
            context_variables={"concept_overview": "job board"},
        )
        self.mod.inject_domain_catalog_context(agent, [])
        msg = agent.system_message
        assert "module.yaml always" in msg or "Include module.yaml always" in msg

    def test_includes_module_archetype_priors(self):
        agent = _FakeAgent(
            name="AppPlanAgent",
            context_variables={"concept_overview": "team messaging app"},
        )
        self.mod.inject_domain_catalog_context(agent, [])
        msg = agent.system_message
        assert "[messaging]" in msg or "[standard]" in msg or "[workflow]" in msg or "[transactional]" in msg

    def test_instructs_against_six_file_default(self):
        agent = _FakeAgent(
            name="AppPlanAgent",
            context_variables={"concept_overview": "crm sales tool"},
        )
        self.mod.inject_domain_catalog_context(agent, [])
        msg = agent.system_message
        # The catalog context must instruct the agent NOT to include all six by default
        assert "do not include all six" in msg.lower()
        # And must not instruct the agent to always include all six
        assert "always include all six" not in msg.lower()

    def test_is_idempotent(self):
        agent = _FakeAgent(
            name="AppPlanAgent",
            context_variables={"concept_overview": "marketplace"},
        )
        self.mod.inject_domain_catalog_context(agent, [])
        first = agent.system_message
        self.mod.inject_domain_catalog_context(agent, [])
        second = agent.system_message
        # Section should be replaced, not duplicated
        assert second.count("[DOMAIN CATALOG CONTEXT]") == 1

    def test_catalog_load_failure_does_not_raise(self):
        agent = _FakeAgent(name="AppPlanAgent")
        with patch.object(self.mod, "_load_catalog", return_value=None):
            # Should exit silently
            self.mod.inject_domain_catalog_context(agent, [])
        assert agent.system_message == ""


# ---------------------------------------------------------------------------
# inject_module_file_manifest_guard tests
# ---------------------------------------------------------------------------

class TestInjectModuleFileManifestGuard:
    @pytest.fixture(autouse=True)
    def _import_hook(self):
        import sys
        tools_path = str(_APPGEN_DIR / "tools")
        if tools_path not in sys.path:
            sys.path.insert(0, tools_path)
        if "hook_domain_catalog_context" in sys.modules:
            del sys.modules["hook_domain_catalog_context"]
        import hook_domain_catalog_context as m
        self.mod = m

    def test_wrong_agent_name_is_noop(self):
        agent = _FakeAgent(name="ServiceAgent")
        self.mod.inject_module_file_manifest_guard(agent, [])
        assert agent.system_message == ""

    def test_non_module_contract_task_type_is_noop(self):
        agent = _FakeAgent(
            name="ConfigMiddlewareAgent",
            context_variables={
                "current_build_task": {
                    "task_type": "backend_foundation",
                    "capability_pack_id": "orders",
                    "owned_paths": [],
                }
            },
        )
        self.mod.inject_module_file_manifest_guard(agent, [])
        assert agent.system_message == ""

    def test_missing_context_variables_is_noop(self):
        agent = _FakeAgent(name="ConfigMiddlewareAgent", context_variables={})
        self.mod.inject_module_file_manifest_guard(agent, [])
        assert agent.system_message == ""

    def test_appends_guard_header_for_module_contract_task(self):
        agent = _FakeAgent(
            name="ConfigMiddlewareAgent",
            context_variables={
                "current_build_task": {
                    "task_type": "module_contract",
                    "capability_pack_id": "orders",
                    "owned_paths": [
                        "modules/orders/module.yaml",
                        "modules/orders/events.yaml",
                        "modules/orders/notifications.yaml",
                    ],
                }
            },
        )
        self.mod.inject_module_file_manifest_guard(agent, [])
        assert "[MODULE FILE MANIFEST GUARD]" in agent.system_message

    def test_declared_files_are_listed(self):
        agent = _FakeAgent(
            name="ConfigMiddlewareAgent",
            context_variables={
                "current_build_task": {
                    "task_type": "module_contract",
                    "capability_pack_id": "orders",
                    "owned_paths": [
                        "modules/orders/module.yaml",
                        "modules/orders/events.yaml",
                    ],
                }
            },
        )
        self.mod.inject_module_file_manifest_guard(agent, [])
        msg = agent.system_message
        assert "module.yaml" in msg
        assert "events.yaml" in msg

    def test_undeclared_files_are_flagged_as_omit(self):
        agent = _FakeAgent(
            name="ConfigMiddlewareAgent",
            context_variables={
                "current_build_task": {
                    "task_type": "module_contract",
                    "capability_pack_id": "orders",
                    "owned_paths": [
                        "modules/orders/module.yaml",
                    ],
                }
            },
        )
        self.mod.inject_module_file_manifest_guard(agent, [])
        msg = agent.system_message
        # subscriptions.yaml was not declared — should appear as omit
        assert "subscriptions.yaml" in msg
        assert "omit" in msg.lower() or "NOT" in msg

    def test_file_manifest_override_is_honoured(self):
        agent = _FakeAgent(
            name="ConfigMiddlewareAgent",
            context_variables={
                "current_build_task": {
                    "task_type": "module_contract",
                    "capability_pack_id": "orders",
                    "owned_paths": [
                        "modules/orders/module.yaml",
                        "modules/orders/events.yaml",
                        "modules/orders/subscriptions.yaml",
                    ],
                    "file_manifest": {
                        "yaml_files": ["module.yaml", "events.yaml"],
                    },
                }
            },
        )
        self.mod.inject_module_file_manifest_guard(agent, [])
        msg = agent.system_message
        # file_manifest overrides owned_paths — subscriptions.yaml should be omitted
        assert "subscriptions.yaml" not in msg.split("[MODULE FILE MANIFEST GUARD]")[1].split("omit")[0]

    def test_is_idempotent(self):
        ctx = {
            "current_build_task": {
                "task_type": "module_contract",
                "capability_pack_id": "orders",
                "owned_paths": ["modules/orders/module.yaml"],
            }
        }
        agent = _FakeAgent(name="ConfigMiddlewareAgent", context_variables=ctx)
        self.mod.inject_module_file_manifest_guard(agent, [])
        self.mod.inject_module_file_manifest_guard(agent, [])
        assert agent.system_message.count("[MODULE FILE MANIFEST GUARD]") == 1
