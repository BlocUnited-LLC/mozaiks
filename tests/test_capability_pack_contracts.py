from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest
import yaml


WORKSPACE = Path(__file__).resolve().parents[1]


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


def _minimal_app_build_plan(build_task: dict) -> dict:
    return {
        "agent_message": "Planned the product.",
        "app_kind": "marketplace",
        "pages": [{"name": "Home", "route": "/", "purpose": "Land on the product"}],
        "entities": [],
        "roles": ["admin"],
        "auth_strategy": "role-based",
        "backend_scope": ["app harness"],
        "frontend_scope": ["home ui"],
        "theme_preferences": None,
        "brand_intent": None,
        "capability_packs": [],
        "external_integrations": [],
        "agent_backend_required": False,
        "build_tasks": [build_task],
        "generation_order": ["control-plane"],
    }


class _Context:
    def __init__(self, **data) -> None:
        self.data = dict(data)

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value) -> None:
        self.data[key] = value

    def __setitem__(self, key, value) -> None:
        self.data[key] = value


def test_valueengine_structured_outputs_define_capability_pack_contract() -> None:
    structured_outputs = _read_yaml(
        "factory_app/workflows/ValueEngine/structured_outputs.yaml"
    )
    models = structured_outputs["models"]

    concept_fields = models["ConceptBlueprint"]["fields"]
    capability_pack_hints = concept_fields["capability_pack_hints"]
    agentic_capabilities = concept_fields["agentic_capabilities"]
    brand_intent = concept_fields["brand_intent"]
    build_plan_fields = models["BuildPlan"]["fields"]
    pack_spec_fields = models["CapabilityPackSpec"]["fields"]

    assert capability_pack_hints["items"] == "str"
    assert agentic_capabilities["items"] == "str"
    assert set(brand_intent["variants"]) == {"BrandIntent", "null"}
    assert build_plan_fields["capability_packs"]["items"] == "CapabilityPackSpec"
    assert "messaging_pack" in pack_spec_fields["pack_type"]["values"]
    assert "marketplace_pack" in pack_spec_fields["pack_type"]["values"]
    assert "agent_backend_integration" in pack_spec_fields["pack_type"]["values"]


def test_appgenerator_structured_outputs_define_capability_first_build_plan() -> None:
    structured_outputs = _read_yaml(
        "factory_app/workflows/AppGenerator/structured_outputs.yaml"
    )
    models = structured_outputs["models"]

    assert "AppCapabilityPack" in models
    assert "BrandIntent" in models
    assert (
        models["AppBuildPlan"]["fields"]["capability_packs"]["items"]
        == "AppCapabilityPack"
    )
    assert set(models["AppBuildPlan"]["fields"]["brand_intent"]["variants"]) == {"BrandIntent", "null"}
    task_variants = set(models["AppBuildTask"]["fields"]["capability_pack_id"]["variants"])
    assert task_variants == {"str", "null"}


def test_app_plan_agent_prompt_requires_capability_first_planning() -> None:
    content = _read_text("factory_app/workflows/AppGenerator/agents.yaml")

    assert "Decompose into product capability packs first" in content
    assert "capability_pack_hints" in content
    assert "experience_spec_document" in content
    assert "The default owner of persistent pages is `AppSchemaAgent`." in content
    assert "Do NOT plan a second raw-frontend lane inside AppGenerator." in content
    assert "domain-specific profile records" in content
    assert "host-owned `/api/me` account/profile contract" in content
    assert '"capability_packs": [' in content
    assert '"capability_pack_id": null' in content
    assert '\n        "workflows": [' in content


def test_appgenerator_context_exposes_current_experience_spec_alias() -> None:
    context_vars = _read_yaml("factory_app/workflows/AppGenerator/context_variables.yaml")
    definitions = context_vars["definitions"]
    agents = context_vars["agents"]

    assert "experience_spec_document" in definitions
    assert definitions["experience_spec_document"]["source"]["query_template"]["kind"] == "ui_schema"
    assert "experience_spec_document" in agents["AppSchemaAgent"]["variables"]


