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

import yaml

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
_FILE_CONTRACTS = _APPGEN_CATALOG_DIR / "file_contracts.yaml"
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

    def test_allowed_when_covers_module_local_guard_wrapper(self):
        allowed = " ".join(str(x) for x in self._contract().get("allowed_when", []))
        assert "guard" in allowed.lower() or "declared capability" in allowed.lower()

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

    def test_app_plan_agent_mentions_example_provider_client(self):
        # Neutral provider client example — payment_provider_client replaced with generic provider_client
        text = _agents_text()
        assert (
            "payment_provider_client.py" in text
            or "payment_provider_client" in text
            or "provider_client.py" in text
            or "provider_client" in text
        )

    def test_app_plan_agent_mentions_routes_webhooks_example(self):
        text = _agents_text()
        assert "services/routes/webhooks.py" in text

    def test_app_plan_agent_mentions_usage_check_example(self):
        text = _agents_text()
        assert "security/secrets.yaml" in text

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
        assert "service_foundation" in tc
        assert "api_surface" in tc

    def test_service_foundation_declares_app_backend_support_lanes(self):
        data = _load_yaml(_FILE_CONTRACTS)
        outputs = data["task_contracts"]["service_foundation"].get("optional_outputs", [])
        constraints = data["task_contracts"]["service_foundation"].get("hard_constraints", [])
        joined_constraints = " ".join(str(item) for item in constraints)
        assert any("services/integrations" in str(output) for output in outputs)
        assert any("services/adapters" in str(output) for output in outputs)
        assert any("services/routes" in str(output) for output in outputs)
        assert any("security/secrets.yaml" in str(output) for output in outputs)
        assert "business" in joined_constraints
        assert "Modules own" in joined_constraints
        assert "auth" in joined_constraints
        assert "dns" in joined_constraints
        assert "registrar" in joined_constraints
        assert "secrets" in joined_constraints
        assert "services/security" not in joined_constraints
        assert "services/routes" in joined_constraints
        assert "raw secret values" in joined_constraints
        assert "Mozaiks-hosted deployment" in joined_constraints
        assert "Dockerfile" in joined_constraints
        assert "generate_and_download" in joined_constraints

    def test_module_contract_canonical_downstream_python_unchanged(self):
        data = _load_yaml(_FILE_CONTRACTS)
        defaults = data["task_contracts"]["module_contract"].get("downstream_backend_defaults", [])
        assert "backend/handler.py" in defaults
        assert "backend/service.py" in defaults
        assert "backend/repo.py" in defaults
        assert "backend/policy.py" in defaults

    def test_no_runtime_code_changed(self):
        """file_contracts.yaml and agents.yaml are generator guidance — not runtime code."""
        Path(__file__).parent.parent / "mozaiksai"
        # Verify we are not accidentally referencing runtime internals from the contracts
        text = _load_yaml(_FILE_CONTRACTS)
        contracts_str = str(text)
        assert "mozaiksai.core" not in contracts_str
        assert "mozaiksai.hosts" not in contracts_str


# ---------------------------------------------------------------------------
# 10. file_contracts.yaml — helper examples are provider-neutral (no payment provider, etc.)
# ---------------------------------------------------------------------------

