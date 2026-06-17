"""
Managed-capability generation smoke test.

Test scenario: "Build a simple creator dashboard app that lets users view wallet
balance and request payouts using the managed wallet capability."

Four validation levels:
    A. Static checks  — hook injection, managed context formatting, adapter guidance
    B. Build plan     — creator dashboard AppBuildPlan fixture self-consistent
    C. Assembly       — template expansion produces correct artifact tree, drift absent
    D. Page binding   — wallet page schema contract (documented follow-up for full binding)

Drift checks fail if the final generated artifact tree contains:
    - app/modules/wallet/
    - app/capability_packs/
    - STRIPE_SECRET_KEY
    - import stripe
    - mozaikspay_client.py
    - managed_entitlements mutation adapter
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

# ---------------------------------------------------------------------------
# Repo paths
# ---------------------------------------------------------------------------

_WORKSPACE = Path(__file__).resolve().parents[1]
_TOOLS_DIR = _WORKSPACE / "factory_app" / "workflows" / "AppGenerator" / "tools"

# Optional managed-capability integration checks are opt-in so the OSS suite never
# changes behavior just because a proprietary consumer workspace is nearby.
_PACKS_ROOT_ENV = os.getenv("MOZAIKS_MANAGED_CAPABILITIES_ROOT", "").strip()
_REAL_PACKS_ROOT = Path(_PACKS_ROOT_ENV).expanduser().resolve() if _PACKS_ROOT_ENV else None
_REAL_WALLET_TEMPLATE = (
    _REAL_PACKS_ROOT / "templates" / "services" / "integrations" / "wallet_client.py"
    if _REAL_PACKS_ROOT
    else None
)
_REAL_WALLET_MANIFEST = _REAL_PACKS_ROOT / "context.yaml" if _REAL_PACKS_ROOT else None

_REAL_PACK_AVAILABLE = bool(_REAL_WALLET_TEMPLATE and _REAL_WALLET_TEMPLATE.exists())

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_module(relative_path: str, module_name: str):
    file_path = _WORKSPACE / relative_path
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_resolver():
    return _load_module(
        "factory_app/workflows/AppGenerator/tools/resolve_managed_capability_templates.py",
        f"tests.resolve_managed_capability_templates.smoke.{id({})}",
    )


def _load_hook():
    tools_path = str(_TOOLS_DIR)
    if tools_path not in sys.path:
        sys.path.insert(0, tools_path)
    # Force reload for test isolation
    if "hook_managed_capabilities_context" in sys.modules:
        del sys.modules["hook_managed_capabilities_context"]
    import hook_managed_capabilities_context as m  # noqa: PLC0415
    return m


def _read(relative_path: str) -> str:
    return (_WORKSPACE / relative_path).read_text(encoding="utf-8")


def _read_yaml(relative_path: str) -> Any:
    return yaml.safe_load(_read(relative_path))


class _FakeAgent:
    def __init__(self, name: str, context_variables: dict[str, Any] | None = None):
        self.name = name
        self.system_message = ""
        self.context_variables = context_variables or {}

    def update_system_message(self, message: str) -> None:
        self.system_message = message


# ---------------------------------------------------------------------------
# Level A: Static checks — hook injection and adapter guidance
# ---------------------------------------------------------------------------


class TestManagedWalletContextInjection:
    """Level A — Hook injects wallet context; OSS mode is a no-op."""

    def test_hook_injects_context_when_managed_wallet_in_capability_packs(self) -> None:
        hook = _load_hook()
        agent = _FakeAgent("AppPlanAgent", context_variables={
            "capability_packs": [
                {"id": "managed_wallet", "display_name": "Managed Wallet",
                 "capability_source": "managed_capability"},
            ],
        })
        hook.inject_managed_capabilities_context(agent, [])
        assert "[MANAGED CAPABILITIES CONTEXT]" in agent.system_message
        assert "managed_wallet" in agent.system_message

    def test_hook_injects_wallet_pack_capabilities(self) -> None:
        hook = _load_hook()
        agent = _FakeAgent("AppPlanAgent", context_variables={
            "capability_packs": [
                {
                    "id": "wallet",
                    "capability_source": "managed_capability",
                    "status": "active",
                    "capabilities": [
                        {"capability_id": "wallet.view"},
                        {"capability_id": "wallet.payout"},
                        {"capability_id": "wallet.connect_stripe"},
                    ],
                }
            ],
        })
        hook.inject_managed_capabilities_context(agent, [])
        msg = agent.system_message
        assert "wallet" in msg
        assert "wallet.view" in msg
        assert "wallet.payout" in msg

    def test_hook_injects_no_module_contract_planning_rule_for_managed_capability(self) -> None:
        hook = _load_hook()
        agent = _FakeAgent("AppPlanAgent", context_variables={
            "capability_packs": [{"id": "wallet", "capability_source": "managed_capability"}],
        })
        hook.inject_managed_capabilities_context(agent, [])
        msg = agent.system_message
        assert "module_contract" in msg
        assert "external_integration" in msg

    def test_hook_supports_selected_active_pack_surfaces(self) -> None:
        hook = _load_hook()
        agent = _FakeAgent("AppPlanAgent", context_variables={
            "capability_packs": [{"id": "mozaikspay", "capability_source": "managed_capability"}],
        })
        hook.inject_managed_capabilities_context(agent, [])
        msg = agent.system_message
        assert "generate one" in msg
        assert "selected active" in msg
        assert "operator contract" in msg

    def test_hook_no_op_in_oss_mode(self) -> None:
        hook = _load_hook()
        agent = _FakeAgent("AppPlanAgent", context_variables={
            "capability_packs": None,
        })
        hook.inject_managed_capabilities_context(agent, [])
        assert agent.system_message == ""

    def test_hook_oss_mode_does_not_mention_wallet_or_stripe(self) -> None:
        hook = _load_hook()
        agent = _FakeAgent("AppPlanAgent", context_variables={})
        hook.inject_managed_capabilities_context(agent, [])
        msg = agent.system_message.lower()
        assert "wallet" not in msg
        assert "stripe" not in msg

    def test_agents_yaml_adapter_path_is_backend_integrations(self) -> None:
        source = _read("factory_app/workflows/AppGenerator/agents.yaml")
        assert "services/integrations/{pack_id}_client.py" in source

    def test_agents_yaml_no_managed_business_logic_rule(self) -> None:
        source = _read("factory_app/workflows/AppGenerator/agents.yaml")
        assert "no provider business logic" in source

    def test_agents_yaml_controller_agent_thin_client_rule(self) -> None:
        source = _read("factory_app/workflows/AppGenerator/agents.yaml")
        assert "thin Python client" in source

    def test_agents_yaml_no_modules_managed_capability_path(self) -> None:
        source = _read("factory_app/workflows/AppGenerator/agents.yaml")
        assert "modules/{pack_id}/" in source

    def test_file_contracts_api_surface_lists_backend_integrations(self) -> None:
        fc = _read_yaml("factory_app/build_context/AppGenerator/file_contracts.yaml")
        outputs = fc["task_contracts"]["api_surface"]["optional_outputs"]
        assert any("services/integrations" in o for o in outputs)

    def test_file_contracts_api_surface_managed_adapter_constraint(self) -> None:
        fc = _read_yaml("factory_app/build_context/AppGenerator/file_contracts.yaml")
        constraints = fc["task_contracts"]["api_surface"]["hard_constraints"]
        assert any("managed capability adapter" in c.lower() for c in constraints)

    def test_file_contracts_api_surface_no_managed_secrets_or_internals_rule(self) -> None:
        fc = _read_yaml("factory_app/build_context/AppGenerator/file_contracts.yaml")
        constraints = fc["task_contracts"]["api_surface"]["hard_constraints"]
        # The contract must prohibit embedding host secrets
        assert any("secret" in c.lower() or "stripe" in c.lower() for c in constraints)


# ---------------------------------------------------------------------------
# Level B: Creator dashboard AppBuildPlan fixture
# ---------------------------------------------------------------------------

_CREATOR_DASHBOARD_MANAGED_CAPABILITY: dict[str, Any] = {
    "capability_pack_id": "wallet",
    "surface_id": "wallet_surface",
    "surface_kind": "external_integration",
    "pack_type": "billing_pack",
    "label": "Managed Wallet",
    "summary": "Managed wallet — view balance and request payouts.",
    "implementation_mode": "external_integration",
    "capability_source": "managed_capability",
}

_CREATOR_DASHBOARD_WALLET_ADAPTER_TASK: dict[str, Any] = {
    "task_id": "creator_dashboard.wallet_adapter",
    "task_type": "api_surface",
    "capability_pack_id": "wallet",
    "surface_id": "wallet_surface",
    "surface_kind": "external_integration",
    "execution_target": "AppGenerator",
    "initial_agent": "ControllerAgent",
    "description": (
        "Generate a thin app-side client for the managed wallet capability. "
        "Copy the managed wallet adapter template to services/integrations/wallet_client.py. "
        "Do not implement wallet business logic or reference Stripe directly."
    ),
    "initial_message": (
        "Generate a thin adapter in services/integrations/wallet_client.py that wraps the "
        "managed wallet module at POST {MOZAIKS_APP_URL}/api/modules/wallet/{action_id}. "
        "Do not import stripe. Do not reference STRIPE_SECRET_KEY. "
        "Do not implement balance calculation or settlement logic."
    ),
    "owned_paths": ["services/integrations/wallet_client.py"],
    "depends_on": [],
    "acceptance_criteria": [
        "services/integrations/wallet_client.py exists",
        "No Stripe imports in adapter file",
        "No managed wallet internals copied",
    ],
}

_CREATOR_DASHBOARD_FACADE_TASK: dict[str, Any] = {
    "task_id": "creator_dashboard.wallet_dashboard_module",
    "task_type": "module_contract",
    "capability_pack_id": "wallet_dashboard",
    "surface_id": "wallet_dashboard_surface",
    "surface_kind": "module",
    "execution_target": "AppGenerator",
    "initial_agent": "ConfigMiddlewareAgent",
    "description": (
        "Generate app-owned façade module 'wallet_dashboard' that wraps the managed wallet adapter. "
        "Declares actions: get_wallet_summary, request_payout."
    ),
    "initial_message": (
        "Generate module contract for 'wallet_dashboard' (generated_module). "
        "Declare actions: get_wallet_summary, request_payout. "
        "Emit only module.yaml and contracts/events.yaml — no backend Python in this task."
    ),
    "owned_paths": [
        "modules/wallet_dashboard/module.yaml",
    ],
    "depends_on": ["creator_dashboard.wallet_adapter"],
    "acceptance_criteria": [
        "modules/wallet_dashboard/module.yaml exists",
        "module.yaml declares get_wallet_summary and request_payout actions",
    ],
}

_CREATOR_DASHBOARD_MODELS_TASK: dict[str, Any] = {
    "task_id": "creator_dashboard.wallet_dashboard_models",
    "task_type": "data_models",
    "capability_pack_id": "wallet_dashboard",
    "surface_id": "wallet_dashboard_surface",
    "surface_kind": "module",
    "execution_target": "AppGenerator",
    "initial_agent": "ModelAgent",
    "description": "Generate wallet_dashboard typed schemas.",
    "initial_message": "Generate modules/wallet_dashboard/backend/schemas.py with typed request/response shapes.",
    "owned_paths": [
        "modules/wallet_dashboard/backend/schemas.py",
    ],
    "depends_on": ["creator_dashboard.wallet_dashboard_module"],
    "acceptance_criteria": [
        "schemas.py has typed shapes for get_wallet_summary and request_payout",
    ],
}

_CREATOR_DASHBOARD_SERVICES_TASK: dict[str, Any] = {
    "task_id": "creator_dashboard.wallet_dashboard_services",
    "task_type": "business_services",
    "capability_pack_id": "wallet_dashboard",
    "surface_id": "wallet_dashboard_surface",
    "surface_kind": "module",
    "execution_target": "AppGenerator",
    "initial_agent": "ServiceAgent",
    "description": "Generate wallet_dashboard module backend: handler and service.",
    "initial_message": (
        "Generate modules/wallet_dashboard/backend/handler.py and service.py. "
        "Service must import ManagedWalletClient from services.integrations.wallet_client "
        "and delegate to it — do not re-implement wallet logic."
    ),
    "owned_paths": [
        "modules/wallet_dashboard/backend/handler.py",
        "modules/wallet_dashboard/backend/service.py",
    ],
    "depends_on": ["creator_dashboard.wallet_dashboard_models"],
    "acceptance_criteria": [
        "service.py imports ManagedWalletClient from services.integrations.wallet_client",
        "No Stripe imports or STRIPE_SECRET_KEY references",
    ],
}

_CREATOR_DASHBOARD_PAGE_TASK: dict[str, Any] = {
    "task_id": "creator_dashboard.pages",
    "task_type": "page_bundle",
    "capability_pack_id": None,
    "surface_id": "app_shell",
    "surface_kind": "ui_only",
    "execution_target": "AppGenerator",
    "initial_agent": "AppSchemaAgent",
    "description": "Generate creator dashboard pages: dashboard, wallet.",
    "initial_message": (
        "Generate pages: dashboard (summary), wallet (balance + payout request). "
        "Wallet page must bind to /api/modules/wallet_dashboard/ — the app-owned façade module."
    ),
    "owned_paths": [
        "app.json",
        "ui/pages/dashboard.yaml",
        "ui/pages/wallet.yaml",
    ],
    "depends_on": ["creator_dashboard.wallet_dashboard_services"],
    "acceptance_criteria": [
        "ui/pages/wallet.yaml binds to /api/modules/wallet_dashboard/ (not /api/modules/wallet/)",
    ],
}

_CREATOR_DASHBOARD_BUILD_TASKS = [
    _CREATOR_DASHBOARD_WALLET_ADAPTER_TASK,
    _CREATOR_DASHBOARD_FACADE_TASK,
    _CREATOR_DASHBOARD_MODELS_TASK,
    _CREATOR_DASHBOARD_SERVICES_TASK,
    _CREATOR_DASHBOARD_PAGE_TASK,
]

_MINIMAL_PLAN_BASE: dict[str, Any] = {
    "agent_message": "Creator dashboard with managed wallet capability.",
    "app_kind": "saas",
    "pages": [
        {"name": "Dashboard", "route": "/dashboard", "purpose": "Creator earnings overview."},
        {"name": "Wallet", "route": "/wallet", "purpose": "View balance and request payouts."},
    ],
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


class TestCreatorDashboardBuildPlan:
    """Level B — Creator dashboard AppBuildPlan fixture is self-consistent."""

    def test_wallet_pack_is_managed_capability(self) -> None:
        assert _CREATOR_DASHBOARD_MANAGED_CAPABILITY["capability_source"] == "managed_capability"

    def test_wallet_pack_surface_kind_is_external_integration(self) -> None:
        assert _CREATOR_DASHBOARD_MANAGED_CAPABILITY["surface_kind"] == "external_integration"

    def test_wallet_pack_implementation_mode_is_external_integration(self) -> None:
        assert _CREATOR_DASHBOARD_MANAGED_CAPABILITY["implementation_mode"] == "external_integration"

    def test_no_module_contract_task_for_wallet(self) -> None:
        for task in _CREATOR_DASHBOARD_BUILD_TASKS:
            if task["task_type"] == "module_contract":
                assert task["capability_pack_id"] != "wallet", (
                    "module_contract task must not exist for wallet (managed_capability)"
                )

    def test_no_modules_wallet_path_in_owned_paths(self) -> None:
        for task in _CREATOR_DASHBOARD_BUILD_TASKS:
            for path in task.get("owned_paths") or []:
                assert not path.startswith("modules/wallet/"), (
                    f"Drift: managed wallet module path in owned_paths: {path}"
                )
                assert "app/modules/wallet/" not in path

    def test_no_capability_packs_path_in_owned_paths(self) -> None:
        for task in _CREATOR_DASHBOARD_BUILD_TASKS:
            for path in task.get("owned_paths") or []:
                assert "capability_packs" not in path

    def test_wallet_adapter_task_is_api_surface_type(self) -> None:
        adapter = _CREATOR_DASHBOARD_WALLET_ADAPTER_TASK
        assert adapter["task_type"] == "api_surface"

    def test_wallet_adapter_owned_path_is_backend_integrations(self) -> None:
        adapter = _CREATOR_DASHBOARD_WALLET_ADAPTER_TASK
        assert adapter["owned_paths"] == ["services/integrations/wallet_client.py"]

    def test_wallet_adapter_initial_agent_is_controller(self) -> None:
        assert _CREATOR_DASHBOARD_WALLET_ADAPTER_TASK["initial_agent"] == "ControllerAgent"

    def test_wallet_adapter_initial_message_prohibits_stripe(self) -> None:
        msg = _CREATOR_DASHBOARD_WALLET_ADAPTER_TASK["initial_message"]
        assert "Do not import stripe" in msg or "not import stripe" in msg.lower()

    def test_wallet_adapter_initial_message_prohibits_stripe_key(self) -> None:
        msg = _CREATOR_DASHBOARD_WALLET_ADAPTER_TASK["initial_message"]
        assert "STRIPE_SECRET_KEY" in msg

    def test_build_plan_validates_with_app_build_plan_tool(self) -> None:
        mod = _load_module(
            "factory_app/workflows/AppGenerator/tools/app_build_plan.py",
            f"tests.app_build_plan_smoke.{id({})}",
        )

        class _Ctx:
            def __init__(self):
                self.data: dict[str, Any] = {}

            def get(self, key, default=None):
                return self.data.get(key, default)

            def set(self, key, value):
                self.data[key] = value

            def __setitem__(self, key, value):
                self.data[key] = value

        ctx = _Ctx()
        result = mod.app_build_plan(
            AppBuildPlan={
                **_MINIMAL_PLAN_BASE,
                "capability_packs": [_CREATOR_DASHBOARD_MANAGED_CAPABILITY],
                "build_tasks": list(_CREATOR_DASHBOARD_BUILD_TASKS),
            },
            context_variables=ctx,
        )
        assert ctx.data.get("app_plan_ready") is True
        cached = ctx.data["app_build_plan"]
        assert cached["capability_packs"][0]["capability_source"] == "managed_capability"
        assert "Build tasks: 5" in result

    def test_build_plan_carries_managed_capability_required_integrations_from_context(self) -> None:
        mod = _load_module(
            "factory_app/workflows/AppGenerator/tools/app_build_plan.py",
            f"tests.app_build_plan_smoke_integrations.{id({})}",
        )

        class _Ctx:
            def __init__(self):
                self.data: dict[str, Any] = {
                    "available_managed_capabilities": [
                        {
                            "id": "mozaikspay",
                            "capability_source": "managed_capability",
                            "required_integrations": [
                                {
                                    "service": "mozaikspay",
                                    "provider": "mozaikspay",
                                    "kind": "api_key",
                                    "required_fields": [
                                        {"name": "api_base", "type": "url", "frontend_safe": True},
                                        {"name": "client_id", "type": "text", "frontend_safe": True},
                                        {"name": "client_secret", "type": "secret", "frontend_safe": False},
                                    ],
                                }
                            ],
                        }
                    ]
                }

            def get(self, key, default=None):
                return self.data.get(key, default)

            def set(self, key, value):
                self.data[key] = value

            def __setitem__(self, key, value):
                self.data[key] = value

        ctx = _Ctx()
        mod.app_build_plan(
            AppBuildPlan={
                **_MINIMAL_PLAN_BASE,
                "capability_packs": [
                    {
                        "capability_pack_id": "mozaikspay",
                        "capability_source": "managed_capability",
                    }
                ],
                "build_tasks": [
                    {
                        **_CREATOR_DASHBOARD_WALLET_ADAPTER_TASK,
                        "task_id": "saas.mozaikspay_adapter",
                        "capability_pack_id": "mozaikspay",
                        "owned_paths": ["services/integrations/mozaikspay_client.py"],
                    }
                ],
            },
            context_variables=ctx,
        )

        pack = ctx.data["app_build_plan"]["capability_packs"][0]
        requirement = pack["required_integrations"][0]
        assert requirement["service"] == "mozaikspay"
        assert {field["name"] for field in requirement["required_fields"]} == {
            "api_base",
            "client_id",
            "client_secret",
        }

    def test_build_plan_rejects_if_wallet_module_contract_added(self) -> None:
        mod = _load_module(
            "factory_app/workflows/AppGenerator/tools/app_build_plan.py",
            f"tests.app_build_plan_smoke_guard.{id({})}",
        )

        class _Ctx:
            def __init__(self):
                self.data: dict[str, Any] = {}

            def get(self, key, default=None):
                return self.data.get(key, default)

            def __setitem__(self, key, value):
                self.data[key] = value

        drift_task = {
            "task_id": "drift_wallet_contract",
            "task_type": "module_contract",
            "capability_pack_id": "wallet",
            "surface_id": "wallet_surface",
            "surface_kind": "module",
            "execution_target": "AppGenerator",
            "initial_agent": "ConfigMiddlewareAgent",
            "description": "DRIFT: attempting to regenerate wallet module.",
            "initial_message": "Generate wallet module contract.",
            "owned_paths": ["modules/wallet/module.yaml"],
            "depends_on": [],
            "acceptance_criteria": [],
        }
        with pytest.raises(ValueError, match="managed capability"):
            mod.app_build_plan(
                AppBuildPlan={
                    **_MINIMAL_PLAN_BASE,
                    "capability_packs": [_CREATOR_DASHBOARD_MANAGED_CAPABILITY],
                    "build_tasks": [drift_task],
                },
                context_variables=_Ctx(),
            )

    def test_dependency_order_is_consistent(self) -> None:
        task_ids = {t["task_id"] for t in _CREATOR_DASHBOARD_BUILD_TASKS}
        for task in _CREATOR_DASHBOARD_BUILD_TASKS:
            for dep in task.get("depends_on") or []:
                assert dep in task_ids, f"Unknown dependency: {dep}"


# ---------------------------------------------------------------------------
# Level C: Assembly + template expansion
# ---------------------------------------------------------------------------


@pytest.fixture()
def wallet_pack_root(tmp_path: Path) -> Path:
    """Minimal pack source with active wallet context and template tree."""
    wallet_dir = tmp_path
    tpl_dir = wallet_dir / "templates" / "services" / "integrations"
    tpl_dir.mkdir(parents=True)

    manifest = {
        "context_id": "wallet",
        "assets": [
            {"path": "templates/", "kind": "templates"},
        ],
        "pack": {
            "id": "wallet",
            "status": "active",
            "capability_source": "managed_capability",
        },
    }
    (wallet_dir / "context.yaml").write_text(yaml.dump(manifest), encoding="utf-8")
    (tpl_dir / "wallet_client.py").write_text(
        '"""\nManaged Wallet Adapter — generated app-side client.\n"""\n'
        "import httpx\n\n"
        "class ManagedWalletClient:\n"
        "    async def get_wallet_summary(self): ...\n"
        "    async def request_payout(self, *, amount=None): ...\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture()
def pack_sources(wallet_pack_root: Path) -> list[dict[str, Any]]:
    return [
        {
            "id": "wallet",
            "capability_source": "managed_capability",
            "pack_source_path": str(wallet_pack_root),
        }
    ]


def _simulate_creator_dashboard_feature_outputs() -> list[dict[str, Any]]:
    """Simulate agent-generated code_files for the creator dashboard app."""
    return [
        {
            "code_files": [
                {"filename": "app.json", "content": '{"appName": "Creator Dashboard"}'},
                {"filename": "ui/pages/dashboard.yaml",
                 "content": "schema_version: mozaiks.page\npage_id: dashboard\n"},
                {"filename": "ui/pages/wallet.yaml",
                 "content": "schema_version: mozaiks.page\npage_id: wallet\n"},
            ]
        }
    ]


class TestCreatorDashboardAssembly:
    """Level C — Assembly with template expansion produces canonical artifact tree."""

    def test_wallet_adapter_appears_at_correct_path(
        self, pack_sources: list[dict[str, Any]]
    ) -> None:
        resolver = _load_resolver()
        template_files = resolver.resolve_managed_capability_templates(pack_sources)
        assert len(template_files) == 1
        assert template_files[0]["filename"] == "services/integrations/wallet_client.py"

    def test_assembly_includes_wallet_adapter_and_pages(
        self, pack_sources: list[dict[str, Any]]
    ) -> None:
        from factory_app.workflows.AppGenerator.tools.assembly_phase import assemble_features

        resolver = _load_resolver()
        template_files = resolver.resolve_managed_capability_templates(pack_sources)
        feature_outputs = _simulate_creator_dashboard_feature_outputs()
        base_result = asyncio.run(assemble_features("creator-dashboard-test", feature_outputs))

        # Overlay templates (mirrors assemble_app_tasks logic)
        file_map = {f["filename"]: f["content"] for f in base_result["code_files"]}
        for tpl in template_files:
            file_map[tpl["filename"]] = tpl["content"]
        final_filenames = set(file_map.keys())

        assert "services/integrations/wallet_client.py" in final_filenames
        assert "ui/pages/wallet.yaml" in final_filenames
        assert "app.json" in final_filenames

    def test_no_modules_wallet_in_assembled_output(
        self, pack_sources: list[dict[str, Any]]
    ) -> None:
        resolver = _load_resolver()
        template_files = resolver.resolve_managed_capability_templates(pack_sources)
        for entry in template_files:
            assert not entry["filename"].startswith("modules/wallet"), (
                f"Drift: modules/wallet/ path in template output: {entry['filename']}"
            )
            assert "app/modules/wallet" not in entry["filename"]

    def test_no_capability_packs_path_in_assembled_output(
        self, pack_sources: list[dict[str, Any]]
    ) -> None:
        resolver = _load_resolver()
        template_files = resolver.resolve_managed_capability_templates(pack_sources)
        for entry in template_files:
            assert "capability_packs" not in entry["filename"]

    def test_wallet_adapter_content_has_no_stripe_import(
        self, pack_sources: list[dict[str, Any]]
    ) -> None:
        resolver = _load_resolver()
        template_files = resolver.resolve_managed_capability_templates(pack_sources)
        for entry in template_files:
            content = entry["content"]
            import_lines = [ln.strip() for ln in content.splitlines()
                            if ln.strip().startswith(("import ", "from "))]
            stripe_imports = [ln for ln in import_lines if "stripe" in ln.lower()]
            assert not stripe_imports, (
                f"Drift: stripe import in wallet adapter: {stripe_imports}"
            )

    def test_wallet_adapter_content_has_no_stripe_secret_key(
        self, pack_sources: list[dict[str, Any]]
    ) -> None:
        resolver = _load_resolver()
        template_files = resolver.resolve_managed_capability_templates(pack_sources)
        for entry in template_files:
            assert "STRIPE_SECRET_KEY" not in entry["content"], (
                "Drift: STRIPE_SECRET_KEY found in wallet adapter content"
            )

    def test_wallet_adapter_content_has_no_managed_module_import(
        self, pack_sources: list[dict[str, Any]]
    ) -> None:
        resolver = _load_resolver()
        template_files = resolver.resolve_managed_capability_templates(pack_sources)
        for entry in template_files:
            import_lines = [ln.strip() for ln in entry["content"].splitlines()
                            if ln.strip().startswith(("import ", "from "))]
            managed_imports = [ln for ln in import_lines if "app.modules.wallet" in ln]
            assert not managed_imports, (
                f"Drift: managed wallet module imported in adapter: {managed_imports}"
            )

    def test_oss_mode_no_template_expansion(self) -> None:
        resolver = _load_resolver()
        result = resolver.resolve_managed_capability_templates(None)
        assert result == []

    def test_oss_mode_with_empty_pack_sources(self) -> None:
        resolver = _load_resolver()
        result = resolver.resolve_managed_capability_templates([])
        assert result == []

    def test_managed_template_wins_over_llm_generated_adapter(
        self, pack_sources: list[dict[str, Any]]
    ) -> None:
        """Template overlay takes priority over any LLM-generated content for adapter paths."""
        from factory_app.workflows.AppGenerator.tools.assembly_phase import assemble_features

        resolver = _load_resolver()
        template_files = resolver.resolve_managed_capability_templates(pack_sources)
        # Simulate LLM generating a (wrong) adapter file
        llm_output = [{
            "code_files": [
                {"filename": "services/integrations/wallet_client.py",
                 "content": "# llm generated stub — should be replaced\n"},
            ]
        }]
        base = asyncio.run(assemble_features("creator-dashboard-test", llm_output))
        file_map = {f["filename"]: f["content"] for f in base["code_files"]}
        # Template wins
        for tpl in template_files:
            file_map[tpl["filename"]] = tpl["content"]

        adapter_content = file_map["services/integrations/wallet_client.py"]
        assert "llm generated stub" not in adapter_content
        assert "ManagedWalletClient" in adapter_content


# ---------------------------------------------------------------------------
# Real wallet template content checks (skipped if mozaiks-app not present)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _REAL_PACK_AVAILABLE,
    reason="Set MOZAIKS_MANAGED_CAPABILITIES_ROOT to run managed wallet pack drift checks",
)
class TestRealWalletTemplateDriftGuards:
    """Verify the real managed wallet template contains no drift patterns."""

    @pytest.fixture(autouse=True)
    def _load_template(self) -> None:
        assert _REAL_WALLET_TEMPLATE is not None
        self.content = _REAL_WALLET_TEMPLATE.read_text(encoding="utf-8")
        self.import_lines = [
            ln.strip() for ln in self.content.splitlines()
            if ln.strip().startswith(("import ", "from "))
        ]

    def test_real_template_has_no_stripe_import(self) -> None:
        bad = [ln for ln in self.import_lines if "stripe" in ln.lower()]
        assert not bad, f"Real wallet template imports stripe: {bad}"

    def test_real_template_has_no_stripe_secret_key(self) -> None:
        # Exclude docstring/comment lines — the template may mention the key
        # as a negative rule ("Never reference STRIPE_SECRET_KEY").
        # Only executable code lines must not contain it.
        code_lines = [
            ln for ln in self.content.splitlines()
            if ln.strip() and not ln.strip().startswith(("#", "-", '"', "'"))
        ]
        assert not any("STRIPE_SECRET_KEY" in ln for ln in code_lines), (
            "STRIPE_SECRET_KEY referenced in executable code of wallet adapter template"
        )

    def test_real_template_has_no_managed_wallet_module_import(self) -> None:
        bad = [ln for ln in self.import_lines if "app.modules.wallet" in ln]
        assert not bad, f"Real wallet template imports managed module: {bad}"

    def test_real_template_has_no_mozaikspay_client(self) -> None:
        assert "mozaikspay_client" not in self.content
        assert "MozaiksPayClient" not in self.content

    def test_real_template_has_no_managed_entitlements_mutation(self) -> None:
        # Adapter must not call entitlements to grant/revoke — read-only usage check is allowed
        assert "grant_capability" not in self.content
        assert "revoke_capability" not in self.content
        assert "managed_entitlements.backend" not in self.content

    def test_real_template_uses_mozaiks_app_url(self) -> None:
        assert "MOZAIKS_APP_URL" in self.content

    def test_real_template_uses_httpx_not_stripe_sdk(self) -> None:
        assert "httpx" in self.content

    def test_real_template_expands_correctly_via_resolver(self) -> None:
        assert _REAL_PACKS_ROOT is not None
        resolver = _load_resolver()
        pack_sources = [
            {
                "id": "wallet",
                "capability_source": "managed_capability",
                "pack_source_path": str(_REAL_PACKS_ROOT),
            }
        ]
        result = resolver.resolve_managed_capability_templates(pack_sources)
        assert len(result) == 1
        assert result[0]["filename"] == "services/integrations/wallet_client.py"
        assert result[0]["content"] == self.content


# ---------------------------------------------------------------------------
# Level D: Page binding
# ---------------------------------------------------------------------------


class TestWalletPageBinding:
    """
    Level D — Wallet page schema must bind to the adapter or platform module action.

    Full UI binding conventions for managed capability adapter pages are a follow-up:
      - wallet.yaml should reference /api/modules/wallet/{action} or the adapter path
      - no direct Stripe API calls from page actions
      - no app/modules/wallet/ path references in page schema

    These tests check the static shape of a minimal wallet page fixture and
    document the follow-up items as explicit marks.
    """

    _WALLET_PAGE_YAML = """\