def test_app_build_plan_tool_preserves_capability_packs_theme_preferences_and_brand_intent() -> None:
    module = _load_module(
        "factory_app/workflows/AppGenerator/tools/app_build_plan.py",
        "tests.app_build_plan_tool_direct",
    )
    context = _Context()

    result = module.app_build_plan(
        AppBuildPlan={
            "agent_message": "Planned the product.",
            "app_kind": "social-platform",
            "pages": [{"name": "Inbox", "route": "/inbox", "purpose": "Read messages"}],
            "entities": [{"name": "Message", "operations": ["create", "read"]}],
            "roles": ["user"],
            "auth_strategy": "role-based",
            "backend_scope": ["messaging api"],
            "frontend_scope": ["inbox ui"],
            "theme_preferences": "clean neon dark",
            "brand_intent": {
                "style_summary": "Futuristic but trustworthy social workspace",
                "brand_keywords": ["neon", "space", "trustworthy"],
                "experience_goals": ["premium", "focused"],
                "appearance_hint": "dark",
            },
            "capability_packs": [
                {
                    "capability_pack_id": "messaging_core",
                    "pack_type": "messaging_pack",
                    "label": "Messaging",
                    "summary": "Inbox and threads",
                    "implementation_mode": "hybrid",
                }
            ],
            "external_integrations": [],
            "agent_backend_required": False,
            "build_tasks": [
                {
                    "task_id": "task_inbox_page",
                    "task_type": "page_bundle",
                    "capability_pack_id": "messaging_core",
                    "execution_target": "AppGenerator",
                    "initial_agent": "AppSchemaAgent",
                    "description": "Compile inbox page schema",
                    "initial_message": "Compile the inbox page schema into app.json/ui/pages/*.yaml.",
                    "owned_paths": ["ui/pages/inbox.yaml"],
                    "depends_on": [],
                    "acceptance_criteria": ["Route exists"],
                }
            ],
            "generation_order": ["backend-foundation", "app-schema-bundle"],
        },
        context_variables=context,
    )

    cached_plan = context.data["app_build_plan"]
    assert cached_plan["theme_preferences"] == "clean neon dark"
    assert cached_plan["brand_intent"]["style_summary"] == "Futuristic but trustworthy social workspace"
    assert cached_plan["capability_packs"][0]["capability_pack_id"] == "messaging_core"
    assert cached_plan["build_tasks"][0]["capability_pack_id"] == "messaging_core"
    assert "Capability packs: 1" in result


def test_app_build_plan_tool_rejects_non_schema_page_bundles() -> None:
    module = _load_module(
        "factory_app/workflows/AppGenerator/tools/app_build_plan.py",
        "tests.app_build_plan_tool_schema_guard",
    )

    with pytest.raises(ValueError, match="non-schema owner"):
        module.app_build_plan(
            AppBuildPlan={
                "agent_message": "Planned the product.",
                "app_kind": "social-platform",
                "pages": [{"name": "Inbox", "route": "/inbox", "purpose": "Read messages"}],
                "entities": [{"name": "Message", "operations": ["create", "read"]}],
                "roles": ["user"],
                "auth_strategy": "role-based",
                "backend_scope": ["messaging api"],
                "frontend_scope": ["inbox ui"],
                "theme_preferences": None,
                "brand_intent": None,
                "capability_packs": [
                    {
                        "capability_pack_id": "messaging_core",
                        "pack_type": "messaging_pack",
                        "label": "Messaging",
                        "summary": "Inbox and threads",
                        "implementation_mode": "hybrid",
                    }
                ],
                "external_integrations": [],
                "agent_backend_required": False,
                "build_tasks": [
                    {
                        "task_id": "task_invalid_page_owner",
                        "task_type": "page_bundle",
                        "capability_pack_id": "messaging_core",
                        "execution_target": "AppGenerator",
                        "initial_agent": "AssemblyAgent",
                        "description": "Compile inbox page",
                        "initial_message": "Compile the inbox page schema",
                        "owned_paths": ["ui/pages/inbox.yaml"],
                        "depends_on": [],
                        "acceptance_criteria": ["Route exists"],
                    }
                ],
                "generation_order": ["pages-and-app-shell"],
            },
            context_variables=_Context(),
        )


