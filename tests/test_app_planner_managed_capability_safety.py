"""
AppPlanAgent managed-capability safety tests.

Verifies:
1. AppCapabilityPack.capability_source is correctly defined in structured_outputs.yaml.
2. app_build_plan.py preserves capability_source through normalization/caching.
3. app_build_plan.py rejects module_contract build tasks for managed_capability entries.
4. app_build_plan.py accepts managed_capability entries with no module_contract build task.
5. generated_module packs still produce module_contract tasks normally.
6. agents.yaml contains the explicit managed_capability rules.
7. hook_managed_capabilities_context.py injects the planning rule and taxonomy.
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

WORKSPACE = Path(__file__).resolve().parents[1]
_TOOLS_DIR = WORKSPACE / "factory_app" / "workflows" / "AppGenerator" / "tools"
_build_context_projection = importlib.import_module("mozaiksai.core.workflow.context.projection")


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

_MANAGED_CAPABILITY: dict = {
    "capability_pack_id": "wallet",
    "surface_id": "wallet_surface",
    "surface_kind": "external_integration",
    "pack_type": "billing_pack",
    "label": "Wallet",
    "summary": "Managed wallet pack.",
    "implementation_mode": "external_integration",
    "capability_source": "managed_capability",
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

_MANAGED_CAPABILITY_MODULE_CONTRACT_TASK: dict = {
    "task_id": "task_wallet_contract",
    "task_type": "module_contract",
    "capability_pack_id": "wallet",  # managed_capability — must be rejected
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

    def test_capability_source_description_covers_managed_capability(self):
        models = _read_yaml(
            "factory_app/workflows/AppGenerator/structured_outputs.yaml"
        )["models"]
        description = models["AppCapabilityPack"]["fields"]["capability_source"].get("description", "")
        assert "managed_capability" in description
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

    def test_required_integrations_use_structured_connector_model(self):
        models = _read_yaml(
            "factory_app/workflows/AppGenerator/structured_outputs.yaml"
        )["models"]
        requirement = models["AppCapabilityPack"]["fields"]["required_integrations"]
        assert requirement["items"] == "AppCapabilityRequiredIntegration"
        fields = models["AppCapabilityRequiredIntegration"]["fields"]
        assert {"service", "provider", "display_name", "kind", "purpose", "required_at", "required_fields"} <= set(fields)
        assert fields["required_fields"]["items"] == "AppIntegrationRequiredField"
        assert "api_key" in fields["kind"]["values"]

    def test_appbuildtask_has_capability_pack_id_field(self):
        models = _read_yaml(
            "factory_app/workflows/AppGenerator/structured_outputs.yaml"
        )["models"]
        task_fields = models["AppBuildTask"]["fields"]
        assert "capability_pack_id" in task_fields
        # Can be null (managed capabilities have no task) or str
        variants = set(task_fields["capability_pack_id"]["variants"])
        assert "str" in variants
        assert "null" in variants


# ---------------------------------------------------------------------------
# 2. app_build_plan.py normalization tests
# ---------------------------------------------------------------------------

class TestAppBuildPlanManagedCapabilityNormalization:
    @pytest.fixture(autouse=True)
    def _module(self):
        self.mod = _load_module(
            "factory_app/workflows/AppGenerator/tools/app_build_plan.py",
            "tests.app_build_plan_managed_capability_safety",
        )

    def test_managed_capability_capability_source_preserved(self):
        ctx = _Context()
        self.mod.app_build_plan(
            AppBuildPlan={
                **_MINIMAL_PLAN_BASE,
                "capability_packs": [_MANAGED_CAPABILITY],
            },
            context_variables=ctx,
        )
        cached = ctx.data["app_build_plan"]
        assert cached["capability_packs"][0]["capability_source"] == "managed_capability"

    def test_context_selected_managed_capability_source_is_normalized(self):
        ctx = _Context(
            capability_packs=[
                {
                    "id": "mozaikspay",
                    "display_name": "MozaiksPay",
                    "capability_source": "managed_capability",
                    "implementation_mode": "external_integration",
                }
            ],
            available_managed_capabilities=[
                {
                    "id": "mozaikspay",
                    "display_name": "MozaiksPay",
                    "capability_source": "managed_capability",
                    "implementation_mode": "external_integration",
                }
            ],
        )
        self.mod.app_build_plan(
            AppBuildPlan={
                **_MINIMAL_PLAN_BASE,
                "capability_packs": [
                    {
                        "capability_pack_id": "mozaikspay",
                        "surface_id": "mozaikspay_surface",
                        "surface_kind": "external_integration",
                        "pack_type": "billing_pack",
                        "label": "MozaiksPay",
                        "summary": "Managed billing.",
                        "implementation_mode": "external_integration",
                    }
                ],
            },
            context_variables=ctx,
        )
        normalized = ctx.data["app_build_plan"]["capability_packs"][0]
        assert normalized["capability_source"] == "managed_capability"
        assert normalized["implementation_mode"] == "external_integration"

    def test_managed_capability_with_no_module_contract_task_is_accepted(self):
        """A managed_capability entry with no build task at all must be accepted."""
        ctx = _Context()
        result = self.mod.app_build_plan(
            AppBuildPlan={
                **_MINIMAL_PLAN_BASE,
                "capability_packs": [_MANAGED_CAPABILITY],
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

    def test_mixed_managed_and_generated_packs_accepted_when_only_generated_has_task(self):
        """Wallet (managed) has no task; orders (generated_module) has module_contract — valid."""
        ctx = _Context()
        self.mod.app_build_plan(
            AppBuildPlan={
                **_MINIMAL_PLAN_BASE,
                "capability_packs": [_MANAGED_CAPABILITY, _GENERATED_PACK],
                "build_tasks": [_GENERATED_MODULE_CONTRACT_TASK],
            },
            context_variables=ctx,
        )
        cached = ctx.data["app_build_plan"]
        assert len(cached["capability_packs"]) == 2
        assert len(cached["build_tasks"]) == 1

    def test_capability_packs_without_capability_source_not_affected(self):
        """Packs with no capability_source are not treated as managed_capability."""
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

    def test_multi_managed_facades_depend_on_their_matching_adapters(self):
        """Multiple managed capabilities must wire each facade to its own adapter task."""
        ctx = _Context()
        notifications_pack = {
            **_MANAGED_CAPABILITY,
            "capability_pack_id": "notifications",
            "surface_id": "notifications_surface",
            "label": "Notifications",
        }
        wallet_adapter = {
            "task_id": "task_wallet_adapter",
            "task_type": "api_surface",
            "capability_pack_id": "wallet",
            "surface_kind": "external_integration",
            "execution_target": "AppGenerator",
            "initial_agent": "ControllerAgent",
            "initial_message": "Generate managed wallet adapter.",
            "owned_paths": ["services/integrations/wallet_client.py"],
            "depends_on": [],
        }
        notifications_adapter = {
            "task_id": "task_notifications_adapter",
            "task_type": "api_surface",
            "capability_pack_id": "notifications",
            "surface_kind": "external_integration",
            "execution_target": "AppGenerator",
            "initial_agent": "ControllerAgent",
            "initial_message": "Generate managed notifications adapter.",
            "owned_paths": ["services/integrations/notifications_client.py"],
            "depends_on": [],
        }
        wallet_facade = {
            "task_id": "task_wallet_dashboard_contract",
            "task_type": "module_contract",
            "capability_pack_id": "wallet_dashboard",
            "surface_kind": "module",
            "execution_target": "AppGenerator",
            "initial_agent": "ConfigMiddlewareAgent",
            "initial_message": "Generate wallet_dashboard facade using wallet_client.",
            "owned_paths": ["modules/wallet_dashboard/module.yaml"],
            "depends_on": [],
        }
        notifications_facade = {
            "task_id": "task_notification_center_contract",
            "task_type": "module_contract",
            "capability_pack_id": "notification_center",
            "surface_kind": "module",
            "execution_target": "AppGenerator",
            "initial_agent": "ConfigMiddlewareAgent",
            "initial_message": "Generate notification_center facade using notifications_client.",
            "owned_paths": ["modules/notification_center/module.yaml"],
            "depends_on": [],
        }
        self.mod.app_build_plan(
            AppBuildPlan={
                **_MINIMAL_PLAN_BASE,
                "capability_packs": [_MANAGED_CAPABILITY, notifications_pack],
                "build_tasks": [
                    wallet_adapter,
                    notifications_adapter,
                    wallet_facade,
                    notifications_facade,
                ],
            },
            context_variables=ctx,
        )
        tasks = {
            task["task_id"]: task
            for task in ctx.data["app_build_plan"]["build_tasks"]
        }
        assert tasks["task_wallet_dashboard_contract"]["depends_on"] == ["task_wallet_adapter"]
        assert tasks["task_notification_center_contract"]["depends_on"] == [
            "task_notifications_adapter"
        ]

    def test_page_task_infers_facade_dependency_from_service_dependency(self):
        """Page tasks with null capability_pack_id still bind to the service's facade contract."""
        ctx = _Context()
        module_task = {
            "task_id": "task_checkout_module",
            "task_type": "module_contract",
            "capability_pack_id": "checkout",
            "surface_kind": "module",
            "execution_target": "AppGenerator",
            "initial_agent": "ConfigMiddlewareAgent",
            "initial_message": "Generate checkout module.",
            "owned_paths": ["modules/checkout/module.yaml"],
            "depends_on": [],
        }
        service_task = {
            "task_id": "task_checkout_services",
            "task_type": "business_services",
            "capability_pack_id": "checkout",
            "surface_kind": "module",
            "execution_target": "AppGenerator",
            "initial_agent": "ServiceAgent",
            "initial_message": "Generate checkout services.",
            "owned_paths": ["modules/checkout/backend/handler.py"],
            "depends_on": ["task_checkout_module"],
        }
        page_task = {
            "task_id": "task_checkout_pages",
            "task_type": "page_bundle",
            "capability_pack_id": None,
            "surface_kind": "ui_only",
            "execution_target": "AppGenerator",
            "initial_agent": "AppSchemaAgent",
            "initial_message": "Generate checkout pages.",
            "owned_paths": ["app.json", "ui/pages/checkout.yaml"],
            "depends_on": ["task_checkout_services"],
        }
        self.mod.app_build_plan(
            AppBuildPlan={
                **_MINIMAL_PLAN_BASE,
                "capability_packs": [],
                "build_tasks": [module_task, service_task, page_task],
            },
            context_variables=ctx,
        )
        tasks = {
            task["task_id"]: task
            for task in ctx.data["app_build_plan"]["build_tasks"]
        }
        assert tasks["task_checkout_pages"]["depends_on"] == [
            "task_checkout_module",
            "task_checkout_services",
        ]

    def test_duplicate_persistence_contract_tasks_are_merged(self):
        ctx = _Context()
        self.mod.app_build_plan(
            AppBuildPlan={
                **_MINIMAL_PLAN_BASE,
                "data_contract": {"version": "1", "surfaces": []},
                "build_tasks": [
                    {
                        "task_id": "task_projects_persistence",
                        "task_type": "persistence_contract",
                        "capability_pack_id": None,
                        "surface_kind": None,
                        "execution_target": "AppGenerator",
                        "initial_agent": "DatabaseAgent",
                        "description": "Project persistence.",
                        "initial_message": "Create projects contract.",
                        "owned_paths": ["data/contract.json"],
                        "depends_on": [],
                        "acceptance_criteria": ["projects declared"],
                    },
                    {
                        "task_id": "task_billing_persistence",
                        "task_type": "persistence_contract",
                        "capability_pack_id": None,
                        "surface_kind": None,
                        "execution_target": "AppGenerator",
                        "initial_agent": "DatabaseAgent",
                        "description": "Billing persistence.",
                        "initial_message": "Create billing contract.",
                        "owned_paths": ["data/contract.json"],
                        "depends_on": [],
                        "acceptance_criteria": ["billing declared"],
                    },
                    {
                        "task_id": "task_billing_module",
                        "task_type": "module_contract",
                        "capability_pack_id": "billing_portal",
                        "surface_kind": "module",
                        "execution_target": "AppGenerator",
                        "initial_agent": "ConfigMiddlewareAgent",
                        "description": "Generate billing facade.",
                        "initial_message": "Generate billing facade.",
                        "owned_paths": ["modules/billing_portal/module.yaml"],
                        "depends_on": ["task_billing_persistence"],
                        "acceptance_criteria": ["module exists"],
                    },
                ],
            },
            context_variables=ctx,
        )
        tasks = ctx.data["app_build_plan"]["build_tasks"]
        persistence_tasks = [task for task in tasks if task["task_type"] == "persistence_contract"]
        module_task = next(task for task in tasks if task["task_id"] == "task_billing_module")

        assert len(persistence_tasks) == 1
        assert persistence_tasks[0]["owned_paths"] == ["data/contract.json"]
        assert "billing declared" in persistence_tasks[0]["acceptance_criteria"]
        assert module_task["depends_on"] == [persistence_tasks[0]["task_id"]]

    def test_duplicate_non_shared_owned_paths_are_rejected(self):
        ctx = _Context()
        task_a = {
            "task_id": "task_a",
            "task_type": "module_contract",
            "capability_pack_id": "billing_portal",
            "surface_kind": "module",
            "execution_target": "AppGenerator",
            "initial_agent": "ConfigMiddlewareAgent",
            "description": "Generate billing facade.",
            "initial_message": "Generate billing facade.",
            "owned_paths": ["modules/billing_portal/module.yaml"],
            "depends_on": [],
            "acceptance_criteria": ["module exists"],
        }
        task_b = {
            **task_a,
            "task_id": "task_b",
            "description": "Generate duplicate billing facade.",
            "initial_message": "Generate duplicate billing facade.",
        }

        with pytest.raises(ValueError, match="overlapping owned_paths"):
            self.mod.app_build_plan(
                AppBuildPlan={
                    **_MINIMAL_PLAN_BASE,
                    "build_tasks": [task_a, task_b],
                },
                context_variables=ctx,
            )


