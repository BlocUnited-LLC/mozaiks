from __future__ import annotations

import asyncio
import importlib.util
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from mozaiksai.core.artifacts.models import (
    ArtifactLifecycleStatus,
    ArtifactValidationStatus,
    ArtifactVersionDoc,
)

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


class _MemoryArtifactStore:
    def __init__(self) -> None:
        self.created: list[ArtifactVersionDoc] = []

    async def create_artifact_version(self, **kwargs: Any) -> ArtifactVersionDoc:
        artifact_id = f"av_{len(self.created) + 1}"
        artifact = ArtifactVersionDoc(
            _id=artifact_id,
            app_id=kwargs["app_id"],
            artifact_kind=kwargs["artifact_kind"],
            artifact_key=kwargs["artifact_key"],
            version_number=len(self.created) + 1,
            lineage_root_id=artifact_id,
            source_workflow=kwargs.get("source_workflow"),
            source_chat_id=kwargs.get("source_chat_id"),
            lifecycle_status=kwargs.get("lifecycle_status", ArtifactLifecycleStatus.DRAFT),
            validation_status=kwargs.get("validation_status", ArtifactValidationStatus.PENDING),
            files_manifest=list(kwargs.get("files_manifest") or []),
            commit_metadata=kwargs.get("commit_metadata") or {},
        )
        self.created.append(artifact)
        return artifact

    async def get_artifact_version(self, *, app_id: str, artifact_version_id: str) -> ArtifactVersionDoc | None:
        for artifact in self.created:
            if artifact.app_id == app_id and artifact.id == artifact_version_id:
                return artifact
        return None

    async def list_artifact_versions(
        self,
        *,
        app_id: str,
        artifact_kind: str | None = None,
        artifact_key: str | None = None,
        lifecycle_status: ArtifactLifecycleStatus | None = None,
        limit: int = 50,
        **_kwargs: Any,
    ) -> list[ArtifactVersionDoc]:
        rows = [
            artifact
            for artifact in self.created
            if artifact.app_id == app_id
            and (artifact_kind is None or artifact.artifact_kind == artifact_kind)
            and (artifact_key is None or artifact.artifact_key == artifact_key)
            and (lifecycle_status is None or artifact.lifecycle_status == lifecycle_status)
        ]
        return rows[:limit]

    async def accept_artifact_version(
        self,
        *,
        app_id: str,
        artifact_version_id: str,
        commit_metadata: dict[str, Any] | None = None,
    ) -> ArtifactVersionDoc | None:
        artifact = await self.get_artifact_version(app_id=app_id, artifact_version_id=artifact_version_id)
        if artifact is None:
            return None
        updates: dict[str, Any] = {"lifecycle_status": ArtifactLifecycleStatus.CURRENT}
        if commit_metadata is not None:
            updates["commit_metadata"] = commit_metadata
        refreshed = artifact.model_copy(update=updates)
        self.created = [refreshed if item.id == artifact.id else item for item in self.created]
        return refreshed


def _install_memory_app_intelligence_store(monkeypatch: pytest.MonkeyPatch) -> _MemoryArtifactStore:
    from mozaiksai.control_plane import app_intelligence as app_intelligence_mod

    store = _MemoryArtifactStore()
    monkeypatch.setattr(app_intelligence_mod, "get_artifact_store", lambda: store)
    return store


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
    assert "Add AI Workflows" in plan_fields["adoption_level"]["description"]
    assert "Build App Features" in plan_fields["adoption_rationale"]["description"]
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
    hooks = _read_yaml("factory_app/workflows/ExistingAppDiscovery/middleware.yaml")
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
    assert "context_graph_pack" in definitions
    assert "context_graph_catalog" in definitions
    assert "context_graph_status" in definitions
    assert "context_graph_reason" in definitions
    assert "context_graph_warnings" in definitions
    assert "context_graph_health" in definitions
    assert "app_intelligence_ready" in definitions
    assert "app_intelligence_status" in definitions
    assert "app_intelligence_summary" in definitions
    assert "app_intelligence_progress" in definitions
    assert "app_intelligence_health" in definitions
    assert "repo_access_status" in definitions
    assert "repo_access_recovery" in definitions
    assert "app_context_graph" in definitions
    assert "adoption_level" in definitions
    assert "ecosystem_bindings" in definitions
    assert any(
        item.get("agent") == "all"
        and item.get("filename") == "../_shared/context_graph/hook_context_graph.py"
        and item.get("function") == "inject_context_graph_context"
        for item in hooks.get("prompt_middleware") or []
    )

    assert "Embed" in agents
    assert "Bridge" in agents
    assert "Ecosystem" in agents
    assert "Gradual Modernization" in agents
    assert "ExistingProductSpec" in agents
    assert "AgentAugmentationPlan" in agents
    assert "app_intelligence_ready" in agents
    assert "app_intelligence_summary" in agents
    assert "repo_access_status" in agents
    assert "repo_access_recovery" in agents
    assert "feature readout" in agents
    assert "Use 4-7 named feature areas" in agents
    assert "Do not say \"and more\", \"many more\", \"various modules\"" in agents
    assert "`activity_feed` -> activity feed or timeline" in agents
    assert "`hosting`, `dns_management`, `domain_registry` -> hosting, DNS, and domain management" in agents
    assert "`hosted_billing`, `wallet`, `mozaikspay`, checkout routes -> billing, wallet, payments, or checkout" in agents
    assert "I see code surfaces for" in agents
    assert "do not ask for `app_url`" in agents
    assert "unless live runtime/browser behavior is actually needed" in agents
    assert "Under the hood" in agents
    assert "product readout to the App" in agents
    assert "Do not list future build ideas in the chat" in agents
    assert "the next ValueEngine step owns the enhancement plan" in agents
    assert "Which of these feature areas should Mozaiks work on first?" in agents
    assert "discovery_mode" in agents
    assert "host_app_source" in agents
    assert "workspace_app" in agents
    assert "theme_adaptation_strategy" in agents
    assert "embed_theme_ready" in agents
    assert "brand_theme_summary" in agents