def test_app_build_plan_tool_rejects_raw_frontend_source_outputs() -> None:
    module = _load_module(
        "factory_app/workflows/AppGenerator/tools/app_build_plan.py",
        "tests.app_build_plan_tool_frontend_source_guard",
    )

    with pytest.raises(ValueError, match="raw frontend source output"):
        module.app_build_plan(
            AppBuildPlan={
                "agent_message": "Planned the product.",
                "app_kind": "social-platform",
                "pages": [{"name": "Inbox", "route": "/inbox", "purpose": "Read messages"}],
                "entities": [{"name": "Message", "operations": ["create", "read"]}],
                "roles": ["user"],
                "auth_strategy": "role-based",
                "backend_scope": ["messaging api"],
                "frontend_scope": ["inbox ui"],
                "theme_preferences": None,
                "brand_intent": None,
                "capability_packs": [
                    {
                        "capability_pack_id": "messaging_core",
                        "pack_type": "messaging_pack",
                        "label": "Messaging",
                        "summary": "Inbox and threads",
                        "implementation_mode": "hybrid",
                    }
                ],
                "external_integrations": [],
                "agent_backend_required": False,
                "build_tasks": [
                    {
                        "task_id": "task_invalid_frontend_source",
                        "task_type": "page_bundle",
                        "capability_pack_id": "messaging_core",
                        "execution_target": "AppGenerator",
                        "initial_agent": "AppSchemaAgent",
                        "description": "Compile inbox page schema",
                        "initial_message": "Compile the inbox page schema",
                        "owned_paths": ["frontend/src/pages/Inbox.jsx"],
                        "depends_on": [],
                        "acceptance_criteria": ["Route exists"],
                    }
                ],
                "generation_order": ["app-schema-bundle"],
            },
            context_variables=_Context(),
        )


def test_app_build_plan_tool_rejects_obsolete_admin_config_task_type() -> None:
    module = _load_module(
        "factory_app/workflows/AppGenerator/tools/app_build_plan.py",
        "tests.app_build_plan_tool_admin_config_guard",
    )

    with pytest.raises(ValueError, match="obsolete task_type 'admin_config'"):
        module.app_build_plan(
            AppBuildPlan={
                "agent_message": "Planned the product.",
                "app_kind": "marketplace",
                "pages": [{"name": "Home", "route": "/", "purpose": "Land on the product"}],
                "entities": [],
                "roles": ["admin"],
                "auth_strategy": "role-based",
                "backend_scope": ["host admin"],
                "frontend_scope": ["home ui"],
                "theme_preferences": None,
                "brand_intent": None,
                "capability_packs": [],
                "external_integrations": [],
                "agent_backend_required": False,
                "build_tasks": [
                    {
                        "task_id": "task_admin_config",
                        "task_type": "admin_config",
                        "capability_pack_id": None,
                        "execution_target": "AppGenerator",
                        "initial_agent": "ConfigMiddlewareAgent",
                        "description": "Generate obsolete host admin config.",
                        "initial_message": "Generate obsolete app/config/admin.json",
                        "owned_paths": ["app/config/admin.json"],
                        "depends_on": [],
                        "acceptance_criteria": ["Admin bootstrap configured"],
                    }
                ],
                "generation_order": ["admin"],
            },
            context_variables=_Context(),
        )


