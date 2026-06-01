"""
AppPlanAgent hosted-pack safety tests.

Verifies:
1. AppCapabilityPack.capability_source is correctly defined in structured_outputs.yaml.
2. app_build_plan.py preserves capability_source through normalization/caching.
3. app_build_plan.py rejects module_contract build tasks for hosted_pack entries.
4. app_build_plan.py accepts hosted_pack entries with no module_contract build task.
5. generated_module packs still produce module_contract tasks normally.
6. agents.yaml contains the explicit hosted_pack rules.
7. hook_hosted_capabilities_context.py injects the planning rule and taxonomy.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

WORKSPACE = Path(__file__).resolve().parents[1]
_TOOLS_DIR = WORKSPACE / "factory_app" / "workflows" / "AppGenerator" / "tools"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_yaml(relative_path: str) -> dict:
    return yaml.safe_load((WORKSPACE / relative_path).read_text(encoding="utf-8")) or {}


def _read_text(relative_path: str) -> str:
    return (WORKSPACE / relative_path).read_text(encoding="utf-8")


def _load_module(relative_path: str, module_name: str):
    file_path = WORKSPACE / relative_path
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load module spec for {file_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Context:
    def __init__(self, **data) -> None:
        self.data = dict(data)

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value) -> None:
        self.data[key] = value

    def __setitem__(self, key, value) -> None:
        self.data[key] = value


# ---------------------------------------------------------------------------
# Minimal plan fixtures
# ---------------------------------------------------------------------------

_MINIMAL_PLAN_BASE: dict = {
    "agent_message": "Test plan.",
    "app_kind": "saas",
    "pages": [{"name": "Dashboard", "route": "/dashboard", "purpose": "Main view."}],
    "entities": [],
    "roles": [],
    "auth_strategy": "basic-login",
    "service_scope": [],
    "frontend_scope": [],
    "theme_preferences": None,
    "brand_intent": None,
    "external_integrations": [],
    "agent_backend_required": False,
    "build_tasks": [],
    "generation_order": [],
}

_HOSTED_PACK: dict = {
    "capability_pack_id": "wallet",
    "surface_id": "wallet_surface",
    "surface_kind": "external_integration",
    "pack_type": "billing_pack",
    "label": "Wallet",
    "summary": "Hosted wallet pack.",
    "implementation_mode": "external_integration",
    "capability_source": "hosted_pack",
}

_GENERATED_PACK: dict = {
    "capability_pack_id": "orders",
    "surface_id": "orders_surface",
    "surface_kind": "module",
    "pack_type": "custom_domain",
    "label": "Orders",
    "summary": "Order management.",
    "implementation_mode": "declarative_module",
    "capability_source": "generated_module",
}

_HOSTED_PACK_MODULE_CONTRACT_TASK: dict = {
    "task_id": "task_wallet_contract",
    "task_type": "module_contract",
    "capability_pack_id": "wallet",  # hosted_pack — must be rejected
    "surface_id": "wallet_surface",
    "surface_kind": "module",
    "execution_target": "AppGenerator",
    "initial_agent": "ConfigMiddlewareAgent",
    "description": "Generate wallet module contract.",
    "initial_message": "Generate wallet module contract.",
    "owned_paths": ["modules/wallet/module.yaml"],
    "depends_on": [],
    "acceptance_criteria": ["module.yaml exists"],
}

_GENERATED_MODULE_CONTRACT_TASK: dict = {
    "task_id": "task_orders_contract",
    "task_type": "module_contract",
    "capability_pack_id": "orders",  # generated_module — must be accepted
    "surface_id": "orders_surface",
    "surface_kind": "module",
    "execution_target": "AppGenerator",
    "initial_agent": "ConfigMiddlewareAgent",
    "description": "Generate orders module contract.",
    "initial_message": "Generate orders module contract.",
    "owned_paths": ["modules/orders/module.yaml"],
    "depends_on": [],
    "acceptance_criteria": ["module.yaml exists"],
}


# ---------------------------------------------------------------------------
# 1. Structured output schema tests
# ---------------------------------------------------------------------------

class TestCapabilitySourceSchema:
    def test_appcapabilitypack_has_capability_source_field(self):
        models = _read_yaml(
            "factory_app/workflows/AppGenerator/structured_outputs.yaml"
        )["models"]
        assert "capability_source" in models["AppCapabilityPack"]["fields"], \
            "AppCapabilityPack missing capability_source field"

    def test_capability_source_is_optional_str(self):
        models = _read_yaml(
            "factory_app/workflows/AppGenerator/structured_outputs.yaml"
        )["models"]
        cs = models["AppCapabilityPack"]["fields"]["capability_source"]
        assert cs.get("type") == "optional_str", \
            f"capability_source.type should be optional_str, got {cs.get('type')!r}"

    def test_capability_source_description_covers_hosted_pack(self):
        models = _read_yaml(
            "factory_app/workflows/AppGenerator/structured_outputs.yaml"
        )["models"]
        description = models["AppCapabilityPack"]["fields"]["capability_source"].get("description", "")
        assert "hosted_pack" in description
        assert "host_universal" in description
        assert "framework_pack" in description
        assert "generated_module" in description
        assert "external_adapter" in description

    def test_appcapabilitypack_has_surface_kind_field(self):
        models = _read_yaml(
            "factory_app/workflows/AppGenerator/structured_outputs.yaml"
        )["models"]
        assert "surface_kind" in models["AppCapabilityPack"]["fields"]
        assert "external_integration" in \
            models["AppCapabilityPack"]["fields"]["surface_kind"]["values"]

    def test_appbuildtask_has_capability_pack_id_field(self):
        models = _read_yaml(
            "factory_app/workflows/AppGenerator/structured_outputs.yaml"
        )["models"]
        task_fields = models["AppBuildTask"]["fields"]
        assert "capability_pack_id" in task_fields
        # Can be null (hosted packs have no task) or str
        variants = set(task_fields["capability_pack_id"]["variants"])
        assert "str" in variants
        assert "null" in variants


# ---------------------------------------------------------------------------
# 2. app_build_plan.py normalization tests
# ---------------------------------------------------------------------------

class TestAppBuildPlanHostedPackNormalization:
    @pytest.fixture(autouse=True)
    def _module(self):
        self.mod = _load_module(
            "factory_app/workflows/AppGenerator/tools/app_build_plan.py",
            "tests.app_build_plan_hosted_pack_safety",
        )

    def test_hosted_pack_capability_source_preserved(self):
        ctx = _Context()
        self.mod.app_build_plan(
            AppBuildPlan={
                **_MINIMAL_PLAN_BASE,
                "capability_packs": [_HOSTED_PACK],
            },
            context_variables=ctx,
        )
        cached = ctx.data["app_build_plan"]
        assert cached["capability_packs"][0]["capability_source"] == "hosted_pack"

    def test_hosted_pack_with_no_module_contract_task_is_accepted(self):
        """A hosted_pack entry with no build task at all must be accepted."""
        ctx = _Context()
        result = self.mod.app_build_plan(
            AppBuildPlan={
                **_MINIMAL_PLAN_BASE,
                "capability_packs": [_HOSTED_PACK],
                "build_tasks": [],  # No module_contract for wallet — correct
            },
            context_variables=ctx,
        )
        assert "Capability packs: 1" in result
        cached = ctx.data["app_build_plan"]
        assert cached["capability_packs"][0]["capability_pack_id"] == "wallet"

    def test_generated_module_pack_with_module_contract_is_accepted(self):
        """generated_module packs must still produce module_contract tasks normally."""
        ctx = _Context()
        result = self.mod.app_build_plan(
            AppBuildPlan={
                **_MINIMAL_PLAN_BASE,
                "capability_packs": [_GENERATED_PACK],
                "build_tasks": [_GENERATED_MODULE_CONTRACT_TASK],
            },
            context_variables=ctx,
        )
        assert "Build tasks: 1" in result

    def test_mixed_hosted_and_generated_packs_accepted_when_only_generated_has_task(self):
        """Wallet (hosted) has no task; orders (generated_module) has module_contract — valid."""
        ctx = _Context()
        self.mod.app_build_plan(
            AppBuildPlan={
                **_MINIMAL_PLAN_BASE,
                "capability_packs": [_HOSTED_PACK, _GENERATED_PACK],
                "build_tasks": [_GENERATED_MODULE_CONTRACT_TASK],
            },
            context_variables=ctx,
        )
        cached = ctx.data["app_build_plan"]
        assert len(cached["capability_packs"]) == 2
        assert len(cached["build_tasks"]) == 1

    def test_capability_packs_without_capability_source_not_affected(self):
        """Packs with no capability_source are not treated as hosted_pack."""
        ctx = _Context()
        pack_no_source = {
            "capability_pack_id": "crm",
            "surface_id": "crm_surface",
            "surface_kind": "module",
            "pack_type": "crud_pack",
            "label": "CRM",
            "summary": "CRM pack.",
            "implementation_mode": "declarative_module",
        }
        self.mod.app_build_plan(
            AppBuildPlan={
                **_MINIMAL_PLAN_BASE,
                "capability_packs": [pack_no_source],
                "build_tasks": [
                    {**_GENERATED_MODULE_CONTRACT_TASK, "capability_pack_id": "crm",
                     "task_id": "task_crm", "owned_paths": ["modules/crm/module.yaml"]},
                ],
            },
            context_variables=ctx,
        )
        assert ctx.data["app_build_plan"]["capability_packs"][0]["capability_pack_id"] == "crm"


# ---------------------------------------------------------------------------
# 3. Negative test: hosted_pack + module_contract → ValueError
# ---------------------------------------------------------------------------

class TestAppBuildPlanHostedPackModuleContractRejection:
    @pytest.fixture(autouse=True)
    def _module(self):
        self.mod = _load_module(
            "factory_app/workflows/AppGenerator/tools/app_build_plan.py",
            "tests.app_build_plan_hosted_module_contract_guard",
        )

    def test_rejects_module_contract_for_hosted_pack(self):
        with pytest.raises(ValueError, match="hosted pack 'wallet'"):
            self.mod.app_build_plan(
                AppBuildPlan={
                    **_MINIMAL_PLAN_BASE,
                    "capability_packs": [_HOSTED_PACK],
                    "build_tasks": [_HOSTED_PACK_MODULE_CONTRACT_TASK],
                },
                context_variables=_Context(),
            )

    def test_error_message_mentions_external_integration(self):
        with pytest.raises(ValueError, match="external_integration"):
            self.mod.app_build_plan(
                AppBuildPlan={
                    **_MINIMAL_PLAN_BASE,
                    "capability_packs": [_HOSTED_PACK],
                    "build_tasks": [_HOSTED_PACK_MODULE_CONTRACT_TASK],
                },
                context_variables=_Context(),
            )

    def test_rejects_investor_marketplace_hosted_pack_module_contract(self):
        im_pack = {
            **_HOSTED_PACK,
            "capability_pack_id": "investor_marketplace",
            "label": "Investor Marketplace",
        }
        im_task = {
            **_HOSTED_PACK_MODULE_CONTRACT_TASK,
            "task_id": "task_im_contract",
            "capability_pack_id": "investor_marketplace",
            "owned_paths": ["modules/investor_marketplace/module.yaml"],
        }
        with pytest.raises(ValueError, match="hosted pack 'investor_marketplace'"):
            self.mod.app_build_plan(
                AppBuildPlan={
                    **_MINIMAL_PLAN_BASE,
                    "capability_packs": [im_pack],
                    "build_tasks": [im_task],
                },
                context_variables=_Context(),
            )

    def test_generated_module_not_blocked(self):
        """generated_module packs must not be blocked even if a hosted_pack is also present."""
        ctx = _Context()
        self.mod.app_build_plan(
            AppBuildPlan={
                **_MINIMAL_PLAN_BASE,
                "capability_packs": [_HOSTED_PACK, _GENERATED_PACK],
                "build_tasks": [_GENERATED_MODULE_CONTRACT_TASK],  # orders — ok
            },
            context_variables=ctx,
        )
        assert ctx.data["app_plan_ready"] is True


# ---------------------------------------------------------------------------
# 4. agents.yaml static text checks
# ---------------------------------------------------------------------------

class TestAgentsYamlHostedPackRules:
    @pytest.fixture(autouse=True)
    def _content(self):
        self._text = _read_text("factory_app/workflows/AppGenerator/agents.yaml")

    def test_hosted_pack_rule_present(self):
        assert "hosted_pack" in self._text

    def test_hosted_pack_no_module_contract_rule(self):
        # Exact rule from agents.yaml lines 153-154
        assert "Do NOT plan a `module_contract` build task for it" in self._text

    def test_hosted_pack_external_integration_rule(self):
        assert "implementation_mode: external_integration" in self._text

    def test_host_universal_no_scaffold_rule(self):
        assert "host_universal" in self._text
        assert "do NOT scaffold" in self._text

    def test_generated_module_full_generation_rule(self):
        assert "generated_module" in self._text
        # Rule that generated_module gets full contracts
        assert "generate full module contracts" in self._text or \
               "must generate full module contracts" in self._text

    def test_surface_realization_rule_only_module_emits_module_contract(self):
        assert "surface_kind = module` may emit `task_type: module_contract`" in self._text

    def test_capability_source_rules_section_present(self):
        assert "Capability source rules" in self._text


# ---------------------------------------------------------------------------
# 5. Hook context injection tests
# ---------------------------------------------------------------------------

class _FakeAgent:
    def __init__(self, name: str, context_variables: dict | None = None):
        self.name = name
        self.system_message = ""
        self.context_variables = context_variables or {}

    def update_system_message(self, message: str) -> None:
        self.system_message = message


def _load_hook():
    tools_path = str(_TOOLS_DIR)
    if tools_path not in sys.path:
        sys.path.insert(0, tools_path)
    if "hook_hosted_capabilities_context" in sys.modules:
        del sys.modules["hook_hosted_capabilities_context"]
    import hook_hosted_capabilities_context as m
    return m


class TestHookHostedPackContextInjection:
    @pytest.fixture(autouse=True)
    def _hook(self):
        self.hook = _load_hook()

    def test_planning_rule_injected_when_hosted_packs_present(self):
        agent = _FakeAgent("AppPlanAgent", context_variables={
            "available_hosted_packs": [{"id": "wallet", "display_name": "Wallet"}],
        })
        self.hook.inject_hosted_capabilities_context(agent, [])
        assert "module_contract" in agent.system_message
        assert "external_integration" in agent.system_message

    def test_hosted_capabilities_context_header_present(self):
        agent = _FakeAgent("AppPlanAgent", context_variables={
            "runtime_capabilities": ["hosted_wallet"],
        })
        self.hook.inject_hosted_capabilities_context(agent, [])
        assert "[HOSTED CAPABILITIES CONTEXT]" in agent.system_message

    def test_taxonomy_covers_all_five_sources(self):
        agent = _FakeAgent("AppPlanAgent", context_variables={
            "runtime_capabilities": ["module_execution"],
        })
        self.hook.inject_hosted_capabilities_context(agent, [])
        msg = agent.system_message
        assert "host_universal" in msg
        assert "framework_pack" in msg
        assert "hosted_pack" in msg
        assert "generated_module" in msg
        assert "external_adapter" in msg

    def test_no_injection_in_oss_mode(self):
        agent = _FakeAgent("AppPlanAgent", context_variables={
            "runtime_capabilities": None,
            "available_hosted_packs": None,
            "pack_sources": None,
        })
        self.hook.inject_hosted_capabilities_context(agent, [])
        assert agent.system_message == ""

    def test_pack_listed_in_injected_context(self):
        agent = _FakeAgent("AppPlanAgent", context_variables={
            "available_hosted_packs": [
                {"id": "wallet", "display_name": "Wallet",
                 "capabilities": [{"capability_id": "wallet.view"}]},
                {"id": "investor_marketplace", "display_name": "Investor Marketplace"},
            ],
        })
        self.hook.inject_hosted_capabilities_context(agent, [])
        assert "wallet" in agent.system_message
        assert "investor_marketplace" in agent.system_message


# ---------------------------------------------------------------------------
# 6. Hosted adapter task validation tests
# ---------------------------------------------------------------------------

_HOSTED_ADAPTER_TASK: dict = {
    "task_id": "task_wallet_adapter",
    "task_type": "api_surface",
    "capability_pack_id": "wallet",
    "surface_id": "wallet_surface",
    "surface_kind": "external_integration",
    "execution_target": "AppGenerator",
    "initial_agent": "ControllerAgent",
    "description": "Generate app-side adapter for hosted wallet capability.",
    "initial_message": "Generate a thin client in services/integrations/wallet_client.py.",
    "owned_paths": ["services/integrations/wallet_client.py"],
    "depends_on": [],
    "acceptance_criteria": [
        "Does not implement wallet internals",
        "Calls hosted wallet API only",
    ],
}


class TestHostedPackAdapterTaskValidation:
    @pytest.fixture(autouse=True)
    def _module(self):
        self.mod = _load_module(
            "factory_app/workflows/AppGenerator/tools/app_build_plan.py",
            "tests.app_build_plan_hosted_adapter_validation",
        )

    def test_hosted_pack_api_surface_adapter_task_accepted(self):
        """hosted_pack with api_surface task at services/integrations/ must be accepted."""
        ctx = _Context()
        result = self.mod.app_build_plan(
            AppBuildPlan={
                **_MINIMAL_PLAN_BASE,
                "capability_packs": [_HOSTED_PACK],
                "build_tasks": [_HOSTED_ADAPTER_TASK],
            },
            context_variables=ctx,
        )
        assert "Build tasks: 1" in result
        cached = ctx.data["app_build_plan"]
        assert cached["build_tasks"][0]["task_id"] == "task_wallet_adapter"

    def test_hosted_pack_adapter_task_with_external_integration_surface_kind(self):
        """surface_kind: external_integration on a hosted adapter task is valid."""
        ctx = _Context()
        self.mod.app_build_plan(
            AppBuildPlan={
                **_MINIMAL_PLAN_BASE,
                "capability_packs": [_HOSTED_PACK],
                "build_tasks": [{**_HOSTED_ADAPTER_TASK, "surface_kind": "external_integration"}],
            },
            context_variables=ctx,
        )
        assert ctx.data["app_plan_ready"] is True

    def test_hosted_pack_no_task_still_valid(self):
        """A hosted_pack with no build task at all must always be accepted."""
        ctx = _Context()
        self.mod.app_build_plan(
            AppBuildPlan={
                **_MINIMAL_PLAN_BASE,
                "capability_packs": [_HOSTED_PACK],
                "build_tasks": [],
            },
            context_variables=ctx,
        )
        assert ctx.data["app_plan_ready"] is True

    def test_hosted_pack_and_generated_module_together(self):
        """hosted_pack adapter + generated_module contract simultaneously accepted."""
        ctx = _Context()
        self.mod.app_build_plan(
            AppBuildPlan={
                **_MINIMAL_PLAN_BASE,
                "capability_packs": [_HOSTED_PACK, _GENERATED_PACK],
                "build_tasks": [_HOSTED_ADAPTER_TASK, _GENERATED_MODULE_CONTRACT_TASK],
            },
            context_variables=ctx,
        )
        assert len(ctx.data["app_build_plan"]["build_tasks"]) == 2

    def test_hosted_pack_owned_path_in_modules_dir_rejected(self):
        """adapter task owned_paths must not target modules/{hosted_pack_id}/."""
        bad_task = {
            **_HOSTED_ADAPTER_TASK,
            "task_id": "task_bad_path",
            "task_type": "api_surface",
            "owned_paths": ["modules/wallet/backend/some_file.py"],
        }
        with pytest.raises(ValueError, match="modules/wallet/"):
            self.mod.app_build_plan(
                AppBuildPlan={
                    **_MINIMAL_PLAN_BASE,
                    "capability_packs": [_HOSTED_PACK],
                    "build_tasks": [bad_task],
                },
                context_variables=_Context(),
            )

    def test_hosted_pack_module_contract_still_rejected_with_adapter_present(self):
        """Adding a valid adapter task does not allow the module_contract task."""
        with pytest.raises(ValueError, match="hosted pack 'wallet'"):
            self.mod.app_build_plan(
                AppBuildPlan={
                    **_MINIMAL_PLAN_BASE,
                    "capability_packs": [_HOSTED_PACK],
                    "build_tasks": [_HOSTED_ADAPTER_TASK, _HOSTED_PACK_MODULE_CONTRACT_TASK],
                },
                context_variables=_Context(),
            )

    def test_investor_marketplace_hosted_adapter_accepted(self):
        """investor_marketplace hosted adapter is also valid."""
        im_pack = {**_HOSTED_PACK, "capability_pack_id": "investor_marketplace",
                   "label": "Investor Marketplace"}
        im_adapter = {**_HOSTED_ADAPTER_TASK,
                      "task_id": "task_im_adapter",
                      "capability_pack_id": "investor_marketplace",
                      "owned_paths": ["services/integrations/investor_marketplace_client.py"]}
        ctx = _Context()
        self.mod.app_build_plan(
            AppBuildPlan={
                **_MINIMAL_PLAN_BASE,
                "capability_packs": [im_pack],
                "build_tasks": [im_adapter],
            },
            context_variables=ctx,
        )
        assert ctx.data["app_plan_ready"] is True


# ---------------------------------------------------------------------------
# 7. agents.yaml hosted adapter guidance static checks
# ---------------------------------------------------------------------------

class TestAgentsYamlHostedAdapterGuidance:
    @pytest.fixture(autouse=True)
    def _content(self):
        self._text = _read_text("factory_app/workflows/AppGenerator/agents.yaml")

    def test_appplanagent_hosted_adapter_task_rule_present(self):
        assert "services/integrations/{pack_id}_client.py" in self._text

    def test_appplanagent_hosted_adapter_no_hosted_business_logic(self):
        assert "no hosted business logic" in self._text

    def test_appplanagent_hosted_adapter_no_module_paths(self):
        assert "modules/{pack_id}/" in self._text

    def test_controller_agent_hosted_adapter_lane_present(self):
        assert "Hosted adapter lane" in self._text

    def test_controller_agent_no_hosted_internals_rule(self):
        assert "Do NOT implement hosted business logic" in self._text

    def test_controller_agent_thin_client_rule(self):
        assert "thin Python client" in self._text

    def test_file_contracts_backend_integrations_listed(self):
        fc = _read_yaml("factory_app/workflows/AppGenerator/tools/file_contracts.yaml")
        api_surface_outputs = fc["task_contracts"]["api_surface"]["optional_outputs"]
        assert any("services/integrations" in o for o in api_surface_outputs)

    def test_file_contracts_hosted_adapter_constraint(self):
        fc = _read_yaml("factory_app/workflows/AppGenerator/tools/file_contracts.yaml")
        constraints = fc["task_contracts"]["api_surface"]["hard_constraints"]
        assert any("hosted" in c.lower() for c in constraints)

    def test_appplanagent_adapter_task_decision_rule_present(self):
        assert "Adapter task decision rule" in self._text

    def test_appplanagent_integration_adapters_phase_present(self):
        assert "integration-adapters" in self._text

    def test_output_format_contains_wallet_adapter_task_example(self):
        assert "task_wallet_adapter" in self._text

    def test_output_format_contains_integrations_path_example(self):
        assert "services/integrations/wallet_client.py" in self._text

    def test_output_format_contains_adapter_task_batch_spec(self):
        assert "current_build_task_type" in self._text
        assert "api_surface" in self._text


# ---------------------------------------------------------------------------
# 8. Adapter task surface_kind validation
# ---------------------------------------------------------------------------

class TestHostedAdapterSurfaceKindValidation:
    @pytest.fixture(autouse=True)
    def _module(self):
        self.mod = _load_module(
            "factory_app/workflows/AppGenerator/tools/app_build_plan.py",
            "tests.app_build_plan_adapter_surface_kind",
        )

    def test_adapter_task_with_wrong_surface_kind_rejected(self):
        """api_surface + hosted_pack + services/integrations/ + surface_kind=module → rejected."""
        bad_task = {
            **_HOSTED_ADAPTER_TASK,
            "surface_kind": "module",
        }
        with pytest.raises(ValueError, match="surface_kind"):
            self.mod.app_build_plan(
                AppBuildPlan={
                    **_MINIMAL_PLAN_BASE,
                    "capability_packs": [_HOSTED_PACK],
                    "build_tasks": [bad_task],
                },
                context_variables=_Context(),
            )

    def test_adapter_task_wrong_surface_kind_error_mentions_external_integration(self):
        bad_task = {**_HOSTED_ADAPTER_TASK, "surface_kind": "control_plane"}
        with pytest.raises(ValueError, match="external_integration"):
            self.mod.app_build_plan(
                AppBuildPlan={
                    **_MINIMAL_PLAN_BASE,
                    "capability_packs": [_HOSTED_PACK],
                    "build_tasks": [bad_task],
                },
                context_variables=_Context(),
            )

    def test_adapter_task_with_correct_surface_kind_accepted(self):
        ctx = _Context()
        self.mod.app_build_plan(
            AppBuildPlan={
                **_MINIMAL_PLAN_BASE,
                "capability_packs": [_HOSTED_PACK],
                "build_tasks": [{**_HOSTED_ADAPTER_TASK, "surface_kind": "external_integration"}],
            },
            context_variables=ctx,
        )
        assert ctx.data["app_plan_ready"] is True

    def test_adapter_task_with_no_surface_kind_accepted(self):
        """surface_kind absent (null) on adapter task is not enforced — graceful."""
        task_no_surface_kind = {k: v for k, v in _HOSTED_ADAPTER_TASK.items()
                                if k != "surface_kind"}
        ctx = _Context()
        self.mod.app_build_plan(
            AppBuildPlan={
                **_MINIMAL_PLAN_BASE,
                "capability_packs": [_HOSTED_PACK],
                "build_tasks": [task_no_surface_kind],
            },
            context_variables=ctx,
        )
        assert ctx.data["app_plan_ready"] is True

    def test_non_hosted_api_surface_task_wrong_surface_kind_not_blocked(self):
        """Surface_kind guard only fires for hosted_pack ids — non-hosted api_surface is unaffected."""
        non_hosted_task = {
            "task_id": "task_crm_api",
            "task_type": "api_surface",
            "capability_pack_id": "crm",
            "surface_id": "crm",
            "surface_kind": "module",   # wrong for hosted, but crm is not hosted
            "execution_target": "AppGenerator",
            "initial_agent": "ControllerAgent",
            "description": "CRM API surface.",
            "initial_message": "Generate CRM API.",
            "owned_paths": ["services/integrations/crm_client.py"],
            "depends_on": [],
            "acceptance_criteria": [],
        }
        crm_pack = {
            **_GENERATED_PACK,
            "capability_pack_id": "crm",
            "capability_source": "generated_module",
        }
        ctx = _Context()
        self.mod.app_build_plan(
            AppBuildPlan={
                **_MINIMAL_PLAN_BASE,
                "capability_packs": [crm_pack],
                "build_tasks": [non_hosted_task],
            },
            context_variables=ctx,
        )
        assert ctx.data["app_plan_ready"] is True


# ---------------------------------------------------------------------------
# 9. Task batch item shape for hosted adapter task
# ---------------------------------------------------------------------------

class TestTaskBatchSpecShape:
    def test_output_format_task_batch_has_task_type_api_surface(self):
        text = _read_text("factory_app/workflows/AppGenerator/agents.yaml")
        assert '"current_build_task_type": "api_surface"' in text

    def test_output_format_task_batch_has_wallet_adapter_owned_path(self):
        text = _read_text("factory_app/workflows/AppGenerator/agents.yaml")
        assert '"services/integrations/wallet_client.py"' in text

    def test_output_format_task_batch_has_surface_kind_external_integration(self):
        text = _read_text("factory_app/workflows/AppGenerator/agents.yaml")
        # The task batch item for adapter work should carry surface_kind.
        assert '"surface_kind": "external_integration"' in text

    def test_output_format_task_batch_initial_agent_is_controller(self):
        text = _read_text("factory_app/workflows/AppGenerator/agents.yaml")
        assert '"initial_agent": "ControllerAgent"' in text
