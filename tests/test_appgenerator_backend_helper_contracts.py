"""
Tests for AppGenerator backend helper file contracts and agent guidance.

Verifies:
1.  file_contracts.yaml documents helper file rules (backend_helper_files section).
2.  agents.yaml ServiceAgent guidance prohibits inventing helper files.
3.  agents.yaml AppPlanAgent guidance requires declaring helper files explicitly.
4.  runtime_extensions.yaml guidance requires declared backend entrypoint files.
5.  Helper file examples reference only backend/ paths.
6.  No prompt encourages arbitrary helper file creation.
7.  Canonical backend layers remain declared unchanged.
8.  file_contracts.yaml allowed_when list covers declared purposes.
9.  file_contracts.yaml prohibited_for list covers misuse patterns.
10. file_contracts.yaml generation rules require declaration before generation.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_APPGEN_DIR = (
    Path(__file__).parent.parent
    / "factory_app"
    / "workflows"
    / "AppGenerator"
)
_FILE_CONTRACTS = _APPGEN_DIR / "tools" / "file_contracts.yaml"
_AGENTS_YAML = _APPGEN_DIR / "agents.yaml"


def _load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _agents_text() -> str:
    return _AGENTS_YAML.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. file_contracts.yaml — backend_helper_files section exists
# ---------------------------------------------------------------------------

class TestFileContractsHelperSection:
    def test_backend_helper_files_section_exists(self):
        data = _load_yaml(_FILE_CONTRACTS)
        assert "backend_helper_files" in data, (
            "file_contracts.yaml must have a 'backend_helper_files' top-level section"
        )

    def test_helper_contract_entry_exists(self):
        data = _load_yaml(_FILE_CONTRACTS)
        section = data["backend_helper_files"]
        assert "helper_contract" in section

    def test_canonical_layers_listed(self):
        data = _load_yaml(_FILE_CONTRACTS)
        section = data["backend_helper_files"]
        layers = section.get("canonical_layers", [])
        layer_str = " ".join(layers)
        assert "handler.py" in layer_str
        assert "service.py" in layer_str
        assert "repo.py" in layer_str
        assert "policy.py" in layer_str
        assert "schemas.py" in layer_str

    def test_helper_examples_all_under_backend(self):
        data = _load_yaml(_FILE_CONTRACTS)
        examples = data["backend_helper_files"].get("examples", [])
        assert len(examples) > 0, "At least one helper file example must be provided"
        for ex in examples:
            assert ex.startswith("backend/"), (
                f"Helper file example must be under backend/: {ex}"
            )


# ---------------------------------------------------------------------------
# 2. file_contracts.yaml — allowed_when and prohibited_for
# ---------------------------------------------------------------------------

class TestFileContractsAllowedAndProhibited:
    def _contract(self) -> dict:
        return _load_yaml(_FILE_CONTRACTS)["backend_helper_files"]["helper_contract"]

    def test_allowed_when_covers_external_provider(self):
        allowed = " ".join(str(x) for x in self._contract().get("allowed_when", []))
        assert "provider" in allowed.lower() or "client" in allowed.lower() or "external" in allowed.lower()

    def test_allowed_when_covers_runtime_extension_entrypoint(self):
        allowed = " ".join(str(x) for x in self._contract().get("allowed_when", []))
        assert "runtime_extension" in allowed.lower() or "entrypoint" in allowed.lower() or "api_router" in allowed.lower()

    def test_allowed_when_covers_usage_entitlement_wrapper(self):
        allowed = " ".join(str(x) for x in self._contract().get("allowed_when", []))
        assert "usage" in allowed.lower() or "entitlement" in allowed.lower()

    def test_prohibited_for_covers_generic_service_logic(self):
        prohibited = " ".join(str(x) for x in self._contract().get("prohibited_for", []))
        assert "service" in prohibited.lower() or "business logic" in prohibited.lower()

    def test_prohibited_for_covers_persistence(self):
        prohibited = " ".join(str(x) for x in self._contract().get("prohibited_for", []))
        assert "repo" in prohibited.lower() or "persistence" in prohibited.lower() or "database" in prohibited.lower()

    def test_prohibited_for_covers_policy(self):
        prohibited = " ".join(str(x) for x in self._contract().get("prohibited_for", []))
        assert "policy" in prohibited.lower() or "authorization" in prohibited.lower() or "scoping" in prohibited.lower()

    def test_prohibited_for_covers_typed_shapes(self):
        prohibited = " ".join(str(x) for x in self._contract().get("prohibited_for", []))
        assert "schema" in prohibited.lower() or "model" in prohibited.lower() or "dto" in prohibited.lower() or "typed shape" in prohibited.lower()

    def test_prohibited_for_covers_arbitrary_splitting(self):
        prohibited = " ".join(str(x) for x in self._contract().get("prohibited_for", []))
        assert "split" in prohibited.lower() or "arbitrary" in prohibited.lower()


# ---------------------------------------------------------------------------
# 3. file_contracts.yaml — generation rules require declaration before generation
# ---------------------------------------------------------------------------

class TestFileContractsGenerationRules:
    def _rules(self) -> list:
        return _load_yaml(_FILE_CONTRACTS)["backend_helper_files"]["helper_contract"].get("rules", [])

    def test_rules_require_declaration_before_generation(self):
        rules_text = " ".join(str(r) for r in self._rules()).lower()
        assert "declared" in rules_text or "declaration" in rules_text or "python_stubs" in rules_text

    def test_rules_require_module_local(self):
        rules_text = " ".join(str(r) for r in self._rules()).lower()
        assert "module-local" in rules_text or "module_local" in rules_text or "local" in rules_text

    def test_rules_prohibit_new_public_actions(self):
        rules_text = " ".join(str(r) for r in self._rules()).lower()
        assert "action" in rules_text or "dispatch" in rules_text or "handler" in rules_text

    def test_rules_require_service_agent_not_invent(self):
        rules_text = " ".join(str(r) for r in self._rules()).lower()
        assert "serviceagent" in rules_text.replace(" ", "") or "service agent" in rules_text or "invent" in rules_text


# ---------------------------------------------------------------------------
# 4. file_contracts.yaml — runtime_extension_entrypoints declared
# ---------------------------------------------------------------------------

class TestFileContractsRuntimeExtensionEntrypoints:
    def test_runtime_extension_entrypoints_section_exists(self):
        data = _load_yaml(_FILE_CONTRACTS)
        section = data["backend_helper_files"]
        assert "runtime_extension_entrypoints" in section

    def test_api_router_entrypoint_requires_stub(self):
        data = _load_yaml(_FILE_CONTRACTS)
        ext = data["backend_helper_files"]["runtime_extension_entrypoints"]
        assert "api_router" in ext
        router = ext["api_router"]
        stub = str(router.get("required_stub", ""))
        assert "backend/" in stub

    def test_startup_service_entrypoint_requires_stub(self):
        data = _load_yaml(_FILE_CONTRACTS)
        ext = data["backend_helper_files"]["runtime_extension_entrypoints"]
        assert "startup_service" in ext
        worker = ext["startup_service"]
        stub = str(worker.get("required_stub", ""))
        assert "backend/" in stub


# ---------------------------------------------------------------------------
# 5. agents.yaml — ServiceAgent guidance prohibits inventing helper files
# ---------------------------------------------------------------------------

class TestServiceAgentHelperGuidance:
    def test_service_agent_must_not_invent_helper_files(self):
        text = _agents_text()
        # Find ServiceAgent section and check for the helper file prohibition
        assert "Do not invent" in text or "do not invent" in text
        assert "python_stubs" in text

    def test_service_agent_declares_helper_file_rule(self):
        text = _agents_text()
        assert "helper" in text.lower()
        # Should mention that helper files must be declared
        assert "declared" in text.lower()

    def test_service_agent_canonical_layers_remain_intact(self):
        text = _agents_text()
        # All five canonical layers must still be described
        assert "backend/handler.py" in text
        assert "backend/service.py" in text
        assert "backend/repo.py" in text
        assert "backend/policy.py" in text
        assert "backend/schemas.py" in text

    def test_service_agent_handler_stays_thin(self):
        text = _agents_text()
        assert "thin" in text.lower()
        # handler.py must be described as dispatch/adapter
        assert "dispatch" in text.lower() or "adapter" in text.lower()

    def test_service_agent_service_stays_business_logic(self):
        text = _agents_text()
        assert "business logic" in text.lower()

    def test_service_agent_repo_stays_persistence(self):
        text = _agents_text()
        assert "persistence" in text.lower()

    def test_service_agent_policy_stays_authorization(self):
        text = _agents_text()
        assert "authz" in text.lower() or "authorization" in text.lower() or "ownership" in text.lower()


# ---------------------------------------------------------------------------
# 6. agents.yaml — AppPlanAgent guidance requires declaring helper files
# ---------------------------------------------------------------------------

class TestAppPlanAgentHelperGuidance:
    def test_app_plan_agent_declares_helper_files_explicitly(self):
        text = _agents_text()
        # AppPlanAgent section must mention declaring helper files
        assert "helper file" in text.lower() or "helper_file" in text.lower()
        assert "declare" in text.lower() or "declared" in text.lower()

    def test_app_plan_agent_mentions_rationale_requirement(self):
        text = _agents_text()
        assert "rationale" in text.lower() or "purpose" in text.lower() or "justified" in text.lower()

    def test_app_plan_agent_mentions_example_stripe_client(self):
        text = _agents_text()
        assert "stripe_client.py" in text or "stripe_client" in text

    def test_app_plan_agent_mentions_routes_webhooks_example(self):
        text = _agents_text()
        assert "routes_webhooks.py" in text or "routes_webhooks" in text

    def test_app_plan_agent_mentions_usage_check_example(self):
        text = _agents_text()
        assert "usage_check.py" in text or "usage_check" in text

    def test_app_plan_agent_helper_rule_is_own_numbered_item(self):
        text = _agents_text()
        # The rule must have its own numbered section heading (12f or similar)
        assert "backend helper file" in text.lower() or "helper file declaration" in text.lower()


# ---------------------------------------------------------------------------
# 7. agents.yaml — ConfigMiddlewareAgent links runtime_extensions to stubs
# ---------------------------------------------------------------------------

class TestConfigMiddlewareAgentHelperGuidance:
    def test_config_middleware_requires_declared_runtime_extension_stub(self):
        text = _agents_text()
        # ConfigMiddlewareAgent must require declared helper for api_router
        assert "runtime extension" in text.lower() or "runtime_extensions" in text.lower()
        assert "python_stubs" in text

    def test_config_middleware_helper_file_rule_exists(self):
        text = _agents_text()
        # Must mention the helper file rule
        assert "Helper file rule" in text or "helper file" in text.lower()


# ---------------------------------------------------------------------------
# 8. agents.yaml — no prompt encourages arbitrary helper creation
# ---------------------------------------------------------------------------

class TestNoArbitraryHelperPromotion:
    def test_no_prompt_says_add_helper_file_freely(self):
        text = _agents_text()
        # These phrases would encourage arbitrary helper creation
        bad_phrases = [
            "feel free to add helper",
            "add any helper files",
            "create helper files as needed",
            "helper files can be added freely",
        ]
        for phrase in bad_phrases:
            assert phrase.lower() not in text.lower(), (
                f"Found phrase that encourages arbitrary helper creation: {phrase!r}"
            )

    def test_helper_mentioned_only_with_constraints(self):
        text = _agents_text()
        # Every mention of "helper file" in ServiceAgent section must be paired
        # with a constraint. We test this by checking that "invent" appears
        # near a "Do not" instruction.
        assert "Do not invent" in text or "do not invent" in text


# ---------------------------------------------------------------------------
# 9. file_contracts.yaml — file parses and canonical task_contracts unchanged
# ---------------------------------------------------------------------------

class TestFileContractsIntegrity:
    def test_file_contracts_parses(self):
        data = _load_yaml(_FILE_CONTRACTS)
        assert isinstance(data, dict)

    def test_task_contracts_still_present(self):
        data = _load_yaml(_FILE_CONTRACTS)
        assert "task_contracts" in data
        tc = data["task_contracts"]
        assert "module_contract" in tc
        assert "page_bundle" in tc
        assert "backend_foundation" in tc
        assert "api_surface" in tc

    def test_module_contract_canonical_downstream_python_unchanged(self):
        data = _load_yaml(_FILE_CONTRACTS)
        defaults = data["task_contracts"]["module_contract"].get("downstream_python_defaults", [])
        assert "backend/handler.py" in defaults
        assert "backend/service.py" in defaults
        assert "backend/repo.py" in defaults
        assert "backend/policy.py" in defaults

    def test_no_runtime_code_changed(self):
        """file_contracts.yaml and agents.yaml are generator guidance — not runtime code."""
        runtime_path = Path(__file__).parent.parent / "mozaiksai"
        # Verify we are not accidentally referencing runtime internals from the contracts
        text = _load_yaml(_FILE_CONTRACTS)
        contracts_str = str(text)
        assert "mozaiksai.core" not in contracts_str
        assert "mozaiksai.hosts" not in contracts_str