def test_app_build_plan_tool_rejects_obsolete_host_admin_config_path() -> None:
    module = _load_module(
        "factory_app/workflows/AppGenerator/tools/app_build_plan.py",
        "tests.app_build_plan_tool_host_admin_path_guard",
    )

    with pytest.raises(ValueError, match="owns obsolete path 'app/config/admin.json'"):
        module.app_build_plan(
            AppBuildPlan={
                "agent_message": "Planned the product.",
                "app_kind": "marketplace",
                "pages": [{"name": "Home", "route": "/", "purpose": "Land on the product"}],
                "entities": [],
                "roles": ["admin"],
                "auth_strategy": "role-based",
                "backend_scope": ["host admin"],
                "frontend_scope": ["home ui"],
                "theme_preferences": None,
                "brand_intent": None,
                "capability_packs": [],
                "external_integrations": [],
                "agent_backend_required": False,
                "build_tasks": [
                    {
                        "task_id": "task_backend_foundation",
                        "task_type": "backend_foundation",
                        "capability_pack_id": None,
                        "execution_target": "AppGenerator",
                        "initial_agent": "ConfigMiddlewareAgent",
                        "description": "Generate backend glue.",
                        "initial_message": "Generate backend glue.",
                        "owned_paths": ["backend/config.py", "app/config/admin.json"],
                        "depends_on": [],
                        "acceptance_criteria": ["Config exists"],
                    }
                ],
                "generation_order": ["backend-foundation"],
            },
            context_variables=_Context(),
        )


def test_app_build_plan_tool_rejects_split_admin_api_with_wrong_task_type() -> None:
    module = _load_module(
        "factory_app/workflows/AppGenerator/tools/app_build_plan.py",
        "tests.app_build_plan_tool_split_admin_api_guard",
    )

    with pytest.raises(ValueError, match="Use the explicit api_surface task"):
        module.app_build_plan(
            AppBuildPlan={
                "agent_message": "Planned the product.",
                "app_kind": "marketplace",
                "pages": [{"name": "Home", "route": "/", "purpose": "Land on the product"}],
                "entities": [],
                "roles": ["admin"],
                "auth_strategy": "role-based",
                "backend_scope": ["split app backend"],
                "frontend_scope": ["home ui"],
                "theme_preferences": None,
                "brand_intent": None,
                "capability_packs": [],
                "external_integrations": [],
                "agent_backend_required": False,
                "build_tasks": [
                    {
                        "task_id": "task_split_admin",
                        "task_type": "backend_foundation",
                        "capability_pack_id": None,
                        "execution_target": "AppGenerator",
                        "initial_agent": "ControllerAgent",
                        "description": "Generate split admin API.",
                        "initial_message": "Generate split admin API.",
                        "owned_paths": ["backend/admin_config.py", "backend/routes/admin.py"],
                        "depends_on": [],
                        "acceptance_criteria": ["Admin config route exists"],
                    }
                ],
                "generation_order": ["backend-foundation"],
            },
            context_variables=_Context(),
        )


def test_app_build_plan_tool_rejects_unknown_task_type() -> None:
    module = _load_module(
        "factory_app/workflows/AppGenerator/tools/app_build_plan.py",
        "tests.app_build_plan_tool_unknown_task_type_guard",
    )

    with pytest.raises(ValueError, match="unsupported task_type 'custom_task'"):
        module.app_build_plan(
            AppBuildPlan={
                "agent_message": "Planned the product.",
                "app_kind": "marketplace",
                "pages": [{"name": "Home", "route": "/", "purpose": "Land on the product"}],
                "entities": [],
                "roles": ["admin"],
                "auth_strategy": "role-based",
                "backend_scope": ["backend glue"],
                "frontend_scope": ["home ui"],
                "theme_preferences": None,
                "brand_intent": None,
                "capability_packs": [],
                "external_integrations": [],
                "agent_backend_required": False,
                "build_tasks": [
                    {
                        "task_id": "task_custom_lane",
                        "task_type": "custom_task",
                        "capability_pack_id": None,
                        "execution_target": "AppGenerator",
                        "initial_agent": "ConfigMiddlewareAgent",
                        "description": "Generate a non-canonical task.",
                        "initial_message": "Generate a non-canonical task.",
                        "owned_paths": ["backend/config.py"],
                        "depends_on": [],
                        "acceptance_criteria": ["Config exists"],
                    }
                ],
                "generation_order": ["backend-foundation"],
            },
            context_variables=_Context(),
        )