# ---------------------------------------------------------------------------
# 3. Negative test: managed_capability + module_contract → ValueError
# ---------------------------------------------------------------------------

class TestAppBuildPlanManagedCapabilityModuleContractRejection:
    @pytest.fixture(autouse=True)
    def _module(self):
        self.mod = _load_module(
            "factory_app/workflows/AppGenerator/tools/app_build_plan.py",
            "tests.app_build_plan_managed_module_contract_guard",
        )

    def test_rejects_module_contract_for_managed_capability(self):
        with pytest.raises(ValueError, match="managed capability 'wallet'"):
            self.mod.app_build_plan(
                AppBuildPlan={
                    **_MINIMAL_PLAN_BASE,
                    "capability_packs": [_MANAGED_CAPABILITY],
                    "build_tasks": [_MANAGED_CAPABILITY_MODULE_CONTRACT_TASK],
                },
                context_variables=_Context(),
            )

    def test_error_message_mentions_external_integration(self):
        with pytest.raises(ValueError, match="external_integration"):
            self.mod.app_build_plan(
                AppBuildPlan={
                    **_MINIMAL_PLAN_BASE,
                    "capability_packs": [_MANAGED_CAPABILITY],
                    "build_tasks": [_MANAGED_CAPABILITY_MODULE_CONTRACT_TASK],
                },
                context_variables=_Context(),
            )

    def test_rejects_investor_marketplace_managed_capability_module_contract(self):
        im_pack = {
            **_MANAGED_CAPABILITY,
            "capability_pack_id": "investor_marketplace",
            "label": "Investor Marketplace",
        }
        im_task = {
            **_MANAGED_CAPABILITY_MODULE_CONTRACT_TASK,
            "task_id": "task_im_contract",
            "capability_pack_id": "investor_marketplace",
            "owned_paths": ["modules/investor_marketplace/module.yaml"],
        }
        with pytest.raises(ValueError, match="managed capability 'investor_marketplace'"):
            self.mod.app_build_plan(
                AppBuildPlan={
                    **_MINIMAL_PLAN_BASE,
                    "capability_packs": [im_pack],
                    "build_tasks": [im_task],
                },
                context_variables=_Context(),
            )

    def test_generated_module_not_blocked(self):
        """generated_module packs must not be blocked even if a managed_capability is also present."""
        ctx = _Context()
        self.mod.app_build_plan(
            AppBuildPlan={
                **_MINIMAL_PLAN_BASE,
                "capability_packs": [_MANAGED_CAPABILITY, _GENERATED_PACK],
                "build_tasks": [_GENERATED_MODULE_CONTRACT_TASK],  # orders — ok
            },
            context_variables=ctx,
        )
        assert ctx.data["app_plan_ready"] is True


