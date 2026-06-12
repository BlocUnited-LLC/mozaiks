from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from factory_app.control_plane.tools.get_context_graph_catalog import get_context_graph_catalog
from mozaiksai.control_plane.contracts import ControlPlaneToolContext
from mozaiksai.control_plane.workspace_snapshot import register_workspace_snapshot
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
async def test_register_workspace_snapshot_creates_artifact_and_context_graph(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "app" / "modules" / "wallet" / "backend").mkdir(parents=True)
    (workspace / "tests").mkdir()
    (workspace / "app" / "modules" / "wallet" / "backend" / "handler.py").write_text(
        "def checkout(payload):\n    return payload\n",
        encoding="utf-8",
    )
    (workspace / "tests" / "test_wallet.py").write_text("def test_checkout():\n    assert True\n", encoding="utf-8")

    store = _MemoryArtifactStore()
    result = await register_workspace_snapshot(
        app_id="app_1",
        workspace_root=workspace,
        artifact_store=store,
        generated_artifacts_root=tmp_path / "generated",
    )

    assert result.indexed_file_count == 2
    assert result.scan_health["selected_by_priority"]["app_modules"] == 1
    assert result.health_report["status"] == "healthy"
    assert result.health_report["coverage"]["core_surface_file_count"] == 1
    assert Path(result.artifact_path).exists()

    kinds = [artifact.artifact_kind for artifact in store.created]
    assert kinds == ["app_bundle", "app_context_graph", "app_context_version"]
    graph_payload = store.created[1].commit_metadata.metadata["summary_payload"]
    assert len(graph_payload["nodes"]) > 0
    assert store.created[1].commit_metadata.metadata["context_graph_health_report"]["status"] == "healthy"
    context_payload = store.created[2].commit_metadata.metadata["summary_payload"]
    assert context_payload["graph_snapshot_ref"] == result.graph_artifact_version_id
    assert context_payload["mode"] == "hybrid"


@pytest.mark.asyncio
async def test_workspace_snapshot_feeds_graph_aware_scope_catalog(tmp_path: Path) -> None:
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
    result = await register_workspace_snapshot(
        app_id="app_1",
        workspace_root=workspace,
        artifact_store=store,
        generated_artifacts_root=tmp_path / "generated",
    )

    catalog = await get_context_graph_catalog(
        context=ControlPlaneToolContext(
            checkpoint="scope_requested",
            app_id="app_1",
            artifact_kind="app_bundle",
            artifact_key="workspace_snapshot",
            artifact_version_id=result.app_bundle_artifact_version_id,
            raw_user_request="Update wallet checkout entitlement behavior",
        ),
        artifact_store=store,
    )

    candidate_paths = [item["path"] for item in catalog["candidate_files"]]
    assert candidate_paths[0].startswith("app/modules/wallet/")
    assert candidate_paths.index("app/modules/wallet/backend/handler.py") < candidate_paths.index("tests/test_wallet.py")
    assert catalog["scan_health"]["selected_by_priority"]["app_modules"] == 2