def test_app_build_plan_tool_rejects_module_contract_with_wrong_initial_agent() -> None:
    module = _load_module(
        "factory_app/workflows/AppGenerator/tools/app_build_plan.py",
        "tests.app_build_plan_tool_module_contract_owner_guard",
    )

    with pytest.raises(ValueError, match="`module_contract` must start at ConfigMiddlewareAgent"):
        module.app_build_plan(
            AppBuildPlan={
                "agent_message": "Planned the product.",
                "app_kind": "marketplace",
                "pages": [{"name": "Home", "route": "/", "purpose": "Land on the product"}],
                "entities": [],
                "roles": ["admin"],
                "auth_strategy": "role-based",
                "backend_scope": ["module contracts"],
                "frontend_scope": ["home ui"],
                "theme_preferences": None,
                "brand_intent": None,
                "capability_packs": [
                    {
                        "capability_pack_id": "orders",
                        "pack_type": "custom_domain",
                        "label": "Orders",
                        "summary": "Order handling",
                        "implementation_mode": "declarative_module",
                    }
                ],
                "external_integrations": [],
                "agent_backend_required": False,
                "build_tasks": [
                    {
                        "task_id": "task_orders_contract",
                        "task_type": "module_contract",
                        "capability_pack_id": "orders",
                        "execution_target": "AppGenerator",
                        "initial_agent": "ServiceAgent",
                        "description": "Generate the orders module contract.",
                        "initial_message": "Generate the orders module contract.",
                        "owned_paths": ["modules/orders/module.yaml"],
                        "depends_on": [],
                        "acceptance_criteria": ["Contract exists"],
                    }
                ],
                "generation_order": ["backend-foundation"],
            },
            context_variables=_Context(),
        )


def test_app_build_plan_tool_rejects_backend_foundation_with_wrong_initial_agent() -> None:
    module = _load_module(
        "factory_app/workflows/AppGenerator/tools/app_build_plan.py",
        "tests.app_build_plan_tool_backend_foundation_owner_guard",
    )

    with pytest.raises(ValueError, match="`backend_foundation` must start at ConfigMiddlewareAgent"):
        module.app_build_plan(
            AppBuildPlan={
                "agent_message": "Planned the product.",
                "app_kind": "marketplace",
                "pages": [{"name": "Home", "route": "/", "purpose": "Land on the product"}],
                "entities": [],
                "roles": ["admin"],
                "auth_strategy": "role-based",
                "backend_scope": ["backend glue"],
                "frontend_scope": ["home ui"],
                "theme_preferences": None,
                "brand_intent": None,
                "capability_packs": [],
                "external_integrations": [],
                "agent_backend_required": False,
                "build_tasks": [
                    {
                        "task_id": "task_backend_foundation",
                        "task_type": "backend_foundation",
                        "capability_pack_id": None,
                        "execution_target": "AppGenerator",
                        "initial_agent": "ControllerAgent",
                        "description": "Generate backend glue.",
                        "initial_message": "Generate backend glue.",
                        "owned_paths": ["backend/config.py"],
                        "depends_on": [],
                        "acceptance_criteria": ["Config exists"],
                    }
                ],
                "generation_order": ["backend-foundation"],
            },
            context_variables=_Context(),
        )


def test_app_build_plan_tool_accepts_control_plane_pack_task() -> None:
    module = _load_module(
        "factory_app/workflows/AppGenerator/tools/app_build_plan.py",
        "tests.app_build_plan_tool_control_plane_pack_accept",
    )
    context = _Context()

    result = module.app_build_plan(
        AppBuildPlan=_minimal_app_build_plan(
            {
                "task_id": "task_control_plane_pack",
                "task_type": "control_plane_pack",
                "capability_pack_id": None,
                "surface_id": "app_refinement_harness",
                "surface_kind": "control_plane",
                "execution_target": "AppGenerator",
                "initial_agent": "ControlPlaneAgent",
                "description": "Generate app-local refinement harness pack.",
                "initial_message": "Route scoped refinements to app_revision.",
                "owned_paths": [
                    "control_plane/config/control_plane.yaml",
                    "control_plane/config/tools.yaml",
                    "control_plane/prompts/change_classifier_system.yaml",
                ],
                "depends_on": [],
                "acceptance_criteria": ["Routes use workflow_sequence only"],
            }
        ),
        context_variables=context,
    )

    assert "Build tasks: 1" in result
    assert context.data["app_build_plan"]["build_tasks"][0]["task_type"] == "control_plane_pack"


