from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from factory_app.refinement_harness.tools.app_intelligence import get_app_intelligence_context
from factory_app.refinement_harness.tools.get_context_graph_catalog import get_context_graph_catalog
from factory_app.refinement_harness.tools.source_context import (
    read_app_source_file,
    search_app_source_context,
)
from mozaiksai.control_plane.app_intelligence import (
    APP_INTELLIGENCE_WORKSPACE_ARTIFACT_KEY,
    index_workspace_app_intelligence,
)
from mozaiksai.control_plane.contracts import ControlPlaneToolContext
from mozaiksai.core.artifacts.models import (
    ArtifactLifecycleStatus,
    ArtifactValidationStatus,
    ArtifactVersionDoc,
)


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

    async def get_artifact_version(
        self,
        *,
        app_id: str,
        artifact_version_id: str,
    ) -> ArtifactVersionDoc | None:
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
        artifact = await self.get_artifact_version(
            app_id=app_id,
            artifact_version_id=artifact_version_id,
        )
        if artifact is None:
            return None
        updates: dict[str, Any] = {"lifecycle_status": ArtifactLifecycleStatus.CURRENT}
        if commit_metadata is not None:
            updates["commit_metadata"] = commit_metadata
        refreshed = artifact.model_copy(update=updates)
        self.created = [refreshed if item.id == artifact.id else item for item in self.created]
        return refreshed


class _FakeTransport:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def send_tool_call_event(
        self,
        *,
        event_id: str,
        chat_id: str | None,
        tool_name: str,
        component_name: str,
        display_type: str,
        payload: dict[str, Any],
        awaiting_response: bool = True,
        agent_name: str | None = None,
    ) -> None:
        self.events.append(
            {
                "event_id": event_id,
                "chat_id": chat_id,
                "tool_name": tool_name,
                "component_name": component_name,
                "display_type": display_type,
                "payload": payload,
                "awaiting_response": awaiting_response,
                "agent_name": agent_name,
            }
        )


def _workspace_root(app_root: Path) -> Path:
    if app_root.name == "app" and (app_root.parent / "app" / "app.json").exists():
        return app_root.parent
    return app_root


def _path_values(items: list[dict[str, Any]]) -> list[str]:
    return [str(item.get("path") or "") for item in items if item.get("path")]


@pytest.mark.asyncio
async def test_active_workspace_app_intelligence_feeds_refinement_and_chat_ui(
    app_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace_root = _workspace_root(app_root)
    store = _MemoryArtifactStore()

    registration = await index_workspace_app_intelligence(
        app_id="workspace_app_intelligence_acceptance",
        workspace_root=workspace_root,
        artifact_store=store,
        generated_artifacts_root=tmp_path / "generated",
        source_workflow="app_intelligence_acceptance",
        source_chat_id="chat_app_intelligence_acceptance",
    )

    assert registration.indexed_file_count >= 25
    assert registration.health_report["status"] in {"healthy", "warning"}
    assert [artifact.artifact_kind for artifact in store.created] == [
        "app_bundle",
        "source_context_bundle",
        "app_context_graph",
        "app_intelligence_snapshot",
        "app_context_version",
    ]
    assert store.created[-1].lifecycle_status == ArtifactLifecycleStatus.CURRENT

    tool_context = ControlPlaneToolContext(
        checkpoint="coding_requested",
        app_id="workspace_app_intelligence_acceptance",
        artifact_kind="app_bundle",
        artifact_key=APP_INTELLIGENCE_WORKSPACE_ARTIFACT_KEY,
        artifact_version_id=registration.app_bundle_artifact_version_id,
        raw_user_request="Update onboarding tour app build flow",
    )

    intelligence = await get_app_intelligence_context(
        query="onboarding app build",
        context=tool_context,
        artifact_store=store,
    )
    assert intelligence["present"] is True
    assert intelligence["source"] == "current_app_context"
    catalog = intelligence["app_intelligence_catalog"]
    coverage = catalog["coverage"]
    assert coverage["file_count"] == registration.indexed_file_count
    assert coverage["symbol_count"] > 0
    assert coverage["import_count"] > 0
    assert coverage["node_count"] > 0
    assert coverage["edge_count"] > 0
    assert coverage["parser_status"]["languages"]["python"]["active_parser"] in {
        "tree_sitter",
        "python_ast",
    }
    assert catalog["agent_context_policy"]["policy"] == "retrieve_not_dump"
    assert "file_contents" not in str(catalog)
    assert any(result["section"] in {"architecture", "capabilities"} for result in intelligence["results"])

    source_search = await search_app_source_context(
        query="onboarding tour",
        context=tool_context,
        artifact_store=store,
    )
    assert source_search["present"] is True
    onboarding_paths = [
        path
        for path in _path_values(source_search["results"])
        if "onboarding" in path.lower()
    ]
    assert onboarding_paths

    source_read = await read_app_source_file(
        path=onboarding_paths[0],
        context=tool_context,
        artifact_store=store,
    )
    assert source_read["present"] is True
    assert "onboarding" in source_read["content"].lower()

    graph_catalog = await get_context_graph_catalog(
        context=tool_context,
        artifact_store=store,
    )
    candidate_paths = _path_values(graph_catalog["candidate_files"])
    assert any("onboarding" in path.lower() for path in candidate_paths)

    from factory_app.workflows.ExistingAppDiscovery.tools.emit_app_intelligence_overview import (
        emit_app_intelligence_overview_card,
    )
    from mozaiksai.core.transport import simple_transport as transport_mod

    fake_transport = _FakeTransport()

    async def _get_instance() -> _FakeTransport:
        return fake_transport

    monkeypatch.setattr(transport_mod.SimpleTransport, "get_instance", staticmethod(_get_instance))

    emitted = await emit_app_intelligence_overview_card(
        context_variables={
            "chat_id": "chat_app_intelligence_acceptance",
            "app_id": "workspace_app_intelligence_acceptance",
            "preload_status": "ready",
            "app_intelligence_catalog": catalog,
            "repo_summary": {"repo_name": workspace_root.name, "source": "app_intelligence_index"},
        }
    )

    assert emitted["success"] is True
    assert len(fake_transport.events) == 1
    event = fake_transport.events[0]
    assert event["chat_id"] == "chat_app_intelligence_acceptance"
    assert event["tool_name"] == "AppIntelligenceOverviewCard"
    assert event["component_name"] == "AppIntelligenceOverviewCard"
    assert event["display_type"] == "artifact"
    assert event["awaiting_response"] is False
    assert event["payload"]["interaction_type"] == "ui_surface"
    assert event["payload"]["app_intelligence_catalog"]["coverage"]["file_count"] == registration.indexed_file_count