def test_existing_app_discovery_before_chat_repo_access_recovery_shown_inline() -> None:
    tools = _read_yaml("factory_app/workflows/ExistingAppDiscovery/tools.yaml")
    before_chat = [item for item in tools["lifecycle_tools"] if item["trigger"] == "before_chat"]
    manifest_text = _read_text("factory_app/workflows/ExistingAppDiscovery/tools.yaml")

    assert [(item["file"], item["function"]) for item in before_chat] == [
        ("preload_discovery_context.py", "collect_prechat_discovery_context"),
        ("emit_app_intelligence_overview.py", "emit_repo_access_recovery_card"),
    ]
    recovery_tool = before_chat[1]
    assert recovery_tool["tool_type"] == "UI_Surface"
    assert recovery_tool["ui"]["component"] == "RepoAccessRecoveryCard"
    assert recovery_tool["ui"]["mode"] == "inline"
    ui_index = _read_text("factory_app/workflows/ExistingAppDiscovery/ui/index.js")
    assert "RepoAccessRecoveryCard" in ui_index
    assert "AppIntelligenceInlineBrief" not in ui_index
    assert "AppIntelligenceOverviewCard" not in ui_index
    assert "get_preloaded_app_intelligence" in manifest_text
    assert "search_preloaded_source_context" in manifest_text
    assert "read_preloaded_source_file" in manifest_text
    assert "get_related_preloaded_source_files" in manifest_text
    assert "emit_app_intelligence_inline_brief" not in manifest_text
    assert "emit_app_intelligence_overview_card" not in manifest_text
    assert "get_repo_app_intelligence" not in manifest_text
    assert "search_repo_source_context" not in manifest_text
    assert "read_repo_source_file" not in manifest_text


def test_repo_access_recovery_emitter_surfaces_private_github_blocker() -> None:
    module = _load_module(
        "factory_app/workflows/ExistingAppDiscovery/tools/emit_app_intelligence_overview.py",
        "tests.emit_repo_access_recovery_direct",
    )

    emitted = {}

    async def _fake_emit(component, payload, **kwargs):
        emitted["component"] = component
        emitted["payload"] = payload
        emitted["kwargs"] = kwargs
        return "ui_repo_access_1"

    module.emit_ui_surface = _fake_emit
    context = _Context(
        chat_id="chat_repo_access",
        repo_access_status="required",
        github_repo="BlocUnited-LLC/mozaiks-app",
        repo_access_recovery={
            "provider": "github",
            "code": "github_repo_access_required",
            "github_repo": "BlocUnited-LLC/mozaiks-app",
            "github_url": "https://github.com/BlocUnited-LLC/mozaiks-app",
            "http_status": 404,
            "phase": "repo_lookup",
            "auth_present": False,
            "message": "Mozaiks could not access this GitHub repository.",
            "recovery_actions": [{"id": "connect_github", "label": "Connect GitHub", "kind": "oauth_retry"}],
        },
        app_intelligence_status="unavailable",
        app_intelligence_progress={
            "stage": "repo_access_required",
            "status": "unavailable",
            "message": "GitHub access is required before App Intelligence can index this repository.",
            "warnings": ["github_repo_lookup_failed:BlocUnited-LLC/mozaiks-app:404"],
        },
    )

    result = asyncio.run(module.emit_repo_access_recovery_card(context_variables=context))

    assert result["success"] is True
    assert result["ui_event_id"] == "ui_repo_access_1"
    assert emitted["component"] == "RepoAccessRecoveryCard"
    assert emitted["payload"]["repo_access_status"] == "required"
    assert emitted["payload"]["github_repo"] == "BlocUnited-LLC/mozaiks-app"
    assert emitted["payload"]["http_status"] == 404
    assert emitted["payload"]["recovery_actions"][0]["id"] == "connect_github"
    assert emitted["payload"]["activity_display_variant"] == "app_intelligence_progress"
    assert emitted["payload"]["activity_component_type"] == "AppIntelligenceProgressCard"
    assert "component_type" not in emitted["payload"]
    assert emitted["kwargs"]["workflow_name"] == "ExistingAppDiscovery"
    assert emitted["kwargs"]["agent_name"] == "App Intelligence"
    assert emitted["kwargs"]["display"] == "inline"