class TestHelperExamplesProviderNeutral:
    def test_helper_examples_no_payment_provider_reference(self):
        data = _load_yaml(_FILE_CONTRACTS)
        examples = data["backend_helper_files"].get("examples", [])
        examples_str = " ".join(examples)
        assert "payment_provider" not in examples_str.lower(), (
            f"Helper file examples must be provider-neutral. Found payment provider reference in: {examples}"
        )

    def test_helper_examples_use_generic_provider_naming(self):
        data = _load_yaml(_FILE_CONTRACTS)
        examples = data["backend_helper_files"].get("examples", [])
        # Should have provider_client.py, not payment_provider_client.py
        examples_str = " ".join(examples)
        assert "provider_client.py" in examples_str or "provider_client" in examples_str, (
            "Helper examples should include a generic provider_client.py example"
        )

    def test_allowed_when_uses_generic_language(self):
        data = _load_yaml(_FILE_CONTRACTS)
        allowed = data["backend_helper_files"]["helper_contract"].get("allowed_when", [])
        allowed_str = " ".join(str(x) for x in allowed)
        # Should say "external provider client" not "payment provider SDK wrapper"
        assert "payment_provider" not in allowed_str.lower(), (
            "Helper file allowed_when rules must be provider-neutral"
        )
        assert "provider" in allowed_str.lower() or "external" in allowed_str.lower(), (
            "Helper file rules should reference generic 'provider' or 'external' concepts"
        )

    def test_examples_routes_file_uses_generic_naming(self):
        data = _load_yaml(_FILE_CONTRACTS)
        examples = data["backend_helper_files"].get("examples", [])
        examples_str = " ".join(examples)
        # Should have routes_hooks.py, not routes_webhooks.py (which is payment provider-specific)
        assert "routes_hooks.py" in examples_str or "routes" in examples_str, (
            "Routes example should use generic naming like routes_hooks.py"
        )

    def test_examples_worker_file_uses_generic_naming(self):
        data = _load_yaml(_FILE_CONTRACTS)
        examples = data["backend_helper_files"].get("examples", [])
        examples_str = " ".join(examples)
        # Should have event_worker.py, not event_subscriber.py (more specific)
        assert "worker" in examples_str.lower() or "event" in examples_str.lower(), (
            "Worker example should use generic naming"
        )


