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
    data = _read_yaml("factory_app/app/workflows/ExistingAppDiscovery/structured_outputs.yaml")

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
    context_vars = _read_yaml("factory_app/app/workflows/ExistingAppDiscovery/context_variables.yaml")
    agents = _read_text("factory_app/app/workflows/ExistingAppDiscovery/agents.yaml")

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
    assert "workspace_host" in agents
    assert "theme_adaptation_strategy" in agents
    assert "embed_theme_ready" in agents
    assert "brand_theme_summary" in agents


def test_create_route_enters_canonical_build_transition() -> None:
    registry = _read_yaml("factory_app/app/workflows/extended_orchestration/extension_registry.json")
    create_page = next(item for item in registry["entrypoints"] if item["path"] == "/create")

    assert create_page["transition"] == "app_type_selector"
    assert create_page["sequence"] == "build"
    assert create_page["requiresAuth"] is False
    assert "showInHeader" not in create_page
    assert "journey" not in create_page


def test_new_app_entry_routes_into_valueengine_first() -> None:
    registry = _read_yaml("factory_app/app/workflows/extended_orchestration/extension_registry.json")
    value_context = _read_yaml("factory_app/app/workflows/ValueEngine/context_variables.yaml")

    workflow_sequences = registry.get("workflow_sequences") or []
    build_journey = next(item for item in workflow_sequences if item["id"] == "build")
    assert "entry_transition" not in build_journey
    assert build_journey["steps"][0]["transition"] == "app_type_selector"
    assert build_journey["steps"][1]["workflows"] == ["ValueEngine"]
    assert build_journey["steps"][2]["workflows"] == ["ThemeCapture"]
    assert build_journey["steps"][3]["transition"] == "coding_journey_selector"
    assert build_journey["steps"][4]["workflows"] == ["DesignDocs"]
    assert build_journey["steps"][5]["workflows"] == ["AgentGenerator"]
    assert build_journey["steps"][6]["workflows"] == ["AppGenerator"]

    transition_map = {item["id"]: item for item in registry["transitions"]}
    app_type_selector = transition_map["app_type_selector"]
    assert app_type_selector["transition_type"] == "user_choice_context"
    new_app_option = next(item for item in app_type_selector["options"] if item["id"] == "new_app")
    assert new_app_option["route_to"] == "ValueEngine"
    assert new_app_option["context_variables"] == {"app_type": "new"}
    assert "app_type" in value_context["definitions"]


def test_existing_app_entry_uses_valueengine_with_context() -> None:
    registry = _read_yaml("factory_app/app/workflows/extended_orchestration/extension_registry.json")
    transition_map = {item["id"]: item for item in registry["transitions"]}

    app_type_selector = transition_map["app_type_selector"]
    existing_app_option = next(item for item in app_type_selector["options"] if item["id"] == "existing_app")
    assert existing_app_option["route_to"] == "ValueEngine"
    assert existing_app_option["context_variables"] == {"app_type": "existing"}

    assert "existing_app_entry_selector" not in transition_map

    coding_selector = transition_map["coding_journey_selector"]
    assert coding_selector["transition_type"] == "user_choice_context"
    coding_options = {item["id"]: item for item in coding_selector["options"]}
    assert coding_options["autonomous"]["route_to"] == "DesignDocs"
    assert coding_options["guided"]["route_to"] == "DesignDocs"
    assert coding_options["autonomous"]["context_variables"]["design_docs_hitl"] is False
    assert coding_options["guided"]["context_variables"]["design_docs_hitl"] is True