def test_existing_app_preload_activity_emits_visible_indexing_status(monkeypatch) -> None:
    module = _load_module(
        "factory_app/workflows/ExistingAppDiscovery/tools/preload_discovery_context.py",
        "tests.preload_activity_emit",
    )
    transport_mod = __import__("mozaiksai.core.transport.simple_transport", fromlist=["SimpleTransport"])

    class _FakeTransport:
        def __init__(self) -> None:
            self.events: list[tuple[dict[str, Any], str | None]] = []

        async def send_event_to_ui(self, event: dict[str, Any], chat_id: str | None = None) -> None:
            self.events.append((event, chat_id))

    fake_transport = _FakeTransport()

    async def _get_instance():
        return fake_transport

    monkeypatch.setattr(transport_mod.SimpleTransport, "get_instance", staticmethod(_get_instance))

    context = _Context(chat_id="chat_app_intelligence_progress", app_id="app_1")
    module._set_app_intelligence_progress(context, "collecting_evidence")
    result = asyncio.run(module._emit_app_intelligence_activity(context))

    assert result["success"] is True
    assert fake_transport.events
    event, chat_id = fake_transport.events[-1]
    assert chat_id == "chat_app_intelligence_progress"
    assert event["kind"] == "activity"
    assert event["activity_type"] == "app_intelligence_indexing"
    assert event["agent"] == "App Intelligence"
    assert event["status"] == "working"
    assert event["progress_percent"] == 25
    assert event["display_variant"] == "app_intelligence_progress"
    assert event["component_type"] == "AppIntelligenceProgressCard"
    assert event["activity_display_variant"] == "app_intelligence_progress"
    assert event["activity_component_type"] == "AppIntelligenceProgressCard"
    assert "Obtaining app context" in event["message"]
    assert event["metadata"]["display_variant"] == "app_intelligence_progress"
    assert event["metadata"]["component_type"] == "AppIntelligenceProgressCard"
    assert event["metadata"]["activity_display_variant"] == "app_intelligence_progress"
    assert event["metadata"]["activity_component_type"] == "AppIntelligenceProgressCard"
    assert event["metadata"]["progress_stage"] == "collecting_evidence"
    assert event["metadata"]["progress_status"] == "indexing"
    assert event["metadata"]["progress"]["stage"] == "collecting_evidence"
    assert event["metadata"]["app_intelligence_progress"]["stage"] == "collecting_evidence"
    assert event["metadata"]["progress_details"] == {}
    assert event["metadata"]["progress_warnings"] == []

    module._set_app_intelligence_progress(context, "ready")
    result = asyncio.run(module._emit_app_intelligence_activity(context))

    assert result["status"] == "complete"
    ready_event, _ = fake_transport.events[-1]
    assert ready_event["status"] == "complete"
    assert ready_event["message"] == "App context ready. Starting the discovery agent."

    module._set_app_intelligence_progress(
        context,
        "fetching_source_files",
        message="Downloading selected source files from GitHub (60/120).",
        percent=48,
    )
    result = asyncio.run(module._emit_app_intelligence_activity(context))

    assert result["status"] == "working"
    download_event, _ = fake_transport.events[-1]
    assert download_event["status"] == "working"
    assert download_event["progress_percent"] == 48
    assert "Downloading selected source files from GitHub (60/120)." in download_event["message"]


def test_chat_page_renders_user_visible_app_intelligence_progress() -> None:
    source = _read_text("chat-ui/src/pages/ChatPage.js")
    chat_message = _read_text("chat-ui/src/components/chat/ChatMessage.jsx")
    chat_interface = _read_text("chat-ui/src/components/chat/ChatInterface.jsx")
    activity_renderer = _read_text("chat-ui/src/components/chat/ActivityRenderer.jsx")
    activity_helper = _read_text("chat-ui/src/components/chat/activityArtifacts.js")
    existing_app_ui_index = _read_text("factory_app/workflows/ExistingAppDiscovery/ui/index.js")

    assert "!data.type.startsWith('ui.')" in source
    assert "buildActivityMessageFromEvent(data, currentWorkflowName)" in source
    assert "shouldShowToolProgress(data)" in source
    assert "buildRestoredActivityMessage" in source
    assert "buildComposerArtifactContext" in source
    assert "const artifactContextPayload = buildComposerArtifactContext" in source
    assert "artifactContextPayload ? { artifact_context: artifactContextPayload } : null" in source
    assert "const scopedStoredChatId = !queryFreshStart && !askCarrierMode" in source
    assert "(!queryMode && conversationMode === 'ask')" in source
    assert "rememberWorkflowChatSession(candidateChatId, resolvedCandidateWorkflow)" in source
    assert "rememberWorkflowChatSession(metaChatId, metaWorkflowName)" in source
    assert "rememberWorkflowChatSession(currentChatId, workflowName)" in source
    assert "snapshotMatchesChat" in _read_text("chat-ui/src/hooks/useChatStartupEffects.js")
    assert "ExistingAppDiscovery" not in source
    assert "ValueEngine" not in source
    assert "DesignDocs" not in source
    assert "AppGenerator" not in source
    assert "AppIntelligence" not in source
    assert "App Intelligence" not in source
    assert "app_intelligence" not in source

    composer_context_helper = activity_helper[
        activity_helper.index("export function buildComposerArtifactContext"):
        activity_helper.index("// Extracted hooks for gradual migration")
        if "// Extracted hooks for gradual migration" in activity_helper
        else len(activity_helper)
    ]
    assert "app_intelligence_catalog:" not in composer_context_helper
    assert "app_intelligence_catalog," not in composer_context_helper
    assert "suggested_adjustments:" not in composer_context_helper
    assert "context_graph_pack:" not in composer_context_helper
    assert "source_context_catalog:" not in composer_context_helper
    assert "file_contents" not in composer_context_helper
    assert "source_chunks" not in composer_context_helper
    assert "graph_relationships" not in composer_context_helper
    assert "suggested_adjustments_count" in composer_context_helper
    assert "artifact_version_ids" in composer_context_helper
    assert "/api/chats/meta/${encodedAppId}/${encodedWorkflow}/${encodedChatId}" in source
    assert "cachedCurrent?.tool_name || cachedCurrent?.toolCall?.tool_name" in source
    assert "cached?.payload || cachedToolCall.payload" in source
    assert "cached.component_type || cachedToolCall.component_type || cachedPayload.component_type" in source
    assert "setCurrentArtifactMessages((prev) =>" in source
    assert "getStoredArtifactPanelOpen(targetChatId) !== false" in source
    assert "setLayoutMode('split')" in source
    assert "cacheServerLastArtifact(data.last_artifact, {" in source
    assert "chatId: metaChatId" in source
    assert "cacheServerLastArtifact(metaData.last_artifact, {" in source
    assert "cacheServerLastArtifact(meta.last_artifact, {" in source
    assert "handleMissingBackendArtifact(metaChatId, metaWorkflowName)" in source
    assert "restoredActivityArtifactRef.current" in source
    assert "restored_from_last_artifact" in activity_helper
    assert "const userVisibleToolProgress = shouldShowToolProgress(data)" in source
    assert "agentName: activityAgent" in activity_helper
    assert "agentName: tool" in source
    assert "shouldShowToolProgress(data)" in source
    assert "ActivityRenderer" in chat_message
    assert "resolveActivityComponent" in chat_message
    assert "AppIntelligenceProgressCard" not in chat_message
    assert "app_intelligence_indexing" not in chat_message
    assert "ExistingAppDiscovery" not in chat_message
    assert "AppIntelligenceProgressCard" in existing_app_ui_index
    assert "app_intelligence_progress" in existing_app_ui_index
    assert "getComponent(candidate)" in activity_renderer
    assert "workflowName && componentType ? `${workflowName}:${componentType}`" in activity_renderer
    assert "metadata={chat.metadata}" in chat_interface

    controller_source = _read_text("chat-ui/src/hooks/useConversationModeController.js")
    assert "restoreStoredArtifactForChat," in controller_source
    assert "restoreStoredArtifactForChat(" in controller_source
    assert "snapshotChatId || currentChatId" in controller_source
    assert "snapshotWorkflowName || currentWorkflowName" in controller_source