# ---------------------------------------------------------------------------
# 4. agents.yaml static text checks
# ---------------------------------------------------------------------------

class TestAgentsYamlManagedCapabilityRules:
    @pytest.fixture(autouse=True)
    def _content(self):
        self._text = _read_text("factory_app/workflows/AppGenerator/agents.yaml")

    def test_managed_capability_rule_present(self):
        assert "managed_capability" in self._text

    def test_managed_capability_no_module_contract_rule(self):
        # Exact rule from agents.yaml lines 153-154
        assert "Do NOT plan a `module_contract` build task for it" in self._text

    def test_managed_capability_external_integration_rule(self):
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

    def test_appplanagent_has_capability_directory_marker(self):
        assert "[CAPABILITY DIRECTORY]" in self._text
        assert "{{CAPABILITY_DIRECTORY_CONTEXT}}" in self._text

    def test_appplanagent_saas_prioritizes_mozaikspay_with_provider_alternatives(self):
        assert "mozaikspay" in self._text
        assert "hosted MozaiksPay API" in self._text
        assert "external_adapter" in self._text


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
    if "hook_managed_capabilities_context" in sys.modules:
        del sys.modules["hook_managed_capabilities_context"]
    import hook_managed_capabilities_context as m
    return m


class TestHookManagedCapabilityContextInjection:
    @pytest.fixture(autouse=True)
    def _hook(self):
        self.hook = _load_hook()

    def test_planning_rule_injected_when_managed_capabilities_present(self):
        agent = _FakeAgent("AppPlanAgent", context_variables={
            "capability_packs": [{"id": "wallet", "display_name": "Wallet"}],
        })
        self.hook.inject_managed_capabilities_context(agent, [])
        assert "module_contract" in agent.system_message
        assert "external_integration" in agent.system_message

    def test_managed_capabilities_context_header_present(self):
        agent = _FakeAgent("AppPlanAgent", context_variables={
            "capability_packs": [{"id": "managed_wallet"}],
        })
        self.hook.inject_managed_capabilities_context(agent, [])
        assert "[MANAGED CAPABILITIES CONTEXT]" in agent.system_message

    def test_taxonomy_covers_all_five_sources(self):
        agent = _FakeAgent("AppPlanAgent", context_variables={
            "capability_packs": [{"id": "module_execution"}],
        })
        self.hook.inject_managed_capabilities_context(agent, [])
        msg = agent.system_message
        assert "host_universal" in msg
        assert "framework_pack" in msg
        assert "managed_capability" in msg
        assert "generated_module" in msg
        assert "external_adapter" in msg

    def test_no_injection_in_oss_mode(self):
        agent = _FakeAgent("AppPlanAgent", context_variables={
            "capability_packs": None,
        })
        self.hook.inject_managed_capabilities_context(agent, [])
        assert agent.system_message == ""

    def test_pack_listed_in_injected_context(self):
        agent = _FakeAgent("AppPlanAgent", context_variables={
            "capability_packs": [
                {"id": "wallet", "display_name": "Wallet",
                 "capabilities": [{"capability_id": "wallet.view"}]},
                {"id": "investor_marketplace", "display_name": "Investor Marketplace"},
            ],
        })
        self.hook.inject_managed_capabilities_context(agent, [])
        assert "wallet" in agent.system_message
        assert "investor_marketplace" in agent.system_message