def test_existing_app_preload_supports_workspace_host_with_split_repo_inputs() -> None:
    module = _load_module(
        "factory_app/app/workflows/ExistingAppDiscovery/tools/preload_discovery_context.py",
        "tests.preload_discovery_context_direct",
    )

    def _fake_host_source_inputs(host_app_source: str | None) -> dict:
        if host_app_source == "workspace_host":
            return {
                "frontend_repo_path": "C:/dogfood/MOZ-UI",
                "backend_repo_path": "C:/dogfood/mozaiks-platform/services",
                "discovery_mode": "guided",
            }
        return {}

    async def _fake_scan(local_repo_path: str | None, github_repo: str | None, github_ref: str | None) -> dict:
        if local_repo_path == "C:/dogfood/MOZ-UI":
            return {
                "success": True,
                "source": "local_repo",
                "repo_name": "MOZ-UI",
                "languages": ["JavaScript/TypeScript"],
                "frameworks": ["React"],
                "target_frameworks": [],
                "route_files": ["src/AppRoutes.js"],
                "service_entrypoints": [],
                "hub_files": [],
                "total_files_scanned": 42,
                "inferred_tech_stack": "JavaScript/TypeScript, React",
            }
        if local_repo_path == "C:/dogfood/mozaiks-platform/services":
            return {
                "success": True,
                "source": "local_repo",
                "repo_name": "services",
                "languages": ["C#"],
                "frameworks": [".NET"],
                "target_frameworks": ["net8.0"],
                "route_files": [],
                "service_entrypoints": ["Services/Messaging/Program.cs"],
                "hub_files": ["Services/Messaging/Hubs/MessageHub.cs"],
                "total_files_scanned": 84,
                "inferred_tech_stack": "C#, .NET, net8.0",
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
    module._scan_repo_source = _fake_scan
    module._collect_openapi = _fake_collect_openapi
    module._probe_backend = _fake_probe_backend
    module._load_theme_capture_preloader = lambda: _FakeThemeModule
    module._find_theme_config_path = lambda repo_path: None
    module._collect_theme_css_snapshot = lambda repo_path: "body { font-family: Rajdhani; background: #060B26; }"

    context = _Context(
        app_type="existing",
        host_app_source="workspace_host",
        backend_base_url="https://api.mozaiks.test",
    )

    result = asyncio.run(module.collect_prechat_discovery_context(context_variables=context))

    assert result["success"] is True
    assert context["discovery_mode"] == "guided"
    assert context["host_app_source"] == "workspace_host"
    assert context["app_name"] == "Mozaiks"
    assert context["frontend_repo_summary"]["repo_name"] == "MOZ-UI"
    assert context["backend_repo_summary"]["repo_name"] == "services"
    assert context["repo_summary"]["source"] == "multi_repo"
    assert context["repo_summary"]["repo_names"] == ["MOZ-UI", "services"]
    assert context["service_surfaces"][0]["kind"] == "rest_api"
    assert context["route_surfaces"][0]["module"] == "src"
    assert context["existing_experience_summary"].startswith("Current host experience is delivered by the MOZ-UI frontend")
    assert context["preload_status"] == "ready"
    assert context["theme_capture_ready"] is True
    assert context["theme_capture_status"] == "ready"
    assert "Rajdhani" in context["theme_capture_summary"]
    assert context["theme_capture_evidence"]["appearance"] == "dark"


def test_existing_app_artifact_saver_persists_canonical_fields() -> None:
    module = _load_module(
        "factory_app/app/workflows/ExistingAppDiscovery/tools/save_existing_app_artifacts.py",
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
            "request_intent": "existing_app",
            "existing_product_spec": {
                "app_name": "MOZ-UI",
                "app_description": "Existing product frontend",
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
    assert context["existing_product_spec"]["app_name"] == "MOZ-UI"
    assert context["capability_specs"][0]["capability_id"] == "direct_messaging"
    assert context["agent_augmentation_plan"]["adoption_level"] == "bridge"
    assert context["existing_app_discovery_artifact"]["request_intent"] == "existing_app"

    assert emitted["component"] == "DiscoveryBriefCard"
    assert emitted["payload"]["adoption_level"] == "bridge"
    assert emitted["payload"]["service_surface_count"] == 1
    assert emitted["payload"]["route_surface_count"] == 1
    assert emitted["payload"]["initial_workflows"] == ["ThreadSummary"]
    assert emitted["payload"]["embed_theme_ready"] is True
    assert "Rajdhani" in emitted["payload"]["brand_theme_summary"]


def test_existing_app_strategy_docs_are_indexed() -> None:
    index_text = _read_text("docs/architecture/specs/INDEX.md")
    strategy_text = _read_text("docs/architecture/specs/agentic-app-generation-strategy.md")
    augmentation_text = _read_text("docs/architecture/specs/existing-app-augmentation-strategy.md")
    checklist_text = _read_text("docs/architecture/specs/agentic-app-generation-checklist.md")

    assert "existing-app-augmentation-strategy.md" in index_text
    assert "Existing-app augmentation first" in strategy_text
    assert "augment first" in augmentation_text
    assert "Embed" in augmentation_text
    assert "Native Migration" in augmentation_text
    assert "guided product walkthrough" in augmentation_text
    assert "workspace_host" in augmentation_text
    assert "guided plain-language onboarding first" in strategy_text
    assert "Existing-app augmentation doc is canonical" in checklist_text
    assert "Mozaiks workspace-host dogfood path" in checklist_text


def test_platform_readme_documents_existing_app_dogfood_path() -> None:
    from conftest import active_app_root
    app_root = active_app_root()
    readme_text = (app_root.parent / "README.md").read_text(encoding="utf-8")

    assert "Existing-App Dogfood" in readme_text
    assert "host_app_source" in readme_text
    assert "workspace_host" in readme_text
    assert "ThemeCapture" in readme_text
    assert "/api/themes/mozaiks-platform" in readme_text