schema_version: mozaiks.page
page_id: wallet
title: Wallet
description: View wallet balance and request payouts.
sections:
  - id: wallet-balance
    primitive: SummaryStrip
    title: Available Balance
    config:
      items:
        - label: Available Balance
          value: "$0"
  - id: payout-action
    primitive: Button
    label: Request Payout
    action:
      type: api
      endpoint: /api/modules/wallet_dashboard/request_payout
      method: POST
"""

    def test_wallet_page_uses_facade_module_api_path(self) -> None:
        """Page actions bind to /api/modules/wallet_dashboard/* — the app-owned façade module."""
        assert "/api/modules/wallet_dashboard/" in self._WALLET_PAGE_YAML

    def test_wallet_page_does_not_bind_directly_to_managed_capability(self) -> None:
        """Page schema must not bind directly to the managed wallet pack id."""
        # The page must go through the façade module, not the managed capability id
        import re
        direct_managed_refs = re.findall(r"/api/modules/wallet/", self._WALLET_PAGE_YAML)
        assert not direct_managed_refs, (
            "Page binds directly to managed wallet pack — must use façade module wallet_dashboard"
        )

    def test_wallet_page_has_no_direct_stripe_reference(self) -> None:
        """Page schema must not reference Stripe directly."""
        assert "stripe.com" not in self._WALLET_PAGE_YAML.lower()
        assert "STRIPE_SECRET_KEY" not in self._WALLET_PAGE_YAML

    def test_wallet_page_has_no_modules_wallet_path(self) -> None:
        """Page schema must not reference app/modules/wallet internal paths."""
        assert "app/modules/wallet" not in self._WALLET_PAGE_YAML
        assert "modules/wallet/backend" not in self._WALLET_PAGE_YAML

    def test_wallet_page_binding_uses_facade_not_direct_module_path(self) -> None:
        """Pages calling managed wallet bind through the façade module, not managed capability id."""
        assert "/api/modules/wallet_dashboard/" in self._WALLET_PAGE_YAML
        assert "/api/modules/wallet/" not in self._WALLET_PAGE_YAML


# ---------------------------------------------------------------------------
# Façade module convention tests
# ---------------------------------------------------------------------------


class TestFacadeModuleConvention:
    """
    Verify the façade module convention is correctly represented across:
    - The build plan fixture (task types, ownership, dependencies)
    - agents.yaml guidance (AppPlanAgent, AppSchemaAgent, ServiceAgent)
    - file_contracts.yaml constraints
    """

    def test_build_plan_includes_facade_module_as_generated_module_task(self) -> None:
        """Build plan must include a module_contract task for the façade module."""
        facade_tasks = [
            t for t in _CREATOR_DASHBOARD_BUILD_TASKS
            if t["task_type"] == "module_contract" and t["capability_pack_id"] == "wallet_dashboard"
        ]
        assert len(facade_tasks) == 1, (
            "Expected exactly one module_contract task for wallet_dashboard façade"
        )

    def test_facade_module_task_is_not_managed_capability(self) -> None:
        """Façade module task must be module_contract, NOT a managed_capability entry."""
        facade_task = next(
            t for t in _CREATOR_DASHBOARD_BUILD_TASKS
            if t.get("capability_pack_id") == "wallet_dashboard"
        )
        assert facade_task["task_type"] == "module_contract"
        # surface_kind must be module, not external_integration
        assert facade_task.get("surface_kind") == "module"

    def test_facade_module_owned_paths_are_under_wallet_dashboard(self) -> None:
        """Façade module owns paths under modules/wallet_dashboard/, not modules/wallet/."""
        facade_task = next(
            t for t in _CREATOR_DASHBOARD_BUILD_TASKS
            if t.get("capability_pack_id") == "wallet_dashboard"
        )
        for path in facade_task["owned_paths"]:
            assert path.startswith("modules/wallet_dashboard/"), (
                f"Façade module path must be under modules/wallet_dashboard/: {path}"
            )

    def test_facade_module_actions_match_page_api_endpoints(self) -> None:
        """Façade module declared actions must match the action ids used in page endpoints."""
        facade_task = next(
            t for t in _CREATOR_DASHBOARD_BUILD_TASKS
            if t.get("capability_pack_id") == "wallet_dashboard"
        )
        initial_message = facade_task["initial_message"]
        # Both actions referenced in the page schema must appear in the module declaration
        assert "get_wallet_summary" in initial_message
        assert "request_payout" in initial_message

    def test_pages_do_not_bind_directly_to_managed_capability_id(self) -> None:
        """Page task initial_message must not instruct binding to /api/modules/wallet/."""
        page_task = next(
            t for t in _CREATOR_DASHBOARD_BUILD_TASKS
            if t["task_type"] == "page_bundle"
        )
        msg = page_task["initial_message"]
        assert "/api/modules/wallet/" not in msg or "/api/modules/wallet_dashboard/" in msg

    def test_modules_wallet_path_absent_in_all_owned_paths(self) -> None:
        """No task in the build plan owns a path starting with modules/wallet/."""
        for task in _CREATOR_DASHBOARD_BUILD_TASKS:
            for path in task.get("owned_paths") or []:
                assert not path.startswith("modules/wallet/"), (
                    f"Drift: owned_paths contains modules/wallet/ — managed capability must not be regenerated: {path}"
                )

    def test_wallet_adapter_client_still_generated_as_api_surface_task(self) -> None:
        """The managed capability adapter client must still be generated as an api_surface task."""
        adapter_tasks = [
            t for t in _CREATOR_DASHBOARD_BUILD_TASKS
            if t["task_type"] == "api_surface" and "wallet_client.py" in str(t.get("owned_paths", []))
        ]
        assert len(adapter_tasks) == 1

    def test_facade_module_depends_on_wallet_adapter(self) -> None:
        """Façade module task must declare a dependency on the wallet adapter task."""
        facade_task = next(
            t for t in _CREATOR_DASHBOARD_BUILD_TASKS
            if t.get("capability_pack_id") == "wallet_dashboard"
        )
        assert "creator_dashboard.wallet_adapter" in facade_task.get("depends_on", [])

    def test_appplanagent_page_binding_rule_present_in_agents_yaml(self) -> None:
        """AppPlanAgent must instruct that pages bind to the façade module."""
        source = _read("factory_app/workflows/AppGenerator/agents.yaml")
        assert "Managed-capability page binding rule" in source
        assert "façade module" in source or "facade module" in source.lower()

    def test_appplanagent_allows_app_owned_facade_for_managed_capability(self) -> None:
        """Managed capabilities must not block app-owned facade module planning."""
        source = _read("factory_app/workflows/AppGenerator/agents.yaml")
        assert "for the managed capability id itself" in source
        assert "also plan a generated facade module" in source
        assert "capability_source: generated_module" in source

    def test_structured_output_managed_capability_description_allows_facade_modules(self) -> None:
        """Capability source schema must distinguish managed capability ids from facades."""
        source = _read("factory_app/workflows/AppGenerator/structured_outputs.yaml")
        assert "do not generate" in source
        assert "for the managed capability id itself" in source
        assert "facade module_contract tasks" in source

    def test_appschemaagent_facade_binding_guidance_present(self) -> None:
        """AppSchemaAgent must instruct pages to use the façade module id, not managed capability id."""
        source = _read("factory_app/workflows/AppGenerator/agents.yaml")
        assert "facade_module_id" in source or "façade module" in source
        assert "managed_capability_id" in source

    def test_serviceagent_managed_adapter_integration_rule_present(self) -> None:
        """ServiceAgent rule 19 must describe how façade service imports the adapter client."""
        source = _read("factory_app/workflows/AppGenerator/agents.yaml")
        assert "Managed capability adapter integration rule" in source
        assert "from services.integrations.{pack_id}_client import {PackIdClient}" in source

    def test_file_contracts_api_surface_page_binding_constraint(self) -> None:
        """file_contracts.yaml api_surface must prohibit direct page binding to managed capability."""
        fc = _read_yaml("factory_app/build_context/AppGenerator/file_contracts.yaml")
        constraints = fc["task_contracts"]["api_surface"]["hard_constraints"]
        facade_rules = [c for c in constraints if "façade" in c.lower() or "facade" in c.lower()]
        assert facade_rules, (
            "api_surface hard_constraints must include façade module page binding rule"
        )






