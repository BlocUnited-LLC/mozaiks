from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

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


class _Context(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def test_existing_app_discovery_structured_outputs_use_augmentation_artifact() -> None:
    data = _read_yaml("factory_app/workflows/ExistingAppDiscovery/structured_outputs.yaml")

    assert data["registry"]["DiscoveryArtifactAssemblerAgent"] == "ExistingAppAugmentationArtifact"

    models = data["models"]
    assert "ExistingProductSpec" in models
    assert "CapabilitySpec" in models
    assert "AgentAugmentationPlan" in models
    assert "BrandThemeEvidence" in models

    plan_fields = models["AgentAugmentationPlan"]["fields"]
    assert "adoption_level" in plan_fields
    assert plan_fields["adoption_level"]["type"] == "str"
    assert plan_fields["ecosystem_bindings"]["items"] == "str"
    assert plan_fields["theme_adaptation_strategy"]["type"] == "str"
    assert plan_fields["embed_theme_ready"]["type"] == "bool"

    product_fields = models["ExistingProductSpec"]["fields"]
    assert product_fields["brand_theme_summary"]["type"] == "str"
    assert product_fields["brand_theme_evidence"]["type"] == "BrandThemeEvidence"

    artifact_fields = models["ExistingAppAugmentationArtifact"]["fields"]
    assert artifact_fields["request_intent"]["type"] == "str"
    assert artifact_fields["existing_product_spec"]["type"] == "ExistingProductSpec"
    assert artifact_fields["capability_specs"]["items"] == "CapabilitySpec"
    assert artifact_fields["agent_augmentation_plan"]["type"] == "AgentAugmentationPlan"


def test_existing_app_discovery_context_and_prompts_use_adoption_language() -> None:
    context_vars = _read_yaml("factory_app/workflows/ExistingAppDiscovery/context_variables.yaml")
    agents = _read_text("factory_app/workflows/ExistingAppDiscovery/agents.yaml")

    definitions = context_vars["definitions"]
    assert "discovery_preset" not in definitions
    assert "host_app_source" in definitions
    assert "discovery_mode" in definitions
    assert "frontend_repo_path" in definitions
    assert "backend_repo_path" in definitions
    assert "frontend_repo_summary" in definitions
    assert "backend_repo_summary" in definitions
    assert "theme_capture_ready" in definitions
    assert "theme_capture_status" in definitions
    assert "theme_capture_summary" in definitions
    assert "theme_capture_evidence" in definitions
    assert "existing_product_spec" in definitions
    assert "capability_specs" in definitions
    assert "agent_augmentation_plan" in definitions
    assert "existing_app_discovery_artifact" in definitions
    assert "adoption_level" in definitions
    assert "ecosystem_bindings" in definitions

    assert "Embed" in agents
    assert "Bridge" in agents
    assert "Ecosystem" in agents
    assert "Native Migration" in agents
    assert "ExistingProductSpec" in agents
    assert "AgentAugmentationPlan" in agents
    assert "discovery_mode" in agents
    assert "host_app_source" in agents
    assert "workspace_app" in agents
    assert "theme_adaptation_strategy" in agents
    assert "embed_theme_ready" in agents
    assert "brand_theme_summary" in agents


def test_create_route_enters_canonical_build_transition() -> None:
    registry = _read_yaml("factory_app/workflows/extended_orchestration/extension_registry.json")
    create_page = next(item for item in registry["entrypoints"] if item["path"] == "/create")

    assert create_page["transition"] == "app_type_selector"
    assert create_page["sequence"] == "build"
    assert create_page["requiresAuth"] is False
    assert "showInHeader" not in create_page
    assert "journey" not in create_page


def test_new_app_entry_routes_into_valueengine_first() -> None:
    registry = _read_yaml("factory_app/workflows/extended_orchestration/extension_registry.json")
    value_context = _read_yaml("factory_app/workflows/ValueEngine/context_variables.yaml")
    design_docs_context = _read_yaml("factory_app/workflows/DesignDocs/context_variables.yaml")

    workflow_sequences = registry.get("workflow_sequences") or []
    build_journey = next(item for item in workflow_sequences if item["id"] == "build")
    assert "entry_transition" not in build_journey
    assert build_journey["steps"][0]["transition"] == "app_type_selector"
    assert build_journey["steps"][1]["workflows"] == ["ValueEngine"]
    assert build_journey["steps"][2]["workflows"] == ["ThemeCapture"]
    assert build_journey["steps"][3]["transition"] == "coding_journey_selector"
    assert build_journey["steps"][4]["transition"] == "database_setup_selector"
    assert build_journey["steps"][5]["workflows"] == ["DesignDocs"]
    assert build_journey["steps"][6]["workflows"] == ["AgentGenerator"]
    assert build_journey["steps"][7]["workflows"] == ["AppGenerator"]

    transition_map = {item["id"]: item for item in registry["transitions"]}
    app_type_selector = transition_map["app_type_selector"]
    assert app_type_selector["transition_type"] == "user_choice_context"
    new_app_option = next(item for item in app_type_selector["options"] if item["id"] == "greenfield_app")
    assert new_app_option["route_to"] == "ValueEngine"
    assert new_app_option["sequence"] == "build"
    assert new_app_option["context_variables"] == {"app_type": "greenfield_app"}
    assert "app_type" in value_context["definitions"]
    assert "database_provider" in design_docs_context["definitions"]
    assert "database_setup_mode" in design_docs_context["definitions"]


def test_existing_app_entry_routes_into_discovery_with_context() -> None:
    registry = _read_yaml("factory_app/workflows/extended_orchestration/extension_registry.json")
    transition_map = {item["id"]: item for item in registry["transitions"]}
    workflow_sequences = registry.get("workflow_sequences") or []

    adoption_journey = next(item for item in workflow_sequences if item["id"] == "brownfield_app_adoption")
    assert adoption_journey["steps"][0]["transition"] == "app_type_selector"
    assert adoption_journey["steps"][1]["workflows"] == ["ExistingAppDiscovery"]

    app_type_selector = transition_map["app_type_selector"]
    existing_app_option = next(item for item in app_type_selector["options"] if item["id"] == "brownfield_app")
    assert existing_app_option["route_to"] == "ExistingAppDiscovery"
    assert existing_app_option["sequence"] == "brownfield_app_adoption"
    assert existing_app_option["context_variables"] == {"app_type": "brownfield_app"}

    assert "existing_app_entry_selector" not in transition_map

    coding_selector = transition_map["coding_journey_selector"]
    assert coding_selector["transition_type"] == "user_choice_context"
    coding_options = {item["id"]: item for item in coding_selector["options"]}
    assert coding_options["autonomous"]["route_to"] == "DesignDocs"
    assert coding_options["guided"]["route_to"] == "DesignDocs"
    assert coding_options["autonomous"]["context_variables"]["design_docs_hitl"] is False
    assert coding_options["guided"]["context_variables"]["design_docs_hitl"] is True

    database_selector = transition_map["database_setup_selector"]
    assert database_selector["transition_type"] == "user_choice_context"
    database_options = {item["id"]: item for item in database_selector["options"]}
    assert database_options["local_mongodb"]["route_to"] == "DesignDocs"
    assert database_options["mongodb_atlas"]["route_to"] == "DesignDocs"
    assert database_options["existing_uri"]["route_to"] == "DesignDocs"
    assert database_options["skip_for_now"]["route_to"] == "DesignDocs"
    assert database_options["local_mongodb"]["context_variables"] == {
        "database_provider": "mongodb",
        "database_setup_mode": "local",
    }
    assert database_options["skip_for_now"]["context_variables"] == {
        "database_provider": "mongodb",
        "database_setup_mode": "skip",
    }


def test_existing_app_preload_supports_workspace_app_preset() -> None:
    module = _load_module(
        "factory_app/workflows/ExistingAppDiscovery/tools/preload_discovery_context.py",
        "tests.preload_discovery_context_direct",
    )

    def _fake_host_source_inputs(host_app_source: str | None) -> dict:
        if host_app_source == "workspace_app":
            return {
                "repo_path": "C:/workspace/mozaiks-app",
                "discovery_mode": "guided",
            }
        return {}

    async def _fake_scan(local_repo_path: str | None, github_repo: str | None, github_ref: str | None) -> dict:
        if local_repo_path == "C:/workspace/mozaiks-app":
            return {
                "success": True,
                "source": "local_repo",
                "repo_name": "mozaiks-app",
                "languages": ["JavaScript/TypeScript", "Python"],
                "frameworks": ["React", "FastAPI"],
                "target_frameworks": [],
                "route_files": ["src/AppRoutes.js"],
                "service_entrypoints": ["Services/Messaging/Program.cs"],
                "hub_files": [],
                "total_files_scanned": 84,
                "inferred_tech_stack": "JavaScript/TypeScript, Python, React, FastAPI",
            }
        return {"success": False, "error": "unexpected repo path"}

    def _fake_scan_local_repo(repo_path: str) -> dict:
        if repo_path == "C:/workspace/mozaiks-app":
            return {
                "success": True,
                "source": "local_repo",
                "repo_name": "mozaiks-app",
                "languages": ["JavaScript/TypeScript", "Python"],
                "frameworks": ["React", "FastAPI"],
                "target_frameworks": [],
                "route_files": ["src/AppRoutes.js"],
                "service_entrypoints": ["Services/Messaging/Program.cs"],
                "hub_files": [],
                "total_files_scanned": 84,
                "inferred_tech_stack": "JavaScript/TypeScript, Python, React, FastAPI",
            }
        return {"success": False, "error": "unexpected repo path"}

    async def _fake_collect_openapi(openapi_url, backend_base_url, uploaded_openapi_path) -> dict:
        return {
            "success": True,
            "source": "openapi_url",
            "path_count": 12,
            "spec_location": "https://api.mozaiks.test/openapi.json",
            "auth_summary": "JWT Bearer",
            "security_schemes": ["bearerAuth"],
            "title": None,
        }

    async def _fake_probe_backend(backend_base_url) -> dict:
        return {
            "success": True,
            "health_url": "https://api.mozaiks.test/health",
            "status_code": 200,
            "details": {"status": "ok"},
        }

    class _FakeThemeModule:
        @staticmethod
        async def collect_prechat_theme_context(context_variables):
            context_variables["preloaded_context_ready"] = True
            context_variables["preload_status"] = "ready"
            context_variables["theme_capture_evidence"] = {
                "appearance": "dark",
                "colors": ["#060B26", "#7e5bef", "#ff49db"],
                "fonts": ["Rajdhani", "Orbitron"],
                "layout_hints": ["sidebar", "glass"],
            }
            context_variables["preload_summary"] = "Dark appearance with purple gradient branding."
            return {"success": True, "preload_status": "ready"}

    module._resolve_host_app_source_inputs = _fake_host_source_inputs
    module._scan_local_repo = _fake_scan_local_repo
    module._collect_openapi = _fake_collect_openapi
    module._probe_backend = _fake_probe_backend
    module._load_theme_capture_preloader = lambda: _FakeThemeModule
    module._find_theme_config_path = lambda repo_path: None
    module._collect_theme_css_snapshot = lambda repo_path: "body { font-family: Rajdhani; background: #060B26; }"

    context = _Context(
        app_type="brownfield_app",
        host_app_source="workspace_app",
        backend_base_url="https://api.mozaiks.test",
    )

    result = asyncio.run(module.collect_prechat_discovery_context(context_variables=context))

    assert result["success"] is True
    assert context["discovery_mode"] == "guided"
    assert context["host_app_source"] == "workspace_app"
    assert context["app_name"] == "mozaiks-app"
    assert context["repo_summary"]["source"] == "local_repo"
    assert context["repo_summary"]["repo_name"] == "mozaiks-app"
    assert context["service_surfaces"][0]["kind"] == "rest_api"
    assert context["route_surfaces"][0]["module"] == "src"
    assert context["existing_experience_summary"].startswith("Current experience appears to be organized around route/module surfaces")
    assert context["preload_status"] == "ready"
    assert context["theme_capture_ready"] is True
    assert context["theme_capture_status"] == "ready"
    assert "Rajdhani" in context["theme_capture_summary"]
    assert context["theme_capture_evidence"]["appearance"] == "dark"


def test_existing_app_artifact_saver_persists_canonical_fields() -> None:
    module = _load_module(
        "factory_app/workflows/ExistingAppDiscovery/tools/save_existing_app_artifacts.py",
        "tests.save_existing_app_artifacts_direct",
    )

    emitted = {}

    async def _fake_emit(component, payload, **kwargs):
        emitted["component"] = component
        emitted["payload"] = payload
        emitted["kwargs"] = kwargs

    module.emit_ui_surface = _fake_emit

    context = _Context(
        chat_id="chat_123",
        structured_output={
            "request_intent": "brownfield_app",
            "existing_product_spec": {
                "app_name": "mozaiks-app",
                "app_description": "Existing product host app",
                "tech_stack": "React, .NET, MongoDB",
                "auth_model": "OIDC JWT",
                "brand_theme_summary": "Dark appearance with purple glass surfaces and Rajdhani/Orbitron typography.",
                "brand_theme_evidence": {
                    "source_summary": "Repo CSS snapshot and tailwind tokens.",
                    "appearance": "dark",
                    "colors": ["#060B26", "#7e5bef", "#ff49db"],
                    "fonts": ["Rajdhani", "Orbitron"],
                    "layout_hints": ["sidebar", "glass"],
                },
                "service_surfaces": [{"name": "Messaging API"}],
                "route_surfaces": [{"path": "/app/*"}],
            },
            "capability_specs": [
                {
                    "capability_id": "direct_messaging",
                    "label": "Direct Messaging",
                    "confidence": "confirmed",
                    "delivery_surface": "rest_api",
                    "agent_ready": True,
                }
            ],
            "agent_augmentation_plan": {
                "adoption_level": "bridge",
                "adoption_rationale": "Messaging already exposes a clean API and hub boundary.",
                "auth_delegation_model": "user_token_forwarding",
                "ui_surface_preference": "side_panel",
                "ai_accessible_capabilities": ["direct_messaging"],
                "initial_workflows": ["ThreadSummary"],
                "ecosystem_bindings": ["payments"],
                "theme_adaptation_strategy": "Keep MOZ-UI shell host-owned and apply captured tokens to the Mozaiks side panel.",
                "embed_theme_ready": True,
            },
            "discovery_brief": "Start by bridging messaging and attaching a summary workflow.",
            "artifact_version": "1.0",
        },
    )

    result = asyncio.run(module.save_existing_app_artifacts(context_variables=context))

    assert result["success"] is True
    assert context["existing_product_spec"]["app_name"] == "mozaiks-app"
    assert context["capability_specs"][0]["capability_id"] == "direct_messaging"
    assert context["agent_augmentation_plan"]["adoption_level"] == "bridge"
    assert context["existing_app_discovery_artifact"]["request_intent"] == "brownfield_app"

    assert emitted["component"] == "DiscoveryBriefCard"
    assert emitted["payload"]["adoption_level"] == "bridge"
    assert emitted["payload"]["service_surface_count"] == 1
    assert emitted["payload"]["route_surface_count"] == 1
    assert emitted["payload"]["initial_workflows"] == ["ThreadSummary"]
    assert emitted["payload"]["embed_theme_ready"] is True
    assert "Rajdhani" in emitted["payload"]["brand_theme_summary"]


def test_existing_app_strategy_docs_are_indexed() -> None:
    index_text = _read_text("docs/architecture/index.md")
    discovery_agents = _read_text("factory_app/workflows/ExistingAppDiscovery/agents.yaml")
    discovery_context = _read_text("factory_app/workflows/ExistingAppDiscovery/context_variables.yaml")
    session_router = _read_text("docs/architecture/workflows/session-router.md")

    assert "Builder and Generation" in index_text
    assert "ExistingAppDiscovery" in session_router
    assert "Embed" in discovery_agents
    assert "Native Migration" in discovery_agents
    assert "host_app_source = \"workspace_app\"" in discovery_agents
    assert "guided" in discovery_context
    assert "workspace_app" in discovery_context
    assert "ExistingProductSpec" in discovery_agents
    assert "AgentAugmentationPlan" in discovery_agents


def test_existing_app_docs_describe_workspace_app_preset() -> None:
    discovery_agents = _read_text("factory_app/workflows/ExistingAppDiscovery/agents.yaml")
    discovery_context = _read_text("factory_app/workflows/ExistingAppDiscovery/context_variables.yaml")
    preload_tool = _read_text("factory_app/workflows/ExistingAppDiscovery/tools/preload_discovery_context.py")

    assert "host_app_source = \"workspace_app\"" in discovery_agents
    assert "`workspace_app` means preload the current workspace's known local app repo" in discovery_context
    assert "mozaiks-app" in preload_tool
    assert "ThemeCapture" in preload_tool