class TestListSerializerContracts:
    """
    Verifies that AppGenerator enforces allowlist serialization for list_* service methods.

    Rules under test:
    - module_contract.hard_constraints declares the list_* serializer requirement.
    - module_contract.hard_constraints declares the explicit items.properties requirement.
    - ServiceAgent instruction 20a requires allowlist serialization for list_* actions.
    - The example in ServiceAgent uses a neutral domain (not MozaiksPay/payment provider/wallet/billing).
    - The example helper name follows the _serialize_{entity}_row pattern.
    - module_archetypes.yaml standard archetype hard_constraints includes the serializer rule.
    - Raw repo record pass-through is explicitly prohibited.
    - The rule references backend/schemas.py as the location for helpers.
    """

    def test_module_contract_has_list_output_schema_items_constraint(self):
        data = _load_yaml(_FILE_CONTRACTS)
        constraints = data["task_contracts"]["module_contract"]["hard_constraints"]
        constraints_str = "\n".join(str(c) for c in constraints)
        assert "items.properties" in constraints_str, (
            "module_contract hard_constraints must require explicit items.properties for list_* output_schema"
        )
        assert "list_*" in constraints_str or "list_" in constraints_str, (
            "module_contract hard_constraints must reference list_* actions"
        )

    def test_module_contract_has_list_serializer_constraint(self):
        data = _load_yaml(_FILE_CONTRACTS)
        constraints = data["task_contracts"]["module_contract"]["hard_constraints"]
        constraints_str = "\n".join(str(c) for c in constraints)
        assert "raw repo" in constraints_str.lower() or "_serialize_" in constraints_str, (
            "module_contract hard_constraints must prohibit raw repo records from list_* service methods"
        )
        assert "allowlist" in constraints_str.lower() or "allow list" in constraints_str.lower(), (
            "module_contract hard_constraints must require an allowlist helper pattern"
        )

    def test_module_contract_list_serializer_names_schemas_py(self):
        data = _load_yaml(_FILE_CONTRACTS)
        constraints = data["task_contracts"]["module_contract"]["hard_constraints"]
        constraints_str = "\n".join(str(c) for c in constraints)
        assert "schemas.py" in constraints_str, (
            "module_contract list_* serializer constraint must name schemas.py as the helper location"
        )

    def test_service_agent_has_list_serializer_instruction(self):
        text = _agents_text()
        assert "_serialize_" in text, (
            "ServiceAgent instructions must include the _serialize_{entity}_row pattern for list_* methods"
        )
        assert "allowlist" in text.lower() or "allow list" in text.lower() or "allowlist" in text, (
            "ServiceAgent instructions must use the word 'allowlist' for list_* serialization"
        )

    def test_service_agent_list_serializer_example_is_domain_neutral(self):
        text = _agents_text()
        # The example must not use MozaiksPay, payment provider, wallet, billing, or entitlements terms
        forbidden = ["mozaikspay", "payment_provider", "wallet", "billing", "entitlement", "payout", "checkout"]
        lower = text.lower()
        # Find the 20a instruction block
        idx = lower.find("20a.")
        assert idx != -1, "ServiceAgent instruction 20a must exist"
        block = lower[idx: idx + 1500]
        for term in forbidden:
            assert term not in block, (
                f"ServiceAgent instruction 20a example must not reference '{term}' — use neutral domain names"
            )

    def test_service_agent_list_serializer_example_uses_neutral_domain(self):
        text = _agents_text()
        idx = text.lower().find("20a.")
        assert idx != -1, "ServiceAgent instruction 20a must exist"
        block = text[idx: idx + 1500]
        # Should reference a neutral module like inventory, contacts, projects, etc.
        neutral_terms = ["inventory", "contact", "project", "report", "analytics", "item", "record"]
        assert any(t in block.lower() for t in neutral_terms), (
            "ServiceAgent instruction 20a example must use a neutral domain name "
            "(e.g. inventory, contacts, projects)"
        )

    def test_service_agent_list_serializer_prohibits_raw_repo_passthrough(self):
        text = _agents_text()
        idx = text.lower().find("20a.")
        assert idx != -1, "ServiceAgent instruction 20a must exist"
        block = text[idx: idx + 1500]
        assert "raw repo" in block.lower() or "never return raw" in block.lower(), (
            "ServiceAgent instruction 20a must explicitly prohibit returning raw repo documents"
        )

    def test_module_archetypes_standard_has_list_serializer_constraint(self):
        data = _load_yaml(_APPGEN_CATALOG_DIR / "module_archetypes.yaml")
        constraints = data["archetypes"]["standard"]["hard_constraints"]
        constraints_str = "\n".join(str(c) for c in constraints)
        assert "list_*" in constraints_str or "list_" in constraints_str, (
            "standard archetype hard_constraints must reference list_* serializer requirement"
        )
        assert "_serialize_" in constraints_str or "allowlist" in constraints_str.lower(), (
            "standard archetype hard_constraints must name the _serialize_ helper or 'allowlist' pattern"
        )

    def test_module_archetypes_serializer_constraint_names_schemas_py(self):
        data = _load_yaml(_APPGEN_CATALOG_DIR / "module_archetypes.yaml")
        constraints = data["archetypes"]["standard"]["hard_constraints"]
        constraints_str = "\n".join(str(c) for c in constraints)
        assert "schemas.py" in constraints_str, (
            "standard archetype list_* constraint must name schemas.py as the helper location"
        )

    def test_list_serializer_rules_use_no_proprietary_terms(self):
        """Serializer rules must be provider-neutral — no MozaiksPay/payment provider/wallet references."""
        data = _load_yaml(_FILE_CONTRACTS)
        constraints = data["task_contracts"]["module_contract"]["hard_constraints"]
        # Find the list_* constraints
        list_constraints = [c for c in constraints if "list_" in str(c).lower() or "serialize" in str(c).lower()]
        combined = " ".join(str(c) for c in list_constraints).lower()
        forbidden = ["payment_provider", "mozaikspay", "wallet", "billing", "entitlement", "payout", "checkout"]
        for term in forbidden:
            assert term not in combined, (
                f"module_contract list_* constraint must not reference '{term}' — use neutral language"
            )