def test_app_build_plan_tool_rejects_control_plane_pack_with_capability_pack_id() -> None:
    module = _load_module(
        "factory_app/workflows/AppGenerator/tools/app_build_plan.py",
        "tests.app_build_plan_tool_control_plane_pack_pack_id_guard",
    )

    with pytest.raises(ValueError, match="capability_pack_id is not null"):
        module.app_build_plan(
            AppBuildPlan=_minimal_app_build_plan(
                {
                    "task_id": "task_control_plane_pack",
                    "task_type": "control_plane_pack",
                    "capability_pack_id": "refinement_harness",
                    "surface_id": "app_refinement_harness",
                    "surface_kind": "control_plane",
                    "execution_target": "AppGenerator",
                    "initial_agent": "ControlPlaneAgent",
                    "description": "Generate app-local refinement harness pack.",
                    "initial_message": "Route scoped refinements to app_revision.",
                    "owned_paths": [
                        "control_plane/config/control_plane.yaml",
                        "control_plane/config/tools.yaml",
                    ],
                    "depends_on": [],
                    "acceptance_criteria": ["Routes use workflow_sequence only"],
                }
            ),
            context_variables=_Context(),
        )


def test_app_build_plan_tool_rejects_control_plane_pack_wrong_initial_agent() -> None:
    module = _load_module(
        "factory_app/workflows/AppGenerator/tools/app_build_plan.py",
        "tests.app_build_plan_tool_control_plane_pack_owner_guard",
    )

    with pytest.raises(ValueError, match="`control_plane_pack` must start at ControlPlaneAgent"):
        module.app_build_plan(
            AppBuildPlan=_minimal_app_build_plan(
                {
                    "task_id": "task_control_plane_pack",
                    "task_type": "control_plane_pack",
                    "capability_pack_id": None,
                    "surface_id": "app_refinement_harness",
                    "surface_kind": "control_plane",
                    "execution_target": "AppGenerator",
                    "initial_agent": "ConfigMiddlewareAgent",
                    "description": "Generate app-local refinement harness pack.",
                    "initial_message": "Route scoped refinements to app_revision.",
                    "owned_paths": [
                        "control_plane/config/control_plane.yaml",
                        "control_plane/config/tools.yaml",
                    ],
                    "depends_on": [],
                    "acceptance_criteria": ["Routes use workflow_sequence only"],
                }
            ),
            context_variables=_Context(),
        )


def test_app_build_plan_tool_rejects_control_plane_pack_invalid_owned_path() -> None:
    module = _load_module(
        "factory_app/workflows/AppGenerator/tools/app_build_plan.py",
        "tests.app_build_plan_tool_control_plane_pack_path_guard",
    )

    with pytest.raises(ValueError, match="invalid control-plane pack paths"):
        module.app_build_plan(
            AppBuildPlan=_minimal_app_build_plan(
                {
                    "task_id": "task_control_plane_pack",
                    "task_type": "control_plane_pack",
                    "capability_pack_id": None,
                    "surface_id": "app_refinement_harness",
                    "surface_kind": "control_plane",
                    "execution_target": "AppGenerator",
                    "initial_agent": "ControlPlaneAgent",
                    "description": "Generate app-local refinement harness pack.",
                    "initial_message": "Route scoped refinements to app_revision.",
                    "owned_paths": [
                        "control_plane/config/control_plane.yaml",
                        "control_plane/config/tools.yaml",
                        "backend/control_plane/custom.py",
                    ],
                    "depends_on": [],
                    "acceptance_criteria": ["Routes use workflow_sequence only"],
                }
            ),
            context_variables=_Context(),
        )