def test_app_intelligence_progress_card_is_stage_based_and_persistent() -> None:
    source = _read_text("factory_app/workflows/ExistingAppDiscovery/ui/AppIntelligenceProgressCard.jsx")

    assert "Understanding your codebase" in source
    assert "if (isReady)" in source
    assert "Codebase context is ready" in source
    assert "Completed" in source
    assert "Mozaiks finished reading the app. The discovery agent can use this context now." in source
    assert "Safe indexing notes are available in the overview." in source
    assert "dedupeWarnings" in source
    assert "Indexing notes" in source
    assert 'aria-live="polite"' in source
    assert "Connect to codebase" in source
    assert "Read app signals" in source
    assert "Choose safe files" in source
    assert "Load source files" in source
    assert "Parse code structure" in source
    assert "Build relationship graph" in source
    assert "Prepare agent brief" in source
    assert "The agent has not started editing files." in source
    assert "repo_access_required" in source
    assert "Needs attention" in source


def test_workflow_component_registration_is_hmr_safe() -> None:
    source = _read_text("chat-ui/src/@chat-workflows/index.js")
    app_source = _read_text("chat-ui/src/app/MozaiksApp.jsx")

    assert "override: true" in source
    assert "return initializeWorkflows(registerComponent)" in app_source


def test_existing_app_preload_mutates_an_empty_context_container() -> None:
    module = _load_module(
        "factory_app/workflows/ExistingAppDiscovery/tools/preload_discovery_context.py",
        "tests.preload_discovery_empty_context",
    )
    context: dict[str, object] = {}

    result = asyncio.run(module.collect_prechat_discovery_context(context_variables=context))

    assert result["success"] is True
    assert context["host_app_source"] == "external"
    assert context["preload_status"] == "none"
    assert context["app_intelligence_ready"] is False
    assert context["app_intelligence_status"] == "unavailable"
    assert context["app_intelligence_progress"]["stage"] == "unavailable"


def test_create_route_enters_canonical_build_transition() -> None:
    registry = _read_yaml("factory_app/workflows/extended_orchestration/extension_registry.json")
    create_page = next(item for item in registry["entrypoints"] if item["path"] == "/create")

    assert create_page["transition"] == "app_type_selector"
    assert create_page["sequence"] == "build"
    assert create_page["requiresAuth"] is False
    assert create_page["meta"]["freshStart"] is True
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
    assert build_journey["steps"][6]["workflows"] == ["SubscriptionContractDesigner"]
    assert build_journey["steps"][7]["workflows"] == ["AgentGenerator"]
    assert build_journey["steps"][8]["workflows"] == ["AppGenerator"]

    transition_map = {item["id"]: item for item in registry["transitions"]}
    app_type_selector = transition_map["app_type_selector"]
    assert app_type_selector["transition_type"] == "user_choice_context"
    assert app_type_selector["ui"]["props"] == {
        "dismissible": True,
        "dismiss_to": "/apps",
        "close_label": "Back to Apps",
    }
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
    assert adoption_journey["steps"][1]["transition"] == "brownfield_repo_input"
    assert adoption_journey["steps"][2]["workflows"] == ["ExistingAppDiscovery"]
    assert adoption_journey["steps"][3]["transition"] == "brownfield_path_selector"

    app_type_selector = transition_map["app_type_selector"]
    existing_app_option = next(item for item in app_type_selector["options"] if item["id"] == "brownfield_app")
    assert existing_app_option["route_to"] == "brownfield_repo_input"
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


