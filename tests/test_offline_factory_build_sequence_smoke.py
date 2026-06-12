from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from mozaiksai.core.artifacts import (
    ArtifactLifecycleStatus,
    ArtifactValidationStatus,
    ArtifactVersionDoc,
)
from tests.import_utils import import_module_directly

_pack_config = import_module_directly("mozaiksai.core.workflow.pack.config")


class _MemoryArtifactStore:
    def __init__(self) -> None:
        self._versions: dict[tuple[str, str], list[ArtifactVersionDoc]] = {}
        self.create_calls: list[dict[str, Any]] = []
        self._counter = 0

    def seed(
        self,
        *,
        app_id: str,
        artifact_kind: str,
        artifact_key: str | None = None,
    ) -> ArtifactVersionDoc:
        artifact_key = artifact_key or artifact_kind
        self._counter += 1
        artifact = ArtifactVersionDoc(
            _id=f"av_{artifact_kind}_{self._counter}",
            app_id=app_id,
            artifact_kind=artifact_kind,
            artifact_key=artifact_key,
            version_number=1,
            lineage_root_id=f"av_{artifact_kind}_{self._counter}",
            lifecycle_status=ArtifactLifecycleStatus.CURRENT,
            validation_status=ArtifactValidationStatus.SKIPPED,
            commit_metadata={"metadata": {"summary_payload": {"seeded": artifact_kind}}},
        )
        self._versions.setdefault((artifact_kind, artifact_key), []).insert(0, artifact)
        return artifact

    async def list_artifact_versions(
        self,
        *,
        app_id: str,
        artifact_kind: str,
        artifact_key: str | None = None,
        lifecycle_status: ArtifactLifecycleStatus | None = None,
        limit: int = 1,
    ) -> list[ArtifactVersionDoc]:
        keys = [(artifact_kind, artifact_key)] if artifact_key else [
            key for key in self._versions if key[0] == artifact_kind
        ]
        versions: list[ArtifactVersionDoc] = []
        for key in keys:
            versions.extend(self._versions.get(key, []))
        versions = [item for item in versions if item.app_id == app_id]
        if lifecycle_status is not None:
            versions = [item for item in versions if item.lifecycle_status == lifecycle_status]
        return versions[:limit]

    async def create_artifact_version(self, **kwargs: Any) -> ArtifactVersionDoc:
        self.create_calls.append(dict(kwargs))
        self._counter += 1
        artifact = ArtifactVersionDoc(
            _id=f"av_{kwargs['artifact_kind']}_{self._counter}",
            app_id=kwargs["app_id"],
            artifact_kind=kwargs["artifact_kind"],
            artifact_key=kwargs["artifact_key"],
            version_number=1,
            parent_version_id=kwargs.get("parent_version_id"),
            lineage_root_id=f"av_{kwargs['artifact_kind']}_{self._counter}",
            canonical_inputs_version=dict(kwargs.get("canonical_inputs_version") or {}),
            lifecycle_status=kwargs["lifecycle_status"],
            validation_status=kwargs["validation_status"],
            commit_metadata=kwargs["commit_metadata"],
        )
        self._versions.setdefault((artifact.artifact_kind, artifact.artifact_key), []).insert(0, artifact)
        return artifact


def _use_repo_factory_workflows(monkeypatch) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("PLATFORM_PATH", str(repo_root / "__no_active_app__"))
    monkeypatch.setenv("MOZAIKS_WORKFLOWS_PATH", str(repo_root / "factory_app" / "workflows"))
    _pack_config._GLOBAL_CACHE = None


@pytest.mark.asyncio
async def test_offline_build_sequence_smoke_persists_agent_and_app_artifact_chain(monkeypatch) -> None:
    """Offline production smoke for the build journey.

    This uses the real factory registry and real summary artifact persistence,
    but replaces storage with an in-memory artifact store. It proves the
    cross-workflow contract without OpenAI, AG2 model calls, MongoDB, or HTTP.
    """

    _use_repo_factory_workflows(monkeypatch)
    graph = _pack_config.load_global_pack_graph()
    assert graph is not None

    build = next(sequence for sequence in graph.journeys if sequence.id == "build")
    workflow_steps = [step.workflows for step in build.steps if step.workflows]
    transition_steps = [step.transition for step in build.steps if step.transition]

    assert workflow_steps == [
        ["ValueEngine"],
        ["ThemeCapture"],
        ["DesignDocs"],
        ["AgentGenerator"],
        ["AppGenerator"],
    ]
    assert transition_steps == [
        "app_type_selector",
        "coding_journey_selector",
        "database_setup_selector",
        "app_review",
        "build_satisfaction_rating",
    ]
    assert graph.artifact_dependency_graph["workflow_bundle"] == ["design_docs"]
    assert "workflow_bundle" in graph.artifact_dependency_graph["app_bundle"]

    store = _MemoryArtifactStore()
    app_id = "offline-build-sequence-smoke"
    design_docs = store.seed(app_id=app_id, artifact_kind="design_docs")
    theme_capture = store.seed(app_id=app_id, artifact_kind="theme_capture")

    monkeypatch.setattr("mozaiksai.core.artifacts.summary_artifacts.get_artifact_store", lambda: store)

    from factory_app.workflows.AgentGenerator.tools.platform.build_lifecycle import (
        _persist_workflow_bundle_artifact,
    )
    from factory_app.workflows.AppGenerator.tools.platform.build_lifecycle import (
        _persist_app_bundle_artifact,
    )

    await _persist_workflow_bundle_artifact(
        app_id=app_id,
        chat_id="chat_agentgenerator",
        user_id="user_1",
        workflow_name="AgentGenerator",
        build_mode=None,
    )
    await _persist_app_bundle_artifact(
        app_id=app_id,
        chat_id="chat_appgenerator",
        user_id="user_1",
        workflow_name="AppGenerator",
        build_mode=None,
    )

    workflow_call = next(call for call in store.create_calls if call["artifact_kind"] == "workflow_bundle")
    app_call = next(call for call in store.create_calls if call["artifact_kind"] == "app_bundle")
    workflow_bundle = store._versions[("workflow_bundle", "workflow_bundle")][0]

    assert workflow_call["source_workflow"] == "AgentGenerator"
    assert workflow_call["canonical_inputs_version"] == {"design_docs": design_docs.id}

    app_inputs = app_call["canonical_inputs_version"]
    assert app_call["source_workflow"] == "AppGenerator"
    assert app_inputs["design_docs"] == design_docs.id
    assert app_inputs["theme_capture"] == theme_capture.id
    assert app_inputs["workflow_bundle"] == workflow_bundle.id
    assert "brand" not in app_inputs
    assert app_call["lifecycle_status"] == ArtifactLifecycleStatus.CURRENT
    assert app_call["validation_status"] == ArtifactValidationStatus.SKIPPED