def test_app_build_plan_tool_rejects_control_plane_pack_missing_required_file() -> None:
    module = _load_module(
        "factory_app/workflows/AppGenerator/tools/app_build_plan.py",
        "tests.app_build_plan_tool_control_plane_pack_required_guard",
    )

    with pytest.raises(ValueError, match="missing required control-plane pack paths"):
        module.app_build_plan(
            AppBuildPlan=_minimal_app_build_plan(
                {
                    "task_id": "task_control_plane_pack",
                    "task_type": "control_plane_pack",
                    "capability_pack_id": None,
                    "surface_id": "app_refinement_harness",
                    "surface_kind": "control_plane",
                    "execution_target": "AppGenerator",
                    "initial_agent": "ControlPlaneAgent",
                    "description": "Generate app-local refinement harness pack.",
                    "initial_message": "Route scoped refinements to app_revision.",
                    "owned_paths": [
                        "control_plane/config/control_plane.yaml",
                    ],
                    "depends_on": [],
                    "acceptance_criteria": ["Routes use workflow_sequence only"],
                }
            ),
            context_variables=_Context(),
        )


def test_app_build_plan_tool_rejects_control_plane_pack_non_yaml_prompt() -> None:
    module = _load_module(
        "factory_app/workflows/AppGenerator/tools/app_build_plan.py",
        "tests.app_build_plan_tool_control_plane_pack_prompt_suffix_guard",
    )

    with pytest.raises(ValueError, match="invalid control-plane pack paths"):
        module.app_build_plan(
            AppBuildPlan=_minimal_app_build_plan(
                {
                    "task_id": "task_control_plane_pack",
                    "task_type": "control_plane_pack",
                    "capability_pack_id": None,
                    "surface_id": "app_refinement_harness",
                    "surface_kind": "control_plane",
                    "execution_target": "AppGenerator",
                    "initial_agent": "ControlPlaneAgent",
                    "description": "Generate app-local refinement harness pack.",
                    "initial_message": "Route scoped refinements to app_revision.",
                    "owned_paths": [
                        "control_plane/config/control_plane.yaml",
                        "control_plane/config/tools.yaml",
                        "control_plane/prompts/change_classifier_system.txt",
                    ],
                    "depends_on": [],
                    "acceptance_criteria": ["Routes use workflow_sequence only"],
                }
            ),
            context_variables=_Context(),
        )


def test_valueengine_manifest_preserves_brand_intent_for_downstream_generators(monkeypatch) -> None:
    module = _load_module(
        "factory_app/workflows/ValueEngine/tools/manifest.py",
        "tests.valueengine_manifest_direct",
    )
    module._HAS_PERSISTENCE = False
    emitted = {}
    summary_artifact = {}

    async def _fake_emit(component, payload, **kwargs):
        emitted["component"] = component
        emitted["payload"] = payload
        emitted["kwargs"] = kwargs

    async def _fake_persist_summary_artifact(**kwargs):
        summary_artifact.update(kwargs)
        return type("ArtifactVersion", (), {"id": "av_concept_1"})()

    module.emit_ui_surface = _fake_emit
    monkeypatch.setattr(module, "persist_summary_artifact", _fake_persist_summary_artifact)

    context = _Context(
        chat_id="chat_value_123",
        app_id="app_123",
        user_id="user_123",
        workflow_name="ValueEngine",
        structured_output={
            "app_name": "Mozaiks Social",
            "value_proposition": "Turn creator communities into modular social apps.",
            "concept_overview": "A social product with messaging, profiles, and app-backed collaboration.",
            "target_user": "Creators and early community operators.",
            "target_users": ["Creators", "Community operators"],
            "core_jobs": ["Coordinate creators", "Launch communities"],
            "core_features": ["Messaging", "Profiles"],
            "deferred_features": ["Marketplace"],
            "unique_differentiators": ["Agentic app creation"],
            "brand_intent": {
                "style_summary": "Cinematic sci-fi creator platform",
                "brand_keywords": ["cinematic", "cosmic", "premium"],
                "experience_goals": ["immersive", "trustworthy"],
                "appearance_hint": "dark",
            },
            "constraints": ["Must support modular feature rollout"],
            "api_endpoints": [{"method": "GET", "path": "/api/messages", "description": "List messages"}],
            "app_ui_requirements": ["Need an immersive dark shell with strong visual hierarchy"],
            "capability_pack_hints": ["messaging_pack"],
            "agentic_capabilities": ["thread_summary"],
        },
    )

    result = asyncio.run(module.save_value_manifest(context_variables=context))

    assert result["success"] is True
    manifest = context.data["value_manifest"]
    assert manifest["brand_intent"]["style_summary"] == "Cinematic sci-fi creator platform"
    assert manifest["app_ui_requirements"] == ["Need an immersive dark shell with strong visual hierarchy"]
    assert manifest["capability_pack_hints"] == ["messaging_pack"]
    assert emitted["component"] == "ConceptBlueprint"
    assert emitted["payload"]["blueprint"]["brand_intent"]["appearance_hint"] == "dark"
    assert emitted["payload"]["blueprint"]["agentic_capabilities"] == ["thread_summary"]
    assert summary_artifact["artifact_kind"] == "concept"
    assert summary_artifact["summary_payload"]["app_name"] == "Mozaiks Social"
    assert summary_artifact["author_user_id"] == "user_123"