def test_brownfield_path_selector_routes_to_downstream_sequences() -> None:
    registry = _read_yaml("factory_app/workflows/extended_orchestration/extension_registry.json")
    transition_map = {item["id"]: item for item in registry["transitions"]}
    sequence_map = {item["id"]: item for item in registry.get("workflow_sequences") or []}

    # Transition declaration
    selector = transition_map["brownfield_path_selector"]
    assert selector["transition_type"] == "user_choice_context"
    assert selector["ui"]["component"] == "BrownfieldPathSelector"
    assert selector["ui"]["shell_mode"] == "focused"

    options = {opt["id"]: opt for opt in selector["options"]}

    # Both options enter ValueEngine first; the selected path scopes downstream generation.
    assert options["light_integration"]["route_to"] == "ValueEngine"
    assert options["light_integration"]["sequence"] == "brownfield_overlay_generation"
    assert options["light_integration"]["context_variables"]["brownfield_build_path"] == "light_integration"

    assert options["full_migration"]["route_to"] == "ValueEngine"
    assert options["full_migration"]["sequence"] == "brownfield_module_generation"
    assert options["full_migration"]["context_variables"]["brownfield_build_path"] == "full_migration"

    legacy_light_sequence = "brownfield_" + "build_light"
    legacy_full_sequence = "brownfield_" + "build_full"
    assert legacy_light_sequence not in sequence_map
    assert legacy_full_sequence not in sequence_map

    expected_downstream = [
        "ValueEngine",
        "ThemeCapture",
        "DesignDocs",
        "SubscriptionContractDesigner",
        "AgentGenerator",
        "AppGenerator",
    ]

    # brownfield_overlay_generation sequence
    light = sequence_map["brownfield_overlay_generation"]
    light_workflow_names = [s["workflows"][0] for s in light["steps"] if "workflows" in s]
    assert light_workflow_names == expected_downstream
    assert any(s.get("transition") == "app_review" for s in light["steps"])

    # brownfield_module_generation sequence
    full = sequence_map["brownfield_module_generation"]
    full_workflow_names = [s["workflows"][0] for s in full["steps"] if "workflows" in s]
    assert full_workflow_names == expected_downstream
    assert any(s.get("transition") == "app_review" for s in full["steps"])

    # BrownfieldPathSelector must be registered in ui/index.js
    index_text = _read_text("factory_app/workflows/extended_orchestration/ui/index.js")
    assert "BrownfieldPathSelector" in index_text