class TestCapabilityDirectoryProjection:
    def test_capability_directory_catalog_declares_mozaikspay_first_for_saas(self):
        catalog = _read_yaml(
            "factory_app/build_context/AppGenerator/capability_directory.yaml"
        )
        entries = {
            str(entry.get("id")): entry
            for entry in catalog.get("capabilities") or []
            if isinstance(entry, dict)
        }

        mozaikspay = entries["mozaikspay"]
        assert mozaikspay["recommendation_rank"] == 1
        assert mozaikspay["capability_kind"] == "operator_pack"
        assert "saas" in mozaikspay["domains"]
        assert any("SaaS" in signal or "plans" in signal.lower() for signal in mozaikspay["intent_signals"])
        alternatives = {item["id"] for item in mozaikspay["alternatives"]}
        assert alternatives == {"custom_payments_provider"}
        assert "billing_pack" not in entries

    def test_appgenerator_manifest_projects_capability_directory_to_appplanagent(self):
        manifest = _read_yaml("factory_app/build_context/AppGenerator/context.yaml")
        assets = manifest["assets"]
        capability_asset = next(
            item for item in assets if item["path"] == "capability_directory.yaml"
        )
        assert capability_asset["kind"] == "catalog"
        assert "packs" not in manifest
        projections = {
            item["id"]: item
            for item in capability_asset["projections"]
            if isinstance(item, dict)
        }
        projection = projections["appgenerator_capability_directory_for_planning"]
        assert projection["records"] == "capabilities"
        assert projection["recipients"] == ["AppPlanAgent"]
        assert projection["render"] == "summary"
        assert projection["marker"] == "CAPABILITY_DIRECTORY_CONTEXT"

    def test_appgenerator_middleware_binds_generic_build_context_projection(self):
        middleware = _read_text("factory_app/workflows/AppGenerator/middleware.yaml")
        assert "agent: AppPlanAgent\n  function: mozaiksai.core.workflow.context.projection.inject_build_context_projections" in middleware

    def test_capability_directory_injects_mozaikspay_first_and_custom_provider_alternative(self):
        class _ProjectionAgent:
            name = "AppPlanAgent"

            def __init__(self):
                self.context_variables = {}
                self._mozaiks_prompt_sections = [
                    {
                        "id": "capability_directory",
                        "heading": "[CAPABILITY DIRECTORY]",
                        "content": "{{CAPABILITY_DIRECTORY_CONTEXT}}",
                    }
                ]
                self._system_message = ""

        agent = _ProjectionAgent()
        _build_context_projection.inject_build_context_projections(agent, [])

        injected = agent._mozaiks_prompt_sections[0]["content"]
        assert "[CAPABILITY DIRECTORY]" in injected
        assert "mozaikspay" in injected
        assert "recommendation_rank: 1" in injected
        assert "custom_payments_provider" in injected
        assert "billing_portal facade" in injected