def test_valueengine_save_build_plan_preserves_capability_packs_and_concept_blueprint(monkeypatch) -> None:
    module = _load_module(
        "factory_app/workflows/ValueEngine/tools/decompose.py",
        "tests.valueengine_decompose_direct",
    )
    module._HAS_PERSISTENCE = False
    summary_artifact = {}

    async def _fake_persist_summary_artifact(**kwargs):
        summary_artifact.update(kwargs)
        return type("ArtifactVersion", (), {"id": "av_build_plan_1"})()

    monkeypatch.setattr(module, "persist_summary_artifact", _fake_persist_summary_artifact)
    context = _Context(
        app_id="app_123",
        chat_id="chat_123",
        user_id="user_123",
        value_manifest={
            "app_name": "AdMarket",
            "value_proposition": "Invest in campaigns",
        },
    )

    result = asyncio.run(
        module.save_build_plan(
            tasks=[
                {
                    "task_id": "task_marketplace_pages",
                    "task_type": "app_module",
                    "capability_pack_id": "marketplace_core",
                    "child_workflow": "AppGenerator",
                    "description": "Build marketplace pages",
                    "initial_message": "Generate marketplace pages",
                }
            ],
            capability_packs=[
                {
                    "capability_pack_id": "marketplace_core",
                    "pack_type": "marketplace_pack",
                    "label": "Marketplace",
                    "summary": "Listings and campaign discovery",
                    "implementation_mode": "declarative_module",
                }
            ],
            required_inputs=["ads_api_key"],
            context_variables=context,
        )
    )

    assert result["success"] is True
    assert context.data["build_plan"]["concept_blueprint"]["app_name"] == "AdMarket"
    assert context.data["build_plan"]["capability_packs"][0]["pack_type"] == "marketplace_pack"
    assert context.data["capability_packs"][0]["capability_pack_id"] == "marketplace_core"
    assert summary_artifact["artifact_kind"] == "build_plan"
    assert summary_artifact["input_artifact_kinds"] == ("concept",)
    assert summary_artifact["summary_payload"]["capability_packs"][0]["capability_pack_id"] == "marketplace_core"


def test_app_generation_strategy_docs_are_indexed_and_examples_are_grounded() -> None:
    index_text = _read_text("docs/architecture/index.md")
    decomposition_text = _read_text("docs/architecture/orchestration-and-decomposition.md")
    boundary_text = _read_text("docs/architecture/foundations/core-product-app-bundle-boundary.md")
    builder_text = _read_text("docs/architecture/builder/app-builder-architecture.md")

    assert "Builder and Generation" in index_text
    assert "Module Systems" in index_text
    assert "ProductSpec" in decomposition_text
    assert "ExperienceSpec" in decomposition_text
    assert "AgentAugmentationPlan" in decomposition_text
    assert "ProductSpec" in boundary_text
    assert "ExperienceSpec" in boundary_text
    assert "AgentAugmentationPlan" in boundary_text
    assert "Decompose into product artifacts first" in decomposition_text
    assert "typed builder contracts" in builder_text

