from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from mozaiksai.core.app_context import (
    APP_CONTEXT_GRAPH_ARTIFACT_KIND,
    APP_CONTEXT_INDEX_SCHEMA_VERSION,
    APP_INTELLIGENCE_ARTIFACT_KIND,
    SOURCE_CONTEXT_ARTIFACT_KIND,
    default_context_graph_scan_policy,
    index_file_map,
    index_workspace_root,
    persist_app_context_index,
)
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


def test_index_file_map_builds_canonical_source_corpus_graph_and_health() -> None:
    indexed = index_file_map(
        app_id="app_1",
        artifact_version_id="av_app_1",
        artifact_kind="app_bundle",
        artifact_key="app_bundle",
        source="artifact_workspace",
        file_map={
            "app/ui/pages/Dashboard.jsx": (
                "import { loadDashboard } from '../services/api';\n"
                "export default function Dashboard() { return loadDashboard(); }\n"
            ),
            "app/ui/services/api.js": "export function loadDashboard() { return fetch('/api/dashboard'); }\n",
            "app/config/settings.js": 'export const API_TOKEN = "secret-token";\n',
        },
    )

    assert indexed.source_corpus.schema_version == "mozaiks.source_context.bundle.v1"
    assert indexed.scan_health["app_context_index_schema_version"] == APP_CONTEXT_INDEX_SCHEMA_VERSION
    assert indexed.health_report["status"] in {"healthy", "warning"}
    assert indexed.app_context_graph.app_id == "app_1"
    assert indexed.app_intelligence_snapshot.app_id == "app_1"
    assert indexed.app_intelligence_snapshot.source_context_bundle_id == indexed.source_corpus.bundle_id
    assert indexed.app_intelligence_snapshot.coverage["file_count"] == len(indexed.source_corpus.files)
    assert indexed.app_intelligence_snapshot.agent_context_policy["policy"] == "retrieve_not_dump"
    assert indexed.source_corpus.file_contents["app/config/settings.js"].count("[REDACTED]") == 1
    assert any(file.role == "page" for file in indexed.source_corpus.files)
    assert any(node.metadata.get("path") == "app/ui/pages/Dashboard.jsx" for node in indexed.app_context_graph.nodes)


def test_index_workspace_root_respects_scan_policy_and_source_ref(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text(
        "def run():\n    return 'ok'\n",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text("IGNORED_ENV_VALUE=must-not-index\n", encoding="utf-8")

    indexed = index_workspace_root(
        app_id="app_2",
        workspace_root=tmp_path,
        artifact_version_id="av_workspace",
        scan_policy=default_context_graph_scan_policy({"max_files": 20}),
    )

    assert indexed.scan_health["selected_file_count"] == 1
    assert "src/main.py" in indexed.safe_file_map
    assert ".env" not in indexed.safe_file_map
    assert indexed.source_ref.uri == "artifact-version://av_workspace"


@pytest.mark.asyncio
async def test_persist_app_context_index_creates_source_and_graph_artifacts() -> None:
    indexed = index_file_map(
        app_id="app_1",
        artifact_version_id="av_app_1",
        file_map={"src/App.jsx": "export default function App() { return <main />; }\n"},
    )
    store = _MemoryArtifactStore()

    persisted = await persist_app_context_index(
        index=indexed,
        artifact_store=store,
        source_workflow="test_workflow",
    )

    assert persisted.source_context_artifact.artifact_kind == SOURCE_CONTEXT_ARTIFACT_KIND
    assert persisted.graph_artifact.artifact_kind == APP_CONTEXT_GRAPH_ARTIFACT_KIND
    assert persisted.intelligence_artifact.artifact_kind == APP_INTELLIGENCE_ARTIFACT_KIND
    assert [artifact.artifact_kind for artifact in store.created] == [
        "source_context_bundle",
        "app_context_graph",
        "app_intelligence_snapshot",
    ]
    metadata = store.created[0].commit_metadata.metadata
    assert metadata["summary_payload"]["bundle_id"] == indexed.source_corpus.bundle_id
    assert metadata["index_schema_version"] == APP_CONTEXT_INDEX_SCHEMA_VERSION