# ---------------------------------------------------------------------------
# 6. Managed capability adapter task validation tests
# ---------------------------------------------------------------------------

_MANAGED_CAPABILITY_ADAPTER_TASK: dict = {
    "task_id": "task_wallet_adapter",
    "task_type": "api_surface",
    "capability_pack_id": "wallet",
    "surface_id": "wallet_surface",
    "surface_kind": "external_integration",
    "execution_target": "AppGenerator",
    "initial_agent": "ControllerAgent",
    "description": "Generate app-side adapter for managed wallet capability.",
    "initial_message": "Generate a thin client in services/integrations/wallet_client.py.",
    "owned_paths": ["services/integrations/wallet_client.py"],
    "depends_on": [],
    "acceptance_criteria": [
        "Does not implement wallet internals",
        "Calls managed wallet API only",
    ],
}


class TestManagedCapabilityAdapterTaskValidation:
    @pytest.fixture(autouse=True)
    def _module(self):
        self.mod = _load_module(
            "factory_app/workflows/AppGenerator/tools/app_build_plan.py",
            "tests.app_build_plan_managed_adapter_validation",
        )

    def test_managed_capability_api_surface_adapter_task_accepted(self):
        """managed_capability with api_surface task at services/integrations/ must be accepted."""
        ctx = _Context()
        result = self.mod.app_build_plan(
            AppBuildPlan={
                **_MINIMAL_PLAN_BASE,
                "capability_packs": [_MANAGED_CAPABILITY],
                "build_tasks": [_MANAGED_CAPABILITY_ADAPTER_TASK],
            },
            context_variables=ctx,
        )
        assert "Build tasks: 1" in result
        cached = ctx.data["app_build_plan"]
        assert cached["build_tasks"][0]["task_id"] == "task_wallet_adapter"

    def test_managed_capability_adapter_task_with_external_integration_surface_kind(self):
        """surface_kind: external_integration on a managed capability adapter task is valid."""
        ctx = _Context()
        self.mod.app_build_plan(
            AppBuildPlan={
                **_MINIMAL_PLAN_BASE,
                "capability_packs": [_MANAGED_CAPABILITY],
                "build_tasks": [{**_MANAGED_CAPABILITY_ADAPTER_TASK, "surface_kind": "external_integration"}],
            },
            context_variables=ctx,
        )
        assert ctx.data["app_plan_ready"] is True

    def test_managed_capability_no_task_still_valid(self):
        """A managed_capability with no build task at all must always be accepted."""
        ctx = _Context()
        self.mod.app_build_plan(
            AppBuildPlan={
                **_MINIMAL_PLAN_BASE,
                "capability_packs": [_MANAGED_CAPABILITY],
                "build_tasks": [],
            },
            context_variables=ctx,
        )
        assert ctx.data["app_plan_ready"] is True

    def test_managed_capability_and_generated_module_together(self):
        """managed_capability adapter + generated_module contract simultaneously accepted."""
        ctx = _Context()
        self.mod.app_build_plan(
            AppBuildPlan={
                **_MINIMAL_PLAN_BASE,
                "capability_packs": [_MANAGED_CAPABILITY, _GENERATED_PACK],
                "build_tasks": [_MANAGED_CAPABILITY_ADAPTER_TASK, _GENERATED_MODULE_CONTRACT_TASK],
            },
            context_variables=ctx,
        )
        assert len(ctx.data["app_build_plan"]["build_tasks"]) == 2

    def test_managed_capability_owned_path_in_modules_dir_rejected(self):
        """adapter task owned_paths must not target modules/{managed_capability_id}/."""
        bad_task = {
            **_MANAGED_CAPABILITY_ADAPTER_TASK,
            "task_id": "task_bad_path",
            "task_type": "api_surface",
            "owned_paths": ["modules/wallet/backend/some_file.py"],
        }
        with pytest.raises(ValueError, match="modules/wallet/"):
            self.mod.app_build_plan(
                AppBuildPlan={
                    **_MINIMAL_PLAN_BASE,
                    "capability_packs": [_MANAGED_CAPABILITY],
                    "build_tasks": [bad_task],
                },
                context_variables=_Context(),
            )

    def test_managed_capability_module_contract_still_rejected_with_adapter_present(self):
        """Adding a valid adapter task does not allow the module_contract task."""
        with pytest.raises(ValueError, match="managed capability 'wallet'"):
            self.mod.app_build_plan(
                AppBuildPlan={
                    **_MINIMAL_PLAN_BASE,
                    "capability_packs": [_MANAGED_CAPABILITY],
                    "build_tasks": [_MANAGED_CAPABILITY_ADAPTER_TASK, _MANAGED_CAPABILITY_MODULE_CONTRACT_TASK],
                },
                context_variables=_Context(),
            )

    def test_investor_marketplace_managed_adapter_accepted(self):
        """investor_marketplace managed capability adapter is also valid."""
        im_pack = {**_MANAGED_CAPABILITY, "capability_pack_id": "investor_marketplace",
                   "label": "Investor Marketplace"}
        im_adapter = {**_MANAGED_CAPABILITY_ADAPTER_TASK,
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
# 7. agents.yaml managed capability adapter guidance static checks
# ---------------------------------------------------------------------------

class TestAgentsYamlManagedAdapterGuidance:
    @pytest.fixture(autouse=True)
    def _content(self):
        self._text = _read_text("factory_app/workflows/AppGenerator/agents.yaml")

    def test_appplanagent_managed_adapter_task_rule_present(self):
        assert "services/integrations/{pack_id}_client.py" in self._text

    def test_appplanagent_managed_adapter_no_managed_business_logic(self):
        assert "no provider business logic" in self._text

    def test_appplanagent_managed_adapter_no_module_paths(self):
        assert "modules/{pack_id}/" in self._text

    def test_controller_agent_managed_adapter_lane_present(self):
        assert "Managed capability adapter lane" in self._text

    def test_controller_agent_no_managed_internals_rule(self):
        assert "Do NOT implement provider business logic" in self._text

    def test_controller_agent_thin_client_rule(self):
        assert "thin Python client" in self._text

    def test_file_contracts_backend_integrations_listed(self):
        fc = _read_yaml("factory_app/build_context/AppGenerator/file_contracts.yaml")
        api_surface_outputs = fc["task_contracts"]["api_surface"]["optional_outputs"]
        assert any("services/integrations" in o for o in api_surface_outputs)

    def test_file_contracts_managed_adapter_constraint(self):
        fc = _read_yaml("factory_app/build_context/AppGenerator/file_contracts.yaml")
        constraints = fc["task_contracts"]["api_surface"]["hard_constraints"]
        assert any("managed capability adapter" in c.lower() for c in constraints)

    def test_appplanagent_adapter_task_decision_rule_present(self):
        assert "Adapter task decision rule" in self._text

    def test_appplanagent_integration_adapters_phase_present(self):
        assert "integration-adapters" in self._text

    def test_output_format_contains_provider_neutral_adapter_task_example(self):
        assert "task_managed_payments_adapter" in self._text

    def test_output_format_contains_integrations_path_example(self):
        assert "services/integrations/managed_payments_client.py" in self._text

    def test_output_format_contains_adapter_task_batch_spec(self):
        assert "current_build_task_type" in self._text
        assert "api_surface" in self._text

    def test_appplanagent_preserves_structured_required_integrations(self):
        assert "Preserve any pack-declared `required_integrations` as structured connector objects" in self._text
        assert "\"required_fields\"" in self._text
        assert "\"frontend_safe\": false" in self._text


# ---------------------------------------------------------------------------
# 8. Adapter task surface_kind validation
# ---------------------------------------------------------------------------

class TestManagedAdapterSurfaceKindValidation:
    @pytest.fixture(autouse=True)
    def _module(self):
        self.mod = _load_module(
            "factory_app/workflows/AppGenerator/tools/app_build_plan.py",
            "tests.app_build_plan_adapter_surface_kind",
        )

    def test_adapter_task_with_wrong_surface_kind_rejected(self):
        """api_surface + managed_capability + services/integrations/ + surface_kind=module → rejected."""
        bad_task = {
            **_MANAGED_CAPABILITY_ADAPTER_TASK,
            "surface_kind": "module",
        }
        with pytest.raises(ValueError, match="surface_kind"):
            self.mod.app_build_plan(
                AppBuildPlan={
                    **_MINIMAL_PLAN_BASE,
                    "capability_packs": [_MANAGED_CAPABILITY],
                    "build_tasks": [bad_task],
                },
                context_variables=_Context(),
            )

    def test_adapter_task_wrong_surface_kind_error_mentions_external_integration(self):
        bad_task = {**_MANAGED_CAPABILITY_ADAPTER_TASK, "surface_kind": "control_plane"}
        with pytest.raises(ValueError, match="external_integration"):
            self.mod.app_build_plan(
                AppBuildPlan={
                    **_MINIMAL_PLAN_BASE,
                    "capability_packs": [_MANAGED_CAPABILITY],
                    "build_tasks": [bad_task],
                },
                context_variables=_Context(),
            )

    def test_adapter_task_with_correct_surface_kind_accepted(self):
        ctx = _Context()
        self.mod.app_build_plan(
            AppBuildPlan={
                **_MINIMAL_PLAN_BASE,
                "capability_packs": [_MANAGED_CAPABILITY],
                "build_tasks": [{**_MANAGED_CAPABILITY_ADAPTER_TASK, "surface_kind": "external_integration"}],
            },
            context_variables=ctx,
        )
        assert ctx.data["app_plan_ready"] is True

    def test_adapter_task_with_no_surface_kind_accepted(self):
        """surface_kind absent (null) on adapter task is not enforced — graceful."""
        task_no_surface_kind = {k: v for k, v in _MANAGED_CAPABILITY_ADAPTER_TASK.items()
                                if k != "surface_kind"}
        ctx = _Context()
        self.mod.app_build_plan(
            AppBuildPlan={
                **_MINIMAL_PLAN_BASE,
                "capability_packs": [_MANAGED_CAPABILITY],
                "build_tasks": [task_no_surface_kind],
            },
            context_variables=ctx,
        )
        assert ctx.data["app_plan_ready"] is True

    def test_non_managed_api_surface_task_wrong_surface_kind_not_blocked(self):
        """Surface_kind guard only fires for managed_capability ids — non-managed api_surface is unaffected."""
        non_managed_task = {
            "task_id": "task_crm_api",
            "task_type": "api_surface",
            "capability_pack_id": "crm",
            "surface_id": "crm",
            "surface_kind": "module",   # wrong for managed, but crm is not managed
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
                "build_tasks": [non_managed_task],
            },
            context_variables=ctx,
        )
        assert ctx.data["app_plan_ready"] is True


# ---------------------------------------------------------------------------
# 9. Task batch item shape for managed capability adapter task
# ---------------------------------------------------------------------------

class TestTaskBatchSpecShape:
    def test_output_format_task_batch_has_task_type_api_surface(self):
        text = _read_text("factory_app/workflows/AppGenerator/agents.yaml")
        assert '"current_build_task_type": "api_surface"' in text

    def test_output_format_task_batch_has_provider_neutral_adapter_owned_path(self):
        text = _read_text("factory_app/workflows/AppGenerator/agents.yaml")
        assert '"services/integrations/managed_payments_client.py"' in text

    def test_output_format_task_batch_has_surface_kind_external_integration(self):
        text = _read_text("factory_app/workflows/AppGenerator/agents.yaml")
        # The task batch item for adapter work should carry surface_kind.
        assert '"surface_kind": "external_integration"' in text

    def test_output_format_task_batch_initial_agent_is_controller(self):
        text = _read_text("factory_app/workflows/AppGenerator/agents.yaml")
        assert '"initial_agent": "ControllerAgent"' in text

