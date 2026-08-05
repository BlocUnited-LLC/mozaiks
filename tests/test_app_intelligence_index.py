from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

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


@pytest.mark.asyncio
async def test_index_workspace_app_intelligence_creates_artifacts_and_context_version(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "app" / "modules" / "wallet" / "backend").mkdir(parents=True)
    (workspace / "tests").mkdir()
    (workspace / "app" / "modules" / "wallet" / "backend" / "handler.py").write_text(
        "def checkout(payload):\n    return payload\n",
        encoding="utf-8",
    )
    (workspace / "tests" / "test_wallet.py").write_text("def test_checkout():\n    assert True\n", encoding="utf-8")

    store = _MemoryArtifactStore()
    result = await index_workspace_app_intelligence(
        app_id="app_1",
        workspace_root=workspace,
        artifact_store=store,
        generated_artifacts_root=tmp_path / "generated",
    )

    assert result.indexed_file_count == 2
    assert result.scan_health["selected_by_priority"]["app_modules"] == 1
    assert result.health_report["status"] == "healthy"
    assert result.health_report["coverage"]["core_surface_file_count"] == 1
    assert result.framework_detection["schema_version"] == "mozaiks.framework_detection.v1"
    assert Path(result.artifact_path).exists()
    assert "app_intelligence" in Path(result.artifact_path).parts

    kinds = [artifact.artifact_kind for artifact in store.created]
    assert kinds == [
        "app_bundle",
        "source_context_bundle",
        "app_context_graph",
        "app_intelligence_snapshot",
        "app_context_version",
    ]
    app_bundle_metadata = store.created[0].commit_metadata.metadata
    assert app_bundle_metadata["framework_detection"]["schema_version"] == "mozaiks.framework_detection.v1"
    source_context_payload = store.created[1].commit_metadata.metadata["summary_payload"]
    assert source_context_payload["schema_version"] == "mozaiks.source_context.bundle.v1"
    assert source_context_payload["file_contents"]["app/modules/wallet/backend/handler.py"]
    assert result.source_context_artifact_version_id == store.created[1].id
    graph_payload = store.created[2].commit_metadata.metadata["summary_payload"]
    assert len(graph_payload["nodes"]) > 0
    assert store.created[2].commit_metadata.metadata["context_graph_health_report"]["status"] == "healthy"
    intelligence_payload = store.created[3].commit_metadata.metadata["summary_payload"]
    assert intelligence_payload["schema_version"] == "mozaiks.app_intelligence.snapshot.v1"
    assert result.app_intelligence_artifact_version_id == store.created[3].id
    context_payload = store.created[4].commit_metadata.metadata["summary_payload"]
    assert context_payload["graph_snapshot_ref"] == result.graph_artifact_version_id
    assert any(ref["artifact_kind"] == "source_context_bundle" for ref in context_payload["artifact_refs"])
    assert any(ref["artifact_kind"] == "app_intelligence_snapshot" for ref in context_payload["artifact_refs"])
    assert context_payload["mode"] == "hybrid"


@pytest.mark.asyncio
async def test_app_intelligence_index_feeds_graph_aware_scope_catalog(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "app" / "modules" / "wallet" / "backend").mkdir(parents=True)
    (workspace / "tests").mkdir()
    (workspace / "app" / "modules" / "wallet" / "module.yaml").write_text(
        "id: wallet\nactions:\n  - id: checkout\n",
        encoding="utf-8",
    )
    (workspace / "app" / "modules" / "wallet" / "backend" / "handler.py").write_text(
        "def checkout(payload):\n    return {'entitlement': payload.get('user_id')}\n",
        encoding="utf-8",
    )
    (workspace / "tests" / "test_wallet.py").write_text("def test_checkout_entitlement():\n    assert True\n", encoding="utf-8")

    store = _MemoryArtifactStore()
    result = await index_workspace_app_intelligence(
        app_id="app_1",
        workspace_root=workspace,
        artifact_store=store,
        generated_artifacts_root=tmp_path / "generated",
    )

    assert result.framework_detection["primary_framework_id"] == "mozaiks_app"
    assert any(item["framework_id"] == "mozaiks_app" for item in result.framework_detection["frameworks"])
    catalog = await get_context_graph_catalog(
        context=ControlPlaneToolContext(
            checkpoint="scope_requested",
            app_id="app_1",
            build_family="app_bundle",
            build_key=APP_INTELLIGENCE_WORKSPACE_ARTIFACT_KEY,
            build_record_id=result.app_bundle_artifact_version_id,
            raw_user_request="Update wallet checkout entitlement behavior",
        ),
        artifact_store=store,
    )

    candidate_paths = [item["path"] for item in catalog["candidate_files"]]
    assert candidate_paths[0].startswith("app/modules/wallet/")
    assert candidate_paths.index("app/modules/wallet/backend/handler.py") < candidate_paths.index("tests/test_wallet.py")
    assert catalog["scan_health"]["selected_by_priority"]["app_modules"] == 1
    assert catalog["scan_health"]["selected_by_priority"]["manifests"] == 1


@pytest.mark.asyncio
async def test_app_intelligence_index_feeds_persisted_source_context_tools(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "app" / "ui" / "pages").mkdir(parents=True)
    (workspace / "app" / "ui" / "services").mkdir(parents=True)
    (workspace / "app" / "ui" / "pages" / "Dashboard.jsx").write_text(
        "import { fetchMetrics } from '../services/metrics';\n"
        "export default function Dashboard() { return fetchMetrics(); }\n",
        encoding="utf-8",
    )
    (workspace / "app" / "ui" / "services" / "metrics.js").write_text(
        "export function fetchMetrics() { return fetch('/api/metrics'); }\n",
        encoding="utf-8",
    )

    store = _MemoryArtifactStore()
    result = await index_workspace_app_intelligence(
        app_id="app_1",
        workspace_root=workspace,
        artifact_store=store,
        generated_artifacts_root=tmp_path / "generated",
    )
    context = ControlPlaneToolContext(
        checkpoint="coding_requested",
        app_id="app_1",
        build_family="app_bundle",
        build_key=APP_INTELLIGENCE_WORKSPACE_ARTIFACT_KEY,
        build_record_id=result.app_bundle_artifact_version_id,
        raw_user_request="Update dashboard metrics",
    )

    search = await search_app_source_context(context=context, artifact_store=store)
    assert search["present"] is True
    assert search["source"] == "current_app_context"
    assert any(item["path"] == "app/ui/pages/Dashboard.jsx" for item in search["results"])

    read = await read_app_source_file(
        path="app/ui/pages/Dashboard.jsx",
        context=context,
        artifact_store=store,
    )
    assert read["present"] is True
    assert "fetchMetrics" in read["content"]