def test_existing_app_preload_supports_workspace_app_preset() -> None:
    module = _load_module(
        "factory_app/workflows/ExistingAppDiscovery/tools/preload_discovery_context.py",
        "tests.preload_discovery_context_direct",
    )

    def _fake_host_source_inputs(host_app_source: str | None) -> dict:
        if host_app_source == "workspace_app":
            return {
                "repo_path": "C:/workspace/sample-existing-app",
                "discovery_mode": "guided",
            }
        return {}

    async def _fake_scan(local_repo_path: str | None, github_repo: str | None, github_ref: str | None) -> dict:
        if local_repo_path == "C:/workspace/sample-existing-app":
            return {
                "success": True,
                "source": "local_repo",
                "repo_name": "sample-existing-app",
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
        if repo_path == "C:/workspace/sample-existing-app":
            return {
                "success": True,
                "source": "local_repo",
                "repo_name": "sample-existing-app",
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
    assert context["app_name"] == "sample-existing-app"
    assert context["repo_summary"]["source"] == "local_repo"
    assert context["repo_summary"]["repo_name"] == "sample-existing-app"
    assert context["service_surfaces"][0]["kind"] == "rest_api"
    assert context["route_surfaces"][0]["module"] == "src"
    assert context["existing_experience_summary"].startswith("Current experience appears to be organized around route/module surfaces")
    assert context["preload_status"] == "ready"
    assert context["theme_capture_ready"] is True
    assert context["theme_capture_status"] == "ready"
    assert "Rajdhani" in context["theme_capture_summary"]
    assert context["theme_capture_evidence"]["appearance"] == "dark"


def test_existing_app_preload_builds_context_graph_pack_for_local_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module(
        "factory_app/workflows/ExistingAppDiscovery/tools/preload_discovery_context.py",
        "tests.preload_discovery_context_graph_direct",
    )
    store = _install_memory_app_intelligence_store(monkeypatch)

    repo = tmp_path / "sample-existing-app"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "service.py").write_text(
        "class TaskService:\n"
        "    def list_tasks(self):\n"
        "        return []\n",
        encoding="utf-8",
    )
    (repo / "package.json").write_text(
        '{"dependencies": {"react": "^18.0.0"}}',
        encoding="utf-8",
    )

    context = _Context(
        app_id="app_1",
        repo_path=str(repo),
        discovery_inputs={"repo_path": str(repo)},
    )

    result = asyncio.run(module.collect_prechat_discovery_context(context_variables=context))

    assert result["success"] is True
    assert result["context_graph_status"] == "loaded"
    assert context["context_graph_status"] == "loaded"
    assert context["context_graph_pack"]["source"] == "existing_app_discovery_preload"
    assert context["context_graph_pack"]["pack_kind"] == "context_graph_prompt_pack"
    assert context["context_graph_catalog"]["indexed_file_count"] >= 1
    assert context["context_graph_health"]["selected_file_count"] >= 1
    assert context["context_graph_pack"]["summary"]["scan"]["selected_file_count"] >= 1
    assert context["source_context_bundle"]["schema_version"] == "mozaiks.source_context.bundle.v1"
    assert context["source_context_catalog"]["file_count"] >= 1
    assert context["app_intelligence_snapshot"]["schema_version"] == "mozaiks.app_intelligence.snapshot.v1"
    assert context["app_intelligence_catalog"]["coverage"]["file_count"] >= 1
    assert context["app_intelligence_ready"] is True
    assert context["app_intelligence_status"] == "ready"
    assert context["app_intelligence_progress"]["stage"] == "ready"
    assert context["app_intelligence_progress"]["schema_version"] == "mozaiks.app_intelligence.progress.v1"
    assert context["app_intelligence_progress"]["details"]["app_context_persisted"] is True
    assert context["current_context_version_id"].startswith("ctx_")
    assert context["current_app_context_version_id"] == context["current_context_version_id"]
    assert context["app_context_version_artifact_version_id"] == store.created[-1].id
    assert context["source_context_artifact_version_id"] == store.created[0].id
    assert context["graph_artifact_version_id"] == store.created[1].id
    assert context["app_intelligence_artifact_version_id"] == store.created[2].id
    assert context["app_intelligence_registration"]["persisted"] is True
    assert [artifact.artifact_kind for artifact in store.created] == [
        "source_context_bundle",
        "app_context_graph",
        "app_intelligence_snapshot",
        "app_context_version",
    ]
    assert store.created[-1].lifecycle_status == ArtifactLifecycleStatus.CURRENT
    context_payload = store.created[-1].commit_metadata.metadata["summary_payload"]
    assert context_payload["mode"] == "brownfield"
    assert any(ref["artifact_kind"] == "source_context_bundle" for ref in context_payload["artifact_refs"])
    assert any(ref["artifact_kind"] == "app_intelligence_snapshot" for ref in context_payload["artifact_refs"])
    assert context["app_intelligence_health"]["status"] in {"healthy", "warning"}
    assert "App Intelligence indexed" in context["app_intelligence_summary"]
    assert context["context_graph_catalog"]["source_context_chunk_count"] >= 1
    assert "src/service.py" in context["context_graph_catalog"]["file_tree"]
    assert "App Intelligence indexed" in context["preload_summary"]


def test_existing_app_refresh_preloads_prior_context_graph_when_no_source_roots(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_module(
        "factory_app/workflows/ExistingAppDiscovery/tools/preload_discovery_context.py",
        "tests.preload_discovery_context_graph_refresh",
    )

    from mozaiksai.control_plane import app_context as app_context_mod
    from mozaiksai.core.app_context.context_graph import build_context_graph_from_file_map

    graph = build_context_graph_from_file_map(
        app_id="app_1",
        artifact_version_id="av_graph_1",
        artifact_kind="app_context_graph",
        file_map={
            "modules/tasks/backend/service.py": "def list_tasks():\n    return []\n",
        },
    )

    async def _prior_graph(**kwargs):
        assert kwargs["app_id"] == "app_1"
        assert kwargs["context_version_id"] == "ctx_before_refresh"
        return SimpleNamespace(graph=graph, warnings=[])

    async def _prior_source_bundle(**kwargs):
        assert kwargs["app_id"] == "app_1"
        assert kwargs["context_version_id"] == "ctx_before_refresh"
        return SimpleNamespace(bundle=None, warnings=[])

    async def _prior_intelligence(**kwargs):
        assert kwargs["app_id"] == "app_1"
        assert kwargs["context_version_id"] == "ctx_before_refresh"
        return SimpleNamespace(snapshot=None, warnings=[])

    monkeypatch.setattr(app_context_mod, "get_app_context_graph_for_version", _prior_graph)
    monkeypatch.setattr(app_context_mod, "get_source_context_bundle_for_version", _prior_source_bundle)
    monkeypatch.setattr(app_context_mod, "get_app_intelligence_snapshot_for_version", _prior_intelligence)
    context = _Context(
        app_id="app_1",
        current_context_version_id="ctx_before_refresh",
        context_refresh_request={
            "app_id": "app_1",
            "current_context_version_id": "ctx_before_refresh",
            "reason": "Refresh repository snapshot.",
        },
        discovery_inputs={},
    )

    result = asyncio.run(module.collect_prechat_discovery_context(context_variables=context))

    assert result["context_graph_status"] == "loaded"
    assert context["context_graph_status"] == "loaded"
    assert context["context_graph_reason"] == "context_refresh_prior_version"
    assert context["context_graph_pack"]["source"] == "previous_app_context_graph"
    assert context["context_graph_pack"]["reason"] == "context_refresh_prior_version"
    assert context["context_graph_catalog"]["current_context_version_id"] == "ctx_before_refresh"
    assert context["context_graph_health"]["source"] == "previous_app_context_graph"
    assert context["source_context_bundle"] is None
    assert context["source_context_catalog"] is None
    assert context["app_intelligence_snapshot"] is None
    assert context["app_intelligence_catalog"] is None
    assert context["app_intelligence_ready"] is False
    assert context["app_intelligence_status"] == "partial"
    assert context["app_intelligence_progress"]["stage"] == "partial"
    assert "modules/tasks/backend/service.py" in context["context_graph_catalog"]["file_tree"]


def test_existing_app_github_repo_identifier_accepts_urls_and_owner_repo() -> None:
    module = _load_module(
        "factory_app/workflows/ExistingAppDiscovery/tools/preload_discovery_context.py",
        "tests.preload_discovery_github_identifier",
    )

    assert module._normalize_github_repo_identifier("BlocUnited-LLC/mozaiks-app") == "BlocUnited-LLC/mozaiks-app"
    assert (
        module._normalize_github_repo_identifier("https://github.com/BlocUnited-LLC/mozaiks-app")
        == "BlocUnited-LLC/mozaiks-app"
    )
    assert (
        module._normalize_github_repo_identifier("git@github.com:BlocUnited-LLC/mozaiks-app.git")
        == "BlocUnited-LLC/mozaiks-app"
    )
    assert module._normalize_github_repo_identifier("not-a-repo") is None


def test_existing_app_github_repo_scan_returns_access_recovery_for_private_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module(
        "factory_app/workflows/ExistingAppDiscovery/tools/preload_discovery_context.py",
        "tests.preload_discovery_github_access_recovery",
    )

    class _FakeResponse:
        status_code = 404

        def json(self) -> dict:
            return {"message": "Not Found"}

    async def _fake_github_request(url: str, token: str | None, **kwargs):
        assert url.endswith("/repos/BlocUnited-LLC/mozaiks-app")
        assert token is None
        return _FakeResponse()

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(module, "_github_request", _fake_github_request)

    result = asyncio.run(module._scan_github_repo("https://github.com/BlocUnited-LLC/mozaiks-app", None))

    assert result["success"] is False
    assert result["source"] == "github_repo_scan"
    assert result["github_repo"] == "BlocUnited-LLC/mozaiks-app"
    assert result["repo_access_recovery"]["schema_version"] == "mozaiks.repo_access_recovery.v1"
    assert result["repo_access_recovery"]["code"] == "github_repo_access_required"
    assert result["repo_access_recovery"]["http_status"] == 404
    assert result["repo_access_recovery"]["auth_present"] is False
    assert result["repo_access_recovery"]["recovery_actions"][0]["id"] == "connect_github"


def test_existing_app_github_context_graph_sets_access_recovery_on_lookup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module(
        "factory_app/workflows/ExistingAppDiscovery/tools/preload_discovery_context.py",
        "tests.preload_discovery_github_context_graph_access_recovery",
    )

    class _FakeResponse:
        status_code = 404

        def json(self) -> dict:
            return {"message": "Not Found"}

    async def _fake_github_request(url: str, token: str | None, **kwargs):
        assert url.endswith("/repos/BlocUnited-LLC/mozaiks-app")
        assert token is None
        return _FakeResponse()

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(module, "_github_request", _fake_github_request)

    result = asyncio.run(
        module._collect_github_context_graph_file_map(
            [("", "https://github.com/BlocUnited-LLC/mozaiks-app")],
            github_ref=None,
            github_token=None,
        )
    )

    assert result.file_map == {}
    assert result.health["access_issues"][0]["code"] == "github_repo_access_required"
    assert result.health["access_issues"][0]["github_repo"] == "BlocUnited-LLC/mozaiks-app"
    assert result.health["skipped"]["github_repo_lookup_failed"] == 1
    assert result.warnings == ["github_repo_lookup_failed:BlocUnited-LLC/mozaiks-app:404"]


def test_existing_app_preload_builds_context_graph_pack_for_github_repo_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module(
        "factory_app/workflows/ExistingAppDiscovery/tools/preload_discovery_context.py",
        "tests.preload_discovery_github_context_graph",
    )
    store = _install_memory_app_intelligence_store(monkeypatch)

    class _FakeResponse:
        def __init__(self, status_code: int, payload: dict):
            self.status_code = status_code
            self._payload = payload

        def json(self) -> dict:
            return self._payload

    tree = {
        "tree": [
            {"type": "blob", "path": "package.json", "size": 74},
            {"type": "blob", "path": "app/modules/listings/backend/service.py", "size": 84},
            {"type": "blob", "path": "app/ui/pages/Listings.jsx", "size": 110},
            {"type": "blob", "path": "README.md", "size": 40},
            {"type": "blob", "path": ".env", "size": 20},
            {"type": "blob", "path": "node_modules/react/index.js", "size": 20},
        ]
    }
    contents = {
        "package.json": b'{"dependencies": {"react": "^18.0.0", "fastapi": "^1.0.0"}}',
        "app/modules/listings/backend/service.py": (
            b"class ListingService:\n"
            b"    def list_listings(self):\n"
            b"        return []\n"
        ),
        "app/ui/pages/Listings.jsx": (
            b"export function ListingsPage() {\n"
            b"  return <main>Listings</main>;\n"
            b"}\n"
        ),
        "README.md": b"# Demo\nExisting app repository.\n",
    }

    async def _fake_github_request(url: str, token: str | None, **kwargs):
        assert token is None
        if "/git/trees/" in url:
            return _FakeResponse(200, tree)
        if url.endswith("/repos/Example/demo"):
            return _FakeResponse(
                200,
                {
                    "name": "demo",
                    "default_branch": "main",
                },
            )
        return _FakeResponse(404, {})

    async def _fake_fetch_github_file(owner: str, repo: str, path: str, ref: str, token: str | None, **kwargs):
        assert owner == "Example"
        assert repo == "demo"
        assert ref == "main"
        assert token is None
        assert "client" in kwargs
        return contents.get(path)

    monkeypatch.setattr(module, "_github_request", _fake_github_request)
    monkeypatch.setattr(module, "_fetch_github_file", _fake_fetch_github_file)

    context = _Context(
        app_id="app_1",
        github_repo="https://github.com/Example/demo",
        discovery_inputs={
            "github_repo": "https://github.com/Example/demo",
            "context_graph_scan_policy": {"max_files": 50},
        },
    )

    result = asyncio.run(module.collect_prechat_discovery_context(context_variables=context))

    assert result["success"] is True
    assert result["context_graph_status"] == "loaded"
    assert context["repo_summary"]["github_repo"] == "Example/demo"
    assert context["repo_summary"]["github_repo_input"] == "https://github.com/Example/demo"
    assert context["context_graph_health"]["source"] == "github_source_scan"
    assert context["context_graph_health"]["selected_file_count"] >= 3
    assert context["context_graph_health"]["skipped"]["sensitive_path"] == 1
    assert context["source_context_bundle"]["schema_version"] == "mozaiks.source_context.bundle.v1"
    assert "app/modules/listings/backend/service.py" in context["source_context_bundle"]["file_contents"]
    assert context["source_context_catalog"]["file_count"] >= 3
    assert context["app_intelligence_snapshot"]["schema_version"] == "mozaiks.app_intelligence.snapshot.v1"
    assert context["app_intelligence_catalog"]["architecture"]["module_roots"]
    assert context["app_intelligence_ready"] is True
    assert context["app_intelligence_status"] == "ready"
    assert context["app_intelligence_progress"]["stage"] == "ready"
    assert context["app_intelligence_progress"]["details"]["app_context_persisted"] is True
    assert context["current_app_context_version_id"] == context["current_context_version_id"]
    assert context["app_intelligence_registration"]["app_context_version_id"] == context["current_context_version_id"]
    assert [artifact.artifact_kind for artifact in store.created] == [
        "source_context_bundle",
        "app_context_graph",
        "app_intelligence_snapshot",
        "app_context_version",
    ]
    context_payload = store.created[-1].commit_metadata.metadata["summary_payload"]
    assert context_payload["source_refs"][0]["kind"] == "repo"
    assert context_payload["source_refs"][0]["uri"] == "https://github.com/Example/demo"
    assert context["context_graph_catalog"]["source_context_chunk_count"] >= 1
    assert "App Intelligence indexed" in context["preload_summary"]


def test_existing_app_refresh_preloads_prior_source_context_bundle_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module(
        "factory_app/workflows/ExistingAppDiscovery/tools/preload_discovery_context.py",
        "tests.preload_discovery_context_source_refresh",
    )

    from mozaiksai.control_plane import app_context as app_context_mod
    from mozaiksai.core.app_context import SourceCorpusBundle, build_source_corpus_bundle
    from mozaiksai.core.app_context.context_graph import build_context_graph_from_file_map
    from mozaiksai.core.app_context.scan_policy import select_source_file_map

    file_map = {
        "modules/tasks/backend/service.py": "def list_tasks():\n    return []\n",
    }
    graph = build_context_graph_from_file_map(
        app_id="app_1",
        artifact_version_id="av_graph_1",
        artifact_kind="app_context_graph",
        file_map=file_map,
    )
    bundle = build_source_corpus_bundle(
        app_id="app_1",
        scan_result=select_source_file_map(file_map, source="previous_app_context_graph"),
    )

    async def _prior_graph(**kwargs):
        assert kwargs["app_id"] == "app_1"
        assert kwargs["context_version_id"] == "ctx_before_refresh"
        return SimpleNamespace(graph=graph, warnings=[])

    async def _prior_source_bundle(**kwargs):
        assert kwargs["app_id"] == "app_1"
        assert kwargs["context_version_id"] == "ctx_before_refresh"
        return SimpleNamespace(bundle=SourceCorpusBundle.model_validate(bundle.model_dump(mode="json")), warnings=[])

    async def _prior_intelligence(**kwargs):
        assert kwargs["app_id"] == "app_1"
        assert kwargs["context_version_id"] == "ctx_before_refresh"
        return SimpleNamespace(snapshot=None, warnings=[])

    monkeypatch.setattr(app_context_mod, "get_app_context_graph_for_version", _prior_graph)
    monkeypatch.setattr(app_context_mod, "get_source_context_bundle_for_version", _prior_source_bundle)
    monkeypatch.setattr(app_context_mod, "get_app_intelligence_snapshot_for_version", _prior_intelligence)
    context = _Context(
        app_id="app_1",
        current_context_version_id="ctx_before_refresh",
        context_refresh_request={
            "app_id": "app_1",
            "current_context_version_id": "ctx_before_refresh",
            "reason": "Refresh repository snapshot.",
        },
        discovery_inputs={},
    )

    result = asyncio.run(module.collect_prechat_discovery_context(context_variables=context))

    assert result["context_graph_status"] == "loaded"
    assert context["source_context_bundle"]["bundle_id"] == bundle.bundle_id
    assert context["source_context_catalog"]["chunk_count"] >= 1
    assert context["app_intelligence_snapshot"]["schema_version"] == "mozaiks.app_intelligence.snapshot.v1"
    assert context["app_intelligence_catalog"]["coverage"]["file_count"] >= 1
    assert context["app_intelligence_ready"] is True
    assert context["app_intelligence_status"] == "ready"
    assert context["context_graph_catalog"]["source_context_chunk_count"] >= 1


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
                "app_name": "existing-product-host",
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
    assert context["existing_product_spec"]["app_name"] == "existing-product-host"
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
    assert "Gradual Modernization" in discovery_agents
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
    assert "MOZAIKS_APP_WORKSPACE_PATH" in preload_tool
    assert "mozaiks-app" not in preload_tool
    assert "ThemeCapture" in preload_tool


def test_brownfield_path_selector_explains_light_vs_migration_choice() -> None:
    selector_ui = _read_text(
        "factory_app/workflows/extended_orchestration/ui/transitions/BrownfieldPathSelector.js"
    )
    agents = _read_text("factory_app/workflows/ExistingAppDiscovery/agents.yaml")
    app_context_doc = _read_text("docs/architecture/foundations/app-context-and-brownfield-adoption.md")

    assert "Add AI Workflows" in selector_ui
    assert "Best first move for most existing apps" in selector_ui
    assert "Your app stays the source of truth" in selector_ui
    assert "Build App Features" in selector_ui
    assert "Advanced build" in selector_ui
    assert "Existing code" in selector_ui
    assert "AI workflows first" in selector_ui

    assert '"Add AI Workflows" maps to `light_integration`' in agents
    assert '"Build App Features" maps to `full_migration`' in agents
    assert "the user can add AI workflows first and build app features later" in agents

    assert "| Add AI Workflows | `light_integration` |" in app_context_doc
    assert "| Build App Features | `full_migration` |" in app_context_doc
    assert "not an automatic whole-repo rewrite" in app_context_doc


